// 本地新建记录的 id：以 Date.now() 为基数，但保证会话内严格递增，
// 同毫秒连续创建不再产生重复 id/React key（09#2）。后端集合写入不做 id
// 去重，前端必须在生成侧保证唯一。
// 152#5：基数前移 1000ms——后端 next_free_id 的 floor 是「当前毫秒+1」
// （31#58），前端在 T 毫秒生成 T+1 而后端恰在 T 毫秒分配 T+1 仍会同值；
// 前移 1 秒后前端序列恒高于任何已发生的后端 floor 分配（后端分配时刻
// 不晚于前端生成时刻，floor ≤ T+1 < T+1000），且 next_free_id 的
// MAX(既有)+1 保证前端 id 落地后后端继续在其上分配，两个空间不再交叠。
let last = 0

export function localId(): number {
  last = Math.max(Date.now() + 1000, last + 1)
  return last
}
