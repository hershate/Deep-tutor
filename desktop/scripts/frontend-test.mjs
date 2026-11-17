#!/usr/bin/env node
// 前端回归测试：Markdown 预处理与主题缓存。
//
// 仓库前端此前没有自动化测试通道（make test 只跑 Rust 套件），P-129 的定界符
// 改写与 P-123 的主题缓存只靠临时探针验证，改动后没有兜底。本脚本用 Node 自带
// 的 test runner 补上这条通道，不新增任何依赖：
// - 公式部分直接加载 desktop/src/markdown/prepare.ts（Node 原生剥离类型），
//   并用应用同款插件链（remark-gfm + remark-math + rehype-katex）渲染成 HTML，
//   断言「渲染成公式」而不是只断言字符串改写结果；
// - 主题部分用最小的 localStorage/document 替身跑 desktop/src/theme.ts，
//   覆盖「缓存按账户分区」这条 P-123 返工口径。
//
// 运行：node desktop/scripts/frontend-test.mjs（make test 会执行）

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { prepareMarkdown } from '../src/markdown/prepare.ts'
import { applyAccountTheme, cacheTheme, readCachedTheme } from '../src/theme.ts'
import { readableErrorMessage } from '../src/errors.ts'
import { DEFAULT_RATE_VALUES, editRateDraft, rateDisplayValues, rateDraftCommitted, savedRateValues } from '../src/usageRateDraft.ts'

/** 读取前端源文件（用于「行为只能靠源码断言」的场景） */
function readSource(relative) {
  return readFileSync(new URL(relative, import.meta.url), 'utf8')
}

/** 前端源码根目录 */
const SRC_ROOT = new URL('../src/', import.meta.url)

/** 递归列出 src 下的前端源文件 */
function listSourceFiles(dir = SRC_ROOT) {
  const files = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const child = new URL(`${entry.name}${entry.isDirectory() ? '/' : ''}`, dir)
    if (entry.isDirectory()) files.push(...listSourceFiles(child))
    else if (/\.tsx?$/.test(entry.name)) files.push(child)
  }
  return files
}

/** 用应用同款插件链渲染预处理后的 Markdown */
function renderHtml(source) {
  return renderToStaticMarkup(createElement(
    ReactMarkdown,
    { remarkPlugins: [remarkGfm, remarkMath], rehypePlugins: [rehypeKatex] },
    prepareMarkdown(source),
  ))
}

/** 断言：渲染结果里出现 KaTeX 公式 */
function assertFormula(source, label) {
  const html = renderHtml(source)
  assert.ok(html.includes('katex'), `${label}：应渲染成公式，实际输出 ${html}`)
}

/** 断言：渲染结果里没有公式（保持字面量） */
function assertNoFormula(source, label) {
  const html = renderHtml(source)
  assert.ok(!html.includes('katex'), `${label}：不应渲染成公式，实际输出 ${html}`)
}

test('P-129：`\\[…\\]` 独占一行按块级显示公式改写', () => {
  assert.equal(prepareMarkdown('\\[x^2 + y^2 = z^2\\]'), '$$\nx^2 + y^2 = z^2\n$$')
  assertFormula('\\[x^2 + y^2 = z^2\\]', '单行块级写法')
})

test('P-129：`\\[…\\]` 跨行块级写法按块级显示公式改写', () => {
  assert.equal(prepareMarkdown('\\[\nx^2\n\\]'), '$$\nx^2\n$$')
  assertFormula('\\[\nx^2\n\\]', '跨行块级写法')
})

test('P-129：段落中间的 `\\[…\\]` 按行内公式改写', () => {
  assert.equal(prepareMarkdown('公式 \\[x^2\\] 结束'), '公式 $$x^2$$ 结束')
  assertFormula('公式 \\[x^2\\] 结束', '行内写法')
})

test('P-129：`\\(…\\)` 一律按行内公式改写', () => {
  assert.equal(prepareMarkdown('公式 \\(x^2\\) 结束'), '公式 $x^2$ 结束')
  assertFormula('公式 \\(x^2\\) 结束', '圆括号行内写法')
})

