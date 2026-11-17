import { invoke } from '@tauri-apps/api/core'
import { invokeDialog } from './client'
import type { BackendError } from './client'

export type StorageOverview = {
  dataRoot: string
  defaultDataRoot: string
  pendingDataRoot: string | null
  ready: boolean
  initializing: boolean
  error: BackendError | null
  /** 106#9：配置指向的目录在启动时缺失，已新建空工作区的提示 */
  notice: string | null
  /** P-044/P-014：待生效目录与本进程已绑定的数据根不同——此时进程内换根
   *  会被拒（storage_root_changed），只能重启应用后在新目录启动 */
  pendingRequiresRestart: boolean
}

type OverviewListener = (overview: StorageOverview) => void

const overviewListeners = new Set<OverviewListener>()

/** 概览变更广播——目录设置改动后全局横幅须即时跟随，否则旧提示一直挂到
 *  重启（P-001：横幅原先只在挂载时取一次概览） */
function publishOverview(next: StorageOverview): StorageOverview {
  for (const listener of [...overviewListeners]) listener(next)
  return next
}

/** 订阅存储概览变化；返回取消订阅函数 */
export function subscribeStorageOverview(listener: OverviewListener): () => void {
  overviewListeners.add(listener)
  return () => { overviewListeners.delete(listener) }
}

export const storageBackend = {
  overview: async () => publishOverview(await invoke<StorageOverview>('get_storage_overview')),
  retry: async () => publishOverview(await invoke<StorageOverview>('retry_storage_initialization')),
  save: async (path: string) => publishOverview(await invoke<StorageOverview>('set_storage_location', { path })),
  reset: async () => publishOverview(await invoke<StorageOverview>('reset_storage_location')),
  restart: () => invoke<void>('restart_application'),
  chooseDirectory: (defaultPath?: string) => invokeDialog<string>('plugin:dialog|open', {
    options: { title: '选择本地学习数据目录', directory: true, multiple: false, defaultPath },
  }),
}
