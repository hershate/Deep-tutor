import { invoke } from '@tauri-apps/api/core'
import type { AppSnapshot, BackendError } from './client'

export type ProjectFeedback = {
  concept: number
  implementation: number
  verification: number
  summary: string
  suggestions: string[]
}
export type ProjectVersion = {
  id: number
  projectId: number
  versionNumber: number
  content: string
  milestoneId: number | null
  milestoneTitle: string | null
  materialId: number | null
  status: 'running' | 'cancelling' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  attempt: number
  providerId: string
  requestId: string | null
  feedback: ProjectFeedback | null
  error: BackendError | null
  createdAt: string
  updatedAt: string
  completedAt: string | null
}
export type ProjectWorkspace = { versions: ProjectVersion[] }
export type ProjectSubmission = { projectId: number; content: string; milestoneId: number | null; materialId: number | null }

export const projectsBackend = {
  workspace: () => invoke<ProjectWorkspace>('get_project_workspace'),
  submit: (input: ProjectSubmission) => invoke<ProjectWorkspace>('submit_project_version', { input }),
  cancel: (id: number) => invoke<ProjectWorkspace>('cancel_project_feedback', { id }),
  resume: (id: number) => invoke<ProjectWorkspace>('resume_project_feedback', { id }),
  saveNote: (versionId: number, content: string) => invoke<AppSnapshot>('save_project_iteration_note', { input: { versionId, content } }),
}
