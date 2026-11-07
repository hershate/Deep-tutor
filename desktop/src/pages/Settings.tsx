import { useEffect, useState } from 'react'
import { Accessibility, Bell, Brain, Check, ChevronRight, Keyboard, LockKeyhole, Moon, ShieldCheck, Sun, Trash2, UserRound, Users } from 'lucide-react'
import { Badge, Button, Modal, SectionHeader, Toggle } from '../components/UI'
import BackupPanel from '../components/BackupPanel'
import DataDirectoryPanel from '../components/DataDirectoryPanel'
import SecurityPanel from '../components/SecurityPanel'
import { onBackendEvent, reportBackendError } from '../backend/client'
import { engagementBackend } from '../backend/engagement'
import type { LearningProfile } from '../backend/engagement'
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
const learningStyles = ['案例与练习结合', '先系统阅读，再集中练习', '以项目实践为主']
const explanationStyles = ['先追问，再针对讲解', '直接给出完整讲解', '苏格拉底式引导']

export default function Settings({ preferences, setPreferences, onImport, counts, onReset, notify }: { preferences: Preferences; setPreferences: (p: Preferences) => Promise<AppSnapshot>; onImport: (path: string) => Promise<AppSnapshot>; counts: { tasks: number; notes: number; resources: number; projects: number }; onReset: () => Promise<AppSnapshot>; notify: (message: string) => void }) {
  const [resetOpen, setResetOpen] = useState(false)
  const [profile, setProfile] = useState<LearningProfile | null>(null)
  const [draft, setDraft] = useState<LearningProfile | null>(null)
  const [profileOpen, setProfileOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    let stop: (() => void) | undefined
    const load = async () => {
      const next = await engagementBackend.profile()
      if (active) {
        setProfile(next)
        setDraft(next)
        document.documentElement.lang = next.language
      }
    }
    void load().catch(reportBackendError)
    void onBackendEvent(event => {
      if (active && (event.kind === 'security.session_started' || (event.kind === 'state.changed' && event.payload.namespace === 'preferences' && !profileOpen))) void load().catch(reportBackendError)
    }).then(unlisten => { if (active) stop = unlisten; else unlisten() }).catch(reportBackendError)
    return () => { active = false; stop?.() }
  }, [profileOpen])
  const update = <K extends keyof Preferences>(key: K, value: Preferences[K]) => {
    void setPreferences({ ...preferences, [key]: value }).catch(reportBackendError)
  }
  const saveProfile = async (input: LearningProfile, close: boolean) => {
    if (busy) return
    setBusy(true)
    try {
      const saved = await engagementBackend.saveProfile(input)
      setProfile(saved)
      setDraft(saved)
      document.documentElement.lang = saved.language
      if (close) setProfileOpen(false)
      notify('个人学习档案与导师偏好已保存')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const restart = async () => {
    if (busy) return
    setBusy(true)
    try {
      const next = await engagementBackend.restartDiagnostic()
      setProfile(next)
      setDraft(next)
      setProfileOpen(true)
      notify('已记录新的诊断开始时间，现有学习记录未删除')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const editDraft = <K extends keyof LearningProfile>(key: K, value: LearningProfile[K]) => {
    if (draft) setDraft({ ...draft, [key]: value })
  }
  const quietValue = profile?.quietEnabled ? `${profile.quietStart}-${profile.quietEnd}` : 'off'
  return <div className="page settings-page">
    <div className="page-heading"><div><h1>设置与偏好</h1><p>决定导师如何参与、数据如何保存，以及界面如何适应你。</p></div><Badge tone="green"><Check size={14} />界面偏好由原生后端保存</Badge></div>
    <div className="settings-layout"><nav className="settings-nav" aria-label="设置分类">{settingNav.map(({ label, icon: Icon }, index) => <button key={label} onClick={() => document.getElementById(`setting-${index}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}><Icon size={18} /><span>{label}</span><ChevronRight size={15} /></button>)}</nav><main className="settings-content">
      <section className="surface setting-section" id="setting-0"><SectionHeader title="个人学习档案" description="这些信息用于生成适合你的计划和讲解。" />{profile && <><div className="profile-block"><span>{profile.name.slice(0, 1)}</span><div><h3>{profile.name}</h3><p>{profile.role} · {profile.goal || '尚未设置学习目标'}</p></div><Button variant="secondary" size="sm" disabled={busy} onClick={() => void restart()}>重新学习诊断</Button></div><div className="form-grid"><label>语言偏好<select value={profile.language} disabled={busy} onChange={event => void saveProfile({ ...profile, language: event.target.value }, false)}><option value="zh-CN">简体中文</option><option value="en-US">English</option></select><p className="form-help">界面文本暂仅提供中文；此偏好保存在学习档案中。</p></label><label>每周可投入<select value={profile.weeklyHours} disabled={busy} onChange={event => void saveProfile({ ...profile, weeklyHours: Number(event.target.value) }, false)}><option value={8}>8 小时</option><option value={15}>15 小时</option><option value={25}>25 小时</option></select></label></div><label>偏好学习方式<select value={profile.learningStyle} disabled={busy} onChange={event => void saveProfile({ ...profile, learningStyle: event.target.value }, false)}>{learningStyles.map(style => <option key={style}>{style}</option>)}</select></label><p className="form-help">{profile.diagnosticStartedAt ? `上次诊断开始于 ${new Date(profile.diagnosticStartedAt).toLocaleString()}` : '尚未开始学习诊断。'}</p></>}</section>
      <section className="surface setting-section" id="setting-1"><SectionHeader title="导师行为" description="控制 AI 的主动程度，以及何时展示答案。" /><Toggle checked={preferences.proactive} onChange={value => update('proactive', value)} label="主动提供学习建议" description="根据掌握度和计划风险推荐下一步，不自动修改计划。" /><Toggle checked={preferences.hintsOnly} onChange={value => update('hintsOnly', value)} label="练习时优先只给提示" description="除非你明确请求，否则不直接展示完整答案。" />{profile && <label>默认讲解方式<select value={profile.explanationStyle} disabled={busy} onChange={event => void saveProfile({ ...profile, explanationStyle: event.target.value }, false)}>{explanationStyles.map(style => <option key={style}>{style}</option>)}</select></label>}<div className="setting-note"><Brain size={17} />全局导师与 Agent 使用已配置档案发起真实模型请求；可用性取决于供应商、凭据与网络。</div></section>
      <section className="surface setting-section" id="setting-2"><SectionHeader title="通知与专注" description="让提醒服务于节奏，而不是制造焦虑。" /><Toggle checked={preferences.notifications} onChange={value => update('notifications', value)} label="学习任务与复习提醒" description="仅提醒已计划任务和到期复习。" /><Toggle checked={preferences.weeklyReport} onChange={value => update('weeklyReport', value)} label="每周学习复盘" description="每周日在通知中心提醒你做一次学习复盘。" />{profile && <label>免打扰时段<select value={quietValue} disabled={busy} onChange={event => { const value = event.target.value; void saveProfile(value === 'off' ? { ...profile, quietEnabled: false, quietStart: '00:00', quietEnd: '00:00' } : { ...profile, quietEnabled: true, quietStart: value.split('-')[0], quietEnd: value.split('-')[1] }, false) }}><option value="22:00-08:00">22:00—次日 08:00</option><option value="23:00-07:00">23:00—次日 07:00</option><option value="off">全天不通知</option></select></label>}</section>
      <section className="surface setting-section" id="setting-3"><SectionHeader title="外观与无障碍" description="设置由原生后端保存，并在提交成功后生效。" /><div className="theme-options"><button className={preferences.theme === 'light' ? 'active' : ''} onClick={() => update('theme', 'light')}><Sun size={19} /><span>明亮</span><small>纸张色工作区</small></button><button className={preferences.theme === 'dark' ? 'active' : ''} onClick={() => update('theme', 'dark')}><Moon size={19} /><span>深色</span><small>低光环境阅读</small></button></div><Toggle checked={preferences.largeText} onChange={value => update('largeText', value)} label="增大正文字号" description="提升阅读与笔记正文尺寸。" /><Toggle checked={preferences.highContrast} onChange={value => update('highContrast', value)} label="增强对比度" description="加深辅助文字和控件边界。" /><Toggle checked={preferences.reducedMotion} onChange={value => update('reducedMotion', value)} label="减少动态效果" description="关闭翻卡与界面过渡动画。" /><div className="keyboard-help"><Keyboard size={18} /><div><strong>键盘快捷键</strong><p>⌘ / Ctrl + K 全局搜索 · Esc 关闭弹窗 · Enter 发送导师消息</p></div></div></section>
      <section className="surface setting-section" id="setting-4"><SectionHeader title="隐私、备份与数据" description="管理本地数据位置，导出当前已接入的数据，预览追加导入及其覆盖面。" /><DataDirectoryPanel /><BackupPanel onImport={onImport} counts={counts} notify={notify} /><div className="data-actions"><Button variant="ghost" onClick={() => setResetOpen(true)}><Trash2 size={17} />清除本地学习数据</Button></div><div className="privacy-line"><ShieldCheck size={16} />本地状态由 Tauri 原生后端保存，不使用浏览器存储充当数据库。</div></section>
      <section className="surface setting-section" id="setting-5"><SectionHeader title="成员与访问安全" description="多用户、个人访问令牌、资源配额与认证链管理。" /><SecurityPanel notify={notify} /></section>
    </main></div>
    <Modal open={resetOpen} onClose={() => setResetOpen(false)} title="清除本地学习数据？"><div className="confirm-dialog"><p>这会清空任务、笔记、资料、项目、草稿并恢复默认偏好。此操作无法撤销，建议先导出备份。</p><div><Button variant="secondary" onClick={() => setResetOpen(false)}>取消</Button><Button onClick={() => { void onReset().then(() => setResetOpen(false)).catch(reportBackendError) }}>确认清除</Button></div></div></Modal>
    <Modal open={profileOpen && Boolean(draft)} onClose={() => setProfileOpen(false)} title="编辑个人学习档案">{draft && <div className="form-stack"><label>显示名称<input value={draft.name} maxLength={80} onChange={event => editDraft('name', event.target.value)} /></label><label>学习者描述<input value={draft.role} maxLength={100} onChange={event => editDraft('role', event.target.value)} placeholder="例如：自学者" /></label><label>学习目标<input value={draft.goal} maxLength={200} onChange={event => editDraft('goal', event.target.value)} placeholder="例如：计算机考研" /></label><label>语言偏好<select value={draft.language} onChange={event => editDraft('language', event.target.value)}><option value="zh-CN">简体中文</option><option value="en-US">English</option></select><p className="form-help">界面文本暂仅提供中文；此偏好保存在学习档案中。</p></label><label>每周可投入<select value={draft.weeklyHours} onChange={event => editDraft('weeklyHours', Number(event.target.value))}><option value={8}>8 小时</option><option value={15}>15 小时</option><option value={25}>25 小时</option></select></label><label>偏好学习方式<select value={draft.learningStyle} onChange={event => editDraft('learningStyle', event.target.value)}>{learningStyles.map(style => <option key={style}>{style}</option>)}</select></label><div className="modal-callout"><Brain size={17} />重新诊断只记录新的开始时间，不会删除已有计划、笔记、练习或复习记录。</div><Button disabled={busy || !draft.name.trim()} onClick={() => void saveProfile(draft, true)}>{busy ? '正在保存…' : '保存学习档案'}</Button></div>}</Modal>
  </div>
}
