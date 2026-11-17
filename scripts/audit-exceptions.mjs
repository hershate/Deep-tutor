#!/usr/bin/env node
// dev 依赖口径的依赖审计判定（例外感知）——110#1 门禁的 dev 全量口径入口。
//
// 背景：picomatch ≤4.0.7 存在 ReDoS（单分支重复 extglob，
// GHSA-fc5q-39xc-vjpf / CVE-2026-112366），上游尚无修复版——npm registry
// 最新即 4.0.7 仍落在受影响区间，上游修复 PR micromatch/picomatch#194
// 未合并；而 vite ≥6.3 全部依赖 picomatch，vite ≤6.4.3 自身带 12 条通告
// （含 Windows server.fs.deny 反斜杠绕过族，即 desktop/vite.config.ts 注明
// 由 vite 6.4.6 修复的那一族），故「回退 vite」不是解。
//
// 判定口径：执行 `npm audit --json`（dev 全量口径），只放行白名单中的确切
// 通告 ID；白名单之外的任何通告（含以后新增的）一律以非零退出码失败——
// 门禁对其余问题保持严格。runtime 口径不在本脚本范围内，由 Makefile 的
// `npm audit --omit=dev` 单独把关（零例外）。
//
// 用法：
//   node scripts/audit-exceptions.mjs                # 实跑 npm audit --json 判定
//   node scripts/audit-exceptions.mjs --input <file> # 对给定 audit JSON 自检
//
// 解析形状以本机 npm 11.19.1 的真实 `npm audit --json` 输出核对：通告对象
// 位于 report.vulnerabilities[*].via 的对象项（含 url / source / range），
// via 的字符串项是同一通告在其他包上的传递标记，不重复计数。
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const desktop = join(root, 'desktop')

// 白名单：只列确切通告 ID。除白名单外的任何通告一律失败。
// 每条自带理由、可达性与退出条件——退出条件达成即删除该条目并恢复裸审计。
const EXCEPTIONS = [
  {
    id: 'GHSA-fc5q-39xc-vjpf',
    alias: 'CVE-2026-112366',
    package: 'picomatch',
    reason:
      '上游无修复版：npm registry 最新即 4.0.7 仍落在受影响区间（≤4.0.7），修复 PR micromatch/picomatch#194 未合并；仅 dev 工具链依赖（vite→tinyglobby→fdir→picomatch），runtime 口径仍为零漏洞',
    reachability:
      '触发需模式本身含单分支重复 extglob（`+(*)`、`+(*a)`）；vite 的 picomatch 模式来源为开发者配置与源码 import 说明符（vite dist/node/chunks/dep-Cohbwjf9.js:562/42148/49422），请求可控值只作被匹配字符串，vite 默认 server.fs.deny 模式不含该结构',
    exit:
      'picomatch 发布含上游修复的 ≥4.0.8 且 vite 依赖链取用后，删除本条目并把 dev 口径恢复为裸 `npm audit`',
  },
]

const usage = () => {
  console.error('用法：')
  console.error('  node scripts/audit-exceptions.mjs                # 实跑 npm audit --json 判定')
  console.error('  node scripts/audit-exceptions.mjs --input <file> # 对给定 audit JSON 自检')
}

const parseArgs = argv => {
  let input = null
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--input') {
      input = argv[i + 1] ?? null
      i++
      continue
    }
    if (argv[i] === '--help' || argv[i] === '-h') {
      usage()
      process.exit(0)
    }
    console.error(`audit-exceptions: 未知参数 ${argv[i]}`)
    usage()
    process.exit(2)
  }
  return input
}

// 实跑 npm audit --json：发现漏洞时 npm 以非零码退出，JSON 仍在 stdout。
const runAudit = () => {
  const npmBin = process.platform === 'win32' ? 'npm.cmd' : 'npm'
  try {
    return execFileSync(npmBin, ['audit', '--json'], {
      cwd: desktop,
      encoding: 'utf8',
      // Windows 下 Node 拒绝直接 spawn .cmd（需 shell），参数为固定字面量
      shell: process.platform === 'win32',
      maxBuffer: 64 * 1024 * 1024,
    })
  } catch (err) {
    if (err.stdout) return err.stdout
    console.error('audit-exceptions: `npm audit --json` 执行失败：')
    console.error(String(err.stderr || err.message || err).trim())
    process.exit(2)
  }
}

