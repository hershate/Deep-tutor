// P-042：remark-math 把同一行内的 `$$…$$` 判为行内公式（mathText 构造，
// rehype-katex 的 displayMode=false），`{align}` 等只允许显示模式的环境直接
// 渲染成 class="katex-error" 的红色源码块。remark-math 的块级公式构造
// （mathFlow）要求 `$$` 独占一行——多行写法 `$$\n…\n$$` 实测正常。
//
// 本模块把「独占一行」的单行 `$$…$$` 改写成多行块公式形式，让粘贴的
// 一行式块公式与后端讲义里的单行块公式走与手写多行完全相同的解析路径。
//
// 语义边界（按 P-042 问题单的界定：单行 `$$…$$` 即「一行式块公式」）：
// - 独占一行的 `$$…$$` 一律按块级（显示）公式处理——`$$` 在 Markdown/KaTeX
//   约定里即表示显示公式，本应用自身的公式工具栏插入的也是多行块公式。
//   可见后果：原先被 remark-math 当行内公式渲染的「独占一行 `$$x$$`」
//   现以显示模式呈现（居中、字号更大、上下留白）——这是按问题单口径的
//   语义纠正，不是回归；同一行内与文字并排的 `$$x^2$$` 不受影响；
// - 围栏代码块（``` / ~~~）与行内代码内的 `$$` 不改写；
// - 行内出现的 `$$…$$`（如「公式 $$x^2$$ 结束」）保持行内公式——它是合法的
//   行内用法，改成块级会把同一段落拆成三段；此类内容若用了只允许显示模式
//   的环境，由 rehypeKatexError（P-043）给出中文错误说明；
// - 列表项、引用块内的单行块公式同样保持行内（remark-math 的 mathFlow
//   要求 `$$` 位于行首）。

/** 围栏代码块的起始标记：行首至多 3 个空格后跟 ``` 或 ~~~ */
const FENCE = /^ {0,3}(`{3,}|~{3,})/
/** 独占一行的单行块公式：至多 3 个空格缩进 + `$$…$$` + 行尾空白 */
const SINGLE_LINE_MATH = /^(\s{0,3})\$\$([^$]+)\$\$(\s*)$/

/**
 * 把独占一行的单行 `$$…$$` 改写为多行块公式形式，其余内容原样返回。
 */
export function normalizeDisplayMath(source: string): string {
  if (!source.includes('$$')) return source
  const lines = source.split('\n')
  const output: string[] = []
  let fence: string | null = null
  for (const line of lines) {
    const marker = FENCE.exec(line)
    if (marker) {
      const char = marker[1][0]
      if (fence === null) fence = char
      else if (char === fence) fence = null
      output.push(line)
      continue
    }
    if (fence !== null) {
      output.push(line)
      continue
    }
    const match = SINGLE_LINE_MATH.exec(line)
    // 内容为空时不动（`$$` 单独一行是块级公式的开始/结束标记）
    if (!match || !match[2].trim()) {
      output.push(line)
      continue
    }
    const indent = match[1]
    output.push(`${indent}$$`, match[2], `${indent}$$`)
  }
  return output.join('\n')
}
