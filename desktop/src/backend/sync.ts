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
}
export type SyncConfigView = { config: SyncConfig; hasCredentials: boolean }
export type SyncConfigInput = SyncConfig & { password: string | null; accessKey: string | null; secretKey: string | null }
export type SyncJob = {
  id: string
  kind: 'check' | 'upload' | 'download' | 'import'
  status: 'running' | 'cancelling' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  remotePath: string
  result: null | { profiles?: number; routingMode?: string; activeProvider?: string | null; sha256?: string; bytes?: number; reachable?: boolean; remoteConfirmed?: boolean }
  localPath: string | null
  error: BackendError | null
  createdAt: string
  updatedAt: string
}
export type SyncOverview = { config: SyncConfigView | null; jobs: SyncJob[] }

export const syncBackend = {
  overview: () => invoke<SyncOverview>('get_sync_overview'),
  saveConfig: (input: SyncConfigInput) => invoke<SyncConfigView>('save_sync_config', { input }),
  start: (kind: SyncJob['kind'], destination: string | null = null) => invoke<SyncJob>('start_sync_job', { kind, destination }),
  get: (id: string) => invoke<SyncJob>('get_sync_job', { id }),
  cancel: (id: string) => invoke<SyncJob>('cancel_sync_job', { id }),
}
