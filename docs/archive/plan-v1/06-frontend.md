# 06 - 前端用户界面设计（细化）

> 默认决策（可调整）：中文 UI；支持浅色/深色双主题（class 切换）；左侧边栏 + 顶部栏布局；Tailwind 设计系统；React 18 + TS + Vite。本文件细化 [03-api-frontend.md](./03-api-frontend.md) 的前端部分。

## 一、设计目标

- **清晰**：方案结构（大纲/阶段/资源/时间线/校验）层次分明，长方案可折叠浏览。
- **可观测**：生成过程实时可见（当前工具、token/成本/进度），不"黑盒"。
- **低门槛**：多模态输入一站式（上传/粘贴/链接/描述），对话式澄清。
- **一致**：统一设计系统（颜色/字体/间距/组件），浅深色双主题。
- **可用**：响应式（桌面优先，平板可用）、键盘可达、状态反馈明确。

## 二、设计系统（Design System）

### 颜色（CSS 变量 / Tailwind 扩展）
| 语义 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| bg | #ffffff | #0b0f17 | 页面背景 |
| surface | #f7f8fa | #131a26 | 卡片/侧栏 |
| surface-2 | #eef1f5 | #1b2330 | 次级面板/悬停 |
| border | #e2e6ec | #26303f | 分隔线/边框 |
| text | #1a2230 | #e6ebf2 | 主文字 |
| text-secondary | #5b6678 | #9aa6b8 | 次要文字 |
| primary | #4f46e5 (indigo) | #6366f1 | 主操作/链接 |
| primary-hover | #4338ca | #818cf8 | 悬停 |
| success | #16a34a | #22c55e | ✅ 已验证/完成 |
| warning | #d97706 | #f59e0b | ⚠️ 存疑/进行中 |
| danger | #dc2626 | #ef4444 | ❌ 无效/错误 |

### 字体与字号
- 展示/标题：`Inter, "PingFang SC", "Microsoft YaHei", sans-serif`
- 正文：同上；代码/大纲节点 id：`ui-monospace, "JetBrains Mono", monospace`
- 字号阶梯：xs 12 / sm 14 / base 16 / lg 18 / xl 20 / 2xl 24 / 3xl 30

### 间距 / 圆角 / 阴影
- 间距：Tailwind 4px 基准（2=8, 3=12, 4=16, 6=24, 8=32）
- 圆角：控件 rounded-md；卡片 rounded-lg；弹层 rounded-xl
- 阴影：卡片 `shadow-sm`；弹层 `shadow-lg`
- 动效：过渡 150-200ms `ease-out`；生成中进度条 600ms

### 组件原子
Button（primary/secondary/ghost/danger，sm/md/lg）、Input、Textarea、Select、Tag/Badge（含 ✅⚠️❌ 状态色）、Card、Collapse/折叠组、Tabs、Modal、Toast、Progress、Skeleton、EmptyState、Tooltip。

## 三、应用骨架（AppShell）

```
┌──────────────────────────────────────────────────────────────┐
│ 顶栏  [Deep-tutor]   方案: xxx        [☀/☾主题] [预算: $0.12] │
├────────────┬─────────────────────────────────────────────────┤
│ 侧栏       │ 主内容区                                         │
│            │                                                  │
│ ＋新建方案  │   <页面内容>                                     │
│ 方案列表    │                                                  │
│ 学习中心(P2)│                                                  │
│ 设置        │                                                  │
│            │                                                  │
│ ── 用量 ──  │                                                  │
│ token 12k  │                                                  │
│ $0.12      │                                                  │
│ cache 96%  │                                                  │
└────────────┴─────────────────────────────────────────────────┘
```
- 侧栏可折叠（图标态）；窄屏自动收起为抽屉。
- 顶栏右侧：主题切换 + 本次会话预算/用量小窗（点击展开明细）。

## 四、页面线框

