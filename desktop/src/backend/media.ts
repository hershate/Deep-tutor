import { invoke } from '@tauri-apps/api/core'

export type MediaInfo = { id: number; total: number; mime: string; sha256: string }
export type MediaChunk = { id: number; offset: number; total: number; mime: string; data: number[] }
const mediaKinds = new Set(['mp4', 'mov', 'm4v', 'webm', 'mp3', 'wav', 'ogg', 'm4a', 'png', 'jpg', 'jpeg', 'gif', 'webp'])

export const mediaBackend = {
  isMedia: (kind: string) => mediaKinds.has(kind.toLowerCase()),
  isText: (kind: string) => ['pdf', 'docx', 'pptx', 'epub', 'txt', 'md'].includes(kind.toLowerCase()),
  info: (id: number) => invoke<MediaInfo>('get_material_media_info', { id }),
  chunk: (id: number, offset: number, length = 256 * 1024) => invoke<MediaChunk>('read_material_media_chunk', { id, offset, length }),
}
