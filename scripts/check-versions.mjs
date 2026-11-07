#!/usr/bin/env node
// 校验三处版本号一致（package.json / tauri.conf.json / Cargo.toml）。
// 升版漏改任一处时在 lint/check 阶段直接失败，而不是静默产出错版本安装包。
import { readFileSync } from 'node:fs'

const packageVersion = JSON.parse(readFileSync('desktop/package.json', 'utf8')).version
const tauriVersion = JSON.parse(readFileSync('desktop/src-tauri/tauri.conf.json', 'utf8')).version
const cargoToml = readFileSync('desktop/src-tauri/Cargo.toml', 'utf8')
const match = cargoToml.match(/^version\s*=\s*"([^"]+)"/m)
if (!match) {
  console.error('check-versions: Cargo.toml 缺少 version 字段')
  process.exit(1)
}
const cargoVersion = match[1]

const versions = { 'package.json': packageVersion, 'tauri.conf.json': tauriVersion, 'Cargo.toml': cargoVersion }
const unique = new Set(Object.values(versions))
if (unique.size !== 1) {
  console.error('check-versions: 版本号不一致，请同步后重试：')
  for (const [file, version] of Object.entries(versions)) {
    console.error(`  ${file}: ${version}`)
  }
  process.exit(1)
}
console.log(`check-versions: 三处版本一致 (${packageVersion})`)
