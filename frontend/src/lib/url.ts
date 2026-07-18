// URL 安全工具：LLM/分享来源的 url 字段仅 http(s) 才可作为链接渲染。
// 防 javascript:/data: 等协议经 <a href> 注入（分享页为跨用户场景，
// 恶意方案资源的 javascript: 链接点击后可在源站上下文执行脚本）。

/** 是否为可安全渲染为链接的 http(s) URL。 */
export function isHttpUrl(url: string | null | undefined): boolean {
  return typeof url === 'string' && /^https?:\/\//i.test(url.trim())
}
