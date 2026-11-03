import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { Note, Project, Resource, Task } from '../data'
import type { Preferences } from '../pages/Settings'

export type CollectionNamespace = 'tasks' | 'notes' | 'resources' | 'projects' | 'preferences'
export type AppSnapshot = {
  tasks: Task[]
  notes: Note[]
  resources: Resource[]
  projects: Project[]
  preferences: Preferences
  versions: Record<CollectionNamespace | 'drafts', number>
  lastEventId: number
}
export type BackendEvent = {
  id: number
  kind: string
  payload: Record<string, unknown>
  createdAt: string
}
export type BackendError = { code: string; message: string }
export type Material = {
  id: number
  title: string
  kind: string
  size: number
  sha256: string
  status: string
  progress: number
  textLength: number
  parseMode: string
  warning: string | null
  error: BackendError | null
  originalAvailable: boolean
}
export type MaterialDetail = Material & { text: string | null }
export type MaterialImportResult = {
  fileName: string
  material: Material | null
  deduplicated: boolean
  error: BackendError | null
}

export type LearningNode = {
  id: string
  title: string
  summary: string
  difficulty: number
  required: boolean
  estimatedMinutes: number
  citations: { materialId: number; quote: string }[]
}
export type LearningGraph = { nodes: LearningNode[]; edges: { from: string; to: string }[] }
export type LearningStage = { id: string; title: string; nodeIds: string[]; difficulty: number }
export type ScheduledStage = LearningStage & { minutes: number; startDate: string; endDate: string }
export type LearningSchedule = {
  startDate: string
  endDate: string
  totalMinutes: number
  days: number
  withinDeadline: boolean
  milestoneDate: string | null
  withinMilestone: boolean | null
  entries: { date: string; stageId: string; nodeIds: string[]; minutes: number }[]
}
export type LearningCheckName = 'dependencyClosure' | 'difficultyStep' | 'cognitiveLoad' | 'milestoneFeasibility' | 'gapAnalysis'
export type LearningValidation = Record<LearningCheckName, { status: 'pass' | 'warn' | 'fail'; details: string }> & { passed: boolean; fixes: string[] }
export type LearningResult = {
  goal: string
  level: string
  deadline: string
  milestone: string | null
  milestoneDate: string | null
  graph: LearningGraph
  stages: ScheduledStage[]
  schedule: LearningSchedule
  resources: {
    items: { materialId: number; title: string; sha256: string; kind: string; verified: boolean }[]
    externalSearch: { status: string; reason: string; queries: string[] }
  }
  validation: LearningValidation
  warnings: string[]
}
export type LearningStep = {
  index: number
  name: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  attempt: number
  requestId: string | null
  output: unknown
  error: BackendError | null
  usage: { inputTokens: number | null; outputTokens: number | null } | null
  upstreamAttempts: number | null
  latencyMs: number | null
}
export type LearningPlan = {
  id: string
  status: 'running' | 'cancelling' | 'done' | 'degraded' | 'failed' | 'cancelled' | 'interrupted'
  providerId: string
  goal: string
  level: string
  dailyMinutes: number
  deadline: string
  materialIds: number[]
  currentStep: number
  progress: number
  version: number
  adoptedProjectId: number | null
  steps: LearningStep[]
  artifacts: {
    step0?: { snapshot: { id: number; title: string; sha256: string; warning: string | null }[] }
    step2?: { graph: LearningGraph }
    step3?: { stages: LearningStage[] }
    step5?: { schedule: LearningSchedule; stages: ScheduledStage[] }
  }
  result: LearningResult | null
  error: BackendError | null
  createdAt: string
  updatedAt: string
}
export type LearningPlanInput = {
  goal: string
  level: string
  dailyMinutes: number
  deadline: string
  materialIds: number[]
  providerId?: string
  milestone: string | null
  milestoneDate: string | null
}

export type KnowledgeOverview = {
  nodes: { nodeId: string; mastery: number | null; evidenceCount: number; readingCompleted: boolean; evidence: string }[]
  overallMastery: number | null
  evaluatedNodes: number
  missingEvidence: number
}
export type ReadingProgress = { completed: boolean; bookmarked: boolean }

