import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { Note, Project, Resource, Task } from '../data'
import type { Preferences } from '../pages/Settings'

export type CollectionNamespace = 'tasks' | 'notes' | 'resources' | 'projects' | 'preferences'
export type AppSnapshot = {
  tasks: Task[]
  notes: Note[]
  resources: Resource[]
  projects: Project[]
  preferences: Preferences
  versions: Record<CollectionNamespace | 'drafts', number>
  lastEventId: number
}
export type BackendEvent = {
  id: number
  kind: string
  payload: Record<string, unknown>
  createdAt: string
}
export type BackendError = { code: string; message: string }
export type BackupPreview = { counts: Record<string, number>; appendOnly: true; format: 'deep-tutor-backup'; version: 2 }
export type Material = {
  id: number
  title: string
  kind: string
  size: number
  sha256: string
  status: string
  progress: number
  textLength: number
  parseMode: string
  warning: string | null
  error: BackendError | null
  originalAvailable: boolean
}
export type MaterialDetail = Material & { text: string | null }
export type MaterialImportResult = {
  fileName: string
  material: Material | null
  deduplicated: boolean
  error: BackendError | null
}

export const backend = {
  bootstrap: () => invoke<AppSnapshot>('bootstrap'),
  getAppState: () => invoke<AppSnapshot>('get_app_state'),
  getCollection: <T>(namespace: CollectionNamespace) => invoke<{ value: T; revision: number }>('get_collection', { namespace }),
  replaceCollection: <T>(namespace: CollectionNamespace, expectedRevision: number, value: T) => invoke<AppSnapshot>('replace_collection', { namespace, expectedRevision, value }),
  saveDraft: (id: string, value: unknown) => invoke<AppSnapshot>('save_draft', { id, value }),
  getDraft: <T>(id: string) => invoke<{ value: T | null; revision: number }>('get_draft', { id }),
  deleteDraft: (id: string) => invoke<AppSnapshot>('delete_draft', { id }),
  resetApplication: () => invoke<AppSnapshot>('reset_application'),
  exportBackup: () => invoke<Record<string, unknown>>('export_backup'),
  previewBackup: (backup: unknown) => invoke<BackupPreview>('preview_backup', { backup }),
  importBackup: (backup: unknown) => invoke<AppSnapshot>('import_backup', { backup }),
  eventsSince: (lastId: number) => invoke<{ events: BackendEvent[]; latestId: number; hasMore: boolean }>('events_since', { lastId }),
  openMaterialFiles: () => invoke<string[] | null>('plugin:dialog|open', {
    options: { title: '导入学习资料', multiple: true, filters: [{ name: '学习资料', extensions: ['pdf', 'docx', 'pptx', 'epub', 'txt', 'md'] }] },
  }),
  saveMaterialFile: (title: string, extension: string) => invoke<string | null>('plugin:dialog|save', {
    options: { title: '保存到本地', defaultPath: title, filters: [{ name: '文件', extensions: [extension] }] },
  }),
  importMaterialFiles: (paths: string[]) => invoke<MaterialImportResult[]>('import_material_files', { paths }),
  listMaterials: () => invoke<Material[]>('list_materials'),
  getMaterial: (id: number) => invoke<MaterialDetail>('get_material', { id }),
  enqueueMaterialParse: (id: number) => invoke<{ jobId: number; material: Material }>('enqueue_material_parse', { id }),
  cancelMaterialParse: (id: number) => invoke<Material>('cancel_material_parse', { id }),
  deleteMaterial: (id: number) => invoke<AppSnapshot>('delete_material', { id }),
  exportMaterialOriginal: (id: number, destination: string) => invoke<{ bytes: number; sha256: string }>('export_material_original', { id, destination }),
  exportMaterialText: (id: number, destination: string) => invoke<{ bytes: number; sha256: string }>('export_material_text', { id, destination }),
}

export function onBackendEvent(handler: (event: BackendEvent) => void) {
  return listen<BackendEvent>('backend://event', message => handler(message.payload))
}

export function backendErrorMessage(error: unknown) {
  if (typeof error === 'object' && error !== null && 'code' in error) {
    const typed = error as Partial<BackendError>
    return `${typed.code || 'backend_error'}：${typed.message || '操作未完成'}`
  }
  return error instanceof Error ? error.message : String(error)
}

export function reportBackendError(error: unknown) {
  const message = backendErrorMessage(error)
  console.error(error)
  window.dispatchEvent(new CustomEvent('backend-error', { detail: message }))
}
