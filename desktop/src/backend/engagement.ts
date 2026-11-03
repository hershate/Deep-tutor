import { invoke } from '@tauri-apps/api/core'
import type { Recommendation, TimeSlice } from './reports'
import type { PageId } from '../data'

export type SearchResult = {
  kind: string
  id: string
  title: string
  subtitle: string
  page: PageId
  planId: string | null
  stageId: string | null
}
export type NotificationItem = {
  id: string
  kind: string
  title: string
  body: string
  page: PageId
  createdAt: string
  readAt: string | null
}
export type NotificationOverview = { items: NotificationItem[]; unreadCount: number; suppressed: boolean }
export type LearningProfile = {
  name: string
  role: string
  goal: string
  language: string
  weeklyHours: number
  learningStyle: string
  explanationStyle: string
  quietStart: string
  quietEnd: string
  quietEnabled: boolean
  diagnosticStartedAt: string | null
}
export type DashboardTask = { title: string; detail: string; minutes: number; kind: string }
export type DashboardItem = { kind: string; title: string; detail: string; date: string; page: PageId }
export type DashboardOverview = {
  name: string
  goal: string
  nextTask: DashboardTask | null
  progress: number
  deadline: string | null
  reviewDue: number
  reviewMinutes: number
  streak: number
  week: TimeSlice[]
  focusSeconds: number
  practiceCount: number
  retentionPercent: number | null
  upcoming: DashboardItem[]
  recommendation: Recommendation | null
}

export const engagementBackend = {
  search: (query: string) => invoke<SearchResult[]>('search_workspace', { query }),
  notifications: () => invoke<NotificationOverview>('get_notifications'),
  markAllRead: () => invoke<NotificationOverview>('mark_all_notifications_read'),
  markRead: (id: string) => invoke<NotificationOverview>('mark_notification_read', { id }),
  profile: () => invoke<LearningProfile>('get_learning_profile'),
  saveProfile: (input: LearningProfile) => invoke<LearningProfile>('save_learning_profile', { input }),
  restartDiagnostic: () => invoke<LearningProfile>('restart_learning_diagnostic'),
  dashboard: () => invoke<DashboardOverview>('get_dashboard'),
}
