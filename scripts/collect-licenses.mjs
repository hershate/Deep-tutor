#!/usr/bin/env node
// 汇总第三方许可文本，供 NSIS 安装包随包分发（履行 MIT 第 1 条等分发义务）。
// 输出 desktop/src-tauri/licenses/（构建产物，不入库），tauri.conf.json 的
// bundle.resources 会把它打进安装包。
// 来源：npm 依赖的 node_modules/*/LICENSE*；Rust 依赖经 `cargo metadata`
// 定位本地源码目录中的 LICENSE*。cargo 不可用时跳过 Rust 部分并在清单中注明。
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// 15#7：以脚本自身位置定位仓库根，不依赖 process.cwd()——CI 的 job 级
// working-directory 或任意子目录运行时，相对 cwd 的路径会整体落空且静默
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outDir = join(root, 'desktop/src-tauri/licenses')
mkdirSync(outDir, { recursive: true })

const copied = []

// 1) npm 依赖
const modules = join(root, 'desktop/node_modules')
if (existsSync(modules)) {
  for (const entry of readdirSync(modules, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const scopeDir = entry.name.startsWith('@') ? join(modules, entry.name) : null
    const candidates = scopeDir
      ? readdirSync(scopeDir, { withFileTypes: true }).filter(child => child.isDirectory()).map(child => join(scopeDir, child.name))
      : [join(modules, entry.name)]
    for (const dir of candidates) {
      const license = readdirSync(dir).find(file => /^licen[cs]e|notice/i.test(file))
      if (license) {
        const name = (scopeDir ? `${entry.name}__${basename(dir)}` : entry.name).replace(/[^a-z0-9._@-]/gi, '_')
        copyFileSync(join(dir, license), join(outDir, `npm-${name}-${license}`))
        copied.push(`npm:${name}`)
      }
    }
  }
}

// 2) Rust 依赖（cargo metadata → 本地源码目录）
let cargoSkipped = false
let cargoMissing = 0
try {
  const metadata = JSON.parse(execFileSync('cargo', ['metadata', '--format-version', '1', '--manifest-path', join(root, 'desktop/src-tauri/Cargo.toml')], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }))
  for (const pkg of metadata.packages) {
    if (!pkg.manifest_path) continue
    try {
      const dir = join(pkg.manifest_path, '..')
      if (!existsSync(dir)) { cargoMissing += 1; continue }
      const license = readdirSync(dir).find(file => /^licen[cs]e|notice/i.test(file))
      if (license) {
        const name = `${pkg.name}-${pkg.version}`.replace(/[^a-z0-9._-]/gi, '_')
        copyFileSync(join(dir, license), join(outDir, `cargo-${name}-${license}`))
        copied.push(`cargo:${pkg.name}`)
      }
    } catch {
      // 单个依赖源码未解包（sparse registry 仅在构建时解包）：跳过并计数
      cargoMissing += 1
    }
  }
} catch {
  cargoSkipped = true
}

writeFileSync(join(outDir, 'COLLECTED.md'), `# 本次构建收录清单

本清单由 scripts/collect-licenses.mjs 在构建时生成，随安装包分发；README.md 为入库占位说明。
已收录 ${copied.length} 个依赖的许可/声明文件${cargoSkipped ? '（Rust 部分因 cargo 不可用被跳过，请在可运行 cargo 的环境重新构建）' : cargoMissing ? `（另有 ${cargoMissing} 个 Rust 依赖源码未解包，许可以 Cargo.lock 记录的上游仓库为准）` : ''}。
完整依赖版本清单见安装包同仓库的 Cargo.lock 与 package-lock.json。
`)
console.log(`collect-licenses: ${copied.length} 个依赖许可已汇总${cargoSkipped ? '（Rust 部分跳过）' : ''}`)

// 15#7：零收录属于管线失效（依赖缺失/路径漂移），不能以成功退出——
// 否则安装包「许可汇总」为空而 NOTICE 的合规承诺形同虚设
if (copied.length === 0) {
  console.error('collect-licenses: 未收录任何依赖许可，请检查 node_modules 与 cargo 环境')
  process.exit(1)
}
