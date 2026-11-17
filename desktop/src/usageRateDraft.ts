// P-087：费率输入框回填已保存费率。
//
// 用量面板的「输入 / 输出 每百万 tokens」输入框原先写死初值 1/2，切换供应商
// 档案或模型时也不把该组合已保存的费率读回输入框；点「保存真实费率」走
// upsert 语义，于是初始默认值会静默覆盖已配置费率——之后的成本、每日预算
// 使用率与预算防线全部按错误费率计算。
//
// 这里把「当前组合该显示什么费率」抽成纯函数，供面板取值与回归测试共用：
// - 有正在编辑的草稿且草稿属于当前组合 → 显示草稿；
// - 否则 → 显示该组合已保存的费率；
// - 该组合没有保存记录 → 显示空值。
//
// 该组合没有保存记录时不再给 1/2 这类兜底初值：兜底初值会参与 upsert，把
// 「新建一条费率记录」变成「静默写入 1/2」，与 P-087 的原始诉求同源。
// 留空时保存按钮禁用，用户必须自行填写真实费率后才会落盘。
//
// 组合标识由档案与模型共同决定，任一变化即视为换了一组，草稿不跨组合串用。

/** 已保存费率记录（用量总览 `rates` 的最小结构） */
export type SavedRate = {
  providerId: string
  model: string
  inputPerMillion: number
  outputPerMillion: number
}

/** 费率输入框的文本值 */
export type RateValues = { input: string; output: string }

/** 正在编辑的费率草稿（key 为所属组合标识） */
export type RateDraft = { key: string; input: string; output: string }

/** 当前选中的供应商档案与模型 */
export type RateSelection = { providerId: string; model: string }

/** 没有保存记录时的取值：留空，不给默认值（默认值会参与 upsert 建出错误费率记录） */
export const EMPTY_RATE_VALUES: RateValues = { input: '', output: '' }

/** 组合标识：档案与模型都相同才算同一组 */
export function rateKey(selection: RateSelection): string {
  return `${selection.providerId}\u0000${selection.model}`
}

/** 费率数值转输入框文本——后端存 REAL，非有限值按 0 处理 */
export function formatRate(value: number): string {
  return Number.isFinite(value) ? String(value) : '0'
}

/** 取某组合已保存的费率；没有保存记录返回 null */
export function savedRateValues(rates: SavedRate[], selection: RateSelection): RateValues | null {
  const found = rates.find(rate => rate.providerId === selection.providerId && rate.model === selection.model)
  if (!found) return null
  return { input: formatRate(found.inputPerMillion), output: formatRate(found.outputPerMillion) }
}

/** 当前组合是否已有保存记录——用于区分「回填已保存费率」与「尚无记录」 */
export function hasSavedRate(rates: SavedRate[], selection: RateSelection): boolean {
  return savedRateValues(rates, selection) !== null
}

/** 当前组合应显示的费率文本：正在编辑的草稿优先，其次已保存费率，最后留空 */
export function rateDisplayValues(draft: RateDraft | null, rates: SavedRate[], selection: RateSelection): RateValues {
  const key = rateKey(selection)
  if (draft && draft.key === key) return { input: draft.input, output: draft.output }
  return savedRateValues(rates, selection) ?? EMPTY_RATE_VALUES
}

/** 编辑某个字段：以当前显示值为底，产出属于该组合的新草稿 */
export function editRateDraft(
  draft: RateDraft | null,
  rates: SavedRate[],
  selection: RateSelection,
  field: 'input' | 'output',
  value: string,
): RateDraft {
  const key = rateKey(selection)
  const base = draft && draft.key === key ? draft : { key, ...rateDisplayValues(null, rates, selection) }
  return { ...base, [field]: value }
}

/**
 * 草稿是否已与该组合的已保存费率一致——一致即可作废草稿，让显示值回到
 * 「已保存费率」这条唯一事实来源（保存落盘后、或手工改回原值时）
 */
export function rateDraftCommitted(draft: RateDraft | null, rates: SavedRate[], selection: RateSelection): boolean {
  if (!draft || draft.key !== rateKey(selection)) return false
  const saved = savedRateValues(rates, selection)
  return !!saved && saved.input === draft.input && saved.output === draft.output
}