test('P-129 返工：内容没有公式信号的 `\\[…\\]` 保留 markdown 转义原样', () => {
  // 字面量方括号不能被改写成公式：`[0]` 原文必须保留
  assert.equal(prepareMarkdown('普通文本 a\\[0\\] 结束'), '普通文本 a\\[0\\] 结束')
  assertNoFormula('普通文本 a\\[0\\] 结束', '字面量方括号')
  const html = renderHtml('普通文本 a\\[0\\] 结束')
  assert.ok(html.includes('a[0]'), `字面量方括号应渲染为 a[0]，实际输出 ${html}`)
})

test('P-129 返工：引用块内的块级写法同样改写', () => {
  assert.equal(prepareMarkdown('> \\[\n> x^2\n> \\]'), '> $$\n> x^2\n> $$')
  assertFormula('> \\[\n> x^2\n> \\]', '引用块块级写法')
})

test('P-129：围栏代码块与行内代码内的定界符原样保留', () => {
  const fenced = '```\n\\[x^2\\]\n```'
  assert.equal(prepareMarkdown(fenced), fenced)
  assertNoFormula(fenced, '围栏代码块')
  const inline = '代码 `\\[x^2\\]` 原样'
  assert.equal(prepareMarkdown(inline), inline)
  assertNoFormula(inline, '行内代码')
})

test('P-129：未闭合或内容为空的定界符原样保留', () => {
  assert.equal(prepareMarkdown('未闭合 \\[x^2'), '未闭合 \\[x^2')
  assert.equal(prepareMarkdown('空内容 \\[\\]'), '空内容 \\[\\]')
  assertNoFormula('未闭合 \\[x^2', '未闭合写法')
})

test('P-042：独占一行的单行 `$$…$$` 规范为多行块公式', () => {
  assert.equal(prepareMarkdown('$$x^2$$'), '$$\nx^2\n$$')
  assertFormula('$$x^2$$', '单行块公式')
})

/** 用最小的 localStorage / document 替身跑主题缓存模块 */
function withFakeStorage(run) {
  const store = new Map()
  const previousWindow = globalThis.window
  const previousDocument = globalThis.document
  globalThis.window = {
    localStorage: {
      getItem: key => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => { store.set(key, String(value)) },
      removeItem: key => { store.delete(key) },
    },
  }
  globalThis.document = { documentElement: { dataset: {} } }
  try {
    return run(store)
  } finally {
    globalThis.window = previousWindow
    globalThis.document = previousDocument
  }
}

test('P-123 返工：主题缓存按账户分区，账户之间互不串用', () => {
  withFakeStorage(() => {
    cacheTheme(1, 'dark')
    cacheTheme(2, 'light')
    assert.equal(readCachedTheme(1), 'dark')
    assert.equal(readCachedTheme(2), 'light')
    applyAccountTheme(1)
    assert.equal(globalThis.document.documentElement.dataset.theme, 'dark')
    applyAccountTheme(2)
    assert.equal(globalThis.document.documentElement.dataset.theme, 'light')
  })
})

test('P-123 返工：切换到未缓存的账户回落默认浅色', () => {
  withFakeStorage(() => {
    cacheTheme(1, 'dark')
    applyAccountTheme(9)
    assert.equal(globalThis.document.documentElement.dataset.theme, 'light')
  })
})

test('P-123 返工：旧版全局单键格式按活动账户兼容一次', () => {
  withFakeStorage(store => {
    store.set('deep-tutor.theme', 'dark')
    store.set('deep-tutor.theme.account', '7')
    assert.equal(readCachedTheme(7), 'dark')
  })
})

/**
 * 按 react-dom 生产版的错误构造器还原真实压缩串形态。
 * 仓库钉住 react-dom 18.3.1，其 cjs/react-dom.production.min.js 的 `function p(a)`
 * 生成：「Minified React error #<invariant>; visit <decoder>?invariant=<n>&args[]=<encoded> …」
 * （React 19 才改成 ?invocation=…，评审 NEW 指出旧夹具用的是 19 的形态）。
 */
function minifiedReactError(invariant, ...args) {
  let url = `https://reactjs.org/docs/error-decoder.html?invariant=${invariant}`
  for (const arg of args) url += `&args[]=${encodeURIComponent(arg)}`
  return `Minified React error #${invariant}; visit ${url} for the full message or use the non-minified dev environment for full errors and additional helpful warnings.`
}

