export type PageId = 'today' | 'generator' | 'plan' | 'study' | 'map' | 'agent' | 'practice' | 'review' | 'library' | 'projects' | 'reports' | 'community' | 'integrations' | 'providers' | 'settings' | 'shared'

export type Task = {
  id: number
  title: string
  subject: string
  duration: number
  done: boolean
  kind: '学习' | '练习' | '复习' | '项目'
}

export type Note = {
  id: number
  title: string
  excerpt: string
  tag: string
  updated: string
  content: string
}

export type Resource = {
  id: number
  title: string
  type: 'PDF' | '视频' | '网页' | '音频' | '文件'
  meta: string
  progress: number
}

export type Milestone = { id: number; title: string; done: boolean }
export type Project = {
  id: number
  title: string
  description: string
  due: string
  progress: number
  tag: string
  milestones: Milestone[]
}

export type ChatMessage = {
  id: number
  role: 'user' | 'assistant'
  text: string
  source?: string
}

export const initialTasks: Task[] = [
  { id: 1, title: '理解贝叶斯定理与先验概率', subject: '概率论', duration: 35, done: true, kind: '学习' },
  { id: 2, title: '完成条件概率变式练习', subject: '概率论', duration: 25, done: false, kind: '练习' },
  { id: 3, title: '复习「递归与分治」错题', subject: '数据结构', duration: 20, done: false, kind: '复习' },
  { id: 4, title: '整理英语阅读长难句笔记', subject: '英语', duration: 30, done: false, kind: '学习' },
]

export const initialNotes: Note[] = [
  { id: 1, title: '贝叶斯定理：从结果反推原因', excerpt: '后验概率 ∝ 似然 × 先验。关键不是背公式，而是区分 P(A|B) 与 P(B|A)。', tag: '概率论', updated: '今天 10:42', content: '后验概率 ∝ 似然 × 先验。\n\n关键不是背公式，而是区分 P(A|B) 与 P(B|A)。\n\n例子：医学筛查中，即使准确率很高，低患病率也会显著影响阳性结果的可信度。' },
  { id: 2, title: '递归函数的三个检查点', excerpt: '终止条件、规模缩减、子问题组合。写代码前先画出递归树。', tag: '数据结构', updated: '昨天 21:18', content: '递归函数的三个检查点：\n1. 终止条件是否覆盖所有路径？\n2. 每次调用是否让问题规模严格缩小？\n3. 子问题的结果如何组合？' },
  { id: 3, title: '英语长难句拆解模板', excerpt: '先找谓语，再识别从句边界，最后恢复修饰关系。', tag: '英语', updated: '10 月 30 日', content: '先找谓语，再识别从句边界，最后恢复修饰关系。\n\n不要逐词翻译，先确定句子的主干。' },
  { id: 4, title: '本周学习复盘', excerpt: '视频学习偏长，主动回忆不足。下周把每次阅读后的闭卷复述加入任务。', tag: '复盘', updated: '10 月 27 日', content: '本周学习复盘：\n\n做得好的：计划完成率稳定。\n需要改进：视频学习偏长，主动回忆不足。\n下周行动：每次阅读后加入 3 分钟闭卷复述。' },
]

export const initialResources: Resource[] = [
  { id: 1, title: '概率论与数理统计 · 第四章', type: 'PDF', meta: '48 页 · 教材', progress: 68 },
  { id: 2, title: '贝叶斯思维的三个直觉实验', type: '视频', meta: '18:24 · 含字幕', progress: 42 },
  { id: 3, title: '递归与分治可视化教程', type: '网页', meta: '交互课程 · 已收藏', progress: 85 },
  { id: 4, title: '英语精听：科技与学习', type: '音频', meta: '12:08 · 含逐句稿', progress: 20 },
]

export const initialProjects: Project[] = [
  { id: 1, title: '用 Python 构建垃圾邮件分类器', description: '把贝叶斯定理应用到一个可验证的真实任务中。', due: '11 月 12 日', progress: 60, tag: '数据科学', milestones: [
    { id: 1, title: '清洗并探索邮件数据集', done: true },
    { id: 2, title: '实现朴素贝叶斯分类器', done: true },
    { id: 3, title: '评估精确率与召回率', done: false },
    { id: 4, title: '撰写项目复盘', done: false },
  ] },
  { id: 2, title: '递归算法可视化学习页', description: '用动画解释调用栈与问题规模缩减。', due: '11 月 20 日', progress: 25, tag: '编程', milestones: [
    { id: 1, title: '确定交互与视觉稿', done: true },
    { id: 2, title: '实现递归树组件', done: false },
    { id: 3, title: '添加复杂度分析', done: false },
  ] },
]

