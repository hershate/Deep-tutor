import { invoke } from '@tauri-apps/api/core'
import type { AppSnapshot } from './client'

export type NotesImportResult = { state: AppSnapshot; imported: number }

export const notesBackend = {
  // 62#7：批量导入名副其实——允许多选 .md/.txt 逐个导入；ZIP 归档（一次
  // 可含至多 1000 条）仍选单文件，由后端解包
  openFiles: () => invoke<string[] | string | null>('plugin:dialog|open', {
    options: { title: '导入笔记', multiple: true, filters: [{ name: 'Markdown、TXT 或笔记归档', extensions: ['zip', 'md', 'txt'] }] },
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
