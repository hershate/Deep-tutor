import { invoke } from '@tauri-apps/api/core'

export type ReviewCard = {
  id: number
  planId: string | null
  nodeId: string | null
  front: string
  back: string
  tag: string
  dueAt: string
  intervalDays: number
  ease: number
  repetitions: number
  lapses: number
  suspended: boolean
  createdAt: string
  updatedAt: string
}
export type ReviewCardInput = {
  front: string
  back: string
  tag: string | null
  planId: string | null
  nodeId: string | null
}
export type ReviewGrade = 'again' | 'hard' | 'good' | 'easy'
export type ReviewStats = {
  dueToday: number
  retention: number | null
  estimatedMinutes: number
  tomorrow: number
  calendar: { date: string; label: string; count: number }[]
}
export type ReviewHistoryEntry = {
  id: number
  grade: ReviewGrade
  previousInterval: number
  nextInterval: number
  ease: number
  reviewedAt: string
}

export const reviewBackend = {
  list: () => invoke<ReviewCard[]>('list_review_cards'),
  next: () => invoke<ReviewCard | null>('next_review_card'),
  stats: () => invoke<ReviewStats>('get_review_stats'),
  history: (cardId: number) => invoke<ReviewHistoryEntry[]>('list_review_history', { cardId }),
  create: (input: ReviewCardInput) => invoke<ReviewCard>('create_review_card', { input }),
  update: (id: number, input: ReviewCardInput) => invoke<ReviewCard>('update_review_card', { id, input }),
  remove: (id: number) => invoke<void>('delete_review_card', { id }),
  suspend: (id: number, suspended: boolean) => invoke<ReviewCard>('set_review_card_suspended', { id, suspended }),
  grade: (id: number, grade: ReviewGrade) => invoke<{ card: ReviewCard; historyId: number }>('grade_review_card', { id, grade }),
}
