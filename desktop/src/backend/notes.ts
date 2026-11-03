import { invoke } from '@tauri-apps/api/core'
import type { AppSnapshot } from './client'

export type NotesImportResult = { state: AppSnapshot; imported: number }

export const notesBackend = {
  openFiles: () => invoke<string[] | string | null>('plugin:dialog|open', {
    options: { title: '导入笔记', multiple: false, filters: [{ name: 'Markdown、TXT 或笔记归档', extensions: ['zip', 'md', 'txt'] }] },
  }),
  import: (path: string) => invoke<NotesImportResult>('import_notes', { path }),
  overview: () => invoke<LibraryOverview>('get_library_overview'),
}

export type KnowledgeLink = { planId: string; stageId: string; nodeId: string; title: string }
export type LibraryIssue = {
  id: string
  severity: 'info' | 'warning' | 'error'
  kind: string
  title: string
  detail: string
  noteId: number | null
  materialId: number | null
}
export type LibraryOverview = {
  notes: { noteId: number; links: KnowledgeLink[] }[]
  issues: LibraryIssue[]
  linkedNodes: number
  duplicateNoteGroups: number
  unlinkedNotes: number
  missingOriginals: number
  failedMaterials: number
}
