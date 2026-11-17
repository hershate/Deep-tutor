// P-043：KaTeX 解析失败的错误呈现
// rehype-katex 捕获 KaTeX 抛错后以 strict:'ignore', throwOnError:false 兜底
// 重渲染（rehype-katex/lib/index.js:104-113），非法公式被渲染成「红色原始
// LaTeX + 英文 title 悬浮提示」——用户看到的是一坨红色源码，无法判断是渲染
// 失败还是内容本身。本插件在 rehypeKatex 之后遍历 hast 树，把这两类失败都
// 改写为中文说明：
//   1) 抛错兜底：KaTeX 输出 <span class="katex-error" title="ParseError: …">，
//      改写为中文说明 + 原始公式源码，原始诊断留在 data-katex-error 备查。
//   2) strict 降级：KaTeX 默认 strict:'warn'，未定义命令一类不抛错，只把该
//      片段按错误色渲染成红色文本（无 .katex-error 标记、无 title），在公式
//      之后补一句中文说明。
type HastNode = {
  type: string
  tagName?: string
  value?: string
  properties?: Record<string, unknown>
  children?: HastNode[]
}

// KaTeX 默认错误色（errorColor 未配置时的取值）——strict 降级时用它识别
// 被染成错误色的片段
const KATEX_ERROR_COLOR = '#cc0000'
const ERROR_COLOR_PATTERN = new RegExp(`color:\\s*${KATEX_ERROR_COLOR}`, 'i')

