// 后端按 UTF-8 字节校验长度，而 HTML maxLength 按字符计：
// 汉字占 3 字节，界面显示的可用长度只有后端限值的约 1/3。
// 这里提供按字节计数与截断的工具，输入侧与后端保持同一度量。
const encoder = new TextEncoder()

export function utf8Bytes(text: string): number {
  return encoder.encode(text).length
}

/** 把文本截断到不超过 limit 字节（在字符边界上截，不产生乱码）。 */
export function clampToBytes(text: string, limit: number): string {
  if (utf8Bytes(text) <= limit) return text
  // 二分查找最大可保留字符数，避免逐字符重编码
  let low = 0
  let high = text.length
  while (low < high) {
    const middle = Math.ceil((low + high) / 2)
    if (utf8Bytes(text.slice(0, middle)) <= limit) low = middle
    else high = middle - 1
  }
  // B-1：二分按 UTF-16 码元切片，可能停在代理对中间——孤立高代理计
  // 3B 而完整代理对计 4B，二分会停在半截处；清除尾部孤立代理，避免
  // 输入框乱字符与提交时 JSON 序列化产出非法转义导致保存必败
  let result = text.slice(0, low)
  const tail = result.charCodeAt(result.length - 1)
  if (tail >= 0xd800 && tail <= 0xdbff) result = result.slice(0, -1)
  return result
}
