import { invoke } from '@tauri-apps/api/core'

export type MediaInfo = { id: number; total: number; mime: string; sha256: string }
const mediaKinds = new Set(['mp4', 'mov', 'm4v', 'webm', 'mp3', 'wav', 'ogg', 'm4a', 'png', 'jpg', 'jpeg', 'gif', 'webp'])

export const mediaBackend = {
  isMedia: (kind: string) => mediaKinds.has(kind.toLowerCase()),
  isText: (kind: string) => ['pdf', 'docx', 'pptx', 'epub', 'txt', 'md'].includes(kind.toLowerCase()),
  info: (id: number) => invoke<MediaInfo>('get_material_media_info', { id }),
  // 46#2：二进制应答（tauri::ipc::Response）——原先 JSON 数字数组每字节一个
  // Number，50MB 视频预览内存峰值 3–4×；offset/total/mime 由 info() 推导
  chunk: (id: number, offset: number, length = 256 * 1024) => invoke<ArrayBuffer>('read_material_media_chunk', { id, offset, length }),
  // 46#2：传输完成后 Rust 侧整文件复核（TOCTOU 关闭），替代前端 JS 全量哈希
  verify: (id: number) => invoke<void>('verify_material_media', { id }),
}
