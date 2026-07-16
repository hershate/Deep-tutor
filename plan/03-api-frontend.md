# 03 - API 设计与前端

## 一、后端 API

基础路径 `/api`。本地单用户用简单 token（`Authorization: Bearer <token>`，配置于 .env），预留 JWT 多用户。

| 方法 | 路径 | 请求 | 响应 | 说明 |
|---|---|---|---|---|
| POST | /materials/upload | multipart 文件 | `{ material_id, kind, meta }` | 上传文件解析 |
| POST | /materials/url | `{ url }` | `{ material_id, kind:"url", meta }` | 网页链接抓取 |
| POST | /materials/text | `{ text }` | `{ material_id, kind:"text" }` | 纯文本提交 |
| POST | /sessions | `{ material_ids:[], title? }` | `{ session_id }` | 创建会话 |
| POST | /plans/generate | `{ session_id, raw_text? }` | SSE 流 | 生成方案（流式推送工具进度） |
| GET | /plans | - | `[{ id, title, topic, created_at }]` | 方案列表 |
| GET | /plans/{id} | - | `{ plan }` | 方案详情 |
| POST | /plans/{id}/regenerate | `{ feedback }` | SSE 流 | 按反馈局部重生成 |
| POST | /quizzes/{id}/submit | `{ user_answer }` | `{ correct, answer, explanation }` | 提交作答（P2） |
| GET | /progress/{plan_id} | - | `{ nodes:[{id,status,mastery}], mastery_avg }` | 进度总览（P2） |
| POST | /progress | `{ plan_id, node_id, status }` | `{ mastery }` | 更新进度（P2） |
| POST | /export/{plan_id} | `{ format: md\|pdf\|slides }` | `{ file_url }` | 导出 md/pdf/slides（slides 为 HTML 幻灯片） |
| POST | /plans/{id}/materials | `{ scope?, depth? }` | SSE 流 | 生成自学材料（P2，复用 self-study-guide） |
| GET | /plans/{id}/materials | - | `{ units:[] }` | 自学材料单元列表（P2） |
| GET | /materials/{unit_id} | - | `{ content_html, resources }` | 单元内容（P2） |
| GET | /usage | - | `{ total_tokens, total_cost, cache_hit_rate, by_session:[] }` | 成本统计 |

### SSE 事件格式（/plans/generate）
```
event: tool_start   data: { tool: "build_knowledge_map" }
event: tool_progress data: { tool, message }
event: tool_end      data: { tool, status, tokens, cost }
event: plan_chunk    data: { partial_plan }   # 增量方案片段
event: done          data: { plan_id, metadata }
event: error         data: { message, stage }
```

### 错误响应统一格式
```json
{ "error": { "code": "unsupported_format", "message": "...", "details": {} } }
```

## 二、前端设计

> 详细 UI 设计（设计系统、页面线框、组件规格、状态管理、SSE、流程、无障碍）见 [06-frontend.md](./06-frontend.md)。

### 技术栈
React 18 + TypeScript + Vite + TailwindCSS + TanStack Query + React Router + Zustand + EventSource(SSE) + react-markdown。

### 页面
| 路由 | 页面 | 主要内容 |
|---|---|---|
| `/` | 首页/新建 | 选择输入方式（上传/粘贴/链接/描述），多资料合并，发起生成 |
| `/clarify/:sessionId` | 澄清对话 | 当 needs_clarify 时对话式追问（基础/目标/时长） |
| `/generate/:sessionId` | 生成中 | SSE 实时进度（当前工具、token/成本/进度条） |
| `/plans/:id` | 方案详情 | 可折叠知识图谱 + 分阶段卡片 + 资源卡(✅⚠️) + 时间线 + 校验报告 + 导出 |
| `/learn/:planId` | 学习中心 | 进度看板、打卡、复习提醒（P2） |
| `/quiz/:planId` | 测验 | 答题 + 即时反馈（P2） |
| `/settings` | 设置 | API Key、模型选择、预算上限、用量统计 |

### 组件
- `Layout`（顶栏 + 侧栏 + 用量小窗）
- `MaterialUploader`（拖拽上传 + 链接 + 粘贴，多文件）
- `ChatClarify`（追问气泡）
- `GenerateProgress`（SSE 进度条 + 当前工具 + 实时 token/cost）
- `PlanOutline`（可折叠知识图谱）
- `StageCard`（阶段：目标/知识点/资源/练习/里程碑）
- `ResourceCard`（标题 + ✅⚠️❌ 标记 + 类型）
- `Timeline`（日程时间线）
- `ValidationReport`（5 项检查结果 + 修复说明）
- `ProgressBoard`（P2，Kanban）
- `QuizCard`（P2）
- `UsageMeter`（token/费用/cache hit）
- `SlidesPreview`（P1，幻灯片 HTML 预览/下载入口）

### 用户主流程（MVP）
1. 首页选择"描述需求" + 上传一份 PDF -> 创建会话
2. 若需澄清 -> 对话追问基础/目标/时长
3. 进入生成中 -> SSE 实时看到 Agent 依次执行 parse→understand→map→stage→resource→schedule→validate
4. 生成完成 -> 方案详情页：浏览大纲/阶段/资源/时间线/校验报告
5. 导出 Markdown / PDF / 幻灯片（HTML）

### 交互细节
- 资源卡点击直接跳转外链；⚠️ 标记提示"建议人工确认"。
- 校验报告展示 5 项检查是否通过 + 自动修复说明。
- 用量小窗实时显示本次会话 token/费用。
- 方案详情支持折叠/展开阶段，便于长方案浏览。
- 幻灯片导出为新窗口打开自包含 HTML（键盘方向键 / 滚轮 / 点击翻页）。
