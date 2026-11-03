import { invoke } from '@tauri-apps/api/core'
import type { BackendError } from './client'

export type KnowledgeSource = { materialId: number; title: string; sha256: string; kind: string }
export type KnowledgeCitation = KnowledgeSource & {
  quote: string
  charStart: number
  charEnd: number
  line: number
}
export type KnowledgeHit = {
  kind: 'material' | 'note'
  id: number
  title: string
  excerpt: string
  score: number
  materialId: number | null
  noteId: number | null
  citation: KnowledgeCitation | null
}
export type KnowledgeSession = {
  id: number
  question: string
  answer: string | null
  sufficient: boolean | null
  citations: KnowledgeCitation[]
  sources: KnowledgeSource[]
  status: 'running' | 'cancelling' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  providerId: string
  requestId: string | null
  attempt: number
  warning: string | null
  error: BackendError | null
  createdAt: string
  updatedAt: string
}

export const knowledgeBackend = {
  search: (query: string) => invoke<KnowledgeHit[]>('search_knowledge', { query }),
  ask: (question: string, materialIds: number[]) => invoke<KnowledgeSession>('ask_knowledge', { question, materialIds }),
  list: () => invoke<KnowledgeSession[]>('list_knowledge_sessions'),
  get: (id: number) => invoke<KnowledgeSession>('get_knowledge_session', { id }),
  cancel: (id: number) => invoke<KnowledgeSession>('cancel_knowledge_answer', { id }),
  resume: (id: number) => invoke<KnowledgeSession>('resume_knowledge_answer', { id }),
  remove: (id: number) => invoke<void>('delete_knowledge_session', { id }),
  clearHistory: () => invoke<void>('clear_knowledge_history'),
}
