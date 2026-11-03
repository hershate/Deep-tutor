import { invoke } from '@tauri-apps/api/core'
import type { AppSnapshot } from './client'

export type NotesImportResult = { state: AppSnapshot; imported: number }

export const notesBackend = {
  openFiles: () => invoke<string[] | string | null>('plugin:dialog|open', {
    options: { title: '导入笔记', multiple: false, filters: [{ name: 'Markdown、TXT 或笔记归档', extensions: ['zip', 'md', 'txt'] }] },
  }),
  import: (path: string) => invoke<NotesImportResult>('import_notes', { path }),
}
