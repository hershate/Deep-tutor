import { useState } from 'react'
import { ArrowDownToLine, CalendarDays, Check, Code2, FileArchive, FileText, Layers3, Presentation, Table2 } from 'lucide-react'
import { Badge, Button, Modal } from './UI'

const formats = [
  { id: 'md', name: 'Markdown', description: '讲义、笔记与方案结构', icon: FileText, ext: 'md' },
  { id: 'pdf', name: 'PDF', description: '调用系统打印，另存为 PDF', icon: FileArchive, ext: 'pdf' },
  { id: 'ics', name: 'ICS 日历', description: 'RFC 5545 学习日程', icon: CalendarDays, ext: 'ics' },
  { id: 'anki', name: 'Anki', description: '演示环境降级为兼容 TSV', icon: Layers3, ext: 'tsv' },
  { id: 'slides', name: 'HTML 幻灯片', description: '安全静态单元幻灯片', icon: Presentation, ext: 'html' },
  { id: 'csv', name: 'CSV', description: '插件导出 · 学习记录', icon: Table2, ext: 'csv' },
  { id: 'txt', name: 'TXT', description: '插件导出 · 纯文本', icon: Code2, ext: 'txt' },
  { id: 'opml', name: 'OPML', description: '插件导出 · 大纲结构', icon: Code2, ext: 'opml' },
]

function download(name: string, content: string, type = 'text/plain;charset=utf-8') {
  const blob = new Blob([content], { type }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export default function ExportCenter({ open, onClose, notify }: { open: boolean; onClose: () => void; notify: (message: string) => void }) {
  const [scope, setScope] = useState('完整学习方案')
  const [done, setDone] = useState('')
  const run = (id: string) => {
    const base = `# 贝叶斯定理学习方案\n\n## 目标\n理解先验、似然与后验，并能解决低基线率问题。\n\n## 检查理解\n为什么准确率高，阳性结果仍可能不可靠？\n`
    if (id === 'pdf') { window.print(); setDone('pdf'); notify('已打开系统打印，可选择“另存为 PDF”'); return }
    if (id === 'md') download('DeepTutor-learning-plan.md', base, 'text/markdown;charset=utf-8')
    if (id === 'ics') download('DeepTutor-schedule.ics', 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//DeepTutor//ZH-CN\r\nBEGIN:VEVENT\r\nUID:bayes-20261103@deeptutor.local\r\nDTSTAMP:20261102T000000Z\r\nDTSTART:20261103T010000Z\r\nDTEND:20261103T013500Z\r\nSUMMARY:贝叶斯定理学习\r\nDESCRIPTION:先验、似然与后验概率\r\nEND:VEVENT\r\nEND:VCALENDAR', 'text/calendar;charset=utf-8')
    if (id === 'anki') download('DeepTutor-anki-cards.tsv', '正面\t背面\t标签\n贝叶斯定理中先验是什么？\t看到新证据前的概率判断\t概率论\n', 'text/tab-separated-values;charset=utf-8')
    if (id === 'slides') download('DeepTutor-unit-slides.html', '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>贝叶斯定理</title><style>body{margin:0;background:#163c34;color:#f4f7f2;font:24px system-ui;display:grid;place-items:center;min-height:100vh}main{max-width:800px;padding:48px}strong{color:#d8f177}</style><main><p>知舟 · 单元讲义</p><h1>贝叶斯定理</h1><p>用<strong>新证据</strong>更新先验判断，得到后验概率。</p><h2>P(A|B) = P(B|A) × P(A) / P(B)</h2></main></html>', 'text/html;charset=utf-8')
    if (id === 'csv') download('DeepTutor-learning-records.csv', '\ufeff日期,知识点,分钟,掌握度\n2026-11-02,贝叶斯定理,35,64\n', 'text/csv;charset=utf-8')
    if (id === 'txt') download('DeepTutor-learning-plan.txt', base)
    if (id === 'opml') download('DeepTutor-outline.opml', '<?xml version="1.0" encoding="UTF-8"?><opml version="2.0"><head><title>知舟学习方案</title></head><body><outline text="贝叶斯定理"><outline text="先验概率"/><outline text="似然"/><outline text="后验概率"/></outline></body></opml>', 'text/x-opml;charset=utf-8')
    setDone(id); notify(id === 'anki' ? '已按降级策略导出 Anki 兼容 TSV，未伪装为 APKG' : '导出已完成')
  }
  return <Modal open={open} onClose={onClose} title="导出学习内容" wide><div className="export-center"><div className="export-scope"><label>导出范围<select value={scope} onChange={e => setScope(e.target.value)}><option>完整学习方案</option><option>当前单元讲义</option><option>笔记与知识库</option><option>复习卡片</option><option>学习日程</option></select></label><div><Badge tone="green">插件导出已启用</Badge><p>PDF 依赖系统打印；APKG 在当前纯前端环境按预设降级为 TSV。</p></div></div><div className="export-grid">{formats.map(format => <button key={format.id} onClick={() => run(format.id)}><format.icon size={22} /><div><strong>{format.name}</strong><span>{format.description}</span></div>{done === format.id ? <Check size={18} /> : <ArrowDownToLine size={18} />}</button>)}</div><div className="export-foot"><span>导出内容默认清理脚本与无效引用；HTML 幻灯片不包含外部资源。</span><Button variant="secondary" onClick={onClose}>完成</Button></div></div></Modal>
}
