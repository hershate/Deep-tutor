// P-129：`\[…\]`（显示公式）与 `\(…\)`（行内公式）是 LaTeX 标准定界符，
// remark-math 只识别 $ 与 $$——粘贴这两种写法时反斜杠被当作 markdown 转义吃掉，
// 正文原样输出「[x^2 + y^2 = z^2]」，既不渲染也不报错，用户无法意识到是
// 分隔符不被支持，内容还会原样存进笔记。
//
// 本模块在 markdown 解析前把两种定界符改写为 remark-math 能识别的 $ / $$ 形式：
// - `\[…\]` 独占整行（内容可跨行）时按显示公式改写为块级 `$$`；
//   出现在段落中间时按行内公式改写为 `$$…$$`（与行内 `$$…$$` 同款处理）；
// - `\(…\)` 一律按行内公式改写为 `$…$`；
// - 围栏代码块与行内代码内的内容原样保留；
// - 定界符未闭合或内容为空时原样保留，不做猜测性改写。
//
// P-129 返工（评审意见）：
// 1) 行内 `\[…\]` 一律改写会把普通正文里的字面量变成公式：`a\[0\]` 被改写成
//    `a$$0$$`，`[0]` 字面量凭空消失、正文被改写。改写前先要求内容含公式信号
//    字符（\ ^ _ = + 等）；不含信号的按 markdown 转义原样保留（渲染为 `[…]`
//    字面量），不做猜测性改写。
// 2) 引用块内的块级写法 `> \[` / `> \]` 原先不被改写（整行仅为 `\[` 的判定不含
//    引用前缀），仍静默退化成方括号原文。本模块识别行首引用前缀，引用块内的
//    块级写法同样改写为 `> $$`；块级公式不跨引用块边界（边界处视为未闭合）。
// 3) 独占一行的单行块级写法 `\[…\]` 原先落到行内路径（渲染为行内公式），与本
//    模块承诺的「独占整行按显示公式」不符——单行块级写法同样改写为块级 `$$`。
//
// 改写只发生在渲染前（prepare.ts）：笔记正文入库仍保留用户原样的 LaTeX 标准
// 定界符。存原始写法而不是存改写后的 `$$`，是因为编辑器文本框要显示用户自己
// 输入的内容，导出与备份也应当保留可移植的标准写法；而 P-129 抱怨的「看起来
// 像公式的乱码固化到用户数据」已由渲染期改写消除——同样的内容现在能渲染成公式。

