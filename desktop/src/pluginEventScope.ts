// P-024 / P-027：插件事件面与「更早投递」分页缓存的判据。
//
// 这两处判据原先内联在集成页里，改坏时没有任何测试会发现：P-024 的口径分叉
// 让内部事件（state.changed 等）继续触发全量刷新，P-027 的无条件清空让用户
// 已翻页的历史投递记录在任意一次刷新后消失。这里把判据抽成纯函数，供集成页
// 取值与 Node 回归测试共用同一份实现。
//
// 投递口径与后端 plugins::matches 一致：
// - 内部事件命名空间（state/draft/application）不对插件开放，通配订阅 `*`
//   也不命中（后端 plugins::INTERNAL_EVENT_NAMESPACES）；
// - 其余事件按 `*` 或 `<命名空间>.*` 判定，与后端的 prefix + '.' 判定等价。

/** 内部事件命名空间：不开放给插件（与后端 plugins::INTERNAL_EVENT_NAMESPACES 同口径） */
export const INTERNAL_EVENT_NAMESPACES: readonly string[] = ['state', 'draft', 'application']

/** 插件订阅来源的最小结构（集成页的插件记录字段更多，这里只取判定所需部分） */
export type PluginSubscription = { enabled: boolean; subscriptions: string[] }

/** 事件命名空间——kind 的第一段 */
export function eventNamespace(kind: string): string {
  return kind.split('.')[0] ?? ''
}

/** 事件是否属于内部命名空间（内部状态同步，不进入插件面） */
export function isInternalEvent(kind: string): boolean {
  return INTERNAL_EVENT_NAMESPACES.includes(eventNamespace(kind))
}

/** 事件是否会被任一启用插件的订阅命中——与后端 plugins::matches 同口径 */
export function deliversTo(plugins: PluginSubscription[], kind: string): boolean {
  if (isInternalEvent(kind)) return false
  return plugins.some(plugin => plugin.enabled && plugin.subscriptions.some(pattern =>
    pattern === '*' || (pattern.endsWith('.*') && kind.startsWith(pattern.slice(0, -1)))
  ))
}

/**
 * 订阅模式是否指向内部命名空间——后端 plugins::matches 对内部命名空间提前
 * 返回 false，因此显式订阅（如 draft.*、application.reset）同样不投递。
 * 存量插件若订阅了这些命名空间，其「真实投递」计数不会再增长：集成页据此
 * 给出可见提示，避免订阅静默失效（NEW 4）。
 */
export function subscriptionTargetsInternalNamespace(pattern: string): boolean {
  if (pattern === '*') return false
  const namespace = pattern.split('.')[0] ?? ''
  return INTERNAL_EVENT_NAMESPACES.includes(namespace)
}

/**
 * 已安装插件集合是否减少——只有减少才使已加载的「更早投递」分页缓存失效。
 * 单纯新增插件不使既有历史失效；减少时服务端已随卸载 CASCADE 清空对应投递，
 * 内存里的旧行必须一并丢弃，否则「卸载后重装同 id」会让旧行复活为幽灵记录。
 */
export function pluginSetShrunk(previous: string[], next: string[]): boolean {
  return previous.some(id => !next.includes(id))
}
