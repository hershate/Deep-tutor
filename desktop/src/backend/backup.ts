import { invoke } from '@tauri-apps/api/core'
import type { AppSnapshot } from './client'

export type BackupPreview = {
  format: 'deep-tutor-backup'
  version: 3
  createdAt: string
  tableCounts: Record<string, number>
  collectionCounts: Record<string, number>
  files: number
  fileBytes: number
  databaseBytes: number
  appendOnly: true
}
export type BackupResult = { bytes: number; sha256: string; files: number; records: number }

export const backupBackend = {
  export: (destination: string) => invoke<BackupResult>('export_full_backup', { destination }),
  preview: (path: string) => invoke<BackupPreview>('preview_full_backup', { path }),
  import: (path: string) => invoke<AppSnapshot>('import_full_backup', { path }),
  open: () => invoke<string | null>('plugin:dialog|open', { options: { title: '选择 Deep Tutor 全量备份', multiple: false, filters: [{ name: 'Deep Tutor 全量备份', extensions: ['dtbackup'] }] } }),
  save: () => invoke<string | null>('plugin:dialog|save', { options: { title: '保存全量备份', defaultPath: 'DeepTutor-backup.dtbackup', filters: [{ name: 'Deep Tutor 全量备份', extensions: ['dtbackup'] }] } }),
}
