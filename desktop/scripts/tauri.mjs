import { spawn } from 'node:child_process'
import { copyFile, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktop = fileURLToPath(new URL('..', import.meta.url))
const repository = path.resolve(desktop, '..')
const cli = path.join(desktop, 'node_modules', '@tauri-apps', 'cli', 'tauri.js')
const args = process.argv.slice(2)

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
  // 79#4：按 --target 参数推导 bundle 目录——原先硬编码 release 路径，
  // `--target <triple>`/`--debug` 构建时找不到安装包误报；同版本旧文件
  // 复制前清理，失败残留不再被误当新产物
  const targetIndex = args.indexOf('--target')
  const triple = targetIndex >= 0 ? args[targetIndex + 1] : null
  const profile = args.includes('--debug') ? 'debug' : 'release'
  const targetDir = path.join(desktop, 'src-tauri', 'target', ...(triple ? [triple, profile] : [profile]))
  const bundle = path.join(targetDir, 'bundle', 'nsis')
  const installers = (await readdir(bundle, { withFileTypes: true }))
    .filter(entry => entry.isFile() && entry.name.includes(`_${config.version}_`) && entry.name.toLowerCase().endsWith('-setup.exe'))
  if (installers.length !== 1) throw new Error(`应当且只能生成一个 ${config.version} NSIS 安装包`)
  const source = path.join(bundle, installers[0].name)
  const destinationDirectory = path.join(repository, 'dist')
  const destination = path.join(destinationDirectory, installers[0].name)
  await mkdir(destinationDirectory, { recursive: true })
  await rm(destination, { force: true })
  await rm(`${destination}.sha256`, { force: true })
  await copyFile(source, destination)
  const hash = bytes => createHash('sha256').update(bytes).digest('hex')
  const expected = hash(await readFile(source))
  const actual = hash(await readFile(destination))
  if (expected !== actual) throw new Error('安装包复制后的 SHA-256 校验失败')
  // 79#1：摘要落盘随产物分发——原先只做同机复制自比对即丢弃，终端用户
  // （尤其 artifact 下载者）无法校验分发物完整性
  await writeFile(`${destination}.sha256`, `${actual}  ${installers[0].name}\n`, 'utf8')
  console.log(`安装包已输出：dist/${installers[0].name}`)
  console.log(`SHA-256：${actual}`)
}

main().catch(error => {
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
})
