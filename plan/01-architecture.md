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

### 循环步骤
1. 组装 prompt：`system`（角色+方法论，固定）+ `tools`（工具定义，固定）+ `messages`（会话历史，只追加）。
2. 调用 Anthropic Messages API（`tools` 传入自研工具定义）。
3. 收到 `tool_use` 块 -> 用 pydantic schema 校验参数：
   - **工具调用修复（Pillar 2）**：schema 不符 -> 把校验错误以 `tool_result(is_error=true)` 反馈，要求模型修正，最多重试 3 次；超限则降级为默认参数并告警记录。
4. 执行工具（同步或异步），回填 `tool_result`。
5. 记录 `tool_calls`（审计）+ `api_usage`（成本计量，Pillar 3）。
6. 重复 2-5 直到 `stop_reason == "end_turn"`。
7. **预算守卫**：累计 cost 超阈值 -> 中断循环，返回已生成部分 + 告警。

### 缓存优先策略（Pillar 1）
- `system` 与 `tools` 作为**稳定前缀**，不随轮次变动（Anthropic prompt caching：`cache_control` 标记）。
- `messages` 历史**只追加**，不重写已存在内容。
- 方案校验反馈通过 `tool_result` 追加，而非改写历史。
- 长会话中保持高 cache hit，降低成本。

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

### 关系图（简）
```
users 1--* sessions 1--* materials
sessions 1--* plans 1--* knowledge_nodes
plans 1--* quizzes 1--* quiz_attempts
plans 1--* progress / learning_logs
sessions 1--* tool_calls
(全局) api_usage
```
