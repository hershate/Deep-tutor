// 构建产物发布与清理：把「dist/ 只保留当前安装包及摘要」从人工维护变成代码保证。
//
// 复核基点：desktop/scripts/tauri.mjs 原先只删除并重写与当前版本同名的
// destination 与 `.sha256`，bundle 目录与仓库根 `dist/` 中的旧版本 NSIS
// 产物会长期并存——人工取包或上传 CI 产物（.github/workflows/ci.yml 上传
// `dist/*.exe`）时容易误取旧版本。该保证此前只由人工删除维持。
//
// 清理范围限定为 `<productName>_<版本号>_<arch>-setup.exe` 及其 `.sha256`
// 摘要；目录中的其他文件（用户放入的文件、其他命名）一律不动。
//
// 发布顺序由 publishInstaller 固定：复制安装包 → 双向 SHA-256 校验 → 写摘要
// → 清理 dist/ 与 NSIS bundle 目录中的旧版本安装包。清理放在最后，保证
// 「先产出再清理」：清理失败时新安装包与摘要已在盘上，错误信息显式区分
// 「清理失败」与「打包失败」，便于人工判断。

import { copyFile, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'

const SUMMARY_SUFFIX = '.sha256'

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 从安装包文件名解析版本号；不是本产品的安装包返回 null。
 * 例：`DeepTutor_1.1.16_x64-setup.exe` → `1.1.16`。
 */
export function installerVersion(name, productName) {
  if (typeof productName !== 'string' || productName.length === 0) return null
  const matched = new RegExp(`^${escapeRegExp(productName)}_(.+)_[^_]+-setup\\.exe$`).exec(name)
  return matched ? matched[1] : null
}

/**
 * 删除目录中版本号不等于 `currentVersion` 的安装包及其摘要，
 * 返回被删除的文件名（目录内的相对名）。
 */
export async function removeStaleInstallers(directory, { productName, currentVersion }) {
  const entries = await readdir(directory, { withFileTypes: true })
  const removed = []
  for (const entry of entries) {
    if (!entry.isFile()) continue
    const base = entry.name.endsWith(SUMMARY_SUFFIX)
      ? entry.name.slice(0, -SUMMARY_SUFFIX.length)
      : entry.name
    const version = installerVersion(base, productName)
    if (version === null || version === currentVersion) continue
    await rm(path.join(directory, entry.name), { force: true })
    removed.push(entry.name)
  }
  return removed
}

/** 仓库根 `dist/` 目录——安装包与摘要的唯一交付位置 */
export function resolveDistDirectory(repository) {
  return path.join(repository, 'dist')
}

/**
 * 按构建参数推导 NSIS bundle 目录：
 * `--target <triple>` → `src-tauri/target/<triple>/<profile>/bundle/nsis`；
 * 否则 `src-tauri/target/<profile>/bundle/nsis`（profile = debug | release）。
 * `--target=<triple>` 形式与空格形式等价（clap 两种写法都接受）。
 */
export function resolveBundleDirectory(desktop, args = []) {
  const targetIndex = args.indexOf('--target')
  const inlineTarget = args.find(argument => argument.startsWith('--target='))
  const triple = inlineTarget
    ? inlineTarget.slice('--target='.length)
    : targetIndex >= 0
      ? args[targetIndex + 1]
      : null
  const profile = args.includes('--debug') ? 'debug' : 'release'
  const targetDir = path.join(
    desktop,
    'src-tauri',
    'target',
    ...(triple ? [triple, profile] : [profile]),
  )
  return path.join(targetDir, 'bundle', 'nsis')
}

/**
 * 发布安装包到 `dist/`：复制 → 双向 SHA-256 校验 → 写摘要 →
 * 清理 `dist/` 与 NSIS bundle 目录中的旧版本安装包。
 *
 * `onStep` 按执行顺序收到步骤名（`copy` / `sha256` / `cleanup:dist` /
 * `cleanup:bundle`），供构建脚本回归测试断言「清理发生在复制与摘要之后」
 * 以及「两个目录都被清理」——这两点是 P-154 的关键不变量。
 */
export async function publishInstaller({
  source,
  destinationDirectory,
  bundleDirectory,
  productName,
  currentVersion,
  onStep = () => {},
}) {
  const name = path.basename(source)
  const destination = path.join(destinationDirectory, name)
  await mkdir(destinationDirectory, { recursive: true })
  // 同版本旧文件复制前清理，失败残留不再被误当新产物
  await rm(destination, { force: true })
  await rm(`${destination}.sha256`, { force: true })
  await copyFile(source, destination)
  const hash = bytes => createHash('sha256').update(bytes).digest('hex')
  const expected = hash(await readFile(source))
  const actual = hash(await readFile(destination))
  if (expected !== actual) throw new Error('安装包复制后的 SHA-256 校验失败')
  onStep('copy')
  // 摘要落盘随产物分发——终端用户（尤其 artifact 下载者）可校验完整性
  await writeFile(`${destination}.sha256`, `${actual}  ${name}\n`, 'utf8')
  onStep('sha256')
  const removed = []
  try {
    removed.push(...(await removeStaleInstallers(destinationDirectory, { productName, currentVersion })))
    onStep('cleanup:dist')
    removed.push(...(await removeStaleInstallers(bundleDirectory, { productName, currentVersion })))
    onStep('cleanup:bundle')
  } catch (error) {
    // 旧安装包被占用（安全软件扫描、资源管理器预览、未关闭的安装程序）时
    // rm 抛 EPERM/EBUSY：此时新安装包与摘要已落盘，构建判失败但产物可用。
    // 错误信息显式带上「清理失败」，避免与打包失败混淆。
    throw new Error(`旧版本安装包清理失败：${error instanceof Error ? error.message : error}`)
  }
  return { destination, name, actual, removed }
}
