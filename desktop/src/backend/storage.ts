import { invoke } from '@tauri-apps/api/core'
import type { BackendError } from './client'

export type StorageOverview = {
  dataRoot: string
  defaultDataRoot: string
  pendingDataRoot: string | null
  ready: boolean
  initializing: boolean
  error: BackendError | null
}

export const storageBackend = {
  overview: () => invoke<StorageOverview>('get_storage_overview'),
  retry: () => invoke<StorageOverview>('retry_storage_initialization'),
  save: (path: string) => invoke<StorageOverview>('set_storage_location', { path }),
  reset: () => invoke<StorageOverview>('reset_storage_location'),
  restart: () => invoke<void>('restart_application'),
  chooseDirectory: (defaultPath?: string) => invoke<string | null>('plugin:dialog|open', {
    options: { title: '选择本地学习数据目录', directory: true, multiple: false, defaultPath },
  }),
}
