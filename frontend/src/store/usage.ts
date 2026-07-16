// Zustand 轻量 UI 状态（plan/01 前端栈）。示例：实时用量/成本展示（plan/01 §三 成本计量）。
import { create } from 'zustand'

export interface UsageState {
  tokensIn: number
  tokensOut: number
  cacheRead: number
  cost: number
  setUsage: (u: Partial<Pick<UsageState, 'tokensIn' | 'tokensOut' | 'cacheRead' | 'cost'>>) => void
  reset: () => void
}

export const useUsageStore = create<UsageState>((set) => ({
  tokensIn: 0,
  tokensOut: 0,
  cacheRead: 0,
  cost: 0,
  setUsage: (u) => set(u),
  reset: () => set({ tokensIn: 0, tokensOut: 0, cacheRead: 0, cost: 0 }),
}))
