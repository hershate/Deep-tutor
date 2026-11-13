import { invoke } from '@tauri-apps/api/core'
import type { Material } from './client'

export const webBackend = {
  import: (url: string) => invoke<{ material: Material; deduplicated: boolean }>('import_webpage', { url }),
}
