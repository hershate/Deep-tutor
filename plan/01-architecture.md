# 01 - 系统架构与数据模型

## 一、技术栈（混合栈）

### 后端：Python 3.11 + FastAPI
| 职责 | 选型 |
|---|---|
| Web 框架 | FastAPI + Uvicorn |
| 文档解析 | PyMuPDF(PDF)、python-docx、python-pptx、markdown、纯文本 |
| 网页抓取 | httpx + trafilatura（正文提取） |
| 图片 OCR | pytesseract / PaddleOCR（P3） |
| 音视频转写 | faster-whisper（P3） |
| AI 编排 | anthropic Python SDK + 自研 Agent 循环 |
| 持久化 | SQLite + SQLAlchemy 2.0（可迁移 Postgres） |
| 迁移 | Alembic |
| 异步任务 | asyncio 后台任务（预留 Celery） |
| 配置 | pydantic-settings（.env） |
| 校验 | pydantic v2 |

### 前端：TypeScript + React + Vite
| 职责 | 选型 |
|---|---|
| 框架 | React 18 + TypeScript + Vite |
| 样式 | TailwindCSS |
| 数据 | TanStack Query |
| 路由 | React Router |
| 流式 | EventSource（SSE） |
| 状态 | Zustand（轻量 UI 状态） |
| 富文本/大纲 | 自研渲染 + react-markdown |

### 部署：docker-compose
- `frontend`：Vite 构建产物由 nginx 托管
- `backend`：uvicorn 服务
- 卷：SQLite 文件 + 上传资料目录

## 二、系统架构

```
┌─────────────────────────────┐
│  浏览器 React UI            │  上传/输入 · 流式展示 · 进度看板 · 测验
└──────────────┬──────────────┘
          REST / SSE
┌──────────────┴──────────────┐
│  FastAPI 网关层             │  认证(简单token,预留JWT) · 文件上传/存储 · 会话管理
└──────────────┬──────────────┘
┌──────────────┴──────────────┐
│  Agent 编排核心             │  自研循环: 缓存优先 + 工具调用修复 + 成本计量 + 预算守卫
└──────────────┬──────────────┘
┌──────────────┴──────────────┐
│  自研工具集                 │  parse / understand / map / stage / resource / schedule
│                             │   / quiz / progress / validate / export
└──────────────┬──────────────┘
┌──────────────┴──────────────┐
│  LLM Provider 抽象          │  Anthropic Claude（可扩展 DeepSeek 等）
└──────────────┬──────────────┘
┌──────────────┴──────────────┐
│  持久化 SQLite              │  users/sessions/materials/plans/nodes/resources
│                             │   /quizzes/attempts/progress/logs/tool_calls/api_usage
└─────────────────────────────┘
```

## 三、Agent 循环详细设计（借鉴 Reasonix 三支柱）

### 执行模型（两层，详见 [10-integration.md](./10-integration.md)）
**外层 确定性编排器（Orchestrator）**：按固定顺序调度工具 pipeline：`parse -> understand -> map -> stage -> resource -> schedule -> validate（->反馈循环）-> export`（可选 slides/materials）。**非**"自由 Agent 让 Claude 选工具"（易跑偏、难复现、贵）。
**内层 工具调用循环（每 LLM 工具）**：一次 Claude 调用，`system` = 对应 Skill 的 SKILL.md 指令（稳定前缀，`cache_control`），`user` = `ToolInput.args` + `upstream.data`；输出 pydantic 校验，不符走工具调用修复。纯 Python 工具无 LLM 调用。

### 工具调用循环步骤（内层，每 LLM 工具）
1. 组装：`system` = Skill SKILL.md 指令（固定前缀）；`user` = `args` + `upstream.data`。
2. 调用 Anthropic Messages API。
3. pydantic 校验输出（`ToolOutput.data`）：
   - **工具调用修复（Pillar 2）**：schema 不符 -> 把校验错误反馈要求修正，最多重试 3 次；超限降级默认参数 + 告警，`status=repaired`。
4. 产出 `ToolOutput`（status/data/errors/metadata）。
5. 记录 `tool_calls`（审计）+ `api_usage`（成本计量，Pillar 3）。
6. 编排器：字段覆盖检查（upstream 覆盖下游 args 必填）-> 不符触发上游重生成或 error；**预算守卫**累计 cost 超限 -> 中断 + 保留部分；SSE 推送 `tool_start`/`tool_end`。

> 原"收到 tool_use / 重复到 end_turn"的自由 Agent 语义废弃，改为此两层模型。完整端到端序列、澄清续接、反馈循环见 [10-integration.md](./10-integration.md)。

### 缓存优先策略（Pillar 1）
- 每个 LLM 工具的 `system`（Skill SKILL.md 指令）作为**稳定前缀**，`cache_control` 标记，跨会话/跨调用复用，提高 cache hit。
- 同一工具的修复重试：校验错误作为新轮 `user` 追加，不重写已有 system/user。
- 长会话/多方案生成中保持高 cache hit，降低成本。

### 成本计量（Pillar 3）
- 每次 API 调用解析 `usage`：`input_tokens` / `output_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens`。
- 按模型计价写入 `api_usage` 表。
- 会话级聚合，前端实时展示（token / 费用 / cache hit 率）。

### 流式与可观测性
- 生成过程通过 SSE 推送每个工具的 `开始 / 进度 / 完成 / 错误` 事件，前端实时可见。
- `tool_calls` 表记录每次调用的输入/输出/状态/耗时，便于审计与回放。

### Skill 层（基于 skill-for-skills，详见 05-skills.md）

