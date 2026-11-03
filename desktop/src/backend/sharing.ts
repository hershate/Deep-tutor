import { invoke } from '@tauri-apps/api/core'
import type { LearningResult } from './client'

export type SharedPlanSnapshot = Pick<LearningResult, 'goal' | 'deadline' | 'graph' | 'stages' | 'schedule' | 'validation' | 'warnings'> & {
  dailyMinutes: number
  planVersion: number
}
export type ShareStatus = 'active' | 'expired' | 'revoked'
export type ShareMetadata = {
  id: number
  planId: string
  planVersion: number
  title: string
  status: ShareStatus
  createdAt: string
  expiresAt: string
  revokedAt: string | null
}
export type CreatedShare = { share: ShareMetadata; token: string; package: string }
export type SharedPlanView = { plan: SharedPlanSnapshot; expiresAt: string; source: 'local' | 'offline' }

export const sharingBackend = {
  create: (planId: string, expiresInDays: number | null = null) => invoke<CreatedShare>('create_shared_plan', { planId, expiresInDays }),
  list: (planId: string | null = null) => invoke<ShareMetadata[]>('list_shared_plans', { planId }),
  read: (token: string) => invoke<SharedPlanView>('read_shared_plan', { token }),
  revoke: (id: number) => invoke<ShareMetadata>('revoke_shared_plan', { id }),
  readPackage: (content: string) => invoke<SharedPlanView>('read_shared_plan_package', { package: content }),
  exportPackage: (content: string, destination: string) => invoke<{ bytes: number }>('export_shared_plan_package', { package: content, destination }),
  readFile: (path: string) => invoke<SharedPlanView>('read_shared_plan_file', { path }),
  openFile: () => invoke<string | null>('plugin:dialog|open', { options: { title: '打开只读学习方案', multiple: false, filters: [{ name: 'Deep Tutor 分享包', extensions: ['dtshare'] }] } }),
  saveFile: (planId: string) => invoke<string | null>('plugin:dialog|save', { options: { title: '保存只读分享包', defaultPath: `${planId}.dtshare`, filters: [{ name: 'Deep Tutor 分享包', extensions: ['dtshare'] }] } }),
}

export function localShareUrl(token: string) {
  return `${window.location.origin}${window.location.pathname}?share=${encodeURIComponent(token)}#shared`
}

