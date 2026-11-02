import { useEffect, useState } from 'react'
import { AlertTriangle, ArrowDownToLine, ArrowLeft, ArrowRight, BookOpen, Brain, Check, CheckCircle2, ChevronRight, Circle, Clipboard, Clock3, CloudUpload, Code2, File, FileText, Globe2, Hash, Layers3, Link2, LoaderCircle, Network, Pause, Play, Plus, RefreshCw, Route, Search, ShieldCheck, Sparkles, Square, Trash2, Upload, X, Zap } from 'lucide-react'
import type { PageId } from '../data'
import { Badge, Button, IconButton, Modal, ProgressBar, SectionHeader } from '../components/UI'
import ExportCenter from '../components/ExportCenter'

type Source = { id: number; type: string; name: string; meta: string; status: '暂存' | '已确认' | '解析中' | '已解析' | '失败'; hash: string }
const toolNames = ['解析资料', '澄清意图', '构建知识图谱', '设计渐进阶段', '检索学习资源', '确定性排期', '五维校验', '方案落盘']
const initialSources: Source[] = [{ id: 1, type: 'PDF', name: '概率论与数理统计_第四章.pdf', meta: '2.8 MB · 48 页', status: '已解析', hash: 'a91f…7c2e' }]

export default function PlanGenerator({ onNavigate, notify }: { onNavigate: (page: PageId) => void; notify: (message: string) => void }) {
  const [step, setStep] = useState(0)
  const [sources, setSources] = useState<Source[]>(initialSources)
  const [sourceTab, setSourceTab] = useState('文件上传')
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [depth, setDepth] = useState('fast')
  const [goal, setGoal] = useState('掌握贝叶斯定理，并能应用到简单分类问题')
  const [level, setLevel] = useState('学过概率基础，但不熟悉贝叶斯应用')
  const [minutes, setMinutes] = useState(45)
  const [deadline, setDeadline] = useState('2026-11-23')
  const [clarify, setClarify] = useState('')
  const [progress, setProgress] = useState(0)
  const [running, setRunning] = useState(false)
  const [interrupted, setInterrupted] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [regenOpen, setRegenOpen] = useState(false)
  const [regenScope, setRegenScope] = useState('资源检索')
  const [regenCount, setRegenCount] = useState(0)
  const [eventId, setEventId] = useState(128)
  const activeTool = Math.min(7, Math.floor(progress / 12.5))

  useEffect(() => {
    if (step !== 2 || !running || interrupted) return
    const timer = window.setInterval(() => {
      setProgress(prev => {
        const next = Math.min(100, prev + 4)
        setEventId(id => id + 1)
        if (next === 100) { setRunning(false); window.setTimeout(() => setStep(3), 350) }
        return next
      })
    }, 260)
    return () => window.clearInterval(timer)
  }, [step, running, interrupted])

  const addSource = (type: string, name: string, meta: string) => {
    const hash = `sha256:${Array.from(name).reduce((a, c) => (a + c.charCodeAt(0)) % 65535, 0).toString(16)}`
    if (sources.some(s => s.name === name)) { notify('检测到相同 content_hash，已阻止重复添加'); return }
    setSources([...sources, { id: Date.now(), type, name, meta, status: '暂存', hash }]); setText(''); setUrl('')
  }
  const parseSources = () => {
    if (!sources.length) return
    setSources(sources.map(s => ({ ...s, status: '解析中' })))
    window.setTimeout(() => { setSources(sources.map(s => ({ ...s, status: '已解析' }))); notify('解析完成：48 页、12 个候选知识节点；无重复资料') }, 800)
  }
  const startGeneration = () => {
    if (!goal.trim() || !level.trim() || !minutes || !deadline) { setClarify('还需要补充目标、当前水平、每日时长与截止日，才能继续。'); return }
    setClarify(''); setProgress(0); setRunning(true); setInterrupted(false); setStep(2)
  }
  const regenerate = () => {
    if (regenCount >= 2) return
    setRegenCount(regenCount + 1); setRegenOpen(false); setProgress(50); setRunning(true); setStep(2); notify(`已按「${regenScope}」局部重生成，级联更新后续排期与校验`)
  }
  return <div className="page generator-page">
    <div className="page-heading"><div><h1>AI 学习方案生成</h1><p>资料先暂存，目标再澄清；每一步都可查看、取消与局部重做。</p></div><Badge tone="green"><ShieldCheck size={14} />本地流程演示</Badge></div>
    <div className="generator-stepper">{['输入资料', '澄清目标', '实时生成', '确认方案'].map((name, i) => <button key={name} className={`${step === i ? 'active' : ''} ${step > i ? 'done' : ''}`} onClick={() => { if (i < step) setStep(i) }} disabled={i > step}><span>{step > i ? <Check size={15} /> : i + 1}</span><strong>{name}</strong>{i < 3 && <ChevronRight size={15} />}</button>)}</div>

    {step === 0 && <div className="generator-source-layout"><main className="surface source-workspace"><SectionHeader title="资料输入" description="支持四种来源；文件先暂存，确认后才开始解析。" action={<Badge tone="neutral">{sources.length} 份资料</Badge>} /><div className="source-tabs">{['文件上传', '纯文本', 'URL 抓取', '浏览器剪藏'].map(item => <button className={sourceTab === item ? 'active' : ''} key={item} onClick={() => setSourceTab(item)}>{item === '文件上传' ? <Upload size={16} /> : item === '纯文本' ? <FileText size={16} /> : item === 'URL 抓取' ? <Globe2 size={16} /> : <Clipboard size={16} />}{item}</button>)}</div>
      {sourceTab === '文件上传' && <label className="source-dropzone"><CloudUpload size={28} /><strong>选择或拖入学习资料</strong><p>PDF / DOCX / PPTX / TXT / MD / EPUB / 图片 / 音视频 / ZIP</p><span>单文件不超过 50 MB，将校验 magic bytes 与 ZIP 安全性</span><input type="file" multiple accept=".pdf,.docx,.pptx,.txt,.md,.epub,image/*,audio/*,video/*,.zip" onChange={e => { Array.from(e.target.files || []).forEach(file => { if (file.size <= 50 * 1024 * 1024) addSource(file.name.split('.').pop()?.toUpperCase() || '文件', file.name, `${(file.size / 1024 / 1024).toFixed(2)} MB · 待确认`); else notify(`「${file.name}」超过 50 MB，已拒绝`) }); e.target.value = '' }} /></label>}
      {sourceTab === '纯文本' && <div className="source-text-input"><textarea rows={8} value={text} onChange={e => setText(e.target.value.slice(0, 500000))} placeholder="粘贴教材章节、课程提纲或已有笔记…" /><div><span>{text.length.toLocaleString()} / 500,000 字符</span><Button onClick={() => text.trim() && addSource('文本', `粘贴文本_${sources.length + 1}`, `${text.length} 字符`)} disabled={!text.trim()}>加入暂存</Button></div></div>}
      {sourceTab === 'URL 抓取' && <div className="source-url-input"><label>公开网页地址<div><Link2 size={17} /><input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://example.com/chapter" /></div></label><p>仅允许 http/https；会阻止内网地址、重定向到私网与不安全的抓取目标。</p><Button onClick={() => { try { const u = new URL(url); if (!['http:', 'https:'].includes(u.protocol)) throw new Error(); addSource('网页', u.hostname, '公开 URL · 待安全验证') } catch { notify('请输入有效的 http/https 公开地址') } }} disabled={!url.trim()}>验证并暂存</Button></div>}
      {sourceTab === '浏览器剪藏' && <div className="clipper-panel"><Code2 size={25} /><h3>从浏览器一键带入</h3><p>安装 MV3 扩展后，可右键剪藏选中文字或整页。当前也可直接粘贴剪藏内容。</p><div><Button variant="secondary" onClick={async () => { try { const value = await navigator.clipboard.readText(); if (value) addSource('剪藏', `浏览器剪藏_${sources.length + 1}`, `${value.length} 字符`); else notify('剪贴板没有可用文本') } catch { notify('浏览器未授权读取剪贴板，请改用纯文本粘贴') } }}><Clipboard size={16} />读取剪贴板</Button><Button variant="ghost" onClick={() => notify('扩展安装说明已加入开发者资源页')}>查看扩展说明</Button></div></div>}
      <div className="parse-options"><label>解析深度<select value={depth} onChange={e => setDepth(e.target.value)}><option value="fast">fast · 快速文本解析</option><option value="full">full · 深度 PDF 版面解析</option></select></label><span><Hash size={15} />content_hash 去重</span><Button variant="secondary" onClick={parseSources} disabled={!sources.length || sources.some(s => s.status === '解析中')}>确认并解析</Button></div>
    </main><aside className="surface staging-panel"><SectionHeader title="暂存与解析" description="暂存 → 确认 → 解析" /><div className="source-list">{sources.map(source => <div className="source-item" key={source.id}><span className="source-file-icon">{source.status === '解析中' ? <LoaderCircle size={18} className="spin" /> : <File size={18} />}</span><div><strong>{source.name}</strong><span>{source.meta}</span><small>{source.hash}</small></div><Badge tone={source.status === '已解析' ? 'green' : source.status === '暂存' ? 'amber' : 'neutral'}>{source.status}</Badge><IconButton label={`移除${source.name}`} onClick={() => setSources(sources.filter(s => s.id !== source.id))}><Trash2 size={15} /></IconButton></div>)}</div><div className="staging-bottom"><span>{sources.filter(s => s.status === '已解析').length} 份已就绪</span><Button onClick={() => setStep(1)} disabled={!sources.some(s => s.status === '已解析')}>继续澄清目标<ArrowRight size={16} /></Button></div></aside></div>}

    {step === 1 && <div className="clarify-layout"><section className="surface clarify-form"><SectionHeader title="先确认目标，再开始生成" description="权威结构化输入会覆盖模型从资料中猜测的时间与水平。" /><div className="form-stack"><label>我想学会什么？<textarea rows={3} value={goal} onChange={e => setGoal(e.target.value)} /></label><label>我的当前基础<textarea rows={3} value={level} onChange={e => setLevel(e.target.value)} placeholder="例如：会计算基本概率，但没接触过条件概率" /></label><div className="form-grid"><label>每日可投入（分钟）<input type="number" min={10} max={600} value={minutes} onChange={e => setMinutes(Number(e.target.value))} /></label><label>希望完成日期<input type="date" value={deadline} onChange={e => setDeadline(e.target.value)} /></label></div><label>里程碑硬约束（可选）<input placeholder="例如：11 月 20 日前必须完成一次综合测验" /></label>{clarify && <div className="clarify-warning"><AlertTriangle size={17} />{clarify}</div>}<div className="clarify-summary"><Brain size={19} /><div><strong>预计生成范围</strong><p>3–6 个渐进阶段 · 不超过 100 个知识节点 · 每日 {minutes} 分钟 · {deadline} 前完成</p></div></div><div className="generator-actions"><Button variant="secondary" onClick={() => setStep(0)}><ArrowLeft size={16} />返回资料</Button><Button onClick={startGeneration}><Sparkles size={17} />开始生成方案</Button></div></div></section><aside className="surface clarify-side"><h3>接下来会发生什么</h3>{[['知识图谱','断环、去悬空边，标注难度与必学项'],['阶段设计','按难度阶梯安排 3–6 个阶段'],['资源检索','并发验证与相关性筛选，宁缺勿滥'],['排期校验','容量不足会警告，但不会阻止交付']].map(([name,desc],i)=><div key={name}><span>0{i+1}</span><p><strong>{name}</strong>{desc}</p></div>)}</aside></div>}

    {step === 2 && <div className="generation-layout"><section className="surface generation-live"><div className="generation-status"><div><span className="live-indicator"><i />{interrupted ? '连接中断' : running ? 'SSE 实时生成中' : '已暂停'}</span><h2>{running ? '正在构建你的学习方案' : progress === 100 ? '方案生成完成' : '生成已暂停'}</h2><p>事件 #{eventId} · 刷新后按 Last-Event-ID 续接，不重复计费</p></div><strong>{progress}%</strong></div><ProgressBar value={progress} /><div className="tool-run-list">{toolNames.map((name, i) => <div className={`${i < activeTool || progress === 100 ? 'done' : i === activeTool && running ? 'running' : ''}`} key={name}><span>{i < activeTool || progress === 100 ? <Check size={15} /> : i === activeTool && running ? <LoaderCircle size={15} className="spin" /> : <Circle size={15} />}</span><strong>{name}</strong><small>{i < activeTool || progress === 100 ? '已完成' : i === activeTool && running ? '处理中…' : '等待'}</small>{i <= activeTool && <code>{(1200 + i * 317).toLocaleString()} tok</code>}</div>)}</div><div className="generation-controls">{running ? <Button variant="secondary" onClick={() => setRunning(false)}><Pause size={16} />取消生成</Button> : <Button onClick={() => { setRunning(true); setInterrupted(false) }} disabled={progress === 100}><Play size={16} />{progress ? '失败重试 / 继续' : '开始'}</Button>}<Button variant="ghost" onClick={() => { setInterrupted(!interrupted); notify(interrupted ? '连接已恢复，从最后事件继续' : '已模拟断线，运行状态保留') }}><RefreshCw size={16} />{interrupted ? '恢复连接' : '模拟断线续接'}</Button></div></section><aside className="surface generation-preview"><SectionHeader title="部分方案预览" description="已生成的内容可以先行查看。" /><div className="preview-metrics"><div><strong>{Math.min(18, Math.floor(progress / 5))}</strong><span>知识节点</span></div><div><strong>{Math.min(4, Math.floor(progress / 25))}</strong><span>学习阶段</span></div><div><strong>${(progress * .00042).toFixed(3)}</strong><span>本轮成本</span></div></div>{progress > 20 && <div className="partial-outline"><span>阶段 1</span><h3>建立概率与条件判断直觉</h3><p>概率基础 → 条件概率 → 独立性</p></div>}{progress > 55 && <div className="partial-outline"><span>阶段 2</span><h3>理解并应用贝叶斯更新</h3><p>先验 → 似然 → 后验 → 低基线率案例</p></div>}<div className="cost-ledger"><Zap size={15} />已消耗 {(progress * 86).toLocaleString()} tokens；取消路径也会如实入账。</div></aside></div>}

    {step === 3 && <div className="plan-result"><section className="result-banner"><div><CheckCircle2 size={28} /><div><h2>学习方案已生成，可以交付。</h2><p>版本 v{regenCount + 1} · 已沉淀至知识库 · 终态可幂等回放</p></div></div><Badge tone="amber">含 1 条排期提醒</Badge></section><div className="result-grid"><main><section className="surface result-stages"><SectionHeader title="四阶段学习路径" description="里程碑为硬约束，难度按 1–5 渐进。" action={<Button variant="secondary" size="sm" onClick={() => setRegenOpen(true)} disabled={regenCount >= 2}><RefreshCw size={15} />局部重生成 ({2 - regenCount} 次)</Button>} />{[['建立概率直觉','2 天 · 4 个节点','概率基础、样本空间、独立性',1],['理解条件概率','4 天 · 5 个节点','条件概率、乘法公式、全概率',2],['掌握贝叶斯更新','7 天 · 6 个节点','先验、似然、后验、低基线率',3],['完成分类项目','8 天 · 3 个节点','朴素贝叶斯、模型评估、复盘',4]].map(([name,meta,nodes,difficulty],i)=><div className="result-stage" key={String(name)}><span className="stage-index">0{i+1}</span><div><h3>{name}</h3><p>{nodes}</p><small>{meta} · 难度 {difficulty}/5</small></div><Badge tone={i===2?'green':'neutral'}>{i===2?'核心阶段':'里程碑'}</Badge></div>)}</section><section className="surface validation-panel"><SectionHeader title="五维校验" description="未通过项会定向重跑，最多两轮；仍不过则带警告交付。" /><div className="validation-grid">{[['依赖闭包','通过'],['难度阶梯','通过'],['认知负荷','通过'],['里程碑可达','通过'],['缺口分析','通过']].map(([name,status])=><div key={name}><Check size={16} /><span>{name}</span><strong>{status}</strong></div>)}</div><div className="capacity-warning"><AlertTriangle size={18} /><div><strong>排期容量提醒</strong><p>项目阶段预留时间比建议值少 2 小时。已按每日 45 分钟确定性排期，仍可按期交付，但建议减少一次拓展阅读。</p></div></div></section></main><aside><section className="surface result-resources"><SectionHeader title="筛选后的资源" description="12 个候选，仅保留 4 个高相关资源。" />{[['教材第 4 章','本地资料 · 高相关'],['贝叶斯直觉实验','公开视频 · 已验证'],['低基线率互动案例','网页 · 已验证'],['分类器项目数据集','CSV · 已验证']].map(([name,meta])=><button key={name} onClick={() => notify(`已打开资源：${name}`)}><BookOpen size={17} /><div><strong>{name}</strong><span>{meta}</span></div><ChevronRight size={15} /></button>)}<p>搜索不可用时会降级为关键词建议，不虚构资源链接。</p></section><section className="surface result-actions"><Button onClick={() => { notify('方案已采用，学习计划已更新'); onNavigate('plan') }}>采用此方案<ArrowRight size={16} /></Button><Button variant="secondary" onClick={() => setExportOpen(true)}><ArrowDownToLine size={16} />导出方案</Button><Button variant="ghost" onClick={() => notify('只读分享已创建，默认 90 天过期，可随时撤销')}>创建只读分享</Button></section></aside></div></div>}
    <ExportCenter open={exportOpen} onClose={() => setExportOpen(false)} notify={notify} />
    <Modal open={regenOpen} onClose={() => setRegenOpen(false)} title="局部重生成"><div className="form-stack"><p className="regen-description">只会重跑选定工具及其下游依赖，保留已确认的资料与目标。每个方案最多重生成 2 次。</p><label>重生成范围<select value={regenScope} onChange={e => setRegenScope(e.target.value)}><option>知识图谱</option><option>阶段设计</option><option>资源检索</option><option>学习排期</option><option>五维校验</option></select></label><div className="modal-callout"><Route size={17} />「{regenScope}」之后的内容将按依赖关系级联更新。</div><Button onClick={regenerate}><RefreshCw size={16} />确认局部重生成</Button></div></Modal>
  </div>
}
