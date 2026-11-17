// 任务/集合状态的统一中文名：历史与列表不再与各页映射表混排英文原词
const taskStatusNames: Record<string, string> = {
  pending: '等待中',
  queued: '排队中',
  staged: '已暂存',
  parsing: '解析中',
  parsed: '已解析',
  generating: '生成中',
  grading: '评分中',
  running: '进行中',
  cancelling: '正在取消',
  done: '已完成',
  completed: '已完成',
  degraded: '降级交付', // 148#7：与 PlanGenerator 的 planStatusNames 统一（原先此处为「部分完成」，同状态两页两个名字）
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
  empty: '未生成',
  ready: '就绪',
}

export function statusName(status: string) {
  return taskStatusNames[status] ?? status
}

// P-107：资料解析状态的徽标色调——原先 Library 页与方案生成器各写一份，
// 后者只区分 parsed/staged，其余（failed/degraded/queued/parsing/cancelling）
// 全部落 neutral，与 Library 的红色失败口径不一致。此处收敛为单一来源。
export type StatusTone = 'neutral' | 'green' | 'amber' | 'red'

export function materialTone(status: string): StatusTone {
  if (status === 'parsed' || status === 'degraded') return 'green'
  if (status === 'failed') return 'red'
  if (['queued', 'parsing', 'cancelling'].includes(status)) return 'amber'
  return 'neutral'
}

// P-113：资料解析状态的显示文案——progress=100 不等价于「已解析」：
// 取消交叠终态（cancelled，保留部分解析结果）同样写 progress=100，
// 按进度判定会把「已取消」显示成「已解析」，与资料库列表的 meta 行
// （status_label）口径矛盾。status 缺失时按旧口径退回进度判定。
// degraded 的文案与后端 status_label（materials/mod.rs）对齐为「部分解析」——
// 原先此处写「已解析」，同一份资料在侧栏与资料库两处不同名。
export function materialStateLabel(status: string | undefined, progress: number) {
  const state = status ?? (progress === 100 ? 'parsed' : '')
  if (state === 'parsed') return '已解析'
  if (state === 'degraded') return '部分解析'
  if (state === 'cancelled') return '已取消'
  if (state === 'failed') return '解析失败'
  if (state === 'staged') return '已暂存'
  if (state === 'metadata_only') return '仅元数据'
  return `解析进度 ${progress}%`
}

// P-113：是否完整解析（可作为默认引用范围）——取消保留的部分解析不计入
export function materialFullyParsed(status: string | undefined, progress: number) {
  if (status === undefined) return progress === 100
  return status === 'parsed' || status === 'degraded'
}
