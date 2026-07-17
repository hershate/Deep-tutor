// 前端类型（对齐后端 plan/03 契约与 plan/01 plan schema）

export interface Session {
  session_id: number
  title: string
}

export interface MaterialOut {
  material_id: number
  kind: string
  meta: Record<string, unknown>
}

export interface PlanSummary {
  id: number
  session_id: number
  version: number
  created_at: string
}

export interface PlanDetail {
  id: number
  session_id: number
  version: number
  plan: Record<string, unknown>
}

export interface Usage {
  total_tokens: number
  total_cost: number
  cache_hit_rate: number
  calls: number
}

// SSE 事件（plan/10 §三）
export type SSEEventName =
  | 'tool_start'
  | 'tool_progress'
  | 'tool_end'
  | 'clarify_needed'
  | 'plan_chunk'
  | 'warn'
  | 'budget_exceeded'
  | 'done'
  | 'error'

export interface ToolEndMeta {
  tokens_in?: number
  tokens_out?: number
  cache_read?: number
  cost?: number
  duration_ms?: number
  model?: string
}
