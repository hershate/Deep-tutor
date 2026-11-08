import { invoke } from '@tauri-apps/api/core'
import type { BackendError } from './client'

export type PracticeSet = {
  id: number
  mode: 'targeted' | 'exam'
  status: 'generating' | 'cancelling' | 'ready' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  planId: string
  stageId: string | null
  nodeId: string | null
  providerId: string
  difficulty: string
  feedbackMode: 'immediate' | 'deferred' | 'hints'
  kinds: string[]
  variantOf: number | null
  examTimeLimitMinutes: number | null
  examStartedAt: string | null
  examDeadline: string | null
  passScorePercent: number | null
  totalQuestions: number
  answeredQuestions: number
  /** hints 模式由后端遮蔽（03#2）：不公布总分与正确率 */
  score: number | null
  maxScore: number | null
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
    unanswered?: boolean
    overallFeedback?: string
    criteria?: { id: string; description: string; score: number; maxScore: number; evidence: string; feedback: string }[]
    citations?: { materialId: number; quote: string }[]
  }
}
export type PracticeDetail = {
  set: PracticeSet
  questions: PracticeQuestion[]
  answers: PracticeAnswer[]
  answeredQuestionIds: number[]
  examExpired: boolean
  examRemainingSeconds: number | null
}
export type PracticeInput = {
  planId: string
  stageId: string | null
  nodeId?: string | null
  count: number
  difficulty: 'adaptive' | 'basic' | 'advanced' | 'comprehensive'
  feedbackMode: 'immediate' | 'deferred' | 'hints'
  kinds: QuestionKind[]
  variantOf?: number
  mode?: 'targeted' | 'exam'
  timeLimitMinutes?: number
  passScorePercent?: number
}
export type PracticeMistake = {
  questionId: number
  setId: number
  planId: string
  stageId: string | null
  kind: QuestionKind
  prompt: string
  nodeIds: string[]
  reason: string
  wrongCount: number
  lastWrongAt: string
  mastered: boolean
  masteredQuestionId: number | null
}

export type PracticeTarget = { planId: string; stageId: string; nodeId: string; nonce: number }

export const practiceBackend = {
  generate: (input: PracticeInput) => invoke<PracticeDetail>('generate_practice', { input }),
  detail: (id: number) => invoke<PracticeDetail>('get_practice_set', { id }),
  history: () => invoke<PracticeSet[]>('list_practice_history'),
  cancel: (id: number) => invoke<PracticeDetail>('cancel_practice_generation', { id }),
  resume: (id: number) => invoke<PracticeDetail>('resume_practice_generation', { id }),
  submit: (setId: number, questionId: number, answer: number | string) => invoke<{ answer: PracticeAnswer; set: PracticeSet }>('submit_practice_answer', { setId, questionId, answer }),
  mistakes: () => invoke<PracticeMistake[]>('list_practice_mistakes'),
  // 37#5：错题 JSON 经原生保存对话框落盘（替代 WebView Blob 下载）；
  // 旧 export_practice_mistakes 命令仅供 IPC 级测试断言，前端不再封装（45#1，
  // 页面组件内的同名本地函数走 exportMistakesFile，与本封装无关）
  exportMistakesFile: (destination: string) => invoke<{ bytes: number; sha256: string }>('export_practice_mistakes_file', { destination }),
  bookmark: (questionId: number, bookmarked: boolean) => invoke<void>('set_practice_bookmark', { questionId, bookmarked }),
  masterMistake: (questionId: number, mastered: boolean) => invoke<void>('set_practice_mistake_mastered', { questionId, mastered }),
  cancelAnswer: (answerId: number) => invoke<PracticeDetail>('cancel_practice_answer', { answerId }),
  resumeAnswer: (answerId: number) => invoke<PracticeDetail>('resume_practice_answer', { answerId }),
  submitExam: (id: number) => invoke<PracticeDetail>('submit_exam', { id }),
}
