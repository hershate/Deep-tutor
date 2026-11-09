import { useEffect, useMemo, useRef, useState } from 'react'
import { clampToBytes } from '../limits'
import { BookOpen, Check, ChevronRight, Download, File, FileArchive, FileAudio, FileText, Film, Folder, Globe2, Link2, NotebookPen, Search, ShieldCheck, Tag, Trash2, Upload, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import type { Note, Resource } from '../data'
import { Badge, Button, ConfirmDialog, EmptyState, Modal, ProgressBar } from '../components/UI'
import { localId } from '../ids'
import WikiPanel from '../components/WikiPanel'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, isCancelRace } from '../backend/client'
import type { AppSnapshot, MaterialDetail } from '../backend/client'
import { exportBackend } from '../backend/export'
import { importMaterialPaths, summarizeMaterialImport } from '../backend/materialImport'
import { notesBackend } from '../backend/notes'
import type { LibraryOverview } from '../backend/notes'
import { mediaBackend } from '../backend/media'
import MediaPlayer from '../components/MediaPlayer'

const materialStatus: Record<string, string> = {
  staged: '已暂存', queued: '等待解析', parsing: '解析中', parsed: '已解析', degraded: '部分解析',
  failed: '解析失败', cancelling: '正在取消', cancelled: '已取消', metadata_only: '仅元数据',
}

function materialTone(status: string): 'neutral' | 'green' | 'amber' | 'red' {
  if (status === 'parsed' || status === 'degraded') return 'green'
  if (status === 'failed') return 'red'
  if (['queued', 'parsing', 'cancelling'].includes(status)) return 'amber'
  return 'neutral'
}