test('P-130：生产版 React 压缩错误改写为中文说明并保留错误编号', () => {
  const raw = minifiedReactError(310, 'object with keys {bad}')
  const message = readableErrorMessage(new Error(raw))
  assert.ok(message.includes('#310'), `应保留错误编号，实际输出 ${message}`)
  assert.ok(!message.includes('reactjs.org'), `不应把外网链接原样展示，实际输出 ${message}`)
  assert.ok(!message.includes('Minified React error'), `不应原样展示压缩串，实际输出 ${message}`)
  assert.ok(/[\u4e00-\u9fff]/.test(message), `应给出中文说明，实际输出 ${message}`)
})

test('P-130 返工：夹具形态与仓库钉住的 react-dom 生产版压缩串一致', () => {
  const production = readSource('../node_modules/react-dom/cjs/react-dom.production.min.js')
  assert.ok(
    production.includes('error-decoder.html?invariant='),
    'react-dom 生产版应使用 ?invariant= 参数；夹具必须与实际产物同形态',
  )
  assert.ok(
    !production.includes('error-decoder.html?invocation='),
    '?invocation= 是 React 19 的形态，仓库钉住的是 18.3.1',
  )
  // 生产包构造器的三段模板都要与夹具生成器同形：前缀、参数拼接、尾句
  assert.ok(production.includes('"Minified React error #"'), '生产包应保留压缩串前缀')
  assert.ok(production.includes('"&args[]="+encodeURIComponent('), '生产包应按 args[] 逐个 URL 编码拼接')
  assert.ok(
    production.includes('for the full message or use the non-minified dev environment'),
    '生产包尾句应与夹具一致',
  )
  // 夹具生成器与生产包构造器同形：编号与参数都挂在 decoder URL 上
  const raw = minifiedReactError(310, 'object with keys {bad}')
  assert.ok(raw.includes('error-decoder.html?invariant=310'), `夹具应带 invariant 参数，实际 ${raw}`)
  assert.ok(raw.includes('&args[]=object%20with%20keys%20%7Bbad%7D'), `夹具应带编码后的 args[]，实际 ${raw}`)
})

test('P-130：非压缩错误原样透出', () => {
  assert.equal(readableErrorMessage(new TypeError('x is not a function')), 'x is not a function')
  assert.equal(readableErrorMessage('纯文本错误'), '纯文本错误')
})

test('P-130 返工：错误描述为空串时给出中文兜底文案，不返回空串', () => {
  const message = readableErrorMessage(new Error(''))
  assert.ok(message.length > 0, '空描述不得返回空串')
  assert.ok(/[\u4e00-\u9fff]/.test(message), `应给出中文说明，实际输出 ${message}`)
})

test('P-130：两级错误边界都经 readableErrorMessage 改写，不直接展示原始 message', () => {
  for (const file of ['../src/main.tsx', '../src/App.tsx']) {
    const source = readSource(file)
    assert.ok(
      source.includes('readableErrorMessage(error)'),
      `${file}：错误边界必须经 readableErrorMessage 改写，否则压缩串会原样显示`,
    )
    assert.ok(
      !source.includes('error instanceof Error ? error.message : String(error)'),
      `${file}：错误边界不得直接展示原始 error.message`,
    )
  }
})

test('P-130 返工：两级错误边界按独立失败标志决定是否渲染兜底页', () => {
  for (const file of ['../src/main.tsx', '../src/App.tsx']) {
    const source = readSource(file)
    assert.ok(source.includes('failed: true'), `${file}：捕获错误时必须置独立失败标志`)
    assert.ok(
      source.includes('if (this.state.failed)'),
      `${file}：兜底页判定必须依据失败标志，而不是文案真值`,
    )
    assert.ok(
      !source.includes('if (this.state.message)'),
      `${file}：不得用文案真值判定是否渲染兜底页（空描述会退回子树再次抛错）`,
    )
  }
})

test('P-007：运维面板不消费失败事件，避免体检失败自激循环', () => {
  const source = readSource('../src/components/MaintenancePanel.tsx')
  assert.ok(
    source.includes("if (event.kind === 'maintenance.failed') return"),
    '面板必须忽略 maintenance.failed——失败事件再拉报告会闭合回路',
  )
})

test('P-135：方案完成事件不得把已回退到输入步骤的用户拉回结果页', () => {
  const source = readSource('../src/pages/PlanGenerator.tsx')
  assert.ok(
    source.includes('if (next.result && stepRef.current === 2) setStep(3)'),
    '只有用户仍在生成步时才跳到结果页',
  )
})

