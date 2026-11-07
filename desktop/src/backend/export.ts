import { invoke } from '@tauri-apps/api/core'

export type ExportScope = 'plan' | 'lecture' | 'knowledge' | 'review' | 'schedule'
export type ExportFormat = 'md' | 'pdf' | 'ics' | 'apkg' | 'html' | 'csv' | 'txt' | 'opml'
export type ExportInput = {
  scope: ExportScope
  format: ExportFormat
  planId: string | null
  stageId: string | null
  unitIndex: number | null
  version: number | null
}
export type ExportResult = { bytes: number; sha256: string; format: ExportFormat; title: string; warning?: string }
export type NotesArchiveResult = { bytes: number; sha256: string; count: number }

export const exportBackend = {
  content: (input: ExportInput, destination: string) => invoke<ExportResult>('export_content', { input, destination }),
  notesArchive: (destination: string) => invoke<NotesArchiveResult>('export_notes_archive', { destination }),
}