// KaTeX 诊断信息 → 中文说明。顺序敏感：先匹配更具体的规则
const EXPLANATIONS: Array<[RegExp, string]> = [
  [/can be used only in display mode/, '这个公式环境只能用于独立成行的块级公式，请把它放进单独一行的 $$…$$ 之间'],
  [/Undefined control sequence/, '公式里用了 KaTeX 不支持的命令'],
  [/Double subscript/, '同一个符号下面出现了两个下标'],
  [/Double superscript/, '同一个符号上面出现了两个上标'],
  [/Expected 'EOF'/, '公式的括号或分组没有闭合'],
  [/Expected group after/, '公式里缺少必需的花括号分组'],
  [/Missing \$ inserted/, '公式缺少 $ 分隔符'],
  [/No such environment|Unknown environment|Invalid environment name/, 'KaTeX 不支持这个公式环境'],
  [/Too many left delimiters|Too many right delimiters|Mismatch: \\begin/, '公式的左右定界符没有配对'],
  [/Mismatched |length of delimiters doesn't match/, '公式的定界符与参数个数不匹配'],
  [/works only in math mode|works only in text mode/, '这条命令只能用在另一种模式下'],
  [/Limit controls must follow a math operator/, '极限符号后面必须跟一个运算符'],
  [/Invalid delimiter|Illegal delimiter|Unknown stretchy delimiter/, '定界符写法不合法'],
  [/Expected & or \\\\ or \\cr or \\end|Expected \\\\ or \\cr or \\end/, '表格类环境里各列要用 & 或 \\ 分隔'],
  [/Can't use function/, '这条命令不能出现在公式里'],
  [/valid only within array environment/, '这条命令只能用在数组环境里'],
  [/Too few columns specified/, '数组环境声明的列数与内容不一致'],
  [/Invalid argument number|Invalid number of arguments|Argument number/, '命令的参数个数不对'],
  [/Unexpected end of input/, '公式内容不完整，命令参数或分组没有闭合'],
  [/Unexpected character/, '公式中含有非法字符'],
]

const FALLBACK_EXPLANATION = '公式语法有误，或含有 KaTeX 不支持的命令，请检查 LaTeX 写法'

function explain(diagnostic: string): string {
  for (const [pattern, text] of EXPLANATIONS) {
    if (pattern.test(diagnostic)) return text
  }
  return FALLBACK_EXPLANATION
}

function classNames(properties: Record<string, unknown> | undefined): string[] {
  const value = properties?.className
  if (Array.isArray(value)) return value.map(item => String(item))
  if (typeof value === 'string') return value.split(/\s+/)
  return []
}

function styleOf(properties: Record<string, unknown> | undefined): string {
  const value = properties?.style
  return typeof value === 'string' ? value : ''
}

function isErrorColor(properties: Record<string, unknown> | undefined): boolean {
  return ERROR_COLOR_PATTERN.test(styleOf(properties))
}

function textOf(node: HastNode): string {
  if (node.type === 'text') return node.value ?? ''
  return (node.children ?? []).map(textOf).join('')
}

// 逐元素遍历，同时给出祖先链（插入兄弟节点与判断最外层都需要）
function walkElements(node: HastNode, ancestors: HastNode[], visit: (element: HastNode, ancestors: HastNode[]) => void): void {
  if (node.type === 'element') visit(node, ancestors)
  for (const child of node.children ?? []) walkElements(child, [...ancestors, node], visit)
}

// 取最外层的 .katex 元素（公式整体）及其父节点——说明插在公式之后。
// ancestors 由外向内排列：ancestors[0] 是根，末位是直接父节点
function katexClassAncestor(element: HastNode, ancestors: HastNode[]): { formula: HastNode; parent: HastNode | null } | null {
  for (let index = 0; index < ancestors.length; index += 1) {
    if (!classNames(ancestors[index].properties).includes('katex')) continue
    return { formula: ancestors[index], parent: index > 0 ? ancestors[index - 1] : null }
  }
  if (!classNames(element.properties).includes('katex')) return null
  return { formula: element, parent: ancestors.length ? ancestors[ancestors.length - 1] : null }
}

/**
 * rehype 插件：把 KaTeX 的失败输出改写为中文错误说明。
 * 必须在 rehypeKatex 之后执行。
 */
export default function rehypeKatexError() {
  return (tree: unknown) => {
    const root = tree as HastNode
    // 第一遍：抛错兜底产出的 .katex-error 元素
    walkElements(root, [], element => {
      if (!classNames(element.properties).includes('katex-error')) return
      const properties = element.properties ?? {}
      const diagnostic = typeof properties.title === 'string' ? properties.title : ''
      const explanation = explain(diagnostic)
      const source = textOf(element).trim()
      element.properties = {
        ...properties,
        // 悬浮提示也给中文，原文案是英文 KaTeX 诊断
        title: `公式渲染失败：${explanation}`,
        // 原始诊断留在数据属性里备查，不进入界面文案
        'data-katex-error': diagnostic,
      }
      // 内联红色由样式表接管（错误块的底色与边框需要一并呈现）
      delete element.properties.style
      element.children = [
        { type: 'text', value: `公式渲染失败：${explanation}。原始公式：` },
        {
          type: 'element',
          tagName: 'code',
          properties: { className: ['katex-error-source'] },
          children: [{ type: 'text', value: source }],
        },
      ]
    })
    // 第二遍：strict 降级——未被 KaTeX 支持的片段只按错误色染红，没有错误
    // 标记也没有提示，在公式之后补一句中文说明
    const annotated = new Set<HastNode>()
    walkElements(root, [], (element, ancestors) => {
      if (!isErrorColor(element.properties)) return
      if (ancestors.some(ancestor => isErrorColor(ancestor.properties))) return
      const target = katexClassAncestor(element, ancestors)
      if (!target) return
      if (annotated.has(target.formula)) return
      annotated.add(target.formula)
      const source = textOf(element).trim()
      const note: HastNode = {
        type: 'element',
        tagName: 'span',
        properties: { className: ['katex-error-note'] },
        children: [{
          type: 'text',
          value: source
            ? `公式渲染提示：KaTeX 不支持其中的「${source}」，该部分已按原文保留`
            : '公式渲染提示：其中有 KaTeX 不支持的内容，已按原文保留',
        }],
      }
      const parent = target.parent
      if (!parent) return
      const index = parent.children?.indexOf(target.formula) ?? -1
      if (index < 0) return
      parent.children?.splice(index + 1, 0, note)
    })
  }
}
