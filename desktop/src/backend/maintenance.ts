import { invoke } from '@tauri-apps/api/core'
import type { BackendError } from './client'

export type FileItem = { path: string; bytes: number }
export type MaintenanceReport = {
  database: {
    integrity: string
    foreignKeyViolations: number
    journalMode: string
    schemaVersion: number
    pageCount: number
    freePages: number
    pageSize: number
    bytes: number
    walBytes: number
  }
  orphans: {
    originals: FileItem[]
    scratch: FileItem[]
    plugins: FileItem[]
    tempdirs: FileItem[]
    missingOriginals: string[]
    issues: BackendError[]
  }
}
export type CleanupResult = {
  deletedFiles: number
  deletedTempItems: number
  freedBytes: number
  failures: BackendError[]
  /** B-12：未轮到的剩余孤儿数，>0 时提示再清一次 */
  remaining: number
}
export type VacuumResult = {
  beforeBytes: number
  afterBytes: number
  walBusy: number
  walLog: number
  walCheckpointed: number
  journalMode: string
}

export const maintenanceBackend = {
  report: () => invoke<MaintenanceReport>('get_maintenance_report'),
  cleanup: () => invoke<CleanupResult>('cleanup_orphan_files'),
  vacuum: () => invoke<VacuumResult>('vacuum_database'),
}