export const knowledgeNodes = [
  { id: 'prob', label: '概率基础', x: 90, y: 190, mastery: 92, status: '熟练', description: '样本空间、事件、概率公理与基本计数。', evidence: '24 道练习 · 最近复习 3 天前' },
  { id: 'cond', label: '条件概率', x: 260, y: 100, mastery: 78, status: '掌握', description: '在已知条件下更新事件发生的概率。', evidence: '18 道练习 · 2 次错题重练' },
  { id: 'bayes', label: '贝叶斯定理', x: 440, y: 175, mastery: 64, status: '学习中', description: '利用新证据，从先验概率得到后验概率。', evidence: '12 道练习 · 当前学习单元' },
  { id: 'indep', label: '独立性', x: 260, y: 285, mastery: 71, status: '掌握', description: '事件独立与条件独立的判断和应用。', evidence: '15 道练习 · 1 次项目应用' },
  { id: 'nb', label: '朴素贝叶斯', x: 625, y: 105, mastery: 28, status: '待巩固', description: '基于特征条件独立假设的概率分类方法。', evidence: '项目进行中 · 尚未完成测评' },
  { id: 'eval', label: '模型评估', x: 625, y: 285, mastery: 12, status: '未学', description: '准确率、精确率、召回率与混淆矩阵。', evidence: '完成朴素贝叶斯后解锁' },
]

export const reviewCards = [
  { id: 1, front: '贝叶斯定理中，先验概率与后验概率有什么区别？', back: '先验概率是在看到新证据前对事件的判断；后验概率是在结合新证据后更新的判断。', tag: '概率论', due: '现在' },
  { id: 2, front: '为什么高准确率筛查仍可能产生很多假阳性？', back: '当疾病的基线患病率很低时，健康人群数量远大于患者，即使假阳性率不高，也可能产生大量假阳性。', tag: '概率论', due: '现在' },
  { id: 3, front: '递归实现必须检查哪三个条件？', back: '终止条件、问题规模严格缩减、子问题结果能够正确组合。', tag: '数据结构', due: '现在' },
  { id: 4, front: 'P(A|B) 与 P(B|A) 为什么通常不相等？', back: '两者条件化的事件不同，对应不同的样本空间和问题含义；需要通过贝叶斯定理结合先验与似然进行转换。', tag: '概率论', due: '现在' },
]

export const practiceQuestions = [
  { id: 1, question: '某疾病患病率为 1%。检测的灵敏度为 99%，特异度为 95%。随机一人检测为阳性，最影响其后验患病率的是哪项信息？', options: ['检测设备的尺寸', '疾病的先验患病率', '检测所需时间', '采样的地点'], answer: 1, explanation: '先验患病率很低时，即使检测性能较高，阳性结果中仍可能包含大量假阳性。贝叶斯更新必须同时考虑先验与似然。', hint: '先不要计算。想一想：在检测之前，这个人患病的可能性由什么决定？' },
  { id: 2, question: '若 P(A)=0.4，P(B)=0.5，且 A、B 相互独立，则 P(A∩B) 等于多少？', options: ['0.1', '0.2', '0.45', '0.9'], answer: 1, explanation: '独立事件满足 P(A∩B)=P(A)P(B)=0.4×0.5=0.2。', hint: '独立意味着一个事件的发生不会改变另一个事件的概率。' },
  { id: 3, question: '在朴素贝叶斯分类器中，“朴素”主要指什么？', options: ['模型没有参数', '只能处理文本', '假设特征之间条件独立', '不需要训练数据'], answer: 2, explanation: '朴素贝叶斯假设在给定类别时，各特征条件独立。这一简化假设让模型高效，但并不总在现实中严格成立。', hint: '它简化的是特征之间的某种关系。' },
]

export const communityPosts = [
  { id: 1, author: '林一', initials: '林', title: '如何避免“看懂了但做不出”？', body: '最近把每次学习后的闭卷复述加入流程，错题率明显下降。我的具体做法是……', tag: '学习方法', replies: 28, likes: 94, time: '18 分钟前' },
  { id: 2, author: '周澄', initials: '周', title: '贝叶斯分类器项目互评招募', body: '刚完成数据清洗和初版模型，希望找一位同学互查评估指标与项目报告。', tag: '项目互评', replies: 12, likes: 36, time: '1 小时前' },
  { id: 3, author: '许言', initials: '许', title: '一份不焦虑的冲刺周计划', body: '不延长学习时长，只调整任务顺序：先补高频缺口，再做整卷，最后处理低频细节。', tag: '备考计划', replies: 41, likes: 128, time: '昨天' },
]
