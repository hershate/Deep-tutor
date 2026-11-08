import { useEffect, useState } from 'react'
import type { Material } from '../backend/client'
import { backendErrorMessage } from '../backend/client'
import { mediaBackend } from '../backend/media'
import { Button, ProgressBar } from './UI'

export default function MediaPlayer({ material }: { material: Pick<Material, 'id' | 'title' | 'kind'> }) {
  const [url, setUrl] = useState('')
  const [mime, setMime] = useState('')
  const [progress, setProgress] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  // 28#7：分块读取/哈希校验失败多为瞬态 IO——提供显式重试，不再只能换选媒体
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let disposed = false
    let objectUrl: string | null = null
    setUrl(''); setError(''); setProgress(0); setLoading(true)
    const load = async () => {
      try {
        const info = await mediaBackend.info(material.id)
        if (info.total <= 0) throw new Error('媒体原件为空')
        const parts: ArrayBuffer[] = []
        let offset = 0
        while (offset < info.total) {
          const data = await mediaBackend.chunk(material.id, offset)
          if (disposed) return
          if (!(data instanceof ArrayBuffer) || !data.byteLength || data.byteLength > 256 * 1024) throw new Error('原生媒体分块不完整')
          parts.push(data)
          offset += data.byteLength
          setProgress(Math.min(99, Math.round(offset / info.total * 100)))
        }
        // 46#2：传输完成后由 Rust 侧整文件复核（info 传输前已校验，此处关闭
        // 预览期间外部改动原件的 TOCTOU 窗口）——替代前端 blob.arrayBuffer()
        // 的第三次复制与 JS 全量 SHA-256；校验失败不创建播放器（28#7）
        await mediaBackend.verify(material.id)
        if (disposed) return
        const blob = new Blob(parts, { type: info.mime })
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl); setMime(info.mime); setProgress(100)
      } catch (cause) {
        if (!disposed) setError(backendErrorMessage(cause))
      } finally {
        if (!disposed) setLoading(false)
      }
    }
    void load()
    return () => { disposed = true; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [material.id, attempt])
  const unsupported = () => setError('应用内播放器无法解码此文件，请检查实际编码；仍可下载原件。')
  return <section className="native-media-preview" aria-label={`预览 ${material.title}`}>
    <div><strong>{material.title}</strong><p>本地原件预览，不生成字幕、OCR 或内容分析。</p></div>
    {loading && <><ProgressBar value={progress} label={`正在校验媒体 ${progress}%`} /><p>正在由原生后端读取并校验原件…</p></>}
    {error && <><p className="form-error" role="alert">{error}</p><Button variant="secondary" size="sm" onClick={() => setAttempt(current => current + 1)}>重试读取</Button></>}
    {url && mime.startsWith('video/') && <video controls preload="metadata" src={url} onError={unsupported} style={{ width: '100%', maxHeight: 480 }} />}
    {url && mime.startsWith('audio/') && <audio controls preload="metadata" src={url} onError={unsupported} style={{ width: '100%' }} />}
    {url && mime.startsWith('image/') && <img src={url} alt={material.title} onError={unsupported} style={{ maxWidth: '100%', maxHeight: 480, objectFit: 'contain' }} />}
  </section>
}