/** 围栏代码块的起始标记：行首至多 3 个空格后跟 ``` 或 ~~~ */
const FENCE = /^[ \t]{0,3}(`{3,}|~{3,})/
/** 行首的引用块前缀（可嵌套）：`> `、`>> ` 等；不含引用前缀时整行即正文 */
const QUOTE = /^([ \t]*(?:>[ \t]?)+)(.*)$/
/** 块级显示公式的开始行：整行只有 `\[`（至多 3 个前导空格） */
const BLOCK_OPEN = /^[ \t]{0,3}\\\[[ \t]*$/
/** 块级显示公式的结束行：整行只有 `\]`（至多 3 个前导空格） */
const BLOCK_CLOSE = /^[ \t]{0,3}\\][ \t]*$/
/** 单行块级显示公式：整行只有 `\[…\]` */
const SINGLE_LINE_BLOCK = /^[ \t]{0,3}\\\[(.*)\\\][ \t]*$/
/** 行内代码片段（内容不改写） */
const CODE_SPAN = /(`+)([^`]*?)\1/g
/** 行内代码片段存在性判定（无 g 标志，不共享 lastIndex 状态） */
const CODE_SPAN_PRESENT = /(`+)([^`]*?)\1/
/** 同一行内的 `\[…\]` */
const DISPLAY_INLINE = /\\\[([^\n]*?)\\\]/g
/** 同一行内的 `\(…\)` */
const INLINE_PAREN = /\\\(([^\n]*?)\\\)/g
/** 公式信号字符：内容含这些字符才把 `\[…\]` 判作公式（返工说明 1） */
const MATH_SIGNAL = /[\\^_=+×÷±≤≥≠≈∑∫√∞π]/

/** 行内拆分：引用块前缀与正文 */
function splitQuote(line: string): { prefix: string; rest: string } {
  const match = QUOTE.exec(line)
  return match ? { prefix: match[1], rest: match[2] } : { prefix: '', rest: line }
}

/** 内容是否像公式：含公式信号字符才判定 */
function looksLikeMath(content: string): boolean {
  return MATH_SIGNAL.test(content)
}

/** 找到与块级开始行配对的结束行；跨引用块边界或找不到返回 -1 */
function findBlockClose(lines: string[], from: number, prefix: string): number {
  for (let index = from; index < lines.length; index += 1) {
    const parts = splitQuote(lines[index])
    if (parts.prefix !== prefix) return -1
    if (BLOCK_CLOSE.test(parts.rest)) return index
  }
  return -1
}

/** 改写一段非代码文本里的定界符 */
function rewriteSegment(segment: string): string {
  return segment
    .replace(DISPLAY_INLINE, (all: string, content: string) => (looksLikeMath(content) ? `$$${content}$$` : all))
    .replace(INLINE_PAREN, (all: string, content: string) => (content.trim() ? `$${content}$` : all))
}

/** 改写一行里的定界符——行内代码片段原样保留 */
function rewriteLine(line: string): string {
  const parts: string[] = []
  let cursor = 0
  CODE_SPAN.lastIndex = 0
  let match = CODE_SPAN.exec(line)
  while (match !== null) {
    parts.push(rewriteSegment(line.slice(cursor, match.index)))
    parts.push(match[0])
    cursor = match.index + match[0].length
    match = CODE_SPAN.exec(line)
  }
  parts.push(rewriteSegment(line.slice(cursor)))
  return parts.join('')
}

/**
 * 把 `\[…\]` / `\(…\)` 定界符改写为 remark-math 可识别的 `$$` / `$` 形式。
 */
export function normalizeMathDelimiters(source: string): string {
  if (!source.includes('\\[') && !source.includes('\\(')) return source
  const lines = source.split('\n')
  const output: string[] = []
  let fence: string | null = null
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const fenceMatch = FENCE.exec(line)
    if (fenceMatch) {
      const marker = fenceMatch[1][0]
      fence = fence === null ? marker : (marker === fence ? null : fence)
      output.push(line)
      continue
    }
    if (fence !== null) {
      output.push(line)
      continue
    }
    const { prefix, rest } = splitQuote(line)
    const indent = /^[ \t]*/.exec(rest)?.[0] ?? ''
    const body = rest.slice(indent.length)
    // 块级显示公式：整行只有 `\[`，配对的 `\]` 独占一行（返工说明 2）
    if (BLOCK_OPEN.test(body)) {
      const closeIndex = findBlockClose(lines, index + 1, prefix)
      if (closeIndex !== -1) {
        output.push(`${prefix}${indent}$$`)
        for (let inner = index + 1; inner < closeIndex; inner += 1) output.push(lines[inner])
        output.push(`${prefix}${indent}$$`)
        index = closeIndex
        continue
      }
      output.push(line)
      continue
    }
    // 单行块级显示公式：整行只有 `\[…\]`（返工说明 3）
    const single = SINGLE_LINE_BLOCK.exec(body)
    if (single && !CODE_SPAN_PRESENT.test(body)) {
      const content = single[1]
      if (looksLikeMath(content)) {
        output.push(`${prefix}${indent}$$`, `${prefix}${indent}${content}`, `${prefix}${indent}$$`)
        continue
      }
      output.push(line)
      continue
    }
    output.push(rewriteLine(line))
  }
  return output.join('\n')
}
