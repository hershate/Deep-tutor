import { useCallback, useEffect, useState } from 'react'
import { ArrowRight, BookOpen, CalendarClock, Check, Heart, MessageCircle, Plus, Search, Send, ShieldCheck, Users } from 'lucide-react'
import type { Project } from '../data'
import { Badge, Button, EmptyState, Modal, SectionHeader } from '../components/UI'
import { backend, onBackendEvent, reportBackendError } from '../backend/client'
import { communityBackend, type CommunityOverview, type PostKind } from '../backend/community'
import { clampToBytes } from '../limits'

const kindNames: Record<PostKind, string> = { discussion: '学习讨论', peer_review: '项目互评', resource: '资料分享' }

export default function Community({ notify }: { notify: (message: string) => void }) {
  const [data, setData] = useState<CommunityOverview | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [tab, setTab] = useState('discussion')
  const [query, setQuery] = useState('')
  const [modal, setModal] = useState<'post' | 'group' | 'room' | null>(null)
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [reply, setReply] = useState('')
  const [rulesOpen, setRulesOpen] = useState(false)
  const [postTitle, setPostTitle] = useState('')
  const [postBody, setPostBody] = useState('')
  const [postKind, setPostKind] = useState<PostKind>('discussion')
  const [projectId, setProjectId] = useState<number | null>(null)
  const [groupName, setGroupName] = useState('')
  const [groupTopic, setGroupTopic] = useState('')
  const [groupSchedule, setGroupSchedule] = useState('')
  const [roomTitle, setRoomTitle] = useState('')
  const [roomTopic, setRoomTopic] = useState('')
  const [roomStart, setRoomStart] = useState('')
  const [roomMinutes, setRoomMinutes] = useState(45)
  const [roomGroup, setRoomGroup] = useState<number | null>(null)

  // 搜索词去抖：避免每个击键都触发一次 overview 请求
  const [debouncedQuery, setDebouncedQuery] = useState('')
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 250)
    return () => window.clearTimeout(timer)
  }, [query])
  const refresh = useCallback(async () => {
    const [next, projectCollection] = await Promise.all([
      communityBackend.overview(tab === 'peer_review' ? 'peer_review' : undefined, debouncedQuery),
      backend.getCollection<Project[]>('projects'),
    ])
    setData(next)
    setProjects(projectCollection.value)
  }, [tab, debouncedQuery])
  useEffect(() => {
    void refresh().catch(reportBackendError)
    let unlisten: (() => void) | undefined
    const listening = onBackendEvent(event => {
      if (event.kind.startsWith('community.')) void refresh().catch(reportBackendError)
    }).then(stop => { unlisten = stop }).catch(reportBackendError)
    return () => { unlisten?.(); void listening }
  }, [refresh])

  const createPost = async () => {
    if (busy || !postTitle.trim() || !postBody.trim()) return
    setBusy(true)
    try {
      await communityBackend.createPost({ title: postTitle, body: postBody, kind: postKind, projectId })
      setPostTitle(''); setPostBody(''); setProjectId(null); setModal(null)
      await refresh()
      notify('讨论已保存到本机共学空间')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const reaction = async (id: number, kind: 'like' | 'bookmark') => {
    try { await communityBackend.reaction(id, kind); await refresh() } catch (error) { reportBackendError(error) }
  }
  const addReply = async (postId: number) => {
    if (!reply.trim() || busy) return
    setBusy(true)
    try {
      await communityBackend.reply(postId, reply)
      setReply('')
      await refresh()
      notify('回复已保存，可将其标记为已采纳')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const accept = async (id: number, accepted: boolean) => {
    try { await communityBackend.acceptReply(id, accepted); await refresh() } catch (error) { reportBackendError(error) }
  }
  const createGroup = async () => {
    if (busy || !groupName.trim() || !groupTopic.trim() || !groupSchedule.trim()) return
    setBusy(true)
    try {
      await communityBackend.createGroup({ name: groupName, topic: groupTopic, schedule: groupSchedule })
      setGroupName(''); setGroupTopic(''); setGroupSchedule(''); setModal(null)
      await refresh()
      notify('本机学习小组已创建，尚未虚构其他成员')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const toggleGroup = async (id: number, joined: boolean) => {
    try { await communityBackend.joinGroup(id, joined); await refresh(); notify(joined ? '已加入本机小组' : '已退出本机小组') }
    catch (error) { reportBackendError(error) }
  }
  const createRoom = async () => {
    if (busy || !roomTitle.trim() || !roomTopic.trim() || !roomStart) return
    const start = new Date(roomStart)
    if (Number.isNaN(start.getTime())) { notify('预约时间无效'); return }
    setBusy(true)
    try {
      await communityBackend.createRoom({ groupId: roomGroup, title: roomTitle, topic: roomTopic, startAt: start.toISOString(), durationMinutes: roomMinutes })
      setRoomTitle(''); setRoomTopic(''); setRoomStart(''); setModal(null)
      await refresh()
      notify('学习安排已保存，可预约本机应用内提醒')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const reserve = async (id: number, reserved: boolean) => {
    try {
      await (reserved ? communityBackend.cancelReservation(id) : communityBackend.reserve(id))
      await refresh()
      notify(reserved ? '已取消预约，未发送的提醒已移除' : '预约已保存，提醒时间由后端计算')
    } catch (error) { reportBackendError(error) }
  }
  const cancelRoom = async (id: number) => {
    try { await communityBackend.cancelRoom(id); await refresh(); notify('学习安排已取消，相关预约与提醒已同步取消') }
    catch (error) { reportBackendError(error) }
  }

  const upcoming = data?.rooms.filter(room => room.status === 'scheduled' && Date.parse(room.startAt) > Date.parse(data.now)).sort((a, b) => Date.parse(a.startAt) - Date.parse(b.startAt))[0]
  return <div className="page community-page">
    <div className="page-heading"><div><h1>学习社区</h1><p>本机共学空间：真实讨论、互评与预约；没有外部服务时，不虚构其他在线用户。</p></div><Button onClick={() => setModal('post')}><Plus size={18} />发起讨论</Button></div>
    <div className="community-layout"><main><div className="community-toolbar"><div className="library-tabs">{([['discussion', '学习讨论'], ['peer_review', '项目互评'], ['groups', '学习小组']] as const).map(item => <button key={item[0]} className={tab === item[0] ? 'active' : ''} onClick={() => setTab(item[0])}>{item[1]}</button>)}</div>{tab !== 'groups' && <div className="search-field"><Search size={16} /><input value={query} onChange={event => setQuery(clampToBytes(event.target.value, 200))} placeholder="搜索真实讨论…" /></div>}</div>
      {tab !== 'groups' ? <div className="post-list">{data?.posts.map(post => <article className="post-card" key={post.id}><div className="post-author"><span>本</span><div><strong>{post.author}</strong><small>{new Date(post.createdAt).toLocaleString()}</small></div><Badge tone="neutral">{kindNames[post.kind]}</Badge></div><h2>{post.title}</h2>{post.projectTitle && <Badge tone="blue">关联真实项目：{post.projectTitle}</Badge>}<p>{post.body}</p><div className="post-actions"><button aria-pressed={post.liked} onClick={() => void reaction(post.id, 'like')}><Heart size={17} />{post.likes} 有帮助</button><button onClick={() => setExpanded(expanded === post.id ? null : post.id)}><MessageCircle size={17} />{post.replies.length} 条回复</button><button aria-pressed={post.bookmarked} onClick={() => void reaction(post.id, 'bookmark')}>{post.bookmarked ? '取消收藏' : '收藏'}</button></div>
        {expanded === post.id && <div className="community-replies">{post.replies.map(item => <div key={item.id}><p>{item.body}</p><small>{item.author} · {new Date(item.createdAt).toLocaleString()}</small><button onClick={() => void accept(item.id, !item.accepted)}>{item.accepted ? <><Check size={14} />已采纳，点击取消</> : '采纳这条回复'}</button></div>)}<form className="reply-composer" onSubmit={event => { event.preventDefault(); void addReply(post.id) }}><textarea rows={3} value={reply} onChange={event => setReply(clampToBytes(event.target.value, 10000))} placeholder="写下基于实际学习或项目内容的回复" /><Button type="submit" disabled={busy || !reply.trim()}>保存回复<Send size={15} /></Button></form></div>}
      </article>)}{data && !data.posts.length && <EmptyState title="还没有匹配的真实讨论" description="创建一条讨论或项目互评；这里不会出现虚构的社区动态。" action={<Button onClick={() => setModal('post')}>发起讨论</Button>} />}</div>
      : <><div className="section-actions"><Button variant="secondary" onClick={() => setModal('group')}><Plus size={16} />创建小组</Button><Button onClick={() => setModal('room')}><CalendarClock size={16} />安排学习时间</Button></div><div className="group-grid">{data?.groups.map(group => <article className="group-card" key={group.id}><div className="group-card-head"><Users size={22} /><Badge tone={group.joined ? 'green' : 'neutral'}>{group.joined ? '已加入' : '本机小组'}</Badge></div><h2>{group.name}</h2><p>{group.topic}</p><div><span>{group.memberCount} 位本机成员</span><span>{group.schedule}</span></div><Button variant={group.joined ? 'secondary' : 'primary'} onClick={() => void toggleGroup(group.id, !group.joined)}>{group.joined ? '退出小组' : '加入小组'}<ArrowRight size={16} /></Button></article>)}{data && !data.groups.length && <EmptyState title="还没有本机小组" description="创建小组、设置常规学习安排，再决定是否加入。" action={<Button onClick={() => setModal('group')}>创建小组</Button>} />}</div></>}
      {data && data.rooms.length > 0 && <section className="surface community-room-list"><SectionHeader title="真实学习安排" description="只保存本机预约和应用内提醒，不提供在线音视频服务" />{data.rooms.map(room => { const group = room.groupId !== null ? data.groups.find(item => item.id === room.groupId) ?? null : null; const groupJoined = !group || group.joined; return <div className="community-room-row" key={room.id}><div><Badge tone={room.status === 'cancelled' ? 'red' : room.reserved ? 'green' : 'neutral'}>{room.status === 'cancelled' ? '已取消' : room.reserved ? '已预约' : '可预约'}</Badge><h3>{room.title}</h3><p>{room.topic}</p><small>{new Date(room.startAt).toLocaleString()} · {room.durationMinutes} 分钟 · {room.reservationCount} 条有效预约{group ? ` · 小组：${group.name}${group.joined ? '' : '（需先加入）'}` : ''}{room.reminderAt ? ` · 提醒 ${new Date(room.reminderAt).toLocaleString()}` : ''}</small></div><div>{room.status === 'scheduled' && <><Button size="sm" variant="secondary" disabled={!groupJoined && !room.reserved} title={groupJoined || room.reserved ? undefined : '先加入关联小组才能预约'} onClick={() => void reserve(room.id, room.reserved)}>{room.reserved ? '取消预约' : groupJoined ? '预约提醒' : '需先加入小组'}</Button><Button size="sm" variant="ghost" onClick={() => void cancelRoom(room.id)}>取消安排</Button></>}</div></div> })}</section>}
    </main><aside className="community-side"><section className="surface next-room"><div><CalendarClock size={20} />{upcoming && <Badge tone="green">{new Date(upcoming.startAt).toLocaleString()}</Badge>}</div>{upcoming ? <><h2>{upcoming.title}</h2><p>{upcoming.topic}</p><p>{upcoming.durationMinutes} 分钟 · {upcoming.reservationCount} 条有效预约</p><Button onClick={() => void reserve(upcoming.id, upcoming.reserved)}>{upcoming.reserved ? '取消预约' : '预约学习提醒'}</Button></> : <><h2>安排一次共同学习</h2><p>保存本机学习安排，并按后端计算的时间收到应用内提醒。</p><Button onClick={() => setModal('room')}>创建学习安排</Button></>}</section><section className="surface community-rules"><ShieldCheck size={21} /><h3>高质量共学约定</h3><ul><li>讨论思路，不直接索要整套答案</li><li>互评对事不对人，指出具体依据</li><li>分享资料时保留来源与版权信息</li></ul><button onClick={() => setRulesOpen(!rulesOpen)}>{rulesOpen ? '收起完整约定' : '阅读完整社区公约'}<ArrowRight size={15} /></button>{rulesOpen && <p>这里的内容只保存在本机，不会自动发布到公共社区。项目互评必须关联真实项目；学习预约不提供实时音视频服务，由学习者自行约定交流方式。</p>}</section><section className="surface peer-progress"><SectionHeader title="本机共学记录" />{data && <><div><strong>{data.stats.reviews}</strong><span>次项目互评</span></div><div><strong>{data.stats.acceptedReplies}</strong><span>条已采纳回复</span></div><div><strong>{data.stats.bookmarks}</strong><span>条收藏讨论</span></div></>}<p>这些是真实本机记录，不影响学习成绩。</p></section></aside></div>

    <Modal open={modal === 'post'} onClose={() => setModal(null)} title="发起学习讨论"><div className="form-stack"><label>标题<input value={postTitle} onChange={event => setPostTitle(clampToBytes(event.target.value, 200))} placeholder="具体描述你想讨论的问题" /></label><label>讨论类型<select value={postKind} onChange={event => { setPostKind(event.target.value as PostKind); if (event.target.value !== 'peer_review') setProjectId(null) }}><option value="discussion">学习讨论</option><option value="peer_review">项目互评</option><option value="resource">资料分享</option></select></label>{postKind === 'peer_review' && <label>关联真实项目<select value={projectId ?? ''} onChange={event => setProjectId(Number(event.target.value) || null)}><option value="">选择项目</option>{projects.map(project => <option key={project.id} value={project.id}>{project.title}</option>)}</select></label>}<label>正文<textarea rows={6} value={postBody} onChange={event => setPostBody(clampToBytes(event.target.value, 20000))} placeholder="已经尝试过什么？具体卡在哪里？" /></label><Button onClick={() => void createPost()} disabled={busy || !postTitle.trim() || !postBody.trim() || (postKind === 'peer_review' && !projectId)}><Send size={17} />保存讨论</Button></div></Modal>
    <Modal open={modal === 'group'} onClose={() => setModal(null)} title="创建本机学习小组"><div className="form-stack"><label>小组名称<input value={groupName} onChange={event => setGroupName(clampToBytes(event.target.value, 100))} /></label><label>共学主题<textarea rows={3} value={groupTopic} onChange={event => setGroupTopic(clampToBytes(event.target.value, 1000))} /></label><label>常规时间安排<input value={groupSchedule} onChange={event => setGroupSchedule(clampToBytes(event.target.value, 100))} placeholder="例如：每周三 20:00" /></label><p className="form-help">小组初始没有虚构成员，加入状态只代表本机用户。</p><Button onClick={() => void createGroup()} disabled={busy || !groupName.trim() || !groupTopic.trim() || !groupSchedule.trim()}>创建小组</Button></div></Modal>
    <Modal open={modal === 'room'} onClose={() => setModal(null)} title="安排学习时间"><div className="form-stack"><label>标题<input value={roomTitle} onChange={event => setRoomTitle(clampToBytes(event.target.value, 200))} placeholder="例如：本周错题互讲" /></label><label>学习内容<textarea rows={3} value={roomTopic} onChange={event => setRoomTopic(clampToBytes(event.target.value, 2000))} /></label><label>开始时间<input type="datetime-local" min={new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)} value={roomStart} onChange={event => setRoomStart(event.target.value)} /></label><label>时长（分钟）<input type="number" min={10} max={240} value={roomMinutes} onChange={event => setRoomMinutes(Number(event.target.value))} /></label><label>关联小组（可选）<select value={roomGroup ?? ''} onChange={event => setRoomGroup(Number(event.target.value) || null)}><option value="">不关联小组</option>{data?.groups.map(group => <option value={group.id} key={group.id}>{group.name}</option>)}</select></label><p className="form-help">预约后由后端保存开始前 15 分钟的应用内提醒；这里不创建在线音视频房间。</p><Button onClick={() => void createRoom()} disabled={busy || !roomTitle.trim() || !roomTopic.trim() || !roomStart}>保存学习安排</Button></div></Modal>
  </div>
}
