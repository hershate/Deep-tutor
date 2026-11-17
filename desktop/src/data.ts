export type PageId = 'today' | 'generator' | 'plan' | 'study' | 'map' | 'agent' | 'practice' | 'review' | 'library' | 'projects' | 'reports' | 'community' | 'integrations' | 'providers' | 'settings' | 'shared'

export type Task = {
  id: number
  title: string
  subject: string
  duration: number
  done: boolean
  completedAt?: string
  kind: '学习' | '练习' | '复习' | '项目'
}

export type Note = {
  id: number
  title: string
  excerpt: string
  tag: string
  updated: string
  content: string
}

export type Resource = {
  id: number
  title: string
  type: 'PDF' | '视频' | '网页' | '音频' | '文件'
  meta: string
  progress: number
}

export type Milestone = { id: number; title: string; done: boolean }
export type Project = {
  id: number
  title: string
  description: string
  due: string
  progress: number
  tag: string
  planId?: string | null
  milestones: Milestone[]
}

// 150#7：方案生成页的输入草稿——「前往模型接入中心」会离开本页，
// 组件本地 state 随之丢失（目标/基础/时长/日期/里程碑与资料勾选全清）。
// 由 App 侧草稿槽持有，离页再回来时按此恢复
export type GeneratorDraft = {
  step: number
  selectedIds: number[]
  goal: string
  level: string
  minutes: number
  deadline: string
  milestone: string
  milestoneDate: string
}
