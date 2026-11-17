import { invoke } from '@tauri-apps/api/core'
import type { BackendError } from './client'

export type SyncConfig = {
  kind: 'webdav' | 's3'
  endpoint: string
  remotePath: string
  region: string
  bucket: string
  authKind: 'none' | 'basic' | 'bearer' | 'aws4'
  username: string
  allowPrivate: boolean
  autoSync: boolean
  updatedAt: string
}
export type SyncConfigView = { config: SyncConfig; hasCredentials: boolean }
export type SyncConfigInput = Omit<SyncConfig, 'updatedAt'> & { password: string | null; accessKey: string | null; secretKey: string | null }
export type SyncJob = {
  id: string
  kind: 'check' | 'upload' | 'download' | 'import' | 'restore'
  status: 'running' | 'cancelling' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  remotePath: string
  // 151#1：删去 Rust 从不写入的 routingMode/activeProvider 两键（死类型字段）
  // P-098：连接检测（check）的结果区分「对象已存在」与「对象尚未创建」
  // （后端 13#30 的正常态区分）——objectPresent/note 是后端实际写入的键
  result: null | { profiles?: number; sha256?: string; bytes?: number; reachable?: boolean; objectPresent?: boolean; note?: string; remoteConfirmed?: boolean; historyPath?: string; versions?: number }
  localPath: string | null
  error: BackendError | null
  createdAt: string
  updatedAt: string
}
// P-150：nextAutoSyncAt 由后端按调度器同源口径给出（调度器锚点取全表最新
// upload 行，面板 job 列表只含最近 50 行，无法自行推导锚点）
export type SyncOverview = { config: SyncConfigView | null; jobs: SyncJob[]; nextAutoSyncAt: string | null }
export type SyncVersion = { path: string; size: number | null; etag: string | null; lastModified: string | null }

export const syncBackend = {
  overview: () => invoke<SyncOverview>('get_sync_overview'),
  saveConfig: (input: SyncConfigInput) => invoke<SyncConfigView>('save_sync_config', { input }),
  start: (kind: SyncJob['kind'], destination: string | null = null) => invoke<SyncJob>('start_sync_job', { kind, destination }),
  get: (id: string) => invoke<SyncJob>('get_sync_job', { id }),
  cancel: (id: string) => invoke<SyncJob>('cancel_sync_job', { id }),
  versions: () => invoke<SyncVersion[]>('get_sync_versions'),
  restore: (versionPath: string, destination: string | null = null) => invoke<SyncJob>('restore_sync_version', { versionPath, destination }),
}