// 19#3：updated 字段渲染为相对时间——历史遗留的相对文案（如"刚刚"）原样展示
function formatUpdated(value: string) {
  const time = Date.parse(value)
  if (Number.isNaN(time)) return value
  const seconds = Math.floor((Date.now() - time) / 1000)
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} 小时前`
  if (seconds < 2_592_000) return `${Math.floor(seconds / 86_400)} 天前`
  return new Date(time).toLocaleDateString()
}

export default function Library({ notes, setNotes, resources, onImportNotes, notify }: { notes: Note[]; setNotes: (notes: Note[] | ((current: Note[]) => Note[])) => Promise<AppSnapshot>; resources: Resource[]; onImportNotes: (path: string) => Promise<number>; notify: (message: string) => void }) {
  const [tab, setTab] = useState('笔记')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('全部')
  const [noteOpen, setNoteOpen] = useState(false)
  const [activeNote, setActiveNote] = useState<Note | null>(null)
  const [noteTitle, setNoteTitle] = useState('')
  const [noteContent, setNoteContent] = useState('')
  const [noteTag, setNoteTag] = useState('概率论')
  const [dirty, setDirty] = useState(false)
  const [discardOpen, setDiscardOpen] = useState(false)
  const [draftRestored, setDraftRestored] = useState(false)
  const [activeMaterial, setActiveMaterial] = useState<MaterialDetail | null>(null)
  const [openingMaterial, setOpeningMaterial] = useState<number | null>(null)
  const [importing, setImporting] = useState(false)
  const [materialBusy, setMaterialBusy] = useState(false)
  const [resourceTab, setResourceTab] = useState('解析文本')
  const [libraryOverview, setLibraryOverview] = useState<LibraryOverview | null>(null)
  const [libraryError, setLibraryError] = useState('')
  const [libraryReloadKey, setLibraryReloadKey] = useState(0)
  const [checkOpen, setCheckOpen] = useState(false)
  const draftWrite = useRef<Promise<unknown>>(Promise.resolve())
  const draftBlocked = useRef(false)
  const [saving, setSaving] = useState(false)
  const noteTags = useMemo(() => Array.from(new Set([...notes.map(note => note.tag), noteTag].filter(Boolean))), [notes, noteTag])
  const filteredNotes = useMemo(() => notes.filter(n => (filter === '全部' || n.tag === filter) && `${n.title}${n.excerpt}${n.content}`.toLowerCase().includes(query.toLowerCase())), [notes, query, filter])
  const filteredResources = useMemo(() => resources.filter(r => (filter === '全部' || r.type === filter) && r.title.toLowerCase().includes(query.toLowerCase())), [resources, query, filter])
  const draftKey = `deep-tutor-draft-${activeNote?.id || 'new'}`

  const enqueueDraft = (force = false) => {
    if (draftBlocked.current && !force) return draftWrite.current
    const write = draftWrite.current.catch(() => {}).then(() => backend.saveDraft(draftKey, { title: noteTitle, content: noteContent, tag: noteTag, savedAt: Date.now() }))
    draftWrite.current = write
    return write
  }
  useEffect(() => {
    if (!noteOpen || !dirty || saving) return
    const timer = window.setTimeout(() => {
      if (!draftBlocked.current) enqueueDraft().catch(reportBackendError)
    }, 350)
    return () => window.clearTimeout(timer)
  }, [noteOpen, dirty, saving, noteTitle, noteContent, noteTag, draftKey])
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => { if (noteOpen && dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', handler); return () => window.removeEventListener('beforeunload', handler)
  }, [noteOpen, dirty])

  useEffect(() => {
    if (!activeMaterial) return
    let stopped = false
    let unlisten: (() => void) | undefined
    void onBackendEvent(event => {
      if (event.payload.materialId !== activeMaterial.id) return
      if (event.kind === 'material.deleted') { setActiveMaterial(null); return }
      if (!event.kind.startsWith('material.')) return
      void backend.getMaterial(activeMaterial.id).then(detail => {
        if (!stopped) setActiveMaterial(detail)
      }).catch(reportBackendError)
    // 32#5：晚解析守卫——卸载早于 listen() resolve 时立即注销
    }).then(stop => { if (stopped) stop(); else unlisten = stop }).catch(reportBackendError)
    return () => { stopped = true; unlisten?.() }
  }, [activeMaterial?.id])

  useEffect(() => {
    let active = true
    let stop: (() => void) | undefined
    const fail = (cause: unknown) => {
      if (active) { setLibraryError(backendErrorMessage(cause)); reportBackendError(cause) }
    }
    const load = async () => {
      try {
        const next = await notesBackend.overview()
        if (active) { setLibraryOverview(next); setLibraryError('') }
      } catch (cause) { fail(cause) }
    }
    void load()
    void onBackendEvent(event => {
      if (active && (event.kind.startsWith('material.') || event.kind.startsWith('plan.') || event.kind === 'study.note_saved' || event.kind === 'security.session_started' || (event.kind === 'state.changed' && event.payload.namespace === 'notes'))) void load()
    }).then(unlisten => { if (active) stop = unlisten; else unlisten() }).catch(fail)
    return () => { active = false; stop?.() }
  }, [libraryReloadKey])

  const importMaterials = async () => {
    if (importing) return
    setImporting(true)
    try {
      const paths = await backend.openMaterialFiles()
      if (!paths?.length) return
      notify(summarizeMaterialImport(await importMaterialPaths(paths)))
    } catch (error) { reportBackendError(error, 'write') } finally { setImporting(false) }
  }
  const openMaterial = async (id: number) => {
    if (openingMaterial !== null) return
    setOpeningMaterial(id)
    setActiveMaterial(null)
    try {
      setActiveMaterial(await backend.getMaterial(id))
      setResourceTab('解析文本')
    } catch (error) { reportBackendError(error) } finally { setOpeningMaterial(null) }
  }
  const startParse = async (id: number) => {
    setMaterialBusy(true)
    try {
      const result = await backend.enqueueMaterialParse(id)
      setActiveMaterial(current => current?.id === id ? { ...current, ...result.material } : current)
      notify('原生解析任务已登记，进度将由后端实时推送')
    } catch (error) { reportBackendError(error, 'write') } finally { setMaterialBusy(false) }
  }
  const cancelParse = async (id: number) => {
    setMaterialBusy(true)
    try {
      const material = await backend.cancelMaterialParse(id)
      setActiveMaterial(current => current?.id === id ? { ...current, ...material } : current)
      notify('已请求取消，等待原生任务写入终态')
    } catch (error) {
      // 20#1：恰在点击瞬间任务已终态时 no_active_job 是竞态码——任务已正常
      // 结束，静默刷新即可（守卫原误放在不会返回该码的 startParse 上）
      if (!isCancelRace(error)) reportBackendError(error)
    } finally { setMaterialBusy(false) }
  }
  // 43#2：导出族补 busy——保存对话框关闭后的写盘期间按钮不再可点
  const [exportBusy, setExportBusy] = useState('')
  const exportOriginal = async () => {
    if (!activeMaterial || exportBusy) return
    setExportBusy('original')
    try {
      const destination = await backend.saveMaterialFile(activeMaterial.title, activeMaterial.kind.toLowerCase())
      if (!destination) return
      await backend.exportMaterialOriginal(activeMaterial.id, destination)
      notify('原件已校验并导出')
    } catch (error) { reportBackendError(error) } finally { setExportBusy('') }
  }
  const exportText = async () => {
    if (!activeMaterial?.text || exportBusy) return
    setExportBusy('text')
    try {
      const destination = await backend.saveMaterialFile(activeMaterial.title, 'txt')
      if (!destination) return
      await backend.exportMaterialText(activeMaterial.id, destination)
      notify('解析文本已由原生后端写入文件')
    } catch (error) { reportBackendError(error) } finally { setExportBusy('') }
  }
  // 20#5：删除资料/笔记为不可恢复操作，补确认对话框
  const [deleteMaterialOpen, setDeleteMaterialOpen] = useState(false)
  const [deleteNoteOpen, setDeleteNoteOpen] = useState(false)
  const deleteMaterial = async (id: number) => {
    setMaterialBusy(true)
    try {
      await backend.deleteMaterial(id)
      setActiveMaterial(null)
      setDeleteMaterialOpen(false)
      notify('资料记录及可用原件已删除')
    } catch (error) { reportBackendError(error, 'write') } finally { setMaterialBusy(false) }
  }

  // 51#4：打开序号守卫——快速连点笔记 A 再 B 时两次 getDraft 并发，A 后到
  // 会把整个编辑器覆盖回 A，用户在「以为是 B」的界面继续打字并保存进 A
  const openingNoteSeq = useRef(0)
  const openNote = (note?: Note) => {
    const ticket = ++openingNoteSeq.current
    const key = `deep-tutor-draft-${note?.id || 'new'}`
    backend.getDraft<{ title: string; content: string; tag: string }>(key).then(({ value: draft }) => {
      if (ticket !== openingNoteSeq.current) return
      setActiveNote(note || null); setDraftRestored(Boolean(draft))
      if (draft) { setNoteTitle(draft.title); setNoteContent(draft.content); setNoteTag(draft.tag); setDirty(true) }
      else { setNoteTitle(note?.title || ''); setNoteContent(note?.content || ''); setNoteTag(note?.tag || '概率论'); setDirty(false) }
      setNoteOpen(true)
    }).catch(error => {
      if (ticket !== openingNoteSeq.current) return
      reportBackendError(error)
      setActiveNote(note || null); setDraftRestored(false); setNoteTitle(note?.title || ''); setNoteContent(note?.content || ''); setNoteTag(note?.tag || '概率论'); setDirty(false); setNoteOpen(true)
    })
  }
  const closeNote = () => { if (draftBlocked.current) return; if (dirty) setDiscardOpen(true); else setNoteOpen(false) }
  const saveNote = async () => {
    if (saving || !noteTitle.trim() || !noteContent.trim()) return
    draftBlocked.current = true
    setSaving(true)
    try {
      await draftWrite.current
      // 19#3：updated 持久化 ISO 时间戳，卡片渲染时格式化为相对时间；
      // 24#5：更新函数以最新集合为基准
      const stamp = new Date().toISOString()
      await setNotes(activeNote
        ? current => current.map(n => n.id === activeNote.id ? { ...n, title: noteTitle.trim(), content: noteContent.trim(), excerpt: noteContent.trim().slice(0, 90), tag: noteTag, updated: stamp } : n)
        : current => [{ id: localId(), title: noteTitle.trim(), content: noteContent.trim(), excerpt: noteContent.trim().slice(0, 90), tag: noteTag, updated: stamp }, ...current])
      await backend.deleteDraft(draftKey)
      setDirty(false); setNoteOpen(false); notify(activeNote ? '笔记修改已保存' : '新笔记已创建')
    } catch (error) { reportBackendError(error, 'write') } finally { draftBlocked.current = false; setSaving(false) }
  }
  const deleteNote = async () => {
    if (!activeNote || saving) return
    draftBlocked.current = true
    setSaving(true)
    try {
      await draftWrite.current.catch(() => {})
      await setNotes(current => current.filter(n => n.id !== activeNote.id))
      await backend.deleteDraft(draftKey)
      setDirty(false); setNoteOpen(false); notify('笔记已删除')
    } catch (error) { reportBackendError(error, 'write') } finally { draftBlocked.current = false; setSaving(false) }
  }
  const settleDraft = async (keep: boolean) => {
    if (saving) return
    draftBlocked.current = true
    setSaving(true)
    try {
      if (keep) await enqueueDraft(true)
      else {
        await draftWrite.current
        await backend.deleteDraft(draftKey)
      }
      setDirty(false); setDiscardOpen(false); setNoteOpen(false)
      if (keep) notify('草稿已由原生后端保存，下次打开可恢复')
    } catch (error) { reportBackendError(error, 'write') } finally { draftBlocked.current = false; setSaving(false) }
  }
  const exportNotes = async () => {
    if (exportBusy) return
    setExportBusy('notes')
    try {
      const destination = await backend.saveMaterialFile('DeepTutor-notes', 'zip')
      if (!destination) return
      const result = await exportBackend.notesArchive(destination)
      notify(`已由原生后端导出 ${result.count} 条 Markdown 笔记与清单（${result.bytes.toLocaleString()} 字节）`)
    } catch (error) { reportBackendError(error) } finally { setExportBusy('') }
  }
  const importNotes = async () => {
    if (importing) return
    setImporting(true)
    try {
      const selected = await notesBackend.openFiles()
      // 62#7：多选逐个导入（ZIP 归档单选一次即可含至多 1000 条）
      const paths = Array.isArray(selected) ? selected : selected ? [selected] : []
      if (!paths.length) return
      let imported = 0
      const failures: string[] = []
      for (const path of paths) {
        try { imported += await onImportNotes(path) }
        catch (error) { failures.push(`${path.split(/[\\\\/]/).pop() ?? path}：${backendErrorMessage(error)}`) }
      }
      if (imported) notify(`已由原生后端追加 ${imported} 条笔记，原有内容未覆盖`)
      if (failures.length) notify(`${failures.length} 个文件导入失败：${failures[0]}${failures.length > 1 ? ' 等' : ''}`)
    } catch (error) { reportBackendError(error, 'write') } finally { setImporting(false) }
  }
  return <div className="page library-page">
    <div className="page-heading"><div><h1>笔记与 Wiki</h1><p>自由笔记、原始资料与可核验的库内引用问答。</p></div><div className="heading-actions">{tab === '笔记' && <Button variant="secondary" disabled={importing} onClick={() => { void importNotes() }}><FileArchive size={17} />{importing ? '正在导入…' : '批量导入'}</Button>}{tab === '笔记' ? <Button onClick={() => openNote()}><NotebookPen size={18} />新建笔记</Button> : <Button disabled={importing} onClick={() => { void importMaterials() }}><Upload size={18} />{importing ? '正在导入…' : '导入资料'}</Button>}</div></div>
    <div className="library-toolbar"><div className="library-tabs">{['笔记', '资料库', 'Wiki 问答'].map(item => <button key={item} className={tab === item ? 'active' : ''} onClick={() => { setTab(item); setFilter('全部') }}>{item === '笔记' ? <NotebookPen size={17} /> : item === '资料库' ? <Folder size={17} /> : <BookOpen size={17} />}{item}<span>{item === '笔记' ? notes.length : item === '资料库' ? resources.length : ''}</span></button>)}</div>{tab !== 'Wiki 问答' && <div className="search-field"><Search size={17} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder={`搜索${tab}…`} aria-label={`搜索${tab}`} />{query && <button aria-label="清除搜索" onClick={() => setQuery('')}><X size={15} /></button>}</div>}</div>
    {tab !== 'Wiki 问答' && <div className="filter-row">{(tab === '笔记' ? ['全部', ...noteTags] : ['全部', 'PDF', '视频', '网页', '音频', '文件']).map(item => <button className={filter === item ? 'active' : ''} key={item} onClick={() => setFilter(item)}>{item}</button>)}<span />{tab === '笔记' && <Button variant="ghost" size="sm" disabled={exportBusy !== ''} onClick={() => { void exportNotes() }}>{exportBusy === 'notes' ? '正在导出…' : <><Download size={16} />ZIP 导出</>}</Button>}</div>}
    {tab === '笔记' && <>{filteredNotes.length ? <div className="notes-grid">{filteredNotes.map(note => <button className="note-card" key={note.id} onClick={() => openNote(note)}><div className="note-card-top"><Badge tone="neutral">{note.tag}</Badge><span>{formatUpdated(note.updated)}</span></div><h2>{note.title}</h2><p>{note.excerpt}</p><div className="note-card-bottom"><span><Link2 size={14} />{libraryOverview?.notes.find(item => item.noteId === note.id)?.links.length ? `关联 ${libraryOverview.notes.find(item => item.noteId === note.id)?.links.length} 个知识节点` : '未关联知识节点'}</span><ChevronRight size={17} /></div></button>)}</div> : <EmptyState title="没有找到匹配的笔记" description="尝试清除搜索词或切换标签，也可以现在创建一条新笔记。" action={<Button onClick={() => openNote()}>新建笔记</Button>} />}</>}
    {tab === '资料库' && <>{filteredResources.length ? <div className="resource-list">{filteredResources.map(resource => <button className="resource-row" key={resource.id} onClick={() => { void openMaterial(resource.id) }}><span className="resource-type">{resource.type === 'PDF' ? <FileText size={22} /> : resource.type === '视频' ? <Film size={22} /> : resource.type === '网页' ? <Globe2 size={22} /> : resource.type === '音频' ? <FileAudio size={22} /> : <File size={22} />}</span><div className="resource-copy"><div><Badge tone="neutral">{resource.type}</Badge><span>{resource.meta}</span></div><h3>{resource.title}</h3><ProgressBar value={resource.progress} /></div><span className="resource-percent">{resource.progress}%</span><ChevronRight size={18} /></button>)}</div> : <EmptyState title="这里还没有匹配的资料" description="通过原生文件选择器导入资料，原件暂存后可开始解析。" action={<Button disabled={importing} onClick={() => { void importMaterials() }}><Upload size={17} />导入资料</Button>} />}</>}
    {tab === 'Wiki 问答' && <WikiPanel resources={resources} notify={notify} />}
    <section className="library-bottom"><div><Tag size={18} /><span>知识库概览</span></div><p><strong>{notes.length + resources.length}</strong> 个条目 · <strong>{libraryOverview?.linkedNodes ?? 0}</strong> 个已关联知识节点 · 笔记、原件及解析文本由原生后端管理</p><button onClick={() => setCheckOpen(true)}>检查知识库<ShieldCheck size={16} /></button></section>

    <Modal open={checkOpen} onClose={() => setCheckOpen(false)} title="真实知识库检查" wide><div className="form-stack"><p className="form-help">检查当前用户的笔记关联、完全相同的正文、原件可用性和解析状态。不会自动删除笔记或原件。</p><div className="form-grid"><div><strong>{libraryOverview?.unlinkedNotes ?? '—'}</strong><p>未关联笔记</p></div><div><strong>{libraryOverview?.duplicateNoteGroups ?? '—'}</strong><p>重复正文组</p></div><div><strong>{libraryOverview?.missingOriginals ?? '—'}</strong><p>缺失原件</p></div><div><strong>{libraryOverview?.failedMaterials ?? '—'}</strong><p>解析失败</p></div></div><div className="milestone-list">{libraryOverview?.issues.map(issue => <div className="milestone-row" key={issue.id}><span className="milestone-marker"><Tag size={15} /></span><div><strong>{issue.title}</strong><p>{issue.detail}</p></div><Badge tone={issue.severity === 'error' ? 'red' : issue.severity === 'warning' ? 'amber' : 'neutral'}>{issue.severity === 'error' ? '失败' : issue.severity === 'warning' ? '需检查' : '提示'}</Badge>{issue.noteId && notes.some(note => note.id === issue.noteId) ? <Button variant="ghost" size="sm" onClick={() => { const note = notes.find(item => item.id === issue.noteId); if (note) { setCheckOpen(false); openNote(note) } }}>打开笔记</Button> : issue.materialId && resources.some(resource => resource.id === issue.materialId) ? <Button variant="ghost" size="sm" onClick={() => { setCheckOpen(false); void openMaterial(issue.materialId!) }}>打开资料</Button> : null}</div>)}{libraryOverview && !libraryOverview.issues.length && <EmptyState title="当前没有需要提示的条目" description="没有重复正文、未关联笔记、缺失原件或解析失败。此结果只描述上述真实检查范围。" />}{!libraryOverview && (libraryError ? <div className="form-error" role="alert"><p>{libraryError}</p><Button variant="secondary" size="sm" onClick={() => { setLibraryError(''); setLibraryReloadKey(current => current + 1) }}>重新检查</Button></div> : <p>正在读取真实笔记、关联和资料状态…</p>)}</div></div></Modal>
    <Modal open={noteOpen} onClose={closeNote} title={activeNote ? '编辑笔记' : '新建笔记'} wide><div className="note-editor"><div className="editor-meta"><input value={noteTitle} disabled={saving} onChange={e => { setNoteTitle(clampToBytes(e.target.value, 500)); setDirty(true) }} placeholder="笔记标题" aria-label="笔记标题" /><select value={noteTag} disabled={saving} onChange={e => { setNoteTag(e.target.value); setDirty(true) }} aria-label="笔记标签">{noteTags.map(tag => <option key={tag}>{tag}</option>)}</select></div>{draftRestored && <div className="draft-notice"><Check size={15} />已恢复未保存草稿；本地草稿与离开警告双重保护。</div>}<div className="editor-toolbar"><span>Markdown + GFM + LaTeX</span>{/* 60#5：51#14 的保存期锁定未覆盖工具栏——点击仍追加内容并置 dirty，不进本次保存载荷 */}<button disabled={saving} onClick={() => { setNoteContent(noteContent + '\n\n## 小标题'); setDirty(true) }}>H2</button><button disabled={saving} onClick={() => { setNoteContent(noteContent + ' **重点** '); setDirty(true) }}>加粗</button><button disabled={saving} onClick={() => { setNoteContent(noteContent + '\n- 列表项'); setDirty(true) }}>列表</button><button disabled={saving} onClick={() => { setNoteContent(noteContent + '\n```ts\n// 代码\n```'); setDirty(true) }}>代码</button><button disabled={saving} onClick={() => { setNoteContent(noteContent + '\n$$P(A|B)=\\frac{P(B|A)P(A)}{P(B)}$$'); setDirty(true) }}>公式</button></div>{/* 51#14：保存 IPC 在途期间禁用输入——原先窗口内继续打字的内容不进保存值即被丢弃 */}
