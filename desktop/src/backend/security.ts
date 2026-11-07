import { invoke } from '@tauri-apps/api/core'

export type LocalUser = {
  id: number
  name: string
  role: '管理员' | '成员'
  active: boolean
  current: boolean
  createdAt: string
  lastLoginAt: string | null
}
export type SessionView = { id: string; createdAt: string; expiresAt: string }
export type TokenView = { id: number; name: string; mask: string; createdAt: string; lastUsedAt: string | null }
export type CreatedToken = { token: string; view: TokenView }
export type QuotaView = { resource: string; used: number; limit: number }
export type AuthMode = { id: string; label: string; enabled: boolean; detail: string }
export type SecurityOverview = {
  users: LocalUser[]
  currentUser: LocalUser | null
  session: SessionView | null
  requiresLogin: boolean
  tokens: TokenView[]
  quotas: QuotaView[]
  modes: AuthMode[]
}

export const securityBackend = {
  overview: () => invoke<SecurityOverview>('get_security_overview'),
  login: (userId: number) => invoke<SecurityOverview>('login_local_user', { userId }),
  logout: () => invoke<void>('logout_local_user'),
  createUser: (name: string, role: string) => invoke<SecurityOverview>('create_local_user', { name, role }),
  setActive: (userId: number, active: boolean) => invoke<SecurityOverview>('set_local_user_active', { userId, active }),
  removeUser: (userId: number) => invoke<SecurityOverview>('remove_local_user', { userId }),
  createToken: (name: string) => invoke<CreatedToken>('create_access_token', { name }),
  revokeToken: (tokenId: number) => invoke<SecurityOverview>('revoke_access_token', { tokenId }),
  // 21#1：PAT 登录入口——验证通过即为令牌所属用户签发新会话
  authenticateToken: (token: string) => invoke<LocalUser>('authenticate_access_token', { token }),
  setQuota: (userId: number, resource: string, limit: number) => invoke<SecurityOverview>('set_security_quota', { userId, resource, limit }),
}