test('P-167：入口引入 KaTeX copy-tex 扩展，复制公式得到 LaTeX 源码', () => {
  const source = readSource('../src/main.tsx')
  assert.ok(
    source.includes("import 'katex/contrib/copy-tex'"),
    '未引入 copy-tex 时复制公式得到视觉层与无障碍层拼接的重复乱码',
  )
})

// P-087：费率输入框回填已保存费率。
// 面板原先把输入框初值写死为 1/2，切换档案/模型时也不回填已保存费率，
// 点「保存真实费率」（upsert 语义）会用初始默认值静默覆盖已配置费率。
const SAVED_RATES = [
  { providerId: 'p1', model: 'model-a', inputPerMillion: 3, outputPerMillion: 15 },
  { providerId: 'p1', model: 'model-b', inputPerMillion: 0.5, outputPerMillion: 1.5 },
]

test('P-087：切到已保存费率的档案/模型组合时回填该组合的已保存费率', () => {
  const values = rateDisplayValues(null, SAVED_RATES, { providerId: 'p1', model: 'model-a' })
  assert.equal(values.input, '3')
  assert.equal(values.output, '15')
})

test('P-087：组合没有保存记录时回落初始值，不沿用上一个组合的残留', () => {
  const draft = editRateDraft(null, SAVED_RATES, { providerId: 'p1', model: 'model-a' }, 'input', '9')
  const other = rateDisplayValues(draft, SAVED_RATES, { providerId: 'p2', model: 'model-c' })
  assert.equal(other.input, DEFAULT_RATE_VALUES.input)
  assert.equal(other.output, DEFAULT_RATE_VALUES.output)
})

test('P-087：切换到另一个已配置组合时显示那个组合的费率，不串用草稿', () => {
  const draft = editRateDraft(null, SAVED_RATES, { providerId: 'p1', model: 'model-a' }, 'input', '9')
  const other = rateDisplayValues(draft, SAVED_RATES, { providerId: 'p1', model: 'model-b' })
  assert.equal(other.input, '0.5')
  assert.equal(other.output, '1.5')
})

test('P-087：正在编辑的草稿不被刷新覆盖，未编辑的字段沿用已保存费率', () => {
  const draft = editRateDraft(null, SAVED_RATES, { providerId: 'p1', model: 'model-a' }, 'output', '20')
  const values = rateDisplayValues(draft, SAVED_RATES, { providerId: 'p1', model: 'model-a' })
  assert.equal(values.input, '3')
  assert.equal(values.output, '20')
})

test('P-087：费率取值按档案与模型同时匹配，档案相同模型不同不算命中', () => {
  assert.equal(savedRateValues(SAVED_RATES, { providerId: 'p1', model: 'model-a' })?.input, '3')
  assert.equal(savedRateValues(SAVED_RATES, { providerId: 'p2', model: 'model-a' }), null)
  assert.equal(savedRateValues(SAVED_RATES, { providerId: 'p1', model: 'model-c' }), null)
})

test('P-087：草稿与已保存费率一致才算落定，编辑中与跨组合的草稿都不作废', () => {
  const selection = { providerId: 'p1', model: 'model-a' }
  const draft = editRateDraft(null, SAVED_RATES, selection, 'input', '9')
  assert.equal(rateDraftCommitted(draft, SAVED_RATES, selection), false)
  // 改回原值后草稿与已保存费率一致——可作废，显示值回到持久化事实
  const restored = editRateDraft(draft, SAVED_RATES, selection, 'input', '3')
  assert.equal(rateDraftCommitted(restored, SAVED_RATES, selection), true)
  // 草稿属于别的组合时不得作废当前组合的显示
  assert.equal(rateDraftCommitted(restored, SAVED_RATES, { providerId: 'p1', model: 'model-b' }), false)
})

test('P-087：用量面板的费率输入框经回填函数取值，不再写死初值 1/2', () => {
  const source = readSource('../src/components/UsagePanel.tsx')
  assert.ok(
    source.includes('rateDisplayValues'),
    '费率输入框必须按当前档案/模型回填已保存费率',
  )
  assert.ok(
    source.includes('value={rateValues.input}') && source.includes('value={rateValues.output}'),
    '费率输入框的显示值必须绑定回填结果',
  )
  assert.ok(
    !source.includes('setInputRate') && !source.includes('setOutputRate'),
    '费率输入框不能保留写死初值 1/2 的独立状态',
  )
})

