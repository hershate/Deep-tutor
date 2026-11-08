import { invoke } from '@tauri-apps/api/core'

export type UsageSettings = { sessionUsd: number; dailyUsd: number; regenerationLimit: number }
export type UsageRate = { id: number; providerId: string; providerName: string; model: string; inputPerMillion: number; outputPerMillion: number; updatedAt: string }
export type UsageEntry = { requestId: string; providerId: string; providerName: string; model: string; task: string; inputTokens: number | null; outputTokens: number | null; cost: number | null; status: string; attempts: number; createdAt: string }
export type UsageSummary = { todayCost: number; inputTokens: number; outputTokens: number; cancelledCost: number; failedCost: number; unpricedRequests: number; missingUsage: number }
export type UsageDay = { date: string; cost: number; inputTokens: number; outputTokens: number; requests: number; unpricedRequests: number }
export type ModelUsage = { providerId: string; providerName: string; model: string; tokens: number; cost: number; requests: number; unpricedRequests: number; share: number }
export type UsageOverview = { settings: UsageSettings; summary: UsageSummary; days: UsageDay[]; models: ModelUsage[]; ledger: UsageEntry[]; rates: UsageRate[] }
export type RateInput = { providerId: string; model: string; inputPerMillion: number; outputPerMillion: number }

export const usageBackend = {
  overview: (days = 7) => invoke<UsageOverview>('get_usage_overview', { days }),
  saveSettings: (settings: UsageSettings) => invoke<UsageSettings>('save_usage_settings', { settings }),
  upsertRate: (input: RateInput) => invoke<UsageRate>('upsert_usage_rate', { input }),
  exportCsv: () => invoke<string>('export_usage_csv'),
  // 37#5：用量 CSV 经原生保存对话框落盘（替代 WebView Blob 下载）
  exportCsvFile: (destination: string) => invoke<{ bytes: number; sha256: string }>('export_usage_csv_file', { destination }),
}
