#!/usr/bin/env node
// 校验架构文档的规模指标与源码一致（P-152/P-153）：
// 1) main.rs 的 generate_handler! 注册集合按模块计数；
// 2) desktop/src 前端 invoke 调用集合与注册集合双向一致、每命令 1 处调用点；
// 3) docs/architecture/01-architecture.md §7 分组表的分组数与合计数；
// 4) 文档正文里「按源码核对」的全部规模指标（注册命令、前端直接调用、
//    TypeScript/TSX 文件、CSS 文件、页面、组件、后端客户端文件、业务域、
//    Rust 测试数）与源码实算一致。
// 文档数字此前从未成立（前端调用数长期写成 173）且分组数 ±1 相互抵消，
// 合计正确掩盖了分组错误——此处改为机械比对源码，数字漂移即门禁失败。
// 正文数字逐项绑定到具体声明句式，避免把历史发布记录里的旧数字
// （如「make check 通过：265 个后端测试」）误判为当前规模声明。
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const read = path => readFileSync(join(root, path), 'utf8')
const failures = []

/** 递归列出目录下的文件（仓库相对路径） */
function listFiles(dir, accept) {
  const files = []
  const walk = current => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const full = join(current, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (!accept || accept(entry.name)) files.push(full.slice(root.length + 1))
    }
  }
  walk(join(root, dir))
  return files
}

/** main.rs 的 generate_handler! 注册命令（按模块前缀计数） */
function registeredCommands() {
  const source = read('desktop/src-tauri/src/main.rs')
  const start = source.indexOf('generate_handler![')
  const end = source.indexOf(']));', start)
  if (start < 0 || end < 0) throw new Error('main.rs 未找到 generate_handler! 块')
  const block = source.slice(start, end)
  const counts = new Map()
  const names = []
  for (const line of block.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:([A-Za-z_][A-Za-z0-9_]*)::)?commands::([A-Za-z0-9_]+)\s*,/)
    if (!match) continue
    const module = match[1] ?? 'commands'
    counts.set(module, (counts.get(module) ?? 0) + 1)
    names.push(match[2])
  }
  return { counts, names }
}

