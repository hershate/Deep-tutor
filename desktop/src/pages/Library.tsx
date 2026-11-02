import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDownToLine, BookOpen, Check, ChevronRight, Download, File, FileArchive, FileAudio, FileText, Film, Folder, Globe2, Link2, NotebookPen, Search, Tag, Trash2, Upload, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import type { Note, Resource } from '../data'
import { Badge, Button, EmptyState, Modal, ProgressBar } from '../components/UI'
import WikiPanel from '../components/WikiPanel'
import { backend, reportBackendError } from '../backend/client'
import type { AppSnapshot } from '../backend/client'

function saveBlob(blob: Blob, name: string) { const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000) }

export default function Library({ notes, setNotes, resources, setResources, notify }: { notes: Note[]; setNotes: (notes: Note[]) => Promise<AppSnapshot>; resources: Resource[]; setResources: (resources: Resource[]) => Promise<AppSnapshot>; notify: (message: string) => void }) {
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
  const [activeResource, setActiveResource] = useState<Resource | null>(null)
  const [resourceTab, setResourceTab] = useState('解析文本')
  const zipRef = useRef<HTMLInputElement>(null)
  const draftWrite = useRef<Promise<unknown>>(Promise.resolve())
  const draftBlocked = useRef(false)
  const [saving, setSaving] = useState(false)
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

  const openNote = (note?: Note) => {
    const key = `deep-tutor-draft-${note?.id || 'new'}`
    backend.getDraft<{ title: string; content: string; tag: string }>(key).then(({ value: draft }) => {
      setActiveNote(note || null); setDraftRestored(Boolean(draft))
      if (draft) { setNoteTitle(draft.title); setNoteContent(draft.content); setNoteTag(draft.tag); setDirty(true) }
      else { setNoteTitle(note?.title || ''); setNoteContent(note?.content || ''); setNoteTag(note?.tag || '概率论'); setDirty(false) }
      setNoteOpen(true)
    }).catch(error => {
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
      const next = activeNote ? notes.map(n => n.id === activeNote.id ? { ...n, title: noteTitle.trim(), content: noteContent.trim(), excerpt: noteContent.trim().slice(0, 90), tag: noteTag, updated: '刚刚' } : n) : [{ id: Date.now(), title: noteTitle.trim(), content: noteContent.trim(), excerpt: noteContent.trim().slice(0, 90), tag: noteTag, updated: '刚刚' }, ...notes]
      await setNotes(next)
      await backend.deleteDraft(draftKey)
      setDirty(false); setNoteOpen(false); notify(activeNote ? '笔记修改已保存' : '新笔记已创建')
    } catch (error) { reportBackendError(error) } finally { draftBlocked.current = false; setSaving(false) }
  }
  const deleteNote = async () => {
    if (!activeNote || saving) return
    draftBlocked.current = true
    setSaving(true)
    try {
      await draftWrite.current.catch(() => {})
      await setNotes(notes.filter(n => n.id !== activeNote.id))
      await backend.deleteDraft(draftKey)
      setDirty(false); setNoteOpen(false); notify('笔记已删除')
    } catch (error) { reportBackendError(error) } finally { draftBlocked.current = false; setSaving(false) }
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
    } catch (error) { reportBackendError(error) } finally { draftBlocked.current = false; setSaving(false) }
  }
  const exportNotes = async () => {
    const { default: JSZip } = await import('jszip')
    const zip = new JSZip(); const folder = zip.folder('知舟笔记')
    notes.forEach((note, i) => folder?.file(`${String(i + 1).padStart(2, '0')}-${note.title.replace(/[\\/:*?"<>|]/g, '_')}.md`, `# ${note.title}\n\n标签：${note.tag}\n更新时间：${note.updated}\n\n${note.content}`))
    folder?.file('manifest.json', JSON.stringify({ version: 1, count: notes.length, exportedAt: new Date().toISOString() }, null, 2))
    saveBlob(await zip.generateAsync({ type: 'blob' }), 'DeepTutor-notes.zip'); notify(`已导出 ${notes.length} 条 Markdown 笔记与清单`)
  }
  const importNotes = async (file: File) => {
    const imported: Note[] = []
    try {
      if (file.name.endsWith('.zip')) {
        const { default: JSZip } = await import('jszip')
        const zip = await JSZip.loadAsync(file)
        for (const [path, entry] of Object.entries(zip.files)) {
          if (!entry.dir && /\.(md|txt)$/i.test(path) && !path.endsWith('manifest.json')) { const content = await entry.async('text'); const title = path.split('/').pop()?.replace(/\.(md|txt)$/i, '') || '导入笔记'; imported.push({ id: Date.now() + imported.length, title, excerpt: content.slice(0, 90), content, tag: '复盘', updated: '刚刚导入' }) }
        }
      } else { const content = await file.text(); imported.push({ id: Date.now(), title: file.name.replace(/\.(md|txt)$/i, ''), excerpt: content.slice(0, 90), content, tag: '复盘', updated: '刚刚导入' }) }
    } catch { notify('导入失败：文件损坏或不是有效 ZIP / Markdown'); return }
    if (!imported.length) { notify('没有可导入的 Markdown 或 TXT 笔记'); return }
    try {
      await setNotes([...imported, ...notes])
      notify(`已保存 ${imported.length} 条导入笔记，原有内容未覆盖`)
    } catch (error) { reportBackendError(error) }
  }
  return <div className="page library-page">
    <div className="page-heading"><div><h1>笔记与 Wiki</h1><p>自由笔记、原始资料与可核验的库内引用问答。</p></div><div className="heading-actions">{tab === '笔记' && <Button variant="secondary" onClick={() => zipRef.current?.click()}><FileArchive size={17} />批量导入</Button>}{tab === '笔记' ? <Button onClick={() => openNote()}><NotebookPen size={18} />新建笔记</Button> : <Button disabled title="原生材料导入与解析尚未接入"><Upload size={18} />材料导入待接入</Button>}</div></div>
    <input ref={zipRef} type="file" className="visually-hidden" accept=".zip,.md,.txt" onChange={e => { const file = e.target.files?.[0]; if (file) importNotes(file); e.target.value = '' }} />
    <div className="library-toolbar"><div className="library-tabs">{['笔记', '资料库', 'Wiki 问答'].map(item => <button key={item} className={tab === item ? 'active' : ''} onClick={() => { setTab(item); setFilter('全部') }}>{item === '笔记' ? <NotebookPen size={17} /> : item === '资料库' ? <Folder size={17} /> : <BookOpen size={17} />}{item}<span>{item === '笔记' ? notes.length : item === '资料库' ? resources.length : ''}</span></button>)}</div>{tab !== 'Wiki 问答' && <div className="search-field"><Search size={17} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder={`搜索${tab}…`} aria-label={`搜索${tab}`} />{query && <button aria-label="清除搜索" onClick={() => setQuery('')}><X size={15} /></button>}</div>}</div>
    {tab !== 'Wiki 问答' && <div className="filter-row">{(tab === '笔记' ? ['全部', '概率论', '数据结构', '英语', '复盘'] : ['全部', 'PDF', '视频', '网页', '音频', '文件']).map(item => <button className={filter === item ? 'active' : ''} key={item} onClick={() => setFilter(item)}>{item}</button>)}<span />{tab === '笔记' && <Button variant="ghost" size="sm" onClick={exportNotes}><Download size={16} />ZIP 导出</Button>}</div>}
    {tab === '笔记' && <>{filteredNotes.length ? <div className="notes-grid">{filteredNotes.map(note => <button className="note-card" key={note.id} onClick={() => openNote(note)}><div className="note-card-top"><Badge tone="neutral">{note.tag}</Badge><span>{note.updated}</span></div><h2>{note.title}</h2><p>{note.excerpt}</p><div className="note-card-bottom"><span><Link2 size={14} />知识关联待接入</span><ChevronRight size={17} /></div></button>)}</div> : <EmptyState title="没有找到匹配的笔记" description="尝试清除搜索词或切换标签，也可以现在创建一条新笔记。" action={<Button onClick={() => openNote()}>新建笔记</Button>} />}</>}
    {tab === '资料库' && <>{filteredResources.length ? <div className="resource-list">{filteredResources.map(resource => <button className="resource-row" key={resource.id} onClick={() => { setActiveResource(resource); setResourceTab('解析文本') }}><span className="resource-type">{resource.type === 'PDF' ? <FileText size={22} /> : resource.type === '视频' ? <Film size={22} /> : resource.type === '网页' ? <Globe2 size={22} /> : resource.type === '音频' ? <FileAudio size={22} /> : <File size={22} />}</span><div className="resource-copy"><div><Badge tone="neutral">{resource.type}</Badge><span>{resource.meta}</span></div><h3>{resource.title}</h3><ProgressBar value={resource.progress} /></div><span className="resource-percent">{resource.progress}%</span><ChevronRight size={18} /></button>)}</div> : <EmptyState title="这里还没有匹配的资料" description="当前没有资料元数据；原件导入将在原生材料切片接入。" action={<Button disabled>材料导入待接入</Button>} />}</>}
    {tab === 'Wiki 问答' && <WikiPanel resources={resources} notify={notify} />}
    <section className="library-bottom"><div><Tag size={18} /><span>知识库概览</span></div><p><strong>{notes.length + resources.length}</strong> 个条目 · 原件解析与知识关联尚未接入</p><button disabled title="知识库分析尚未接入">知识库检查待接入<ArrowDownToLine size={16} /></button></section>

    <Modal open={noteOpen} onClose={closeNote} title={activeNote ? '编辑笔记' : '新建笔记'} wide><div className="note-editor"><div className="editor-meta"><input value={noteTitle} onChange={e => { setNoteTitle(e.target.value); setDirty(true) }} placeholder="笔记标题" aria-label="笔记标题" /><select value={noteTag} onChange={e => { setNoteTag(e.target.value); setDirty(true) }} aria-label="笔记标签"><option>概率论</option><option>数据结构</option><option>英语</option><option>复盘</option></select></div>{draftRestored && <div className="draft-notice"><Check size={15} />已恢复未保存草稿；本地草稿与离开警告双重保护。</div>}<div className="editor-toolbar"><span>Markdown + GFM + LaTeX</span><button onClick={() => { setNoteContent(noteContent + '\n\n## 小标题'); setDirty(true) }}>H2</button><button onClick={() => { setNoteContent(noteContent + ' **重点** '); setDirty(true) }}>加粗</button><button onClick={() => { setNoteContent(noteContent + '\n- 列表项'); setDirty(true) }}>列表</button><button onClick={() => { setNoteContent(noteContent + '\n```ts\n// 代码\n```'); setDirty(true) }}>代码</button><button onClick={() => { setNoteContent(noteContent + '\n$$P(A|B)=\\frac{P(B|A)P(A)}{P(B)}$$'); setDirty(true) }}>公式</button></div><div className="note-editor-grid"><textarea value={noteContent} onChange={e => { setNoteContent(e.target.value); setDirty(true) }} placeholder="用自己的话记录理解，而不只是摘抄…" aria-label="笔记 Markdown 正文" /><article className="note-preview"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{noteContent || '实时预览将在这里显示。'}</ReactMarkdown></article></div><div className="editor-footer"><span>{noteContent.length} 字 · {dirty ? '草稿自动暂存中' : '已保存'}</span><div>{activeNote && <Button variant="ghost" disabled={saving} onClick={deleteNote}><Trash2 size={16} />删除</Button>}<Button onClick={saveNote} disabled={saving || !noteTitle.trim() || !noteContent.trim()}>保存笔记</Button></div></div></div></Modal>
    <Modal open={discardOpen} onClose={() => { if (!saving) setDiscardOpen(false) }} title="关闭但保留草稿？"><div className="confirm-dialog"><p>保留时会先等待原生后端写入；丢弃时会删除后端草稿。</p><div><Button variant="ghost" disabled={saving} onClick={() => { void settleDraft(false) }}>丢弃草稿</Button><Button disabled={saving} onClick={() => { void settleDraft(true) }}>保留草稿并关闭</Button></div></div></Modal>
    <Modal open={Boolean(activeResource)} onClose={() => setActiveResource(null)} title="KB 文档元数据" wide>{activeResource && <div className="resource-detail"><div className="resource-detail-head"><FileText size={25} /><div><h2>{activeResource.title}</h2><p>{activeResource.meta}</p></div><Badge>未解析</Badge></div><div className="library-tabs">{['解析文本', '原件信息'].map(item => <button className={resourceTab === item ? 'active' : ''} key={item} onClick={() => setResourceTab(item)}>{item}</button>)}</div>{resourceTab === '解析文本' ? <div className="parsed-text"><p>此资料尚未由原生材料解析器处理，暂无解析文本或引用锚点。</p></div> : <div className="original-info"><div><span>已保存的元数据</span><strong>{activeResource.meta || '无'}</strong></div><div><span>原件与内容校验</span><strong>尚未接入原生材料处理</strong></div><Button variant="secondary" disabled title="原生解析尚未接入">重新解析待接入</Button><Button variant="ghost" onClick={() => { void setResources(resources.filter(r => r.id !== activeResource.id)).then(() => { setActiveResource(null); notify('资料元数据已删除') }).catch(reportBackendError) }}><Trash2 size={16} />删除元数据</Button></div>}</div>}</Modal>
  </div>
}
