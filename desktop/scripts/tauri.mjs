import { spawn } from 'node:child_process'
import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  publishInstaller,
  resolveBundleDirectory,
  resolveDistDirectory,
} from './artifacts.mjs'

const desktop = fileURLToPath(new URL('..', import.meta.url))
const repository = path.resolve(desktop, '..')
const cli = path.join(desktop, 'node_modules', '@tauri-apps', 'cli', 'tauri.js')
const args = process.argv.slice(2)

/**
 * 构建成功后发布安装包：定位当前版本的 NSIS 产物 → 复制到仓库根 `dist/` →
 * 双向 SHA-256 校验 → 写摘要 → 清理 `dist/` 与 bundle 目录中的旧版本安装包。
 *
 * 导出为独立函数，构建脚本回归测试（build-test.mjs）以临时目录调用，
 * 覆盖「dist/ 取仓库根」「bundle 目录按 --target/--debug 推导」
 * 「两个目录都被清理」三条接线口径——只测 artifacts.mjs 的函数覆盖不到
 * 本函数传入的目录来源。
 */
export async function publishBuildArtifacts({ desktop: desktopRoot, repository: repositoryRoot, config, args: buildArgs = [] }) {
  // 79#4：按 --target 参数推导 bundle 目录——原先硬编码 release 路径，
  // `--target <triple>`/`--debug` 构建时找不到安装包误报；同版本旧文件
  // 复制前清理，失败残留不再被误当新产物
  const bundle = resolveBundleDirectory(desktopRoot, buildArgs)
  const installers = (await readdir(bundle, { withFileTypes: true }))
    .filter(entry => entry.isFile() && entry.name.includes(`_${config.version}_`) && entry.name.toLowerCase().endsWith('-setup.exe'))
  if (installers.length !== 1) throw new Error(`应当且只能生成一个 ${config.version} NSIS 安装包`)
  const source = path.join(bundle, installers[0].name)
  // P-154：旧版本安装包清理——原先只覆盖与当前版本同名的文件，bundle 目录与
  // 仓库根 dist/ 会残留多版本安装包，人工取包或上传 CI 产物时容易误取旧版；
  // 「dist/ 只保留当前安装包及摘要」此前只由人工维护，现由代码保证
  const published = await publishInstaller({
    source,
    destinationDirectory: resolveDistDirectory(repositoryRoot),
    bundleDirectory: bundle,
    productName: config.productName,
    currentVersion: config.version,
  })
  return { ...published, bundle }
}

/** 是否作为入口脚本直接执行（被测试 import 时不触发 Tauri CLI） */
function isMainModule() {
  const entry = process.argv[1]
  if (!entry) return false
  const resolved = path.resolve(entry)
  const self = fileURLToPath(import.meta.url)
  return process.platform === 'win32'
    ? resolved.toLowerCase() === self.toLowerCase()
    : resolved === self
}

async function main() {
  const code = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [cli, ...args], { cwd: desktop, stdio: 'inherit' })
    child.once('error', reject)
    child.once('exit', resolve)
  })
  if (code !== 0) {
    process.exitCode = typeof code === 'number' ? code : 1
    return
  }
  if (args[0] !== 'build') return

  const config = JSON.parse(await readFile(path.join(desktop, 'src-tauri', 'tauri.conf.json'), 'utf8'))
  const published = await publishBuildArtifacts({ desktop, repository, config, args })
  if (published.removed.length > 0) console.log(`已清理旧版本安装包：${published.removed.join('、')}`)
  console.log(`安装包已输出：dist/${published.name}`)
  console.log(`SHA-256：${published.actual}`)
}

if (isMainModule()) {
  main().catch(error => {
    console.error(error instanceof Error ? error.message : error)
    process.exitCode = 1
  })
}
