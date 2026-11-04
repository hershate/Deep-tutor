import { spawn } from 'node:child_process'
import { copyFile, mkdir, readFile, readdir } from 'node:fs/promises'
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
  const bundle = path.join(desktop, 'src-tauri', 'target', 'release', 'bundle', 'nsis')
  const installers = (await readdir(bundle, { withFileTypes: true }))
    .filter(entry => entry.isFile() && entry.name.includes(`_${config.version}_`) && entry.name.toLowerCase().endsWith('-setup.exe'))
  if (installers.length !== 1) throw new Error(`应当且只能生成一个 ${config.version} NSIS 安装包`)
  const source = path.join(bundle, installers[0].name)
  const destinationDirectory = path.join(repository, 'dist')
  const destination = path.join(destinationDirectory, installers[0].name)
  await mkdir(destinationDirectory, { recursive: true })
  await copyFile(source, destination)
  const hash = bytes => createHash('sha256').update(bytes).digest('hex')
  const expected = hash(await readFile(source))
  const actual = hash(await readFile(destination))
  if (expected !== actual) throw new Error('安装包复制后的 SHA-256 校验失败')
  console.log(`安装包已输出：dist/${installers[0].name}`)
}

main().catch(error => {
  console.error(error instanceof Error ? error.message : error)
  process.exitCode = 1
})
