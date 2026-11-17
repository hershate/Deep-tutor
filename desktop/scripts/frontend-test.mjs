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
import { EMPTY_RATE_VALUES, editRateDraft, hasSavedRate, rateDisplayValues, rateDraftCommitted, savedRateValues } from '../src/usageRateDraft.ts'
import { deliversTo, pluginSetShrunk } from '../src/pluginEventScope.ts'

/** 读取前端源文件（用于「行为只能靠源码断言」的场景） */
function readSource(relative) {
  return readFileSync(new URL(relative, import.meta.url), 'utf8')
}

/** 去掉行注释（源码断言不能被注释里的示例代码命中） */
function stripLineComments(source) {
  return source
    .split(/\r?\n/)
    .map(line => line.replace(/\/\/.*$/, ''))
    .join('\n')
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

test('P-099 返工：事件驱动的只读拉取失败不再在页面顶部钉住红色横幅', () => {
  const source = readSource('../src/pages/PlanGenerator.tsx')
  assert.ok(
    source.includes('throttledMaterials(() => void refreshMaterials(true))'),
    'material.* 事件触发的节流刷新必须走事件路径——瞬时争用由下一轮事件自愈',
  )
  assert.ok(
    source.includes('if (eventDriven) reportBackendError(cause)'),
    '事件驱动的资料刷新失败须走 reportBackendError，不得 setError 钉住页面横幅',
  )
  assert.ok(
    source.includes('if (ticket === selectSeq.current) reportBackendError(cause)'),
    'plan.started 事件触发的方案拉取失败须走 reportBackendError',
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

test('P-087：组合没有保存记录时显示空值，不沿用上一个组合的残留', () => {
  const draft = editRateDraft(null, SAVED_RATES, { providerId: 'p1', model: 'model-a' }, 'input', '9')
  const other = rateDisplayValues(draft, SAVED_RATES, { providerId: 'p2', model: 'model-c' })
  assert.equal(other.input, '')
  assert.equal(other.output, '')
  assert.equal(EMPTY_RATE_VALUES.input, '')
  assert.equal(EMPTY_RATE_VALUES.output, '')
})

test('P-087：尚无保存记录的组合不带默认值参与保存（不会静默写入 1/2）', () => {
  const selection = { providerId: 'p2', model: 'model-c' }
  assert.equal(hasSavedRate(SAVED_RATES, selection), false)
  const values = rateDisplayValues(null, SAVED_RATES, selection)
  // 空值经 Number() 会变成 0，但面板在留空时禁用保存按钮，落盘前不会提交
  assert.equal(values.input.trim(), '')
  assert.equal(values.output.trim(), '')
  assert.equal(hasSavedRate(SAVED_RATES, { providerId: 'p1', model: 'model-a' }), true)
})

test('P-087：用量面板对尚无保存记录的组合显式提示，且不留兜底初值', () => {
  const source = readSource('../src/components/UsagePanel.tsx')
  assert.ok(
    source.includes('尚无保存费率'),
    '没有保存记录的组合必须显式提示，不能让用户以为输入框里的值就是已保存费率',
  )
  const module = readSource('../src/usageRateDraft.ts')
  assert.ok(
    !/input:\s*'1'/.test(module) && !/output:\s*'2'/.test(module),
    '费率模块不得再内置 1/2 兜底初值——默认值会参与 upsert 建出错误费率记录',
  )
})

test('P-087：面板注释不再声称后端会按上限钳制费率', () => {
  const source = readSource('../src/components/UsagePanel.tsx')
  assert.ok(
    !source.includes('后端可能按上限钳制'),
    '后端对超区间费率是拒绝（invalid_usage_rate）而非钳制，注释口径必须一致',
  )
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
  const wrapper = stripLineComments(readSource('../src/components/icons.tsx'))
  const body = wrapper.slice(wrapper.indexOf('function decorative'))
  assert.ok(
    !/aria-hidden="true"\s*\{\.\.\./.test(body),
    '图标包装层不得用「默认值在前、props 展开在后」的写法——调用方传 aria-hidden={undefined} 会把默认值覆盖掉',
  )
  assert.ok(
    body.includes('aria-hidden={ariaHidden ?? true}'),
    '图标包装层必须显式合并 aria-hidden：undefined/null 落回装饰模式，false/' + "'false'" + ' 退出装饰模式',
  )
  assert.ok(
    body.indexOf('aria-hidden={ariaHidden ?? true}') > body.indexOf('{...rest}'),
    '合并后的 aria-hidden 必须写在 props 展开之后，否则仍会被静默覆盖',
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

test('P-164：图标包装层的「退出装饰模式」开关不被 undefined 覆盖', () => {
  const wrapper = stripLineComments(readSource('../src/components/icons.tsx'))
  const body = wrapper.slice(wrapper.indexOf('function decorative'))
  const merged = body.match(/aria-hidden=\{([^}]+)\}/)
  assert.ok(merged, '未找到包装层的 aria-hidden 合并表达式')
  assert.ok(
    /\?\?/.test(merged[1]) && !/\|\|/.test(merged[1]),
    '合并必须用 ??——|| 会把调用方显式传的 false 变成 true，等于关掉退出装饰模式的开关',
  )
  // 用包装层里真实的合并表达式求值：undefined/null 落回 true，显式 false/'false' 退出装饰模式
  const merge = new Function('ariaHidden', `return ${merged[1]}`)
  assert.equal(merge(undefined), true, 'aria-hidden={undefined} 必须落回装饰模式')
  assert.equal(merge(null), true, 'aria-hidden={null} 必须落回装饰模式')
  assert.equal(merge(false), false, 'aria-hidden={false} 必须能退出装饰模式')
  assert.equal(merge('false'), 'false', "aria-hidden='false' 必须能退出装饰模式")
})

test('P-164：带 aria-label 的图标必须声明 role="img"', () => {
  const offenders = []
  for (const file of listSourceFiles()) {
    const source = readFileSync(file, 'utf8')
    // 只查图标包装层导出的图标（按本地别名解析），不查 Button 等 UI 组件
    const iconNames = new Set()
    for (const match of source.matchAll(/import\s*\{([^}]*)\}\s*from\s*'[^']*icons'/g)) {
      for (const raw of match[1].split(',')) {
        const parts = raw.trim().split(/\s+as\s+/)
        const local = (parts[1] ?? parts[0]).trim()
        if (local) iconNames.add(local)
      }
    }
    for (const match of source.matchAll(/<([A-Z][A-Za-z0-9]*)\b([^>]*aria-label[^>]*)>/g)) {
      const [, name, attrs] = match
      if (!iconNames.has(name)) continue
      if (!attrs.includes('role="img"')) {
        offenders.push(`${file.href.slice(SRC_ROOT.href.length)} → <${name}>`)
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `带 aria-label 的图标必须同时声明 role="img"，否则读屏播报的是无角色节点：${offenders.join('、')}`,
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

test('P-011：笔记预览经 memo 组件渲染，击键不再触发整篇 Markdown/KaTeX 重解析', () => {
  const source = readSource('../src/pages/Library.tsx')
  assert.ok(
    source.includes('const MemoMarkdown = memo('),
    '笔记预览必须用 memo 化的 Markdown 组件——未 memo 时每次击键都重建插件数组并整篇重解析',
  )
  assert.ok(
    source.includes('<MemoMarkdown text={previewText'),
    '预览区必须渲染 memo 组件',
  )
  assert.ok(
    !source.includes('<article className="note-preview"><ReactMarkdown'),
    '预览区不得直接渲染未 memo 的 ReactMarkdown',
  )
})

test('P-013：空标签按「未分类」落库，不再被后端必填校验整批拒收', () => {
  const source = readSource('../src/pages/Library.tsx')
  assert.ok(
    source.includes('const noteTagValue = noteTag.trim() ? noteTag : DEFAULT_NOTE_TAG'),
    '保存与草稿必须用归一化后的标签——后端要求标签非空',
  )
  assert.ok(
    source.includes("const DEFAULT_NOTE_TAG = '未分类'"),
    '无标签时的落库值必须与打开笔记时的默认标签同口径',
  )
  assert.ok(
    source.includes('tag: noteTagValue'),
    '笔记落库必须使用归一化后的标签',
  )
})

// P-025：读取历史版本时后端只回传被读版本，前端据此丢失「返回当前版本」
// 入口，并把旧版的 done 误当当前状态（force 误判重复生成与计费、生成中
// 取消入口消失）。
test('P-025：讲义读历史版本时按当前版本判定生成态并保留返回入口', () => {
  const source = readSource('../src/pages/StudyRoom.tsx')
  assert.ok(
    source.includes('const currentStatus = lecture?.currentStatus ?? lecture?.status'),
    '生成态必须按当前版本状态判定——读历史版本时响应的 status 是被读版本的 done',
  )
  assert.ok(
    source.includes('const running = currentStatus === \'running\' || currentStatus === \'cancelling\''),
    '取消入口与弹窗可关性必须跟当前版本的生成态走',
  )
  assert.ok(
    source.includes('backend.generateLecture(plan.id, stageId, currentStatus === \'done\')'),
    'force 必须取当前版本状态——按被读版本判定会重复生成并计费',
  )
  assert.ok(
    source.includes('{readingOldVersion && <Button variant="ghost" size="sm" onClick={() => void refreshLecture()}>返回当前版本'),
    '读历史版本时必须给出「返回当前版本」入口',
  )
  assert.ok(
    !source.includes("lecture.status === 'done' && lecture.version > 1"),
    '不得再用被读版本的 version/status 判定是否存在当前版本',
  )
})

// P-030：取消交叠保留的评价（终态 cancelled，评价结果照存）此前被后端按
// status == 'done' 必拒，前端按钮却只看 feedback 是否存在，用户看着完整
// 量规却存不了笔记。
test('P-030：迭代笔记按钮的门禁与后端一致（评价结果存在即可保存）', () => {
  const source = readSource('../src/pages/Projects.tsx')
  assert.ok(
    source.includes('disabled={!feedback || evaluating}'),
    '按钮门禁必须与后端一致：有真实评价结果且不在评价中才可保存',
  )
  assert.ok(
    source.includes("const evaluating = Boolean(currentVersion && ['running', 'cancelling'].includes(currentVersion.status))"),
    '评价中的版本不得开放迭代笔记',
  )
})

// P-033：/models 返回超过 200 个模型的供应商此前整单提交必被后端拒收，
// 且编辑器没有任何删减模型的手段。
test('P-033：模型列表超上限时按上限截断并提供删减入口', () => {
  const source = readSource('../src/pages/ProviderHub.tsx')
  assert.ok(
    source.includes('models.length > MAX_PROVIDER_MODELS'),
    '超过单档案上限时必须截断而非整单提交',
  )
  assert.ok(
    source.includes('trimmed.length < models.length'),
    '截断后必须如实告知未保存的条数',
  )
  assert.ok(
    source.includes('removeModel(model)'),
    '模型区必须提供删减入口',
  )
  assert.ok(
    source.includes('aria-label={`删除模型 ${model}`}'),
    '删减按钮须有可读标签',
  )
  const limits = readSource('../src/limits.ts')
  assert.ok(
    limits.includes('export const MAX_PROVIDER_MODELS = 200'),
    '前端上限常量必须与后端档案校验同值',
  )
})

// P-024：后端 plugins::matches 已对 state/draft/application 三个内部命名空间
// 提前返回 false（内部事件不对插件开放），前端原先仍把 `*` 视为命中任意
// kind——口径分叉使 state.changed（打字保存的高频写路径）继续触发全量刷新，
// 且页面注释声称「与后端同口径」失真。
test('P-024：前端投递判定与后端 plugins::matches 同口径（内部事件不命中通配订阅）', () => {
  const wildcard = [{ enabled: true, subscriptions: ['*'] }]
  for (const kind of ['state.changed', 'draft.saved', 'draft.deleted', 'application.reset']) {
    assert.equal(
      deliversTo(wildcard, kind),
      false,
      `通配订阅不得命中内部事件 ${kind}——后端 matches 对 state/draft/application 提前返回 false`,
    )
  }
  assert.equal(deliversTo(wildcard, 'practice.graded'), true, '通配订阅必须命中业务事件')
  assert.equal(deliversTo(wildcard, 'plan.created'), true, '通配订阅必须命中业务事件')
  const scoped = [{ enabled: true, subscriptions: ['practice.*'] }]
  assert.equal(deliversTo(scoped, 'practice.graded'), true, '命名空间订阅必须命中本命名空间事件')
  assert.equal(deliversTo(scoped, 'plan.created'), false, '命名空间订阅不得命中其他命名空间事件')
  assert.equal(deliversTo(scoped, 'practice'), false, '命名空间订阅不得命中不带后缀的事件')
  assert.equal(
    deliversTo([{ enabled: false, subscriptions: ['*'] }], 'practice.graded'),
    false,
    '停用的插件不得被判为命中',
  )
  const source = readSource('../src/pages/Integrations.tsx')
  assert.ok(
    source.includes("import { deliversTo, pluginSetShrunk } from '../pluginEventScope'"),
    '集成页必须使用与后端同口径的判据实现',
  )
  assert.ok(
    !/function\s+deliversTo\s*\(/.test(source),
    '集成页不得再内联一份与后端分叉的投递判定',
  )
})

// P-027：任意一次 overview 刷新都无条件清空「加载更早事件」的分页缓存，
// 用户已翻页的历史投递记录会无故消失。判据是「已安装插件 id 集合减少」，
// 本轮把判据下沉到纯函数并补上回归测试——原先无任何测试固化，重构把
// 无条件清空改回去也不会被 CI 发现。
test('P-027：「更早投递」分页缓存只在插件集减少时清空', () => {
  assert.equal(pluginSetShrunk(['a', 'b'], ['a', 'b']), false, '插件集不变时不得清空分页缓存')
  assert.equal(pluginSetShrunk(['a', 'b'], ['a', 'b', 'c']), false, '新增插件不得清空分页缓存')
  assert.equal(pluginSetShrunk(['a', 'b'], ['a']), true, '插件被卸载时必须清空分页缓存')
  assert.equal(pluginSetShrunk(['a', 'b'], []), true, '插件全部卸载时必须清空分页缓存')
  assert.equal(pluginSetShrunk([], ['a']), false, '首次记录快照不算减少')
  const source = readSource('../src/pages/Integrations.tsx')
  assert.ok(
    source.includes('if (pluginSetShrunk(installedIds.current, nextIds)) {'),
    '刷新成功分支必须以插件集减少为条件清空分页缓存',
  )
  assert.ok(
    source.includes('installedIds.current = nextIds'),
    '刷新成功后必须记录本次已安装插件 id 快照',
  )
  assert.ok(
    /if \(pluginSetShrunk\(installedIds\.current, nextIds\)\) \{\s*setEarlier\(current => current\.length \? \[\] : current\)\s*setEarlierDone\(false\)\s*\}/.test(source),
    '清空动作必须落在插件集减少的守卫内，不得无条件执行',
  )
})

// P-034：下载配置的实际落盘发生在后台任务里（sync/mod.rs atomic_export），
// 面板原先在选定保存位置后立刻渲染「在资源管理器中显示」——落盘尚未发生、
// reveal 回执也未登记，点击必然被 reveal 白名单拒绝。
test('P-034：「在资源管理器中显示」按钮只在下载任务落盘完成后出现', () => {
  const source = readSource('../src/components/SyncPanel.tsx')
  assert.ok(
    source.includes('if (kind === \'download\') setDownloadJobId(accepted.id)'),
    '发起下载时只记住任务 id，不得在落盘前记下目标路径',
  )
  assert.ok(
    source.includes("item.id === downloadJobId && item.status === 'done' && item.localPath"),
    '定位目标必须取自已完成且带落盘路径的下载任务',
  )
  assert.ok(
    !source.includes('setDownloadTarget('),
    '不得在任务受理时直接登记定位目标',
  )
  assert.ok(
    source.includes('{downloadTarget && <RevealExport path={downloadTarget} />}'),
    '定位按钮仍需按目标路径渲染',
  )
})
