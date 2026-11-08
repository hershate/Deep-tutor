#!/usr/bin/env node
// 汇总第三方许可文本，供 NSIS 安装包随包分发（履行 MIT 第 1 条等分发义务）。
// 输出 desktop/src-tauri/licenses/（构建产物，不入库），tauri.conf.json 的
// bundle.resources 会把它打进安装包。
// 来源：npm 依赖的 node_modules/*/LICENSE*；Rust 依赖经 `cargo metadata`
// 定位本地源码目录中的 LICENSE*。cargo 不可用时跳过 Rust 部分并在清单中注明。
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { gunzipSync } from 'node:zlib'
import { execFileSync } from 'node:child_process'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// 15#7：以脚本自身位置定位仓库根，不依赖 process.cwd()——CI 的 job 级
// working-directory 或任意子目录运行时，相对 cwd 的路径会整体落空且静默
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outDir = join(root, 'desktop/src-tauri/licenses')
mkdirSync(outDir, { recursive: true })
// 清理上一轮收录（README.md 占位与 COLLECTED.md 留存重写）：依赖移除或挑选
// 优先级修正后，过期文件不得继续随包分发
for (const stale of readdirSync(outDir)) {
  if ((stale.startsWith('npm-') || stale.startsWith('cargo-')) && statSync(join(outDir, stale)).isFile()) {
    rmSync(join(outDir, stale))
  }
}

const copied = []

const licensePattern = /^licen[cs]e|notice/i

// 49#1：许可文件按优先级挑选，全文优先、SPDX 元数据存根最后——readdir
// 字母序中 `.`(0x2E) < `_`(0x5F)，`LICENSE.spdx` 恒排第一，旧实现 find
// 首个命中即止，会把 20 行 SPDX-2.1 元数据清单当许可全文收录
function licenseScore(name) {
  const lower = name.toLowerCase()
  if (lower === 'license' || lower === 'license.txt' || lower === 'license.md') return 0
  if (/^licen[cs]e[-_.]?(mit|apache|mpl|bsd|gpl|isc|unlicense)/.test(lower) || /^copying[._-]|^copying$/.test(lower)) return 1
  if (lower.endsWith('.spdx')) return 9
  return 2
}

function pickFromNames(names, fileCheck) {
  let best = null
  let bestScore = Infinity
  for (const name of names) {
    if (!licensePattern.test(name)) continue
    if (fileCheck && !fileCheck(name)) continue
    const score = licenseScore(name)
    if (score < bestScore) {
      best = name
      bestScore = score
    }
  }
  return best
}

// 49#3：包内无许可文件时依据 package.json 元数据生成声明（MIT 附标准全文，
// 持有人取 author 字段——不虚构年份与持有人）；其余 SPDX 标识只生成声明
const MIT_TEXT = `Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
`

