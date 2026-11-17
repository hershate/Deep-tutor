import type { AppSnapshot } from './client'

// P-090 返工：快照守卫此前是 useBackendState 内的模块私有函数，且对
// null/undefined 等非对象入参会在读 `raw.workspaceId` 时抛 TypeError
//（"Cannot read properties of null"）——守卫的价值正在于形状漂移时给出
// 可定位错误，用户看到的却是运行时崩溃提示。
// 抽出为独立模块有两个目的：非对象入参显式判型后抛同款可读错误；守卫
// 可被前端测试直接加载断言（此前只有源码保证，改回即无人发现）。

/** 形状漂移时统一抛出的可读错误文案 */
export const SNAPSHOT_INCOMPATIBLE_MESSAGE = '导入返回的数据结构与当前应用版本不兼容，请更新应用后再试。'

/** 入参类型描述（仅用于错误日志定位） */
function describeShape(value: unknown): string {
  if (value === null) return 'null'
  if (Array.isArray(value)) return 'array'
  return typeof value
}

/**
 * 运行时守卫：后端返回的是自由 Value，TS 断言静默通过，键形漂移
 * （改名/缺失）时渲染层读出 undefined 空白。坏形状以可定位错误暴露。
 */
export function guardAppSnapshot(value: unknown, context: string): AppSnapshot {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    console.error(
      `guardAppSnapshot: ${context}返回值不是对象（${describeShape(value)}），后端 JSON 形状漂移？`,
      value,
    )
    throw new Error(SNAPSHOT_INCOMPATIBLE_MESSAGE)
  }
  const raw = value as Record<string, unknown>
  if (typeof raw.workspaceId !== 'number' || typeof raw.lastEventId !== 'number'
    || raw.tasks === undefined || raw.notes === undefined || raw.resources === undefined
    || raw.projects === undefined || raw.preferences === undefined || raw.versions === undefined) {
    console.error(`guardAppSnapshot: ${context}快照形状断言失败（核心集合/versions/workspaceId 缺失，后端 JSON 键变更？）`, value)
    throw new Error(SNAPSHOT_INCOMPATIBLE_MESSAGE)
  }
  return value as AppSnapshot
}
