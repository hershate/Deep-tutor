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

export type ChatMessage = {
  id: number
  role: 'user' | 'assistant'
  text: string
  source?: string
}
