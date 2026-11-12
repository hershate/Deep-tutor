import { backend, backendErrorMessage } from './client'
import type { MaterialImportResult } from './client'

export type MaterialImportSummary = {
  outcomes: MaterialImportResult[]
  /** ZIP 资料包内不支持条目的累计跳过数 */
  skipped: number
}

/** 按后端口径批量导入资料：普通文件每 32 个一批（后端单次上限，超过整批
 * 被拒），ZIP 逐包经 Rust 预检导入。文件选择器与窗口拖放（37#3）共用此
 * 通道；调用方负责 toast 与后续选择逻辑。
 * 133#1：批级 try/catch——第 N 批瞬时失败（连接异常等非预期错误）原先让
 * 整个 promise reject，前 N-1 批已入库的结果被丢弃且剩余批次与全部 ZIP 被
 * 跳过（ZIP 通道 42#3 早有逐包容错，同一函数两种通道口径不对称）；失败批
 * 降级为整批 error outcome 后继续。
 * 133#8：onProgress（已处理数/总数）——数百文件分钟级导入原先全程静默；
 * 调用方挂轻量进度指示（ProgressBar/按钮计数），completion 前逐批回调。 */
export async function importMaterialPaths(
  paths: string[],
  onProgress?: (processed: number, total: number) => void,
): Promise<MaterialImportSummary> {
  const regular = paths.filter(path => !path.toLowerCase().endsWith('.zip'))
  const archives = paths.filter(path => path.toLowerCase().endsWith('.zip'))
  const outcomes: MaterialImportResult[] = []
  let processed = 0
  for (let index = 0; index < regular.length; index += 32) {
    const batch = regular.slice(index, index + 32)
    try {
      outcomes.push(...await backend.importMaterialFiles(batch))
    } catch (error) {
      for (const path of batch) {
        const name = path.split(/[\\/]/).pop() ?? path
        outcomes.push({ fileName: name, material: null, deduplicated: false, error: { code: 'import_failed', message: backendErrorMessage(error) } })
      }
    }
    processed += batch.length
    onProgress?.(Math.min(processed, paths.length), paths.length)
  }
  let skipped = 0
  for (const path of archives) {
    // 42#3：单个 ZIP 失败不再让整批 reject——后续包继续导入，失败项以
    // 逐文件 outcome 汇总（原先第 2 个包抛错后第 3 个及之后不再导入）
    try {
      const archive = await backend.importMaterialArchive(path)
      outcomes.push(...archive.outcomes)
      skipped += archive.skipped.length
    } catch (error) {
      const name = path.split(/[\\/]/).pop() ?? path
      outcomes.push({ fileName: name, material: null, deduplicated: false, error: { code: 'import_failed', message: backendErrorMessage(error) } })
    }
    processed += 1
    onProgress?.(Math.min(processed, paths.length), paths.length)
  }
  return { outcomes, skipped }
}

/** 导入结果的统一摘要文案（暂存/去重/跳过/失败计数与首个失败原因）。 */
export function summarizeMaterialImport({ outcomes, skipped }: MaterialImportSummary): string {
  const staged = outcomes.filter(item => item.material && !item.deduplicated).length
  const duplicates = outcomes.filter(item => item.deduplicated).length
  const errors = outcomes.flatMap(item => item.error ? [item.error] : [])
  if (errors.length) return `${staged} 份已暂存，${duplicates} 份去重，${skipped} 个不支持条目已跳过，${errors.length} 份失败：${errors[0].message}`
  return `${staged} 份原件已暂存，${duplicates} 份已去重，${skipped} 个不支持条目已跳过`
}
