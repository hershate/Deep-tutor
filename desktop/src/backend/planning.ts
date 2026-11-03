import { invoke } from '@tauri-apps/api/core'
import type { LearningPlan } from './client'

export type ScheduleEvent = {
  taskId: number | null
  title: string
  kind: string
  minutes: number
  done: boolean
  date: string | null
  stageId: string | null
}
export type ScheduleDay = { date: string; events: ScheduleEvent[] }
export type StageStatus = {
  id: string
  title: string
  minutes: number
  difficulty: number
  completed: boolean
  nodeIds: string[]
}
export type PlanOverview = {
  plan: LearningPlan | null
  weekStart: string
  days: ScheduleDay[]
  stages: StageStatus[]
  weeklyMinutes: number
  weeklyHours: number
  loadPercent: number
  intensity: '稳妥' | '标准' | '冲刺'
  unscheduled: ScheduleEvent[]
}
export type PlanAlternative = { intensity: '稳妥' | '标准' | '冲刺'; weeklyMinutes: number; loadPercent: number }
export type GoalInput = { planId?: string; goal: string; deadline: string; weeklyHours: number; level?: string }

export const planningBackend = {
  overview: (weekStart?: string) => invoke<PlanOverview>('get_plan_overview', { weekStart }),
  alternatives: (weekStart?: string) => invoke<PlanAlternative[]>('get_plan_alternatives', { weekStart }),
  applyIntensity: (intensity: string, weekStart?: string) => invoke<PlanOverview>('apply_plan_intensity', { intensity, weekStart }),
  updateGoal: (input: GoalInput) => invoke<LearningPlan>('update_learning_goal', { input }),
  assignTaskDate: (taskId: number, dateValue: string) => invoke<PlanOverview>('assign_task_date', { taskId, dateValue }),
}
