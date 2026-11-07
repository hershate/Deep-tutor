// 本地新建记录的 id：以 Date.now() 为基数，但保证会话内严格递增，
// 同毫秒连续创建不再产生重复 id/React key（09#2）。后端集合写入不做 id
// 去重，前端必须在生成侧保证唯一。
let last = 0

export function localId(): number {
  last = Math.max(Date.now(), last + 1)
  return last
}