### 1. 首页 / 新建（`/`）
```
┌───────────────────────────────────────────────────┐
│  开始一个学习方案                                  │
│  选择你已有的材料，或直接告诉我你想学什么           │
├───────────────────────────────────────────────────┤
│ [上传文件]  [粘贴文本]  [网页链接]  [描述需求]  ← Tabs│
├───────────────────────────────────────────────────┤
│  拖拽文件到此处 或 点击选择                        │
│  支持 PDF / DOCX / PPTX / TXT / MD                │
│  ── 已添加材料 ──                                 │
│   📄 react入门.pdf  12页  [×]                      │
│   📄 notes.md       3KB  [×]                       │
├───────────────────────────────────────────────────┤
│  我想学：[_______________________________]         │
│  （可选，留空则从材料推断）                         │
├───────────────────────────────────────────────────┤
│                         [开始生成 →]               │
└───────────────────────────────────────────────────┘
```
- 多模态可混合：同时上传文件 + 填描述。
- "开始生成" -> 创建 session -> 若 needs_clarify 跳澄清页，否则跳生成中页。

### 2. 澄清对话（`/clarify/:sessionId`）
```
┌───────────────────────────────────────────────────┐
│  帮我把方案定得更准                                 │
├───────────────────────────────────────────────────┤
│  🤖 你之前接触过这个方向吗？目前是什么基础？        │
│  🧑 [零基础]                                       │
│  🤖 想学到什么程度？找工作/做项目/兴趣了解？         │
│  🧑 [做项目]                                       │
│  🤖 每天大概能投入多少时间？                        │
│  🧑 [1-2 小时]                                     │
├───────────────────────────────────────────────────┤
│  快捷回复：[零基础][入门][1-2h][找工作]...          │
│                              [继续生成 →]          │
└───────────────────────────────────────────────────┘
```
- 气泡式；每轮快捷选项按钮；"继续生成"提交澄清结果进入生成中。

### 3. 生成中（`/generate/:sessionId`）★核心
```
┌───────────────────────────────────────────────────┐
│  正在生成方案...                                   │
│  ▓▓▓▓▓▓▓▓░░░░  60%                                │
├───────────────────────────────────────────────────┤
│  ✅ parse_material      资料解析      1.2k tok     │
│  ✅ understand_intent   需求理解      0.8k tok     │
│  ⏳ build_knowledge_map 知识图谱  ▓▓▓░░  正在...   │
│  ○ design_stages       阶段规划                    │
│  ○ search_resources    资源检索                    │
│  ○ schedule_plan       排期                        │
│  ○ validate_plan       方案校验                    │
├───────────────────────────────────────────────────┤
│  实时用量：input 8.4k · output 1.1k · $0.07 · cache 97% │
│  [取消]                                  [查看部分→] │
└───────────────────────────────────────────────────┘
```
- 工具列表按 pipeline 顺序；每项状态：○ 待执行 / ⏳ 进行中 / ✅ 完成 / ❌ 失败。
- SSE 实时更新；失败项可点看错误；"查看部分"在生成中即可浏览已产出片段。
- 取消 -> 终止并保留已生成部分。

### 4. 方案详情（`/plans/:id`）★核心
```
┌──────────────────────────────────────────────────────┐
│  前端开发学习路线                       [导出 ▾] [✎重生成]│
│  零基础→做项目 · 约 12 周 · 每天 1-2h                  │
├──────────────────────────────────────────────────────┤
│ Tabs: [学习路径] [时间线] [资源] [校验报告] [自学材料(P2)]│
├──────────────────────────────────────────────────────┤
│ ▸ 学习路径                                            │
│   ▾ Stage 1: HTML & CSS 基础  (~2周, 难度2)  [展开/收起]│
│      目标：能用 HTML 组织内容、CSS 美化页面            │
│      知识点：· HTML 结构  · CSS 选择器  · 盒模型 ...    │
│      资源：✅ MDN  ✅ freeCodeCamp  ⚠️ 某博客          │
│      练习：· 个人主页 (~3h)                            │
│      里程碑：完成个人主页                              │
│   ▸ Stage 2: JavaScript 入门 (~3周, 难度2)            │
│   ▸ Stage 3: 工具链与框架 (~3周, 难度3)               │
│   ▸ Stage 4: 实战项目 (~4周, 难度4)                   │
├──────────────────────────────────────────────────────┤
│ 导出 ▾： [Markdown] [PDF] [幻灯片(HTML)]   ← build_plan_slides│
└──────────────────────────────────────────────────────┘
```
- 大纲可折叠（默认展开 Stage 1，其余收起）。
- 时间线 Tab：按日期排的日程（甘特/列表）。
- 资源 Tab：全部资源卡（✅⚠️❌ 筛选）。
- 校验报告 Tab：5 项检查通过状态 + 修复说明。
- 幻灯片导出 -> 新窗口打开 HTML。

