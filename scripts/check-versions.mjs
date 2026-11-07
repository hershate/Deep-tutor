#!/usr/bin/env node
// 校验版本号一致（package.json / tauri.conf.json / Cargo.toml / 根 README）。
// 升版漏改任一处时在 lint/check 阶段直接失败，而不是静默产出错版本安装包
// （15#9/C-1：README 版本行纳入白名单；路径以脚本位置定位，不依赖 cwd）。
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const read = path => readFileSync(join(root, path), 'utf8')

const packageVersion = JSON.parse(read('desktop/package.json')).version
const tauriVersion = JSON.parse(read('desktop/src-tauri/tauri.conf.json')).version
const cargoToml = read('desktop/src-tauri/Cargo.toml')
const match = cargoToml.match(/^version\s*=\s*"([^"]+)"/m)
if (!match) {
  console.error('check-versions: Cargo.toml 缺少 version 字段')
  process.exit(1)
}
const cargoVersion = match[1]
const readmeVersion = read('README.md').match(/\*\*版本\s*(\d+\.\d+\.\d+)/)?.[1]
if (!readmeVersion) {
  console.error('check-versions: 根 README.md 未找到「**版本 x.y.z」声明')
  process.exit(1)
}

const versions = { 'desktop/package.json': packageVersion, 'desktop/package-lock.json': JSON.parse(read('desktop/package-lock.json')).version, 'desktop/src-tauri/tauri.conf.json': tauriVersion, 'desktop/src-tauri/Cargo.toml': cargoVersion, 'README.md': readmeVersion }
const unique = new Set(Object.values(versions))
if (unique.size !== 1) {
  console.error('check-versions: 版本号不一致，请同步后重试：')
  for (const [file, version] of Object.entries(versions)) {
    console.error(`  ${file}: ${version}`)
  }
  process.exit(1)
}
console.log(`check-versions: 五处版本一致 (${packageVersion})`)