/** desktop/src 下的前端 invoke 调用点（排除注释行） */
function invokedCommands() {
  const sites = []
  for (const file of listFiles('desktop/src', name => /\.(ts|tsx)$/.test(name))) {
    readFileSync(join(root, file), 'utf8')
      .split(/\r?\n/)
      .forEach((line, index) => {
        const trimmed = line.trim()
        if (trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')) return
        for (const match of line.matchAll(/invoke(?:<[^>]*>)?\(\s*['"`]([a-z0-9_]+)['"`]/g)) {
          sites.push({ name: match[1], file, line: index + 1 })
        }
      })
  }
  return sites
}

/** §7 分组表：分组名 → 声明数量 */
function architectureGroups() {
  const content = read('docs/architecture/01-architecture.md')
  const start = content.indexOf('## 7. 命令注册')
  if (start < 0) throw new Error('01-architecture.md 未找到 §7 命令注册')
  const rest = content.slice(start)
  const end = rest.indexOf('\n## ')
  const section = end < 0 ? rest : rest.slice(0, end)
  const groups = new Map()
  let total = null
  for (const line of section.split(/\r?\n/)) {
    const cells = line.split('|').map(cell => cell.trim())
    if (cells.length < 4) continue
    const name = cells[1].replace(/\*/g, '').trim()
    const count = Number(cells[2].replace(/\*/g, ''))
    if (!name || !Number.isInteger(count)) continue
    if (name === '分组' || name === '合计') {
      if (name === '合计') total = count
      continue
    }
    groups.set(name, count)
  }
  if (total === null) throw new Error('01-architecture.md §7 未找到合计行')
  return { groups, total }
}

const { counts, names } = registeredCommands()
const sites = invokedCommands()
const registered = new Set(names)
const invoked = new Map()
for (const site of sites) invoked.set(site.name, (invoked.get(site.name) ?? 0) + 1)

for (const name of invoked.keys()) {
  if (!registered.has(name)) failures.push(`前端调用了未注册的命令：${name}`)
}
for (const name of registered) {
  if (!invoked.has(name)) failures.push(`注册命令没有前端调用点：${name}`)
  else if (invoked.get(name) !== 1) failures.push(`命令 ${name} 的前端调用点为 ${invoked.get(name)} 处，应为 1 处`)
}

const { groups, total } = architectureGroups()
const moduleGroups = {
  '启动与通用': ['startup', 'commands'],
  '安全': ['security'],
  '供应商、路由与用量': ['providers'],
  '材料、笔记与网页导入': ['materials', 'notes', 'web'],
  '学习、讲义、知识与排期': ['learning', 'knowledge', 'planning'],
  '练习、复习与项目': ['practice', 'review', 'projects'],
  'Agent': ['agent'],
  '备份、分享、同步与运维': ['backup', 'sharing', 'sync', 'maintenance'],
  '社区、插件、导出、报告与搜索': ['community', 'plugins', 'export', 'reports', 'engagement'],
}
for (const [name, modules] of Object.entries(moduleGroups)) {
  const expected = modules.reduce((sum, module) => sum + (counts.get(module) ?? 0), 0)
  const stated = groups.get(name)
  if (stated === undefined) failures.push(`§7 分组表缺少分组：${name}`)
  else if (stated !== expected) failures.push(`§7 分组「${name}」标注 ${stated}，源码实算 ${expected}`)
}
for (const name of groups.keys()) {
  if (!(name in moduleGroups)) failures.push(`§7 分组「${name}」未登记模块归属，无法核对`)
}
const registeredTotal = names.length
if (total !== registeredTotal) failures.push(`§7 合计标注 ${total}，main.rs 注册 ${registeredTotal} 个`)
if (invoked.size !== registeredTotal) failures.push(`前端唯一调用 ${invoked.size} 个命令，注册命令 ${registeredTotal} 个`)

/** 其余按源码核对的规模指标 */
const frontendTsFiles = listFiles('desktop/src', name => /\.(ts|tsx)$/.test(name)).length
const frontendCssFiles = listFiles('desktop/src', name => name.endsWith('.css')).length
const pageFiles = listFiles('desktop/src/pages', name => name.endsWith('.tsx')).length
const componentFiles = listFiles('desktop/src/components', name => name.endsWith('.tsx')).length
const clientFiles = listFiles('desktop/src/backend', name => name.endsWith('.ts')).length
const clientFilesWithInvoke = new Set(sites.map(site => site.file)).size
const domainCount = readdirSync(join(root, 'desktop/src-tauri/src/backend'), { withFileTypes: true })
  .filter(entry => entry.isDirectory()).length
const rustTestCount = listFiles('desktop/src-tauri/src', name => name.endsWith('.rs'))
  .reduce((sum, file) => sum + (read(file).match(/#\[test\]/g) ?? []).length, 0)

const metricValues = {
  '注册命令': registeredTotal,
  '前端直接调用': invoked.size,
  'TypeScript/TSX 文件': frontendTsFiles,
  'CSS 文件': frontendCssFiles,
  '页面': pageFiles,
  '组件': componentFiles,
  '后端客户端文件': clientFiles,
  '前端客户端文件': clientFiles,
  '业务域': domainCount,
  'Rust 测试': rustTestCount,
  '含直接调用点的客户端文件': clientFilesWithInvoke,
  '经 client.ts 封装的客户端文件': clientFiles - clientFilesWithInvoke,
}

/** 各指标在文档里的声明句式（逐条绑定，避免误抓历史记录里的旧数字） */
const metricPatterns = {
  '注册命令': [/(\d+)\s*个注册命令/g, /注册\s*(\d+)\s*个命令/g, /命令仍为\s*(\d+)\s*个/g, /和\s*(\d+)\s*个命令/g],
  '前端直接调用': [/(\d+)\s*个前端直接调用/g, /前端直接调用(?:仍为|为)?\s*(\d+)\s*个/g],
  'TypeScript/TSX 文件': [/(\d+)\s*个 TypeScript\/TSX 文件/g],
  'CSS 文件': [/(\d+)\s*个 CSS 文件/g],
  '页面': [/(\d+)\s*个页面/g],
  '组件': [/(\d+)\s*个组件/g],
  '后端客户端文件': [/(\d+)\s*个后端客户端文件/g],
  '前端客户端文件': [/(\d+)\s*个前端客户端文件/g],
  '业务域': [/(\d+)\s*个业务域/g],
  'Rust 测试': [/(\d+)\s*个 Rust\s*`#\[test\]`/g, /和\s*(\d+)\s*个后端测试/g, /后端测试仍为\s*(\d+)\s*个/g],
  '含直接调用点的客户端文件': [/(\d+)\s*个含直接/g],
  '经 client.ts 封装的客户端文件': [/其余\s*(\d+)\s*个经/g],
}

/** 各文档声明了哪些指标（缺声明即视为漂移，登记失败） */
const proseChecks = [
  ['docs/architecture/index.md', ['注册命令', '前端直接调用', 'TypeScript/TSX 文件', 'CSS 文件', '页面', '组件', '后端客户端文件', '业务域', 'Rust 测试']],
  ['docs/architecture/01-architecture.md', ['注册命令', '前端直接调用', '页面', '后端客户端文件', '业务域']],
  ['docs/architecture/03-workflow.md', ['注册命令', '前端直接调用', '组件', '后端客户端文件', '含直接调用点的客户端文件', '经 client.ts 封装的客户端文件', 'Rust 测试']],
  ['note/release/1.1.15.md', ['注册命令', '前端直接调用', '业务域', '页面', '组件', '前端客户端文件', 'Rust 测试']],
  ['note/release/1.1.16.md', ['注册命令', '前端直接调用', 'Rust 测试']],
]

for (const [file, metrics] of proseChecks) {
  const content = read(file)
  for (const metric of metrics) {
    const expected = metricValues[metric]
    const found = metricPatterns[metric]
      .flatMap(pattern => [...content.matchAll(pattern)].map(match => Number(match[1])))
    if (found.length === 0) {
      failures.push(`${file} 未找到「${metric}」数字声明`)
      continue
    }
    for (const value of found) {
      if (value !== expected) failures.push(`${file} 的「${metric}」写成 ${value}，源码实算 ${expected}`)
    }
  }
}

if (failures.length > 0) {
  console.error('check-doc-metrics: 架构文档指标与源码不一致：')
  for (const failure of failures) console.error(`  ${failure}`)
  process.exit(1)
}
console.log(
  `check-doc-metrics: 注册命令 ${registeredTotal} 个、前端调用 ${invoked.size} 个（各 1 处调用点）、` +
    `§7 分组 ${groups.size} 组一致、前端 ${frontendTsFiles} 个 TS/TSX 文件（${pageFiles} 页面 / ${componentFiles} 组件 / ` +
    `${clientFiles} 客户端文件）、业务域 ${domainCount} 个、Rust 测试 ${rustTestCount} 个`,
)
