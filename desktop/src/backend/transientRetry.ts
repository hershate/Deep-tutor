// P-089 返工：瞬态 database_busy 的重试策略此前只存在于 useBackendState
// 的闭包里，无法被测试固化——改回「重试上限」「重试间隔」或让重试重新
// 被 settled 短路时 CI 都不会发现。策略下沉为纯函数供前端测试直接断言，
// 闭包只负责执行（定时器、代际与存活判定）。

/** 瞬态失败的最大静默重试次数（24#1 承诺：连续 5 次仍失败才落错误页） */
export const MAX_TRANSIENT_RETRIES = 5

/** 瞬态重试的等待窗口（毫秒） */
export const TRANSIENT_RETRY_DELAY_MS = 1500

export type TransientDecision =
  | { kind: 'retry'; attempt: number; delayMs: number }
  | { kind: 'error-page' }

/**
 * 判定下一次瞬态失败的处理方式。
 * @param retries 已经重试过的次数
 */
export function decideTransientRetry(retries: number): TransientDecision {
  if (retries < MAX_TRANSIENT_RETRIES) {
    return { kind: 'retry', attempt: retries + 1, delayMs: TRANSIENT_RETRY_DELAY_MS }
  }
  return { kind: 'error-page' }
}