export type LectureContent = {
  title: string
  overview: string
  sections: { heading: string; content: string }[]
  examples: { title: string; content: string }[]
  checkpoints: { question: string; answer: string }[]
  citations: { materialId: number; quote: string }[]
}
export type LectureUnit = {
  index: number
  nodeId: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  attempt: number
  requestId: string | null
  content: LectureContent | null
  error: BackendError | null
  usage: { inputTokens: number | null; outputTokens: number | null } | null
}
export type Lecture = {
  planId: string
  stageId: string
  version: number
  status: 'empty' | 'running' | 'cancelling' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  totalUnits: number
  completedUnits: number
  currentUnit: number
  previousVersion: number | null
  units: LectureUnit[]
  error: BackendError | null
  updatedAt: string | null
}

export type ProviderProtocol = 'OpenAI Chat' | 'OpenAI Responses' | 'Anthropic Messages' | 'Gemini Native'
export type ProviderKind = '官方' | '兼容服务' | '本地模型'
export type ProviderProfile = {
  id: string
  name: string
  preset: string
  kind: ProviderKind
  protocol: ProviderProtocol
  baseUrl: string
  models: string[]
  defaultModel: string
  hasKey: boolean
  health: 'unknown' | 'healthy' | 'error'
  latencyMs: number | null
  enabled: boolean
  note: string | null
}
export type ProviderProfileInput = {
  id?: string
  name: string
  preset: string
  kind: ProviderKind
  protocol: ProviderProtocol
  baseUrl: string
  defaultModel: string
  models: string[]
  enabled?: boolean
  note: string | null
  apiKey?: string
}
export type ProviderTestResult = {
  profileId: string
  kind: 'models' | 'chat'
  ok: boolean
  latencyMs: number
  models: string[] | null
  result: { text: string; reasoning: string; model: string; latencyMs: number; attempts: number } | null
}
export type ProviderChatMessage = { role: 'system' | 'user' | 'assistant'; content: string }
export type ProviderRequest = {
  requestId: string
  providerId: string
  model: string
  status: 'running' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  output: string
  reasoning: string
  usage: { inputTokens: number | null; outputTokens: number | null; cachedInputTokens: number | null } | null
  finishReason: string | null
  error: BackendError | null
  attempts: number
  latencyMs: number | null
  createdAt: string
  updatedAt: string
}
export type TaskRouteId = 'agent' | 'plan' | 'lecture' | 'practice' | 'wiki' | 'resources' | 'embedding'
export type RouteAssignment = { providerId: string | null; model: string | null }
export type CircuitSettings = { failures: number; cooldown: number; timeout: number; retries: number }
export type RoutingSettings = {
  mode: 'direct' | 'routing' | 'aggregate'
  routes: Partial<Record<TaskRouteId, RouteAssignment>>
  aggregateProviderIds: string[]
  aggregateDefault: string | null
  failover: string[]
  circuit: CircuitSettings
}
export type CircuitSnapshot = { providerId: string; state: 'closed' | 'open' | 'half_open'; failures: number; openedAt: string | null; lastError: BackendError | null }
export type ProviderRouteAttempt = {
  attempt: number
  providerId: string
  model: string
  upstreamRequestId: string | null
  status: string
  latencyMs: number | null
  error: BackendError | null
}
export type ProviderRouteLog = {
  requestId: string
  task: TaskRouteId
  mode: 'direct' | 'routing' | 'aggregate'
  status: string
  providerId: string | null
  model: string | null
  error: BackendError | null
  attempts: ProviderRouteAttempt[]
  createdAt: string
  updatedAt: string
}
export type ProviderExport = { version: 1; activeProvider: string | null; routing: RoutingSettings; providers: ProviderProfile[]; notice: string }

export type AgentToolCall = { id: string; name: string; arguments: string }
export type AgentMessage = {
  id: number
  role: 'user' | 'assistant' | 'tool'
  content: string
  reasoning: string
  toolCalls: AgentToolCall[]
  toolCallId: string | null
  name: string | null
  stepIndex: number
  partial: boolean
  createdAt: string
}
export type AgentRun = {
  id: number
  status: 'running' | 'cancelling' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  steps: number
  inputTokens: number
  outputTokens: number
  currentRequestId: string | null
  error: BackendError | null
  createdAt: string
  updatedAt: string
}
export type AgentArtifact = {
  id: number
  path: string
  planId: string
  stageId: string
  unitIndex: number
  status: string
  content: LectureContent
  createdAt: string
  expiresAt: string
}
export type AgentApproval = {
  id: number
  kind: 'memory.save' | 'memory.delete' | 'artifact.promote'
  payload: Record<string, unknown>
  status: string
  createdAt: string
}
export type AgentSession = {
  id: number
  title: string
  planId: string
  providerId: string
  budgetTokens: number
  outputTokensUsed: number
  status: string
  messages: AgentMessage[]
  runs: AgentRun[]
  artifacts: AgentArtifact[]
  approvals: AgentApproval[]
  memory: [string, string][]
  updatedAt: string
}

