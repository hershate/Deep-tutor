import { invoke } from '@tauri-apps/api/core'
import type { Material } from './client'

export const webBackend = {
  // 21#3：fetch 封装无 UI 调用方（fetch_webpage 命令保留给后端内部
  // import_webpage 复用通道），已删除；接入网页抓取 UI 时再补封装
  import: (url: string) => invoke<{ material: Material; deduplicated: boolean }>('import_webpage', { url }),
}