function generatedNotice(meta) {
  const spdx = typeof meta.license === 'string' ? meta.license.trim() : null
  if (!spdx) return null
  const authorRaw = typeof meta.author === 'string' ? meta.author : (meta.author && meta.author.name) || null
  const holder = authorRaw ? authorRaw.replace(/\s*[<（(].*$/, '').trim() : null
  const repoRaw = typeof meta.repository === 'string' ? meta.repository : (meta.repository && meta.repository.url) || null
  const lines = [
    `# 许可声明（构建时依据包内 package.json 元数据生成）`,
    '',
    `${meta.name}@${meta.version} 声明 ${spdx} 许可；作者：${holder ?? '见上游仓库'}；上游仓库：${repoRaw ?? '见 npm registry'}。`,
    '该包发布物内未随附许可全文文件，本声明由 collect-licenses.mjs 生成以随包披露来源。',
  ]
  if (spdx.toUpperCase() === 'MIT' && holder) {
    lines.push('', '----- MIT License -----', '', 'MIT License', '', `Copyright (c) ${holder}`, '', MIT_TEXT)
  }
  return { spdx, text: `${lines.join('\n')}\n` }
}

// 49#3：开发依赖不进安装包（vite/typescript 等仅构建期使用）；读 package-lock
// 的 dev 标记判定，锁文件缺失或不可解析时回退全量收录（宁多勿漏）
function runtimeFilter() {
  try {
    const lock = JSON.parse(readFileSync(join(root, 'desktop/package-lock.json'), 'utf8'))
    if (lock.lockfileVersion < 3 || !lock.packages) return null
    const devOnly = new Set()
    for (const [path, info] of Object.entries(lock.packages)) {
      const name = path.replace(/^node_modules\//, '')
      // 仅开发链可达的包（含其平台二进制 optional 依赖）不随安装包分发
      if (info.dev) devOnly.add(name)
    }
    return devOnly
  } catch {
    return null
  }
}

// 1) npm 依赖
let npmMissing = 0
let npmGenerated = 0
let npmDevSkipped = 0
const modules = join(root, 'desktop/node_modules')
if (existsSync(modules)) {
  const devOnly = runtimeFilter()
  for (const entry of readdirSync(modules, { withFileTypes: true })) {
    // `.bin`/`.vite` 等点开头的目录是 npm/vite 工具目录，不是依赖包
    if (!entry.isDirectory() || entry.name.startsWith('.')) continue
    const scopeDir = entry.name.startsWith('@') ? join(modules, entry.name) : null
    const candidates = scopeDir
      ? readdirSync(scopeDir, { withFileTypes: true }).filter(child => child.isDirectory()).map(child => join(scopeDir, child.name))
      : [join(modules, entry.name)]
    for (const dir of candidates) {
      if (devOnly) {
        const rel = scopeDir ? `${entry.name}/${basename(dir)}` : basename(dir)
        if (devOnly.has(rel)) {
          npmDevSkipped += 1
          continue
        }
      }
      const names = readdirSync(dir)
      // 49#2：statSync 过滤目录（licenses/ 等目录名同样命中 /^licen[cs]e/i）
      const license = pickFromNames(names, name => statSync(join(dir, name)).isFile())
      if (license) {
        const name = (scopeDir ? `${entry.name}__${basename(dir)}` : entry.name).replace(/[^a-z0-9._@-]/gi, '_')
        copyFileSync(join(dir, license), join(outDir, `npm-${name}-${license}`))
        copied.push(`npm:${name}`)
        continue
      }
      // 49#3：无许可文件不再静默跳过——元数据可生成声明则生成，否则计入缺失
      try {
        const meta = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'))
        const notice = generatedNotice(meta)
        if (notice) {
          const name = (scopeDir ? `${entry.name}__${basename(dir)}` : entry.name).replace(/[^a-z0-9._@-]/gi, '_')
          writeFileSync(join(outDir, `npm-${name}-LICENSE.generated.txt`), notice.text)
          copied.push(`npm:${name}`)
          if (notice.spdx.toUpperCase() !== 'MIT') npmMissing += 1
          else npmGenerated += 1
          continue
        }
      } catch {
        // package.json 不可读：走缺失计数
      }
      npmMissing += 1
    }
  }
}

// 2) Rust 依赖（cargo metadata → 本地源码目录；未解包时回退 .crate 归档直读）
let cargoSkipped = false
let cargoMissing = 0

// 30#2：cargo metadata/fetch 都不解包 registry src（实测确认），冷缓存单独
// 跑 make build 时源码目录为空。从 registry/cache 的 .crate（gzip tar）里
// 直接抽取 LICENSE*/NOTICE*，消除对「已构建过」的隐式依赖
function licenseFromCrateArchive(name, version) {
  const cacheRoot = join(process.env.CARGO_HOME ?? `${process.env.USERPROFILE ?? process.env.HOME}/.cargo`, 'registry', 'cache')
  if (!existsSync(cacheRoot)) return null
  const wanted = `${name}-${version}.crate`
  for (const registry of readdirSync(cacheRoot, { withFileTypes: true })) {
    if (!registry.isDirectory()) continue
    const archive = join(cacheRoot, registry.name, wanted)
    if (!existsSync(archive)) continue
    // 最小 tar 解析：512 字节头（name/typeflag/size 八进制），条目名形如
    // {pkg}-{ver}/LICENSE；GNU 长名条目（typeflag L）为下一头提供全名
    const tar = gunzipSync(readFileSync(archive))
    let offset = 0
    let longName = ''
    const matches = new Map()
    while (offset + 512 <= tar.length) {
      const header = tar.subarray(offset, offset + 512)
      if (header.every(byte => byte === 0)) break
      const rawName = header.subarray(0, 100).toString('utf8').replace(/\0.*$/, '')
      const name = longName || rawName
      longName = ''
      const typeflag = String.fromCharCode(header[156])
      const size = parseInt(header.subarray(248, 256).toString('utf8').replace(/\0.*$/, '').trim() || '0', 8) || 0
      offset += 512
      if (typeflag === 'L') {
        longName = tar.subarray(offset, offset + size).toString('utf8').replace(/\0.*$/, '')
      } else if (typeflag === '0' || typeflag === '\0') {
        const base = name.split('/').pop() ?? ''
        if (name.includes('/') && licensePattern.test(base) && !matches.has(base)) {
          matches.set(base, tar.subarray(offset, offset + size))
        }
      }
      offset += Math.ceil(size / 512) * 512
    }
    // 49#1：归档内同样按优先级挑选，首个命中不再短路
    const best = pickFromNames([...matches.keys()])
    return best ? { base: best, bytes: matches.get(best) } : null
  }
  return null
}

try {
  const metadata = JSON.parse(execFileSync('cargo', ['metadata', '--format-version', '1', '--manifest-path', join(root, 'desktop/src-tauri/Cargo.toml')], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }))
  // 49#2：workspace 根包是本仓库自身，不计第三方依赖（旧实现把脚本自建的
  // licenses/ 目录当候选，copyFileSync 抛 EISDIR 后被计入 cargoMissing）
  const rootPackageDir = resolve(root, 'desktop/src-tauri')
  for (const pkg of metadata.packages) {
    if (!pkg.manifest_path) continue
    const dir = resolve(dirname(pkg.manifest_path))
    if (dir === rootPackageDir) continue
    try {
      if (existsSync(dir)) {
        const license = pickFromNames(readdirSync(dir), name => statSync(join(dir, name)).isFile())
        if (license) {
          const name = `${pkg.name}-${pkg.version}`.replace(/[^a-z0-9._-]/gi, '_')
          copyFileSync(join(dir, license), join(outDir, `cargo-${name}-${license}`))
          copied.push(`cargo:${pkg.name}`)
          continue
        }
      }
      // 源码目录缺失或无许可文件：回退 .crate 归档直读（sparse registry
      // 仅在构建时解包 src；cache 归档在 cargo metadata 时已下载）
      const archived = licenseFromCrateArchive(pkg.name, pkg.version)
      if (archived) {
        const name = `${pkg.name}-${pkg.version}`.replace(/[^a-z0-9._-]/gi, '_')
        writeFileSync(join(outDir, `cargo-${name}-${archived.base}`), archived.bytes)
        copied.push(`cargo:${pkg.name}`)
      } else {
        cargoMissing += 1
      }
    } catch {
      // 单个依赖源码未解包且归档不可读：跳过并计数
      cargoMissing += 1
    }
  }
} catch {
  cargoSkipped = true
}

const npmNotes = [
  npmMissing ? `另有 ${npmMissing} 个 npm 依赖未找到许可文件且元数据不足` : '',
  npmGenerated ? `${npmGenerated} 个 npm 依赖依据包内元数据生成了许可声明` : '',
  npmDevSkipped ? `已按 package-lock 排除 ${npmDevSkipped} 个仅开发期依赖` : '',
].filter(Boolean).join('；')
writeFileSync(join(outDir, 'COLLECTED.md'), `# 本次构建收录清单

本清单由 scripts/collect-licenses.mjs 在构建时生成，随安装包分发；README.md 为入库占位说明。
已收录 ${copied.length} 个依赖的许可/声明文件${cargoSkipped ? '（Rust 部分因 cargo 不可用被跳过，请在可运行 cargo 的环境重新构建）' : cargoMissing ? `（另有 ${cargoMissing} 个 Rust 依赖在本地源码与 .crate 归档中均未找到许可文件，许可以 Cargo.lock 记录的上游仓库为准）` : ''}${npmNotes ? `；npm 侧：${npmNotes}` : ''}。
完整依赖版本清单见安装包同仓库的 Cargo.lock 与 package-lock.json。
`)
console.log(`collect-licenses: ${copied.length} 个依赖许可已汇总${cargoSkipped ? '（Rust 部分跳过）' : ''}${npmMissing ? `（npm 缺失 ${npmMissing}）` : ''}${npmDevSkipped ? `（开发依赖排除 ${npmDevSkipped}）` : ''}`)

// 15#7：零收录属于管线失效（依赖缺失/路径漂移），不能以成功退出——
// 否则安装包「许可汇总」为空而 NOTICE 的合规承诺形同虚设
if (copied.length === 0) {
  console.error('collect-licenses: 未收录任何依赖许可，请检查 node_modules 与 cargo 环境')
  process.exit(1)
}