export const backend = {
  bootstrap: () => invoke<AppSnapshot>('bootstrap'),
  getAppState: () => invoke<AppSnapshot>('get_app_state'),
  getCollection: <T>(namespace: CollectionNamespace) => invoke<{ value: T; revision: number }>('get_collection', { namespace }),
  replaceCollection: <T>(namespace: CollectionNamespace, expectedRevision: number, value: T) => invoke<AppSnapshot>('replace_collection', { namespace, expectedRevision, value }),
  saveDraft: (id: string, value: unknown) => invoke<AppSnapshot>('save_draft', { id, value }),
  getDraft: <T>(id: string) => invoke<{ value: T | null; revision: number }>('get_draft', { id }),
  deleteDraft: (id: string) => invoke<AppSnapshot>('delete_draft', { id }),
  resetApplication: () => invoke<AppSnapshot>('reset_application'),
  eventsSince: (lastId: number) => invoke<{ events: BackendEvent[]; latestId: number; hasMore: boolean }>('events_since', { lastId }),
  openMaterialFiles: () => invoke<string[] | null>('plugin:dialog|open', {
    options: { title: '导入学习资料', multiple: true, filters: [{ name: '学习资料', extensions: ['pdf', 'docx', 'pptx', 'epub', 'txt', 'md'] }] },
  }),
  saveMaterialFile: (title: string, extension: string) => invoke<string | null>('plugin:dialog|save', {
    options: { title: '保存到本地', defaultPath: title, filters: [{ name: '文件', extensions: [extension] }] },
  }),
  importMaterialFiles: (paths: string[]) => invoke<MaterialImportResult[]>('import_material_files', { paths }),
  importMaterialText: (title: string, content: string) => invoke<{ material: Material; deduplicated: boolean }>('import_material_text', { title, content }),
  listMaterials: () => invoke<Material[]>('list_materials'),
  getMaterial: (id: number) => invoke<MaterialDetail>('get_material', { id }),
  enqueueMaterialParse: (id: number) => invoke<{ jobId: number; material: Material }>('enqueue_material_parse', { id }),
  cancelMaterialParse: (id: number) => invoke<Material>('cancel_material_parse', { id }),
  deleteMaterial: (id: number) => invoke<AppSnapshot>('delete_material', { id }),
  exportMaterialOriginal: (id: number, destination: string) => invoke<{ bytes: number; sha256: string }>('export_material_original', { id, destination }),
  exportMaterialText: (id: number, destination: string) => invoke<{ bytes: number; sha256: string }>('export_material_text', { id, destination }),
  listProviderProfiles: () => invoke<ProviderProfile[]>('list_provider_profiles'),
  getActiveProvider: () => invoke<string | null>('get_active_provider'),
  upsertProviderProfile: (input: ProviderProfileInput) => invoke<ProviderProfile>('upsert_provider_profile', { input }),
  setActiveProvider: (id: string) => invoke<void>('set_active_provider', { id }),
  deleteProviderProfile: (id: string) => invoke<void>('delete_provider_profile', { id }),
  exportProviderConfig: () => invoke<ProviderExport>('export_provider_config'),
  importProviderDeepLink: (link: string) => invoke<ProviderProfile>('import_provider_deep_link', { link }),
  testProvider: (id: string, realRequest: boolean) => invoke<ProviderTestResult>('test_provider', { id, realRequest }),
  streamProviderChat: (requestId: string, profileId: string, messages: ProviderChatMessage[], maxTokens: number) => invoke<ProviderRequest>('stream_provider_chat', { requestId, profileId, messages, maxTokens }),
  cancelProviderChat: (requestId: string) => invoke<void>('cancel_provider_chat', { requestId }),
  getProviderChat: (requestId: string) => invoke<ProviderRequest>('get_provider_chat', { requestId }),
  startLearningPlan: (input: LearningPlanInput) => invoke<LearningPlan>('start_learning_plan', { input }),
  listLearningPlans: () => invoke<LearningPlan[]>('list_learning_plans'),
  getLearningPlan: (id: string) => invoke<LearningPlan>('get_learning_plan', { id }),
  cancelLearningPlan: (id: string) => invoke<LearningPlan>('cancel_learning_plan', { id }),
  resumeLearningPlan: (id: string) => invoke<LearningPlan>('resume_learning_plan', { id }),
  adoptLearningPlan: (id: string) => invoke<AppSnapshot>('adopt_learning_plan', { id }),
  regenerateLearningPlan: (id: string, scope: number) => invoke<LearningPlan>('regenerate_learning_plan', { id, scope }),
  getKnowledgeOverview: (id: string) => invoke<KnowledgeOverview>('get_knowledge_overview', { id }),
  getKnowledgePath: (id: string, from: string, to: string) => invoke<string[]>('get_knowledge_path', { id, from, to }),
  addKnowledgeTask: (id: string, nodeId: string) => invoke<AppSnapshot>('add_knowledge_task', { id, nodeId }),
  getReadingProgress: (id: string, stageId: string) => invoke<ReadingProgress>('get_reading_progress', { id, stageId }),
  setReadingProgress: (id: string, stageId: string, completed: boolean, bookmarked?: boolean) => invoke<ReadingProgress>('set_reading_progress', { id, stageId, completed, bookmarked }),
  getLecture: (id: string, stageId: string, version?: number) => invoke<Lecture>('get_lecture', { id, stageId, version }),
  generateLecture: (id: string, stageId: string, force: boolean) => invoke<Lecture>('generate_lecture', { id, stageId, force }),
  cancelLecture: (id: string, stageId: string) => invoke<Lecture>('cancel_lecture', { id, stageId }),
  saveStudyNote: (id: string, stageId: string, content: string) => invoke<AppSnapshot>('save_study_note', { id, stageId, content }),
  createAgentSession: (input: { title?: string; planId?: string; budgetTokens?: number }) => invoke<AgentSession>('create_agent_session', input),
  listAgentSessions: () => invoke<AgentSession[]>('list_agent_sessions'),
  getAgentSession: (id: string) => invoke<AgentSession>('get_agent_session', { id }),
  sendAgentMessage: (sessionId: string, content: string) => invoke<AgentRun>('send_agent_message', { sessionId, content }),
  getAgentRun: (id: string) => invoke<AgentRun>('get_agent_run', { id }),
  cancelAgentRun: (id: string) => invoke<AgentRun>('cancel_agent_run', { id }),
  resumeAgentRun: (id: string) => invoke<AgentRun>('resume_agent_run', { id }),
  deleteAgentSession: (id: string) => invoke<void>('delete_agent_session', { id }),
  approveAgentRequest: (id: string) => invoke<void>('approve_agent_request', { id }),
  cancelAgentApproval: (id: string) => invoke<void>('cancel_agent_approval', { id }),
  getRoutingSettings: () => invoke<RoutingSettings>('get_routing_settings'),
  saveRoutingSettings: (settings: RoutingSettings) => invoke<RoutingSettings>('save_routing_settings', { settings }),
  listProviderRouteLogs: () => invoke<ProviderRouteLog[]>('list_provider_route_logs'),
  listProviderCircuits: () => invoke<CircuitSnapshot[]>('list_provider_circuits'),
  routeProviderChat: (requestId: string, task: TaskRouteId, profileId: string | null, model: string | null, messages: ProviderChatMessage[], maxTokens: number) => invoke<ProviderRequest>('route_provider_chat', { requestId, task, profileId, model, messages, maxTokens }),
}

export function onBackendEvent(handler: (event: BackendEvent) => void) {
  return listen<BackendEvent>('backend://event', message => handler(message.payload))
}

export function backendErrorMessage(error: unknown) {
  if (typeof error === 'object' && error !== null && 'code' in error) {
    const typed = error as Partial<BackendError>
    return `${typed.code || 'backend_error'}：${typed.message || '操作未完成'}`
  }
  return error instanceof Error ? error.message : String(error)
}

export function reportBackendError(error: unknown) {
  const message = backendErrorMessage(error)
  console.error(error)
  window.dispatchEvent(new CustomEvent('backend-error', { detail: message }))
}