### 5. 学习中心（`/learn/:planId`，P2）
- 进度看板（Kanban：未开始/学习中/已掌握）+ 打卡日历 + 复习提醒。
- 节点点击可标记状态 / 跳测验。

### 6. 测验（`/quiz/:planId`，P2）
- 单题卡片：题干 + 选项；提交后即时反馈（对错 + 解析）；进度条。

### 6.1 自学材料（`/plans/:id/materials`，P2）
```
┌──────────────────────────────────────────────────────┐
│ 自学材料：前端开发学习路线             [生成材料] [导出]│
├──────────────┬───────────────────────────────────────┤
│ 单元导航      │ 01-HTML 结构             ◀上一  下一▶ │
│ ✓ 01 HTML结构 │ 预估 45min · 前置：无                 │
│ ▸ 02 CSS基础  │ ── 概述 ──                             │
│   03 JS基础   │ <200-300 字引入>                       │
│   04 React组件│ ── 核心概念 ──                          │
│   ...         │ 概念1：...  概念2：...                 │
│              │ ── 实践练习 ──  ── 检查理解 ──           │
│ [标记已学]    │ [去测验]                               │
└──────────────┴───────────────────────────────────────┘
```
- 左侧单元导航（已学 ✓ / 当前 ▸ / 未学）；右侧阅读区；上一/下一；标记已学；"检查理解"跳测验。

### 7. 设置（`/settings`）
- API Key（掩码存储于本地 .env 对应后端配置）、模型选择、每日/每会话预算上限、用量统计图表、主题。

## 五、组件规格（关键组件）

| 组件 | 职责 | 关键 props / 状态 | 交互 |
|---|---|---|---|
| `AppShell` | 侧栏+顶栏+主区 | `collapsed`, `theme` | 折叠侧栏、切主题 |
| `MaterialUploader` | 多模态输入 | `tabs: file/text/url/desc`, `materials[]` | 拖拽上传、删除、切换 tab |
| `ChatClarify` | 澄清对话 | `messages[]`, `quickReplies[]` | 发送、快捷回复、继续 |
| `GenerateProgress` | 生成进度 | `steps[]`(`{tool,status,progress,tokens}`), `usage` | SSE 更新、取消、查看部分 |
| `PlanOutline` | 知识图谱/阶段树 | `plan`, `expandedIds` | 折叠展开、跳节点 |
| `StageCard` | 单阶段卡 | `stage` | 展开详情、标状态(P2) |
| `ResourceCard` | 资源卡 | `resource{verified}` | 跳外链、状态标签、筛选 |
| `Timeline` | 日程时间线 | `schedule[]` | 按日期分组 |
| `ValidationReport` | 校验报告 | `report`, `fixes[]` | 展示 5 项 + 修复说明 |
| `SlidesPreview` | 幻灯片入口(P1) | `planId`, `url?` | 触发生成/新窗口打开 |
| `UsageMeter` | 用量小窗 | `usage` | 悬停展开明细 |
| `ExportMenu` | 导出菜单 | `planId`, `formats` | md/pdf/slides |
| `StudyMaterialReader` | 自学材料阅读(P2) | `units[]`, `currentUnitId` | 单元导航、上下页、标记已学 |
| `EmptyState` / `Skeleton` / `Toast` | 辅助 | - | 加载/空/反馈 |

## 六、状态管理

### Zustand stores
- `useUIStore`：`theme: 'light'|'dark'`、`sidebarCollapsed`、`toast[]`
- `useSessionStore`：`currentSessionId`、`materials[]`、`clarifyAnswers`
- `useGenerateStore`：`steps[]`、`usage`、`status: idle|running|done|error`、`partialPlan`、SSE 连接控制
- `useUsageStore`：聚合用量（token/费用/cache）

### TanStack Query keys
- `['plans']` 方案列表；`['plan', id]` 方案详情
- `['materials', sessionId]`；`['usage']`
- 生成用 SSE 不走 Query；mutation：`useCreateSession`、`useGenerate`（SSE）、`useRegenerate`、`useExport`、`useSubmitQuiz`(P2)、`useUpdateProgress`(P2)

