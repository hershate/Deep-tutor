import { invoke } from '@tauri-apps/api/core'

export type PluginRecord = {
  id: string
  name: string
  version: string
  description: string
  kind: string
  enabled: boolean
  generation: number
  subscriptions: string[]
  eventCount: number
  createdAt: string
  updatedAt: string
}
export type PluginEvent = { pluginId: string; pluginName: string; eventId: number; kind: string; createdAt: string }
export type DeveloperAsset = { id: string; title: string; fileName: string; kind: string; bytes: number; sha256: string }
export type DeveloperAssetContent = { id: string; title: string; fileName: string; content: string; sha256: string }
export type PluginOverview = { plugins: PluginRecord[]; events: PluginEvent[]; assets: DeveloperAsset[]; enabledCount: number; eventCount: number }

export const pluginBackend = {
  overview: () => invoke<PluginOverview>('get_plugin_overview'),
  install: (content: string) => invoke<PluginRecord>('install_plugin', { content }),
  setEnabled: (id: string, enabled: boolean) => invoke<PluginRecord>('set_plugin_enabled', { id, enabled }),
  reload: (id: string) => invoke<PluginRecord>('reload_plugin', { id }),
  uninstall: (id: string) => invoke<void>('uninstall_plugin', { id }),
  ping: () => invoke<number>('emit_plugin_ping'),
  // before 为 event_id 游标：取早于该 ID 的 100 条（14#18）
  events: (pluginId?: string, before?: number) => invoke<PluginEvent[]>('list_plugin_events', { pluginId: pluginId ?? null, before: before ?? null }),
  asset: (id: string) => invoke<DeveloperAssetContent>('get_developer_asset', { id }),
}
