// 讲义正文与笔记预览共用的 Markdown 预处理入口。
//
// 顺序固定：先把 LaTeX 的 `\[…\]` / `\(…\)` 定界符改写为 remark-math 可识别的
// 形式（P-129），再把独占一行的单行 `$$…$$` 规范为多行块公式形式（P-042）——
// 单行 `$$…$$` 的改写依赖行首行尾形态，必须在定界符改写之后进行。
//
// 改写只发生在渲染前：笔记正文入库保留用户原样的 LaTeX 标准定界符（编辑器
// 文本框要显示用户输入的内容，导出与备份也保留可移植的标准写法），渲染期统一
// 归一化后由 remark-math 解析。
//
// 导入带 `.ts` 扩展名（tsconfig 已开 allowImportingTsExtensions）：回归测试脚本
// 以 Node 直接加载本模块（desktop/scripts/markdown-test.mjs），Node 的 ESM 解析
// 要求显式扩展名。

import { normalizeDisplayMath } from './displayMath.ts'
import { normalizeMathDelimiters } from './mathDelimiters.ts'

/** 讲义与笔记预览的 Markdown 预处理（公式定界符与块级公式写法） */
export function prepareMarkdown(source: string): string {
  return normalizeDisplayMath(normalizeMathDelimiters(source))
}