## 七、SSE 流式处理

- 封装 `usePlanGeneration(sessionId)` hook，内部用 `fetch('/api/plans/generate', {headers:{Authorization:`Bearer ${token}`}})` + `ReadableStream` 解析 SSE（参考 Claude Code/Anthropic SDK 的 Bearer 头流式；不用 EventSource 以便携带 Authorization 头）。
- 事件映射：`tool_start`->置 ⏳；`tool_progress`->更新进度文案；`tool_end`->置 ✅/❌ 并累加 usage；`plan_chunk`->合并 `partialPlan`；`done`->关闭流、置 done、跳详情页；`error`->置 error + Toast。
- 断线重连：`onerror` 时若非主动取消，指数退避重连最多 3 次；超限提示失败。
- 取消：`eventSource.close()` + 调后端取消（或前端置 canceled，后端按会话检测）。
- 缓存友好：SSE 仅为推送通道，不参与 LLM prompt prefix（prefix 在后端管理）。

## 八、用户流程（含异常分支）

**主流程（MVP）**
1. 首页：选输入方式 -> 加材料 + 填描述 -> 开始生成
2. 后端 needs_clarify? -> 澄清页逐轮问答 -> 继续生成
3. 生成中：SSE 实时进度，可取消/查看部分
4. 完成 -> 跳方案详情：浏览路径/时间线/资源/校验
5. 导出：Markdown / PDF / 幻灯片(HTML)

**异常分支**
- 文件解析失败：Uploader 内该文件标红 + 错误提示，可移除后重试，不阻塞其他材料。
- 澄清中用户跳过：用默认假设继续，详情页标注"基于默认假设"。
- 生成中工具失败：该项 ❌ + 错误可展开；非致命工具失败继续后续；致命中断 -> 保留部分结果 + 提示重试。
- 生成中网络断开：自动重连 3 次；失败提示，已产出部分仍可查看。
- 校验未通过：详情页校验 Tab 标红未过项 + 修复说明 + "按建议重生成"按钮。
- 导出失败：Toast 提示，幻灯片模板缺失降级为简单 HTML。
- 预算超限：生成中进度条标红 + 中断提示，保留部分结果。

## 九、响应式与无障碍

- 断点：桌面 ≥1024（侧栏常驻）/ 平板 768-1023（侧栏抽屉）/ 移动 <768（单列，P4 再优化）。
- 移动端 MVP 不重点优化（本地桌面优先），但保证不溢出、可滚动。
- 无障碍：语义化标签、键盘可达（Tab/Enter/Esc）、`aria-label`（图标按钮）、颜色对比度 AA、`prefers-reduced-motion` 关闭动画、资源 ✅⚠️❌ 同时用图标+文字（不只靠颜色）。

## 十、MVP 前端任务分解（P0）

- [ ] 项目骨架：Vite+React+TS+Tailwind+路由+TanStack Query+Zustand
- [ ] 设计系统：tokens、双主题、原子组件（Button/Card/Tag/...）
- [ ] AppShell（侧栏+顶栏+UsageMeter+主题切换）
- [ ] 首页/新建（MaterialUploader：上传+粘贴+链接+描述）
- [ ] 澄清对话页（ChatClarify）
- [ ] 生成中页（GenerateProgress + usePlanGeneration SSE hook）
- [ ] 方案详情页（PlanOutline + StageCard + ResourceCard + Timeline + ValidationReport + ExportMenu）
- [ ] 设置页（API Key/模型/预算）
- [ ] 路由守卫 + 空状态/骨架/Toast
- [ ] 端到端联调（见 04）

P1：SlidesPreview + 幻灯片预览；P2：学习中心 + 测验页 + 进度看板。

## 十一、与后端/Skill 的对接点

- 生成进度消费 `/api/plans/generate` SSE（事件格式见 [03](./03-api-frontend.md)）。
- 方案详情渲染 `plan` schema（见 [README](./README.md) 第四节）。
- 导出调 `/api/export/{plan_id}`（md/pdf/slides），slides 由 `build_plan_slides`（复用 ppt-slide-builder，见 [05](./05-skills.md)）产出 HTML，前端新窗口打开。
- 工具阶段名映射后端 `tool_calls`（见 [02](./02-tools.md)）。
