// 认证状态（P4 多用户，iter 59）。
// JWT 存 localStorage（dt_token / dt_user）；未登录时回退 VITE_AUTH_TOKEN（开发/单用户部署兼容，
// 与后端 AUTH_TOKEN 回退双轨一致）。注销仅清除本地存储（JWT 无服务端会话）。

const TOKEN_KEY = 'dt_token'
const USER_KEY = 'dt_user'

export interface AuthUser {
  id: number
  username: string
  name: string
  is_admin: boolean
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
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}
