// 认证状态（P4 多用户，iter 59）。
// JWT 存 localStorage（dt_token / dt_user）；未登录时回退 VITE_AUTH_TOKEN（开发/单用户部署兼容，
// 与后端 AUTH_TOKEN 回退双轨一致）。注销仅清除本地存储（JWT 无服务端会话）。
//
// err.md D7 设计权衡（明示）：
// - JWT 明文存 localStorage：XSS 可窃取。本应用为本地/内网单部署工具，接受该风险换
//   实现简单（httpOnly cookie 需 CSRF 防护 + 跨域配置，复杂度更高）。公网部署应改 cookie 方案。
// - VITE_AUTH_TOKEN 打包进客户端 bundle：任何能访问前端的人都能拿到该 token。
//   仅应用于开发/受信网络的单用户部署；多用户部署不要设置 VITE_AUTH_TOKEN，走登录页 JWT。

const TOKEN_KEY = 'dt_token'
const USER_KEY = 'dt_user'

/** 登录态/用户信息变化事件（Layout 等据此响应式刷新，err.md D17）。 */
export const AUTH_CHANGED_EVENT = 'dt-auth-changed'

export interface AuthUser {
  id: number
  username: string
  name: string
  is_admin: boolean
}

function notifyAuthChanged(): void {
  window.dispatchEvent(new Event(AUTH_CHANGED_EVENT))
}

/** 当前请求应使用的 token：本地存储 JWT 优先，否则 .env 注入的 AUTH_TOKEN（可能为空串）。 */
export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? import.meta.env.VITE_AUTH_TOKEN ?? ''
}

/** 是否经登录页签发的 JWT 会话（注销按钮仅此时有意义；env token 无登录态可注销）。 */
export function hasJwtSession(): boolean {
  return localStorage.getItem(TOKEN_KEY) !== null
}

export function getStoredUser(): AuthUser | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as AuthUser
  } catch {
    return null
  }
}

export function setAuth(token: string, user: AuthUser): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  notifyAuthChanged()
}

/** 合并更新本地缓存的用户信息（如改名后同步顶栏显示，err.md D17）。 */
export function updateStoredUser(patch: Partial<AuthUser>): void {
  const cur = getStoredUser()
  if (!cur) return
  localStorage.setItem(USER_KEY, JSON.stringify({ ...cur, ...patch }))
  notifyAuthChanged()
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  notifyAuthChanged()
}

/**
 * JWT 会话下 401（token 过期/用户被删）-> 清本地会话并跳登录页
 * （env token 部署无登录态，不跳）。apiFetch / SSE / 上传共用（err.md D5）。
 */
export function handleUnauthorized(status: number): void {
  if (status === 401 && hasJwtSession()) {
    clearAuth()
    window.location.assign('/login')
  }
}
