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
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { prepareMarkdown } from '../src/markdown/prepare.ts'
import { applyAccountTheme, cacheTheme, readCachedTheme } from '../src/theme.ts'

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
