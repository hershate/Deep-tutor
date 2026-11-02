import { invoke } from '@tauri-apps/api/core'
import type { BackendError } from './client'

export type PracticeSet = {
  id: number
  mode: 'targeted' | 'exam'
  status: 'generating' | 'cancelling' | 'ready' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  planId: string
  stageId: string | null
  providerId: string
  difficulty: string
  feedbackMode: 'immediate' | 'deferred' | 'hints'
  kinds: string[]
  totalQuestions: number
  answeredQuestions: number
  score: number
  maxScore: number
  attempt: number
  requestId: string | null
  error: BackendError | null
  createdAt: string
  updatedAt: string
  completedAt: string | null
}
export type QuestionKind = 'choice' | 'fill' | 'short' | 'essay'
export type RubricCriterion = { id: string; description: string; points: number }
export type PracticeQuestion = {
  id: number
  position: number
  kind: QuestionKind
  prompt: string
  options: string[]
  hint: string
  difficulty: number
  nodeIds: string[]
  citations: { materialId: number; quote: string }[]
  rubric: RubricCriterion[]
  variantOf: number | null
  bookmarked: boolean
}
export type PracticeAnswer = {
  id: number
  questionId: number
  score: number | null
  maxScore: number | null
  correct: boolean | null
  status: 'done' | 'grading' | 'cancelling' | 'failed' | 'cancelled' | 'interrupted'
  error: BackendError | null
  requestId: string | null
  response: number | string | null
  feedback: {
    answer?: number | string
    explanation?: string
    hint?: string
    status?: string
    overallFeedback?: string
    criteria?: { id: string; description: string; score: number; maxScore: number; evidence: string; feedback: string }[]
    citations?: { materialId: number; quote: string }[]
  }
}
export type PracticeDetail = { set: PracticeSet; questions: PracticeQuestion[]; answers: PracticeAnswer[]; answeredQuestionIds: number[] }
export type PracticeInput = {
  planId: string
  stageId: string | null
  count: number
  difficulty: 'adaptive' | 'basic' | 'advanced' | 'comprehensive'
  feedbackMode: 'immediate' | 'deferred' | 'hints'
  kinds: QuestionKind[]
}
export type PracticeMistake = {
  questionId: number
  prompt: string
  nodeIds: string[]
  reason: string
  wrongCount: number
  lastWrongAt: string
  mastered: boolean
}

export const practiceBackend = {
  generate: (input: PracticeInput) => invoke<PracticeDetail>('generate_practice', { input }),
  detail: (id: number) => invoke<PracticeDetail>('get_practice_set', { id }),
  history: () => invoke<PracticeSet[]>('list_practice_history'),
  cancel: (id: number) => invoke<PracticeDetail>('cancel_practice_generation', { id }),
  resume: (id: number) => invoke<PracticeDetail>('resume_practice_generation', { id }),
  submit: (setId: number, questionId: number, answer: number | string) => invoke<{ answer: PracticeAnswer; set: PracticeSet }>('submit_practice_answer', { setId, questionId, answer }),
  mistakes: () => invoke<PracticeMistake[]>('list_practice_mistakes'),
  exportMistakes: () => invoke<Record<string, unknown>>('export_practice_mistakes'),
  bookmark: (questionId: number, bookmarked: boolean) => invoke<void>('set_practice_bookmark', { questionId, bookmarked }),
  masterMistake: (questionId: number, mastered: boolean) => invoke<void>('set_practice_mistake_mastered', { questionId, mastered }),
  cancelAnswer: (answerId: number) => invoke<PracticeDetail>('cancel_practice_answer', { answerId }),
  resumeAnswer: (answerId: number) => invoke<PracticeDetail>('resume_practice_answer', { answerId }),
}
