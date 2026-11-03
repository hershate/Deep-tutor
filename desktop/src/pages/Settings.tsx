import { useState } from 'react'
import { Accessibility, Bell, Brain, Check, ChevronRight, Database, Download, Globe2, Keyboard, LockKeyhole, Moon, RotateCcw, ShieldCheck, Sun, Trash2, UserRound, Users } from 'lucide-react'
import { Badge, Button, Modal, SectionHeader, Toggle } from '../components/UI'
import BackupPanel from '../components/BackupPanel'
import SecurityPanel from '../components/SecurityPanel'
import { reportBackendError } from '../backend/client'
import type { AppSnapshot } from '../backend/client'

export type Preferences = {
  proactive: boolean
  hintsOnly: boolean
  notifications: boolean
  weeklyReport: boolean
  highContrast: boolean
  reducedMotion: boolean
  largeText: boolean
  theme: 'light' | 'dark'
}

const settingNav = [
  { label: '个人学习档案', icon: UserRound },
  { label: '导师行为', icon: Brain },
  { label: '通知与专注', icon: Bell },
  { label: '外观与无障碍', icon: Accessibility },
  { label: '隐私与数据', icon: LockKeyhole },
  { label: '成员与访问', icon: Users },
]

export default function Settings({ preferences, setPreferences, onImport, counts, onReset, notify }: { preferences: Preferences; setPreferences: (p: Preferences) => Promise<AppSnapshot>; onImport: (path: string) => Promise<AppSnapshot>; counts: { tasks: number; notes: number; resources: number; projects: number }; onReset: () => Promise<AppSnapshot>; notify: (message: string) => void }) {
  const [resetOpen, setResetOpen] = useState(false)
  const update = <K extends keyof Preferences>(key: K, value: Preferences[K]) => {
    void setPreferences({ ...preferences, [key]: value }).catch(reportBackendError)
  }
  return <div className="page settings-page">
    <div className="page-heading"><div><h1>设置与偏好</h1><p>决定导师如何参与、数据如何保存，以及界面如何适应你。</p></div><Badge tone="green"><Check size={14} />界面偏好由原生后端保存</Badge></div>
    <div className="settings-layout"><nav className="settings-nav" aria-label="设置分类">{settingNav.map(({ label, icon: Icon }, index) => <button key={label} onClick={() => document.getElementById(`setting-${index}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}><Icon size={18} /><span>{label}</span><ChevronRight size={15} /></button>)}</nav><main className="settings-content">
      <section className="surface setting-section" id="setting-0"><SectionHeader title="个人学习档案" description="这些信息用于生成适合你的计划和讲解。" /><div className="profile-block"><span>陈</span><div><h3>陈默</h3><p>自学者 · 计算机考研目标</p></div><Button variant="secondary" size="sm" onClick={() => notify('学习诊断已重新开始，将保留现有学习记录')}>重新学习诊断</Button></div><div className="form-grid"><label>界面语言<select defaultValue="简体中文"><option>简体中文</option><option>English</option></select></label><label>每周可投入<select defaultValue="15 小时"><option>8 小时</option><option>15 小时</option><option>25 小时</option></select></label></div><label>偏好学习方式<select defaultValue="案例与练习结合"><option>案例与练习结合</option><option>先系统阅读，再集中练习</option><option>以项目实践为主</option></select></label></section>
      <section className="surface setting-section" id="setting-1"><SectionHeader title="导师行为" description="控制 AI 的主动程度，以及何时展示答案。" /><Toggle checked={preferences.proactive} onChange={v => update('proactive', v)} label="主动提供学习建议" description="根据掌握度和计划风险推荐下一步，不自动修改计划。" /><Toggle checked={preferences.hintsOnly} onChange={v => update('hintsOnly', v)} label="练习时优先只给提示" description="除非你明确请求，否则不直接展示完整答案。" /><label>默认讲解方式<select defaultValue="先追问，再针对讲解"><option>先追问，再针对讲解</option><option>直接给出完整讲解</option><option>苏格拉底式引导</option></select></label><div className="setting-note"><Brain size={17} />全局导师与 Agent 使用已配置档案发起真实模型请求；可用性取决于供应商、凭据与网络。</div></section>
      <section className="surface setting-section" id="setting-2"><SectionHeader title="通知与专注" description="让提醒服务于节奏，而不是制造焦虑。" /><Toggle checked={preferences.notifications} onChange={v => update('notifications', v)} label="学习任务与复习提醒" description="仅提醒已计划任务和到期复习。" /><Toggle checked={preferences.weeklyReport} onChange={v => update('weeklyReport', v)} label="每周学习复盘" description="每周日生成一份可编辑的学习报告。" /><label>免打扰时段<select defaultValue="22:00—次日 08:00"><option>22:00—次日 08:00</option><option>23:00—次日 07:00</option><option>全天不通知</option></select></label></section>
      <section className="surface setting-section" id="setting-3"><SectionHeader title="外观与无障碍" description="设置由原生后端保存，并在提交成功后生效。" /><div className="theme-options"><button className={preferences.theme === 'light' ? 'active' : ''} onClick={() => update('theme', 'light')}><Sun size={19} /><span>明亮</span><small>纸张色工作区</small></button><button className={preferences.theme === 'dark' ? 'active' : ''} onClick={() => update('theme', 'dark')}><Moon size={19} /><span>深色</span><small>低光环境阅读</small></button></div><Toggle checked={preferences.largeText} onChange={v => update('largeText', v)} label="增大正文字号" description="提升阅读与笔记正文尺寸。" /><Toggle checked={preferences.highContrast} onChange={v => update('highContrast', v)} label="增强对比度" description="加深辅助文字和控件边界。" /><Toggle checked={preferences.reducedMotion} onChange={v => update('reducedMotion', v)} label="减少动态效果" description="关闭翻卡与界面过渡动画。" /><div className="keyboard-help"><Keyboard size={18} /><div><strong>键盘快捷键</strong><p>⌘ / Ctrl + K 全局搜索 · Esc 关闭弹窗 · Enter 发送导师消息</p></div></div></section>
      <section className="surface setting-section" id="setting-4"><SectionHeader title="隐私、备份与数据" description="导出当前已接入的数据，预览追加导入及其覆盖面。" /><BackupPanel onImport={onImport} counts={counts} notify={notify} /><div className="data-actions"><Button variant="ghost" onClick={() => setResetOpen(true)}><Trash2 size={17} />清除本地学习数据</Button></div><div className="privacy-line"><ShieldCheck size={16} />本地状态由 Tauri 原生后端保存，不使用浏览器存储充当数据库。</div></section>
      <section className="surface setting-section" id="setting-5"><SectionHeader title="成员与访问安全" description="多用户、个人访问令牌、资源配额与认证链管理。" /><SecurityPanel notify={notify} /></section>
    </main></div>
    <Modal open={resetOpen} onClose={() => setResetOpen(false)} title="清除本地学习数据？"><div className="confirm-dialog"><p>这会清空任务、笔记、资料、项目、草稿并恢复默认偏好。此操作无法撤销，建议先导出备份。</p><div><Button variant="secondary" onClick={() => setResetOpen(false)}>取消</Button><Button onClick={() => { void onReset().then(() => setResetOpen(false)).catch(reportBackendError) }}>确认清除</Button></div></div></Modal>
  </div>
}
