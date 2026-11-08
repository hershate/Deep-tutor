import { backend, backendErrorMessage } from './client'
import type { MaterialImportResult } from './client'

export type MaterialImportSummary = {
  outcomes: MaterialImportResult[]
  /** ZIP 资料包内不支持条目的累计跳过数 */
  skipped: number
}

/** 按后端口径批量导入资料：普通文件每 32 个一批（后端单次上限，超过整批
 * 被拒），ZIP 逐包经 Rust 预检导入。文件选择器与窗口拖放（37#3）共用此
 * 通道；调用方负责 toast 与后续选择逻辑。 */
export async function importMaterialPaths(paths: string[]): Promise<MaterialImportSummary> {
  const regular = paths.filter(path => !path.toLowerCase().endsWith('.zip'))
  const outcomes: MaterialImportResult[] = []
  for (let index = 0; index < regular.length; index += 32) {
    outcomes.push(...await backend.importMaterialFiles(regular.slice(index, index + 32)))
  }
  let skipped = 0
  for (const path of paths.filter(path => path.toLowerCase().endsWith('.zip'))) {
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
