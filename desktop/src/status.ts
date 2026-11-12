// 任务/集合状态的统一中文名：历史与列表不再与各页映射表混排英文原词
export const taskStatusNames: Record<string, string> = {
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
