import { invoke } from '@tauri-apps/api/core'
import type { Material } from './client'

export type WebPage = { url: string; title: string; text: string; sha256: string }

export const webBackend = {
  fetch: (url: string) => invoke<WebPage>('fetch_webpage', { url }),
  import: (url: string) => invoke<{ material: Material; deduplicated: boolean }>('import_webpage', { url }),
}
