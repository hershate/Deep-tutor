/**
 * P-130：生产构建的 React 内部错误被压缩成英文串
 * 「Minified React error #310; visit https://reactjs.org/docs/error-decoder.html?…」
 * 直接展示给中文用户只剩一串错误码与外网链接，既无法说明发生了什么，
 * 也无法据此准确描述问题。开发构建才有完整文案与组件栈，生产包两者都没有。
 *
 * 这里把压缩串解析为中文说明并保留错误编号（编号是唯一可追溯的线索），
 * 非压缩错误（业务异常、TypeError 等）原样透出。
 *
 * 压缩串里 decoder URL 的 args[] 参数是英文片段（如 object with keys {bad}），
 * 正是 P-130 要求从界面上去掉的内容，故不并入用户可见文案；完整原串由
 * 两级错误边界的 console.error 保留（main.tsx / App.tsx），排查时仍可取到。
 *
 * 评审 NEW（空描述）：错误对象不带任何描述时返回中文兜底文案而不是空串——
 * 空串会让错误边界判定为「尚未捕获」而重新渲染子树并再次抛错，兜底页不出现。
 */
const MINIFIED_REACT_ERROR = /^Minified React error #(\d+)/

export function readableErrorMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)
  const matched = MINIFIED_REACT_ERROR.exec(raw)
  if (matched) {
    return `界面渲染遇到 React 内部错误（编号 #${matched[1]}）。当前版本未包含该错误的完整说明；请先重新加载，若反复出现请记录该编号以便排查。`
  }
  if (!raw.trim()) return '界面渲染遇到未知错误（错误对象未提供描述）。'
  return raw
}
