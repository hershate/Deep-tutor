// 统一 Markdown 渲染（iter 354，BUG4/5）：react-markdown + GFM + KaTeX 公式 + XSS 清洗。
// 全站唯一渲染入口——历史各页内联的 ReactMarkdown 收敛于此，配置一处统一。
//
// 插件链（顺序关键）：
//   remark：remark-gfm（表格/任务列表/删除线）→ remark-math（解析 $...$ / $$...$$）
//   rehype：rehype-sanitize → rehype-katex
//     · sanitize 在 katex 之前：先清洗用户原始 HTML（防 XSS；react-markdown v9 默认本就不渲染
//       裸 HTML，此处双保险）。remark-math 的 math 节点（<code class="language-math
//       math-display|inline">）经 sanitizeSchema 放行其 className，公式原文作为 text 子节点穿过清洗保留。
//     · katex 在 sanitize 之后：把 math 节点渲染为 KaTeX HTML；其输出由库自身保证安全
//       （无脚本/事件处理器），不再清洗 → 字体/排版完整。若 katex 在 sanitize 之前，sanitize 会
//       剥离其 class/style/MathML 标签致排版损坏，故必须此顺序。
//   KaTeX 字体经 katex.min.css 全局引入一次（Vite 打包去重）。
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeSanitize, { defaultSchema, type Options } from 'rehype-sanitize'
import 'katex/dist/katex.min.css'

// 默认 schema 仅放行 code 上匹配 /^language-./ 的 className（hast-util-sanitize schema.js），
// 会剥离 math-display / math-inline（导致 $$ 块公式被当成行内渲染）。此处补放行 math 相关类，
// 让 remark-math 的 math 节点完整穿过 sanitize 交由 katex 渲染。
const sanitizeSchema: Options = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [['className', /^language-./, 'math', 'math-inline', 'math-display', 'language-math']],
  },
}

export default function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[
        [rehypeSanitize, sanitizeSchema],
        rehypeKatex,
      ]}
    >
      {children ?? ''}
    </ReactMarkdown>
  )
}
