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

function saveBlob(blob: Blob, name: string) { const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000) }

export default function Library({ notes, setNotes, resources, setResources, notify }: { notes: Note[]; setNotes: (notes: Note[]) => void; resources: Resource[]; setResources: (resources: Resource[]) => void; notify: (message: string) => void }) {
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
  const fileRef = useRef<HTMLInputElement>(null)
  const zipRef = useRef<HTMLInputElement>(null)
  const filteredNotes = useMemo(() => notes.filter(n => (filter === '全部' || n.tag === filter) && `${n.title}${n.excerpt}${n.content}`.toLowerCase().includes(query.toLowerCase())), [notes, query, filter])
  const filteredResources = useMemo(() => resources.filter(r => (filter === '全部' || r.type === filter) && r.title.toLowerCase().includes(query.toLowerCase())), [resources, query, filter])
  const draftKey = `deep-tutor-draft-${activeNote?.id || 'new'}`

  useEffect(() => {
    if (!noteOpen || !dirty) return
    const timer = window.setTimeout(() => localStorage.setItem(draftKey, JSON.stringify({ title: noteTitle, content: noteContent, tag: noteTag, savedAt: Date.now() })), 350)
    return () => window.clearTimeout(timer)
  }, [noteOpen, dirty, noteTitle, noteContent, noteTag, draftKey])
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => { if (noteOpen && dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', handler); return () => window.removeEventListener('beforeunload', handler)
  }, [noteOpen, dirty])

  const openNote = (note?: Note) => {
    const key = `deep-tutor-draft-${note?.id || 'new'}`
    const draft = localStorage.getItem(key)
    setActiveNote(note || null); setDraftRestored(Boolean(draft))
    if (draft) { try { const value = JSON.parse(draft); setNoteTitle(value.title); setNoteContent(value.content); setNoteTag(value.tag); setDirty(true) } catch { /* ignore malformed local draft */ } }
    else { setNoteTitle(note?.title || ''); setNoteContent(note?.content || ''); setNoteTag(note?.tag || '概率论'); setDirty(false) }
    setNoteOpen(true)
  }
  const closeNote = () => { if (dirty) setDiscardOpen(true); else setNoteOpen(false) }
  const saveNote = () => {
    if (!noteTitle.trim() || !noteContent.trim()) return
    if (activeNote) setNotes(notes.map(n => n.id === activeNote.id ? { ...n, title: noteTitle.trim(), content: noteContent.trim(), excerpt: noteContent.trim().slice(0, 90), tag: noteTag, updated: '刚刚' } : n))
    else setNotes([{ id: Date.now(), title: noteTitle.trim(), content: noteContent.trim(), excerpt: noteContent.trim().slice(0, 90), tag: noteTag, updated: '刚刚' }, ...notes])
    localStorage.removeItem(draftKey); setDirty(false); setNoteOpen(false); notify(activeNote ? '笔记修改已保存' : '新笔记已创建')
  }
  const deleteNote = () => {
    if (!activeNote) return
    setNotes(notes.filter(n => n.id !== activeNote.id)); localStorage.removeItem(draftKey); setDirty(false); setNoteOpen(false); notify('笔记已删除')
  }
  const exportNotes = async () => {
    const { default: JSZip } = await import('jszip')
    const zip = new JSZip(); const folder = zip.folder('知舟笔记')
    notes.forEach((note, i) => folder?.file(`${String(i + 1).padStart(2, '0')}-${note.title.replace(/[\\/:*?"<>|]/g, '_')}.md`, `# ${note.title}\n\n标签：${note.tag}\n更新时间：${note.updated}\n\n${note.content}`))
    folder?.file('manifest.json', JSON.stringify({ version: 1, count: notes.length, exportedAt: new Date().toISOString() }, null, 2))
    saveBlob(await zip.generateAsync({ type: 'blob' }), 'DeepTutor-notes.zip'); notify(`已导出 ${notes.length} 条 Markdown 笔记与清单`)
  }
  const importNotes = async (file: File) => {
    try {
      const imported: Note[] = []
      if (file.name.endsWith('.zip')) {
        const { default: JSZip } = await import('jszip')
        const zip = await JSZip.loadAsync(file)
        for (const [path, entry] of Object.entries(zip.files)) {
          if (!entry.dir && /\.(md|txt)$/i.test(path) && !path.endsWith('manifest.json')) { const content = await entry.async('text'); const title = path.split('/').pop()?.replace(/\.(md|txt)$/i, '') || '导入笔记'; imported.push({ id: Date.now() + imported.length, title, excerpt: content.slice(0, 90), content, tag: '复盘', updated: '刚刚导入' }) }
        }
      } else { const content = await file.text(); imported.push({ id: Date.now(), title: file.name.replace(/\.(md|txt)$/i, ''), excerpt: content.slice(0, 90), content, tag: '复盘', updated: '刚刚导入' }) }
      if (imported.length) { setNotes([...imported, ...notes]); notify(`已追加导入 ${imported.length} 条笔记，原有内容未覆盖`) } else notify('压缩包中没有可导入的 Markdown 或 TXT 笔记')
    } catch { notify('导入失败：文件损坏或不是有效 ZIP / Markdown') }
  }
  return <div className="page library-page">
    <div className="page-heading"><div><h1>笔记与 Wiki</h1><p>自由笔记、原始资料与可核验的库内引用问答。</p></div><div className="heading-actions">{tab === '笔记' && <Button variant="secondary" onClick={() => zipRef.current?.click()}><FileArchive size={17} />批量导入</Button>}<Button onClick={() => tab === '笔记' ? openNote() : fileRef.current?.click()}>{tab === '笔记' ? <NotebookPen size={18} /> : <Upload size={18} />}{tab === '笔记' ? '新建笔记' : '导入资料'}</Button></div></div>
    <input ref={fileRef} type="file" className="visually-hidden" accept=".pdf,.docx,.pptx,.txt,.md,.epub,image/*,audio/*,video/*,.zip" onChange={e => { const file = e.target.files?.[0]; if (file) { setResources([{ id: Date.now(), title: file.name, type: '文件', meta: `${Math.max(1, Math.round(file.size / 1024))} KB · 原件已暂存`, progress: 0 }, ...resources]); notify(`已暂存「${file.name}」，确认后进入解析队列`); e.target.value = '' } }} />
    <input ref={zipRef} type="file" className="visually-hidden" accept=".zip,.md,.txt" onChange={e => { const file = e.target.files?.[0]; if (file) importNotes(file); e.target.value = '' }} />
    <div className="library-toolbar"><div className="library-tabs">{['笔记', '资料库', 'Wiki 问答'].map(item => <button key={item} className={tab === item ? 'active' : ''} onClick={() => { setTab(item); setFilter('全部') }}>{item === '笔记' ? <NotebookPen size={17} /> : item === '资料库' ? <Folder size={17} /> : <BookOpen size={17} />}{item}<span>{item === '笔记' ? notes.length : item === '资料库' ? resources.length : ''}</span></button>)}</div>{tab !== 'Wiki 问答' && <div className="search-field"><Search size={17} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder={`搜索${tab}…`} aria-label={`搜索${tab}`} />{query && <button aria-label="清除搜索" onClick={() => setQuery('')}><X size={15} /></button>}</div>}</div>
    {tab !== 'Wiki 问答' && <div className="filter-row">{(tab === '笔记' ? ['全部', '概率论', '数据结构', '英语', '复盘'] : ['全部', 'PDF', '视频', '网页', '音频', '文件']).map(item => <button className={filter === item ? 'active' : ''} key={item} onClick={() => setFilter(item)}>{item}</button>)}<span />{tab === '笔记' && <Button variant="ghost" size="sm" onClick={exportNotes}><Download size={16} />ZIP 导出</Button>}</div>}
    {tab === '笔记' && <>{filteredNotes.length ? <div className="notes-grid">{filteredNotes.map(note => <button className="note-card" key={note.id} onClick={() => openNote(note)}><div className="note-card-top"><Badge tone="neutral">{note.tag}</Badge><span>{note.updated}</span></div><h2>{note.title}</h2><p>{note.excerpt}</p><div className="note-card-bottom"><span><Link2 size={14} />关联 3 个知识点</span><ChevronRight size={17} /></div></button>)}</div> : <EmptyState title="没有找到匹配的笔记" description="尝试清除搜索词或切换标签，也可以现在创建一条新笔记。" action={<Button onClick={() => openNote()}>新建笔记</Button>} />}</>}
    {tab === '资料库' && <>{filteredResources.length ? <div className="resource-list">{filteredResources.map(resource => <button className="resource-row" key={resource.id} onClick={() => { setActiveResource(resource); setResourceTab('解析文本') }}><span className="resource-type">{resource.type === 'PDF' ? <FileText size={22} /> : resource.type === '视频' ? <Film size={22} /> : resource.type === '网页' ? <Globe2 size={22} /> : resource.type === '音频' ? <FileAudio size={22} /> : <File size={22} />}</span><div className="resource-copy"><div><Badge tone="neutral">{resource.type}</Badge><span>{resource.meta}</span></div><h3>{resource.title}</h3><ProgressBar value={resource.progress} /></div><span className="resource-percent">{resource.progress}%</span><ChevronRight size={18} /></button>)}</div> : <EmptyState title="这里还没有匹配的资料" description="导入后可在详情中管理原件、解析状态与解析文本。" action={<Button onClick={() => fileRef.current?.click()}>导入资料</Button>} />}</>}
    {tab === 'Wiki 问答' && <WikiPanel resources={resources} notify={notify} />}
    <section className="library-bottom"><div><Tag size={18} /><span>知识库概览</span></div><p><strong>{notes.length + resources.length}</strong> 个条目 · <strong>18</strong> 个关联知识点 · 方案生成后自动去重沉淀</p><button onClick={() => notify('知识库检查完成：3 条笔记可补充来源，未发现重复文档')}>检查知识库<ArrowDownToLine size={16} /></button></section>

    <Modal open={noteOpen} onClose={closeNote} title={activeNote ? '编辑笔记' : '新建笔记'} wide><div className="note-editor"><div className="editor-meta"><input value={noteTitle} onChange={e => { setNoteTitle(e.target.value); setDirty(true) }} placeholder="笔记标题" aria-label="笔记标题" /><select value={noteTag} onChange={e => { setNoteTag(e.target.value); setDirty(true) }} aria-label="笔记标签"><option>概率论</option><option>数据结构</option><option>英语</option><option>复盘</option></select></div>{draftRestored && <div className="draft-notice"><Check size={15} />已恢复未保存草稿；本地草稿与离开警告双重保护。</div>}<div className="editor-toolbar"><span>Markdown + GFM + LaTeX</span><button onClick={() => { setNoteContent(noteContent + '\n\n## 小标题'); setDirty(true) }}>H2</button><button onClick={() => { setNoteContent(noteContent + ' **重点** '); setDirty(true) }}>加粗</button><button onClick={() => { setNoteContent(noteContent + '\n- 列表项'); setDirty(true) }}>列表</button><button onClick={() => { setNoteContent(noteContent + '\n```ts\n// 代码\n```'); setDirty(true) }}>代码</button><button onClick={() => { setNoteContent(noteContent + '\n$$P(A|B)=\\frac{P(B|A)P(A)}{P(B)}$$'); setDirty(true) }}>公式</button></div><div className="note-editor-grid"><textarea value={noteContent} onChange={e => { setNoteContent(e.target.value); setDirty(true) }} placeholder="用自己的话记录理解，而不只是摘抄…" aria-label="笔记 Markdown 正文" /><article className="note-preview"><ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{noteContent || '实时预览将在这里显示。'}</ReactMarkdown></article></div><div className="editor-footer"><span>{noteContent.length} 字 · {dirty ? '草稿自动暂存中' : '已保存'}</span><div>{activeNote && <Button variant="ghost" onClick={deleteNote}><Trash2 size={16} />删除</Button>}<Button onClick={saveNote} disabled={!noteTitle.trim() || !noteContent.trim()}>保存笔记</Button></div></div></div></Modal>
    <Modal open={discardOpen} onClose={() => setDiscardOpen(false)} title="关闭但保留草稿？"><div className="confirm-dialog"><p>未保存内容已暂存在浏览器。你可以保留草稿稍后继续，或明确丢弃。</p><div><Button variant="ghost" onClick={() => { localStorage.removeItem(draftKey); setDirty(false); setDiscardOpen(false); setNoteOpen(false) }}>丢弃草稿</Button><Button onClick={() => { setDirty(false); setDiscardOpen(false); setNoteOpen(false); notify('草稿已保留，下次打开会自动恢复') }}>保留草稿并关闭</Button></div></div></Modal>
    <Modal open={Boolean(activeResource)} onClose={() => setActiveResource(null)} title="KB 文档管理" wide>{activeResource && <div className="resource-detail"><div className="resource-detail-head"><FileText size={25} /><div><h2>{activeResource.title}</h2><p>{activeResource.meta}</p></div><Badge tone="green">已去重</Badge></div><div className="library-tabs">{['解析文本', '原件信息'].map(item => <button className={resourceTab === item ? 'active' : ''} key={item} onClick={() => setResourceTab(item)}>{item}</button>)}</div>{resourceTab === '解析文本' ? <div className="parsed-text"><div><span>fast 解析 · 12,420 字符</span><Button variant="secondary" size="sm" onClick={() => { saveBlob(new Blob(['贝叶斯定理解析文本\n\n先验概率、似然与后验概率。'], { type: 'text/plain' }), 'DeepTutor-parsed-text.txt'); notify('解析文本已下载') }}><Download size={15} />下载文本</Button></div><p>第四章　条件概率与贝叶斯定理</p><p>贝叶斯定理描述了在获得新证据后如何更新事件的概率判断。设事件 A 与 B 满足相应概率条件，则后验概率可由先验概率与似然计算……</p><blockquote>引用锚点：chapter-04 / block-018，可用于 Wiki 白名单定位。</blockquote></div> : <div className="original-info">{[['原件状态','已暂存，未上传服务器'],['内容哈希','sha256:a91f…7c2e'],['解析状态','已完成，可随时重新解析'],['安全校验','文件类型与大小检查通过']].map(([name,value])=><div key={name}><span>{name}</span><strong>{value}</strong></div>)}<Button variant="secondary" onClick={() => notify('已重新进入解析队列，原件保留不变')}>重新解析</Button><Button variant="ghost" onClick={() => { setResources(resources.filter(r => r.id !== activeResource.id)); setActiveResource(null); notify('文档及其解析文本已删除') }}><Trash2 size={16} />删除文档</Button></div>}</div>}</Modal>
  </div>
}
