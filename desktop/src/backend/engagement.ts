import { invoke } from '@tauri-apps/api/core'
import type { Recommendation, TimeSlice } from './reports'
import type { PageId } from '../data'

/** 空查询时的页面快捷入口：与后端 pages() 保持一致，避免每次打开搜索都走一次 IPC。 */
export const quickPages: SearchResult[] = [
  ['today', '今日'], ['generator', 'AI 方案生成'], ['plan', '学习计划'], ['study', '学习空间'],
  ['map', '知识地图'], ['agent', 'Agent 助手'], ['practice', '练习测评'], ['review', '记忆复习'],
  ['library', '笔记与 Wiki'], ['projects', '项目作品'], ['reports', '学习报告'], ['community', '学习社区'],
  ['providers', '模型供应商'], ['integrations', '插件与事件'], ['settings', '设置与偏好'],
].map(([page, title]) => ({ kind: 'page', id: page, title, subtitle: '功能页面', page: page as PageId, planId: null, stageId: null }))

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
