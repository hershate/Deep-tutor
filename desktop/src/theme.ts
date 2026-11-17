// P-123：主题偏好存在后端 SQLite，bootstrap 完成前前端拿不到真实值——
// 冷启动引导页与重新登录后界面按空快照的默认浅色渲染，深色主题用户每次都
// 看到一次浅→深闪变。
//
// 本模块把「最近一次已知主题」缓存在 localStorage：启动时在首帧之前同步套用
// 缓存主题消除闪变；真实偏好到达后由 App 的 effect 覆盖并回写缓存。
// 缓存只用于消除闪变，真实主题始终以后端快照为准（缓存过期只会造成一次极短的
// 反向切换，不会改变最终配色）。
//
// P-123 返工（评审：跨账户视觉串用）：缓存原先是全局单键，切换本地账户时
// 登录页与 bootstrap 期间沿用上一个账户的主题。缓存改为按账户分区：
// - `deep-tutor.theme` 存 `{ 账户 id: 'light' | 'dark' }` 映射；
// - `deep-tutor.theme.account` 记最近一次活动的账户 id，只用于首帧（bootstrap
//   前还不知道当前账户）套用缓存主题；
// - 账户切换（security.session_started 事件带 userId）时套用目标账户自己的
//   缓存主题，未缓存则回落默认浅色——不再沿用上一个账户的配色；
// - 账户选择页（access-gate）不沿用任何账户的主题。
//
// 不涉及任何凭据：这里只缓存明/暗两项界面偏好，不进入系统密钥库之外的敏感数据。

const THEME_KEY = 'deep-tutor.theme'
const ACTIVE_ACCOUNT_KEY = 'deep-tutor.theme.account'

export type ThemeName = 'light' | 'dark'

function isTheme(value: unknown): value is ThemeName {
  return value === 'light' || value === 'dark'
}

/** 最近一次活动的账户 id（首帧套用缓存主题的依据） */
function readActiveAccount(): number | null {
  try {
    const raw = window.localStorage.getItem(ACTIVE_ACCOUNT_KEY)
    if (raw === null) return null
    const value = Number(raw)
    return Number.isInteger(value) && value > 0 ? value : null
  } catch {
    return null
  }
}

/** 读取账户 → 主题的映射；旧版单值格式（'light'/'dark'）按活动账户兼容一次 */
function readThemeMap(): Record<string, ThemeName> {
  try {
    const raw = window.localStorage.getItem(THEME_KEY)
    if (!raw) return {}
    if (isTheme(raw)) {
      // 旧版全局单键格式：升级后首次启动按活动账户继承一次，避免浅→深闪变
      const legacy: Record<string, ThemeName> = {}
      const account = readActiveAccount()
      if (account !== null) legacy[String(account)] = raw
      return legacy
    }
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const map: Record<string, ThemeName> = {}
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (isTheme(value)) map[key] = value
    }
    return map
  } catch {
    return {}
  }
}

function writeThemeMap(map: Record<string, ThemeName>) {
  try {
    window.localStorage.setItem(THEME_KEY, JSON.stringify(map))
  } catch {
    /* 缓存不可用（隐私模式、存储被禁用）时忽略——真实主题照常生效 */
  }
}

/** 读取指定账户的缓存主题；不指定账户时取最近一次活动的账户 */
export function readCachedTheme(accountId?: number | null): ThemeName | null {
  const map = readThemeMap()
  const id = accountId ?? readActiveAccount()
  if (id === null) return null
  return map[String(id)] ?? null
}

/** 首帧之前套用缓存主题（无缓存则保持默认浅色） */
export function applyCachedTheme(): ThemeName | null {
  const theme = readCachedTheme()
  if (theme) document.documentElement.dataset.theme = theme
  return theme
}

/** 真实偏好到达后按账户回写缓存 */
export function cacheTheme(accountId: number, theme: unknown): void {
  if (!isTheme(theme) || !Number.isInteger(accountId) || accountId <= 0) return
  const map = readThemeMap()
  map[String(accountId)] = theme
  writeThemeMap(map)
  try {
    window.localStorage.setItem(ACTIVE_ACCOUNT_KEY, String(accountId))
  } catch {
    /* 同上：写不进去只影响下次首帧的缓存命中，不影响当前配色 */
  }
}

/** 切换到指定账户时套用该账户自己的缓存主题；未缓存则回落默认浅色 */
export function applyAccountTheme(accountId: number | null): ThemeName | null {
  const theme = accountId === null ? null : readCachedTheme(accountId)
  document.documentElement.dataset.theme = theme ?? 'light'
  return theme
}
