import { invoke } from '@tauri-apps/api/core'

export type FocusSession = {
  id: number
  taskId: number | null
  status: 'running' | 'paused' | 'completed' | 'cancelled'
  plannedSeconds: number
  actualSeconds: number
  remainingSeconds: number
  startedAt: string
  endedAt: string | null
}
export type FocusOverview = { active: FocusSession | null; recent: FocusSession[] }
export type ReportMetrics = {
  focusSeconds: number
  previousFocusSeconds: number
  practiceCount: number
  correctPercent: number | null
  retentionPercent: number | null
  goalStatus: string
  goalDetail: string
}
export type TrendPoint = { label: string; value: number | null }
export type TimeSlice = { kind: string; seconds: number }
export type MasteryRow = {
  planId: string
  nodeId: string
  title: string
  mastery: number | null
  practiceAccuracy: number | null
  retention: number | null
  evidenceCount: number
}
export type Recommendation = { id: string; title: string; detail: string; kind: string; planId: string | null; nodeId: string | null }
export type PlanStats = {
  id: string
  title: string
  status: string
  progress: number
  focusSeconds: number
  practiceCount: number
  completedStages: number
  totalStages: number
}
export type LearningReport = {
  period: string
  metrics: ReportMetrics
  trend: TrendPoint[]
  timeComposition: TimeSlice[]
  masteries: MasteryRow[]
  recommendations: Recommendation[]
  plans: PlanStats[]
  weekly: TimeSlice[]
}
export type CalendarDay = { date: string; minutes: number; note: string; source: 'timer' | 'self_report' | 'none' }
export type CalendarReport = { streak: number; totalMinutes: number; noteCount: number; checkinRate: number; days: CalendarDay[] }
export type AdoptionResult = { added: number; focus: FocusSession | null }
export type ReportDownload = { bytes: number; sha256: string; title: string }

export const reportsBackend = {
  report: (period: string) => invoke<LearningReport>('get_learning_report', { period }),
  calendar: () => invoke<CalendarReport>('get_learning_calendar'),
  focus: () => invoke<FocusOverview>('get_focus_overview'),
  startFocus: (taskId: number | null, plannedSeconds: number) => invoke<FocusSession>('start_focus_session', { taskId, plannedSeconds }),
  completeFocus: () => invoke<FocusSession>('complete_focus_session'),
  pauseFocus: () => invoke<FocusSession>('pause_focus_session'),
  resumeFocus: () => invoke<FocusSession>('resume_focus_session'),
  cancelFocus: () => invoke<FocusSession>('cancel_focus_session'),
  saveCheckin: (input: { date: string; minutes: number; note: string }) => invoke<CalendarReport>('save_study_checkin', { input }),
  adopt: (ids: string[], period: string) => invoke<AdoptionResult>('adopt_report_recommendations', { ids, period }),
  export: (destination: string, period: string) => invoke<ReportDownload>('export_learning_report', { destination, period }),
  save: () => invoke<string | null>('plugin:dialog|save', { options: { title: '导出真实学习报告', defaultPath: 'DeepTutor-learning-report.csv', filters: [{ name: 'CSV 学习报告', extensions: ['csv'] }] } }),
}
