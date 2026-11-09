import { invoke } from '@tauri-apps/api/core'
import type { Material } from './client'

export const webBackend = {
  // 21#3：fetch 封装无 UI 调用方，已删除；61 号：fetch_webpage IPC 命令
  // 同步移除（抓取链由 import_webpage 内部复用）；接入网页抓取 UI 时再补封装
  import: (url: string) => invoke<{ material: Material; deduplicated: boolean }>('import_webpage', { url }),
}