<div className="note-editor-grid"><textarea value={noteContent} disabled={saving} onChange={e => { setNoteContent(e.target.value); setDirty(true) }} placeholder="用自己的话记录理解，而不只是摘抄…" aria-label="笔记 Markdown 正文" /><article className="note-preview"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{noteContent || '实时预览将在这里显示。'}</ReactMarkdown></article></div><div className="editor-footer"><span>{noteContent.length} 字 · {dirty ? '草稿自动暂存中' : '已保存'}</span><div>{activeNote && <Button variant="ghost" disabled={saving} onClick={() => setDeleteNoteOpen(true)}><Trash2 size={16} />删除</Button>}<Button onClick={saveNote} disabled={saving || !noteTitle.trim() || !noteContent.trim()}>保存笔记</Button></div></div></div></Modal>
    <ConfirmDialog open={deleteMaterialOpen} onClose={() => setDeleteMaterialOpen(false)} onConfirm={() => { if (activeMaterial) void deleteMaterial(activeMaterial.id) }} title="删除这份资料？" description="将同时删除资料记录与可用原件文件，已生成的知识引用会失效；此操作不可恢复。" confirmLabel="确认删除" busy={materialBusy || exportBusy !== ''} />
    <ConfirmDialog open={deleteNoteOpen} onClose={() => setDeleteNoteOpen(false)} onConfirm={() => { setDeleteNoteOpen(false); void deleteNote() }} title="删除这条笔记？" description="笔记正文与关联知识节点将一并移除；此操作不可恢复（可先保留草稿再删除）。" confirmLabel="确认删除" busy={saving} />
    <Modal open={discardOpen} onClose={() => { if (!saving) setDiscardOpen(false) }} title="关闭但保留草稿？"><div className="confirm-dialog"><p>保留时会先等待原生后端写入；丢弃时会删除后端草稿。</p><div><Button variant="ghost" disabled={saving} onClick={() => { void settleDraft(false) }}>丢弃草稿</Button><Button disabled={saving} onClick={() => { void settleDraft(true) }}>保留草稿并关闭</Button></div></div></Modal>
    <Modal open={Boolean(activeMaterial) || openingMaterial !== null} onClose={() => setActiveMaterial(null)} title="KB 文档管理" wide>{activeMaterial ? <div className="resource-detail"><div className="resource-detail-head"><FileText size={25} /><div><h2>{activeMaterial.title}</h2><p>{activeMaterial.kind} · {activeMaterial.size} 字节</p></div><Badge tone={materialTone(activeMaterial.status)}>{materialStatus[activeMaterial.status] || activeMaterial.status}</Badge></div><div className="library-tabs">{['解析文本', '原件信息'].map(item => <button className={resourceTab === item ? 'active' : ''} key={item} onClick={() => setResourceTab(item)}>{item}</button>)}</div>{resourceTab === '解析文本' ? <div className="parsed-text"><div><span>{activeMaterial.textLength} 字 · {activeMaterial.parseMode || '尚未解析'}</span><Button variant="secondary" size="sm" disabled={!activeMaterial.text || exportBusy !== '' || materialBusy} onClick={() => { void exportText() }}><Download size={15} />{exportBusy === 'text' ? '正在导出…' : '下载文本'}</Button></div>{activeMaterial.warning && <p className="setting-note">{activeMaterial.warning}</p>}{activeMaterial.error && <p className="setting-note">{activeMaterial.error.code}：{activeMaterial.error.message}</p>}{activeMaterial.text ? <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{activeMaterial.text}</pre> : <p>{['queued', 'parsing', 'cancelling'].includes(activeMaterial.status) ? '正在等待原生后端写入解析结果。' : '当前没有已保存的解析文本。'}</p>}</div> : <div className="original-info">{mediaBackend.isMedia(activeMaterial.kind) && <MediaPlayer material={activeMaterial} />}<div><span>原件状态</span><strong>{activeMaterial.originalAvailable ? '可用，已通过导入校验' : '无可用原件'}</strong></div><div><span>解析状态</span><strong>{materialStatus[activeMaterial.status] || activeMaterial.status} · {activeMaterial.progress}%</strong></div><div><span>SHA-256</span><code style={{ overflowWrap: 'anywhere' }}>{activeMaterial.sha256 || '无原件哈希'}</code></div><div className="data-actions">{['queued', 'parsing', 'cancelling'].includes(activeMaterial.status) ? <Button variant="secondary" disabled={materialBusy || activeMaterial.status === 'cancelling'} onClick={() => { void cancelParse(activeMaterial.id) }}>请求取消</Button> : <Button disabled={!activeMaterial.originalAvailable || materialBusy || !mediaBackend.isText(activeMaterial.kind)} onClick={() => { void startParse(activeMaterial.id) }}>{mediaBackend.isText(activeMaterial.kind) ? activeMaterial.status === 'staged' ? '开始解析' : '重新解析' : '仅原件预览，不支持内容识别'}</Button>}<Button variant="secondary" disabled={!activeMaterial.originalAvailable || exportBusy !== '' || materialBusy} onClick={() => { void exportOriginal() }}>{exportBusy === 'original' ? '正在导出…' : '下载原件'}</Button><Button variant="ghost" disabled={materialBusy || exportBusy !== '' || ['queued', 'parsing', 'cancelling'].includes(activeMaterial.status)} onClick={() => setDeleteMaterialOpen(true)}><Trash2 size={16} />删除资料</Button></div></div>}</div> : <p>正在读取原生资料记录…</p>}</Modal>
  </div>
}
