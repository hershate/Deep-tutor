import { useState } from 'react'
import { ArrowRight, BookOpen, Check, Heart, MessageCircle, Plus, Search, Send, ShieldCheck, Users, Video } from 'lucide-react'
import { communityPosts } from '../data'
import { Badge, Button, Modal, SectionHeader } from '../components/UI'

export default function Community({ notify }: { notify: (message: string) => void }) {
  const [posts, setPosts] = useState(communityPosts)
  const [joined, setJoined] = useState<number[]>([1])
  const [postOpen, setPostOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [tab, setTab] = useState('学习讨论')
  const [query, setQuery] = useState('')
  const publish = () => {
    if (!title.trim() || !body.trim()) return
    setPosts([{ id: Date.now(), author: '陈默', initials: '陈', title: title.trim(), body: body.trim(), tag: '学习讨论', replies: 0, likes: 0, time: '刚刚' }, ...posts]); setTitle(''); setBody(''); setPostOpen(false); notify('讨论已发布到学习社区')
  }
  return <div className="page community-page">
    <div className="page-heading"><div><h1>学习社区</h1><p>围绕真实任务交流、互评与同行，不制造无关的信息流。</p></div><Button onClick={() => setPostOpen(true)}><Plus size={18} />发起讨论</Button></div>
    <div className="community-layout"><main><div className="community-toolbar"><div className="library-tabs">{['学习讨论', '项目互评', '学习小组'].map(item => <button key={item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{item}</button>)}</div><div className="search-field"><Search size={16} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索讨论…" /></div></div>
      {tab !== '学习小组' ? <div className="post-list">{posts.filter(p => (tab === '项目互评' ? p.tag === '项目互评' : true) && `${p.title}${p.body}`.includes(query)).map(post => <article className="post-card" key={post.id}><div className="post-author"><span>{post.initials}</span><div><strong>{post.author}</strong><small>{post.time}</small></div><Badge tone="neutral">{post.tag}</Badge></div><h2>{post.title}</h2><p>{post.body}</p><div className="post-actions"><button onClick={() => setPosts(posts.map(p => p.id === post.id ? { ...p, likes: p.likes + 1 } : p))}><Heart size={17} />{post.likes} 有帮助</button><button onClick={() => notify(`已展开「${post.title}」的 ${post.replies} 条回复`)}><MessageCircle size={17} />{post.replies} 条回复</button><button onClick={() => notify('已收藏这条讨论')}>收藏</button></div></article>)}</div> : <div className="group-grid">{[
        { id: 1, name: '概率论共学小组', members: 18, time: '周三 20:00', topic: '本周：贝叶斯应用与错题互讲' },
        { id: 2, name: '30 天 Python 项目营', members: 24, time: '周六 14:00', topic: '本周：模型评估与项目互评' },
        { id: 3, name: '英语精读早间房', members: 12, time: '每天 07:30', topic: '本周：科技类长文精读' },
      ].map(group => <article className="group-card" key={group.id}><div className="group-card-head"><Users size={22} /><Badge tone={joined.includes(group.id) ? 'green' : 'neutral'}>{joined.includes(group.id) ? '已加入' : '开放加入'}</Badge></div><h2>{group.name}</h2><p>{group.topic}</p><div><span>{group.members} 位成员</span><span>{group.time}</span></div><Button variant={joined.includes(group.id) ? 'secondary' : 'primary'} onClick={() => { setJoined(joined.includes(group.id) ? joined.filter(id => id !== group.id) : [...joined, group.id]); notify(joined.includes(group.id) ? `已退出「${group.name}」` : `已加入「${group.name}」`) }}>{joined.includes(group.id) ? '退出小组' : '加入小组'}<ArrowRight size={16} /></Button></article>)}</div>}
    </main><aside className="community-side"><section className="surface next-room"><div><Video size={20} /><Badge tone="green">明晚 20:00</Badge></div><h2>错题互讲 · 贝叶斯专题</h2><p>每人带一道错题，用 5 分钟讲清自己的误区。无需露脸，可选择语音。</p><div className="room-avatars"><span>林</span><span>周</span><span>许</span><small>+15 人已预约</small></div><Button onClick={() => notify('已预约错题互讲，开始前 15 分钟提醒')}>预约学习房间</Button></section><section className="surface community-rules"><ShieldCheck size={21} /><h3>高质量共学约定</h3><ul><li>讨论思路，不直接索要整套答案</li><li>互评对事不对人，指出具体依据</li><li>分享资料时保留来源与版权信息</li></ul><button onClick={() => notify('社区公约已展开阅读')}>阅读完整社区公约<ArrowRight size={15} /></button></section><section className="surface peer-progress"><SectionHeader title="本周同伴贡献" /><div><strong>2</strong><span>次项目互评</span></div><div><strong>5</strong><span>次回答被采纳</span></div><p>贡献不影响你的成绩，只是一份共学记录。</p></section></aside></div>
    <Modal open={postOpen} onClose={() => setPostOpen(false)} title="发起学习讨论"><div className="form-stack"><label>标题<input value={title} onChange={e => setTitle(e.target.value)} placeholder="具体描述你想讨论的问题" /></label><label>讨论类型<select><option>学习讨论</option><option>项目互评</option><option>资料分享</option></select></label><label>正文<textarea rows={6} value={body} onChange={e => setBody(e.target.value)} placeholder="已经尝试过什么？具体卡在哪里？" /></label><Button onClick={publish} disabled={!title.trim() || !body.trim()}><Send size={17} />发布讨论</Button></div></Modal>
  </div>
}