const parseReport = text => {
  let report
  try {
    report = JSON.parse(text)
  } catch {
    console.error('audit-exceptions: npm audit --json 输出不是合法 JSON：')
    console.error(text.slice(0, 400))
    process.exit(2)
  }
  if (!report || typeof report !== 'object' || !report.vulnerabilities) {
    console.error('audit-exceptions: npm audit --json 缺少 vulnerabilities 段')
    process.exit(2)
  }
  return report
}

// 通告级提取：只有 via 的对象项是通告本身；字符串项是同通告的传递标记。
const collectAdvisories = report => {
  const seen = new Map()
  for (const [pkg, vuln] of Object.entries(report.vulnerabilities ?? {})) {
    for (const via of vuln.via ?? []) {
      if (!via || typeof via !== 'object') continue
      const ids = []
      const ghsa = String(via.url ?? '').match(/\/advisories\/(GHSA-[A-Za-z0-9-]+)/)
      if (ghsa) ids.push(ghsa[1])
      if (via.cve) ids.push(via.cve)
      if (via.cve_id) ids.push(via.cve_id)
      const advisory = {
        package: via.name ?? via.dependency ?? pkg,
        title: via.title ?? '',
        severity: via.severity ?? vuln.severity ?? '',
        range: via.range ?? '',
        url: via.url ?? '',
        ids,
      }
      const key = `${advisory.ids.join('|') || advisory.url}::${advisory.package}`
      if (!seen.has(key)) seen.set(key, advisory)
    }
  }
  return [...seen.values()]
}

const matchException = advisory =>
  EXCEPTIONS.find(
    entry => advisory.ids.includes(entry.id) || (entry.alias && advisory.ids.includes(entry.alias))
  )

const input = parseArgs(process.argv.slice(2))
const reportText = input ? readFileSync(resolve(input), 'utf8') : runAudit()
const report = parseReport(reportText)
const advisories = collectAdvisories(report)

const allowed = []
const violations = []
for (const advisory of advisories) {
  const entry = matchException(advisory)
  if (entry && entry.package === advisory.package) {
    allowed.push({ advisory, entry })
  } else {
    violations.push({ advisory, entry })
  }
}

const meta = report.metadata?.vulnerabilities ?? {}
console.log(
  `audit-exceptions: npm audit 包级统计 total=${meta.total ?? '未知'}（high=${meta.high ?? 0}，moderate=${meta.moderate ?? 0}，low=${meta.low ?? 0}），通告级 ${advisories.length} 条`
)

for (const { advisory, entry } of allowed) {
  console.log(`audit-exceptions: 放行白名单通告 ${entry.id}${entry.alias ? ` / ${entry.alias}` : ''}（${advisory.package}，${advisory.severity}，受影响 ${advisory.range}）`)
  console.log(`audit-exceptions:   理由：${entry.reason}`)
  console.log(`audit-exceptions:   可达性：${entry.reachability}`)
  console.log(`audit-exceptions:   退出条件：${entry.exit}`)
}

const used = new Set(allowed.map(item => item.entry.id))
for (const entry of EXCEPTIONS) {
  if (!used.has(entry.id)) {
    console.log(
      `audit-exceptions: 提示：白名单条目 ${entry.id} 未命中当前依赖树，可按退出条件清理`
    )
  }
}

if (violations.length > 0) {
  console.error('audit-exceptions: 以下通告不在白名单内，门禁失败：')
  for (const { advisory, entry } of violations) {
    const ids = advisory.ids.join(' / ') || advisory.url || '未知 ID'
    console.error(
      `  - ${ids}（${advisory.package}，${advisory.severity}，受影响 ${advisory.range}）：${advisory.title}`
    )
    if (entry) {
      console.error(
        `    白名单 ${entry.id} 命中但包名不符（白名单 ${entry.package} ≠ 实际 ${advisory.package}），不放行`
      )
    }
  }
  console.error(`audit-exceptions: 白名单外通告 ${violations.length} 条`)
  process.exit(1)
}

console.log(`audit-exceptions: 放行 ${allowed.length} 条白名单通告，白名单外通告 0 条`)
process.exit(0)