test('P-028：全局摘要刷新订阅 study.progress_changed', () => {
  const source = readSource('../src/App.tsx')
  const anchor = source.indexOf('engagementBackend.dashboard()')
  assert.ok(anchor > 0, '未找到全局摘要加载点')
  const section = source.slice(anchor, anchor + 1500)
  assert.ok(
    section.includes("'study.progress_changed'"),
    '摘要刷新前缀表必须包含 study.progress_changed——勾选阶段完成只发这一条事件，漏配会让「今日」页持续推荐已完成阶段',
  )
})

test('P-072：IME 组字期间的 Esc 不关闭弹层', () => {
  const ui = readSource('../src/components/UI.tsx')
  const modal = ui.slice(ui.indexOf('export function Modal'))
  assert.ok(
    modal.includes("event.key !== 'Escape' || event.isComposing"),
    'Modal 的 Esc 监听必须带 isComposing 守卫——组字时按 Esc 是取消候选词',
  )
  const app = readSource('../src/App.tsx')
  assert.ok(
    app.includes("event.key === 'Escape' && !event.isComposing"),
    'App 级 Esc 仲裁必须带 isComposing 守卫',
  )
})

test('P-091：ProgressRing 的描边与环内文本取钳位值', () => {
  const source = readSource('../src/components/UI.tsx')
  const ring = source.slice(source.indexOf('export function ProgressRing'))
  assert.ok(!ring.includes('1 - value / 100'), 'strokeDashoffset 不得使用未钳位的原始 value')
  assert.ok(
    ring.includes('strokeDashoffset={circumference * (1 - clamped / 100)}'),
    'strokeDashoffset 必须使用钳位值',
  )
  assert.ok(!ring.includes('{value}%'), '环内百分比文本必须使用钳位值')
})

test('P-161：全局搜索的 ↑↓ 高亮对读屏可见', () => {
  const source = readSource('../src/App.tsx')
  const layer = source.slice(source.indexOf('function SearchLayer'))
  for (const marker of [
    'role="combobox"',
    'aria-activedescendant=',
    "role={results.length ? 'listbox' : undefined}",
    'role="option"',
    'aria-selected={index === activeIndex}',
  ]) {
    assert.ok(layer.includes(marker), `全局搜索缺少 ${marker}——↑↓ 高亮只有视觉底色，读屏感知不到当前选中项`)
  }
  assert.ok(
    layer.includes('aria-live="polite"') && layer.includes('当前选中'),
    'live region 必须播报当前选中项',
  )
})

test('P-164：装饰性图标统一经包装层补 aria-hidden', () => {
  const wrapper = readSource('../src/components/icons.tsx')
  assert.ok(
    wrapper.includes('aria-hidden="true"'),
    '图标包装层必须补 aria-hidden——lucide-react 的 svg 默认无可访问名称语义',
  )
  const direct = listSourceFiles()
    .filter(file => readFileSync(file, 'utf8').includes("from 'lucide-react'"))
    .map(file => file.href.slice(SRC_ROOT.href.length))
  assert.deepEqual(
    direct,
    ['components/icons.tsx'],
    `只有图标包装层可以从 lucide-react 取图标，实际：${direct.join('、')}`,
  )
})

test('P-164：图标包装层覆盖全部在用图标', () => {
  const wrapper = readSource('../src/components/icons.tsx')
  const missing = []
  for (const file of listSourceFiles()) {
    if (file.href === new URL('components/icons.tsx', SRC_ROOT).href) continue
    const source = readFileSync(file, 'utf8')
    for (const match of source.matchAll(/import(\s+type)?\s*\{([^}]*)\}\s*from\s*'([^']*icons)'/g)) {
      if (match[1]) continue
      for (const raw of match[2].split(',')) {
        const name = raw.trim().split(/\s+as\s+/)[0].trim()
        if (!name) continue
        if (!wrapper.includes(`export const ${name} =`)) missing.push(`${file.href.slice(SRC_ROOT.href.length)} → ${name}`)
      }
    }
  }
  assert.deepEqual(missing, [], `图标包装层缺少导出：${missing.join('、')}`)
})