- 架构位置：横跨"自研工具集"与"LLM Provider"之间。方法论密集工具调用 LLM 时，加载对应 Skill 的 SKILL.md 作为 system prompt 指令。
- 由 skill-for-skills 生成的 Skill：`deep-tutor-intent` / `-knowledge-map` / `-stages` / `-resources` / `-validate` / `-quiz`（P2）。
- Python 启动时扫描 `skills/`，建立 `skill_name -> SKILL.md` 注册表；调用对应工具时把 Skill 的 Workflow/Constraints/Examples 作为本次 LLM 调用的 system prompt（稳定前缀，缓存友好，符合 Pillar 1）。
- Skill 输出 schema 在其 Constraints 中固化，Python 端 pydantic 校验，不符走工具调用修复（Pillar 2）。
- Skill 升级（skill-for-skills UPGRADE）后重新加载即生效，无需改 Python 代码。
- 基础设施工具（parse_material / schedule_plan / record_progress / export_plan）为纯 Python，不建 Skill。
- 另复用既有 `ppt-slide-builder`（HTML 幻灯片）作为 `build_plan_slides` 工具的方法论与模板源（`templates/base.html`），非 skill-for-skills 生成。

## 四、数据模型（SQLite / SQLAlchemy）

### 表与字段

#### users
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| name | str | 显示名（本地单用户默认 1 个） |
| created_at | datetime | |

#### sessions
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| user_id | FK | |
| title | str | 会话标题 |
| created_at | datetime | |

#### orchestrator_state（编排器状态，1:1 与 session，见 [10](./10-integration.md) 第十一节）
| 字段 | 类型 | 说明 |
|---|---|---|
| session_id | PK/FK | |
| status | enum | idle/running/paused_clarify/done/error/interrupted |
| current_tool | str? | 当前执行工具 |
| partial_plan | json | 已产出部分 |
| tool_call_ids | json | 已完成工具调用 id 列表 |
| budget_used | float | 已用预算 |
| trace_id | str | 贯穿审计 |
| last_event_id | str? | SSE 续接游标 |
| updated_at | datetime | |

#### materials
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| session_id | FK | |
| kind | enum | file/text/url/image/audio/video |
| source | str | 文件路径/URL/原文摘要 |
| parsed_text | text | 解析后纯文本 |
| meta | json | 页数、字符数、来源元信息 |
| created_at | datetime | |

#### plans
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| session_id | FK | |
| content | json | 完整 plan schema |
| version | int | 版本号（支持重生成） |
| created_at | datetime | |

#### knowledge_nodes
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| plan_id | FK | |
| name | str | |
| difficulty | int(1-5) | |
| must_learn | bool | |
| parent_id | FK? | 依赖关系 |

#### resources
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| node_id | FK? | 关联知识点（或 stage） |
| url | str | |
| title | str | |
| type | enum | tutorial/video/doc/book |
| verified | enum | ok/uncertain/invalid |
| note | str | |

#### quizzes
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| plan_id | FK | |
| node_id | FK | |
| type | enum | choice/fill/short/essay |
| payload | json | 题干/选项/答案/解析 |

#### quiz_attempts
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| quiz_id | FK | |
| user_answer | json | |
| correct | bool? | |
| created_at | datetime | |

#### progress
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| plan_id | FK | |
| node_id | FK | |
| status | enum | not_started/learning/done |
| mastery | float(0-1) | 掌握度 |
| updated_at | datetime | |

#### learning_logs
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| plan_id | FK | |
| date | date | |
| minutes | int | |
| note | str? | |

#### tool_calls（Agent 审计 + 成本归集）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| session_id | FK | |
| tool | str | 工具名 |
| input | json | |
| output | json | |
| status | enum | ok/error/repaired |
| error | str? | |
| llm_tokens_in | int | |
| llm_tokens_out | int | |
| cost | float | |
| duration_ms | int | |
| created_at | datetime | |

#### api_usage（原始 API 用量）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| provider | str | anthropic |
| model | str | |
| tokens_in | int | |
| tokens_out | int | |
| cache_read | int | |
| cache_write | int | |
| cost | float | |
| created_at | datetime | |

#### study_units（自学材料单元，P2）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| plan_id | FK | |
| node_id | FK? | 关联知识点 |
| stage_index | int? | 关联阶段 |
| title | str | 单元名 |
| prerequisites | json | 前置知识 |
| objectives | json | 学习目标 |
| estimated_time | str | 预估时间 |
| order | int | 学习顺序 |

#### study_materials（单元内容，P2）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| unit_id | FK | |
| content_md | text | Markdown 内容 |
| content_html | text | 渲染后 HTML |
| resources | json | 扩展阅读链接 |
| created_at | datetime | |

### 关系图（简）
```
users 1--* sessions 1--* materials
sessions 1--* plans 1--* knowledge_nodes
plans 1--* quizzes 1--* quiz_attempts
plans 1--* progress / learning_logs
plans 1--* study_units 1--* study_materials (P2)
sessions 1--* tool_calls
(全局) api_usage
```

## 五、存储策略（DB vs 文件系统，详见 [09](./09-audit.md)）

大内容入文件系统，DB 存路径 + 元数据，避免 SQLite 膨胀/锁/备份成本：
- **文件存储**：上传原件、解析文本(parsed_text)、自学材料(content_md/content_html)、导出/幻灯片、大 `plan.content`(>512KB 溢出)。
- **DB 存储**：元数据、路径、进度、`tool_calls`、`api_usage`、小 JSON。
- **目录**：`data/deep-tutor.db` + `data/sessions/<sid>/{originals,parsed,plans,materials,exports}/`。
- **SQLite**：WAL 模式、`synchronous=NORMAL`；定期 VACUUM；删 session 级联删目录；P4 迁 Postgres + 对象存储。
- **分层**：08 工具信封在内存传递实际文本；持久化层写文件、DB 存路径，两层分离。
