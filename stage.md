# stage.md - Deep-tutor 实施进度跟踪

> **本文件用途（用户原始要求，须始终遵循）**：
> 分阶段执行计划，不需要在本次会话全部完成，但是注意，你的所有操作进度都应该写入目录下的 `stage.md` 以便于后期在其他会话继续工作，或者中断后随时继续（请把这一段要求也直接写入 stage.md）。
>
> 因此：**每完成一个步骤、每次 git commit、每次关键决策或阻塞，都必须更新本文件**。新会话或中断恢复时，先读本文件确定"下一步做什么"，再继续。

---

## 〇、如何续接（给未来会话的自己）

1. 读本文件"四、进度日志"与"五、进度日志"确定当前进度与下一步。
2. 读 `plan/` 下相关计划文档确认实现细节（见"三、计划文档索引"）。
3. 检查 `git log --oneline` 与本文件记录的 commit hash 是否一致。
4. 遵守"二、执行约定"。
5. 继续执行下一个未完成步骤，完成后更新本文件并 commit。

**当前下一步**：步骤 3 - Agent 循环骨架（工具注册 + 调用修复 + 成本计量 + 预算守卫）。详见 §4.3。

---

## 一、总体阶段路线图

来源：[plan/README.md](plan/README.md) 第五节、[plan/04-mvp-risk.md](plan/04-mvp-risk.md)。

| Phase | 目标 | 状态 |
|---|---|---|
| **P1 MVP** | 资料/需求 -> 完整学习方案主线（大纲+路径+时间+资源+校验） | 🟡 进行中（步骤 1 完成） |
| P2 | 练习/测验生成 + 进度跟踪 + 掌握度评估 + 自学材料 + 批量导入 | ⬜ 未开始 |
| P3 | 图片 OCR + 音视频转写 | ⬜ 未开始 |
| P4 | 多用户 + 云部署 + Skills/MCP 扩展 + 浏览器插件 | ⬜ 未开始 |
| P5 | 缓存深度优化 + Anki/ics 导出 + 协作分享 | ⬜ 未开始 |

---

## 二、执行约定

- **技术栈**（[plan/01-architecture.md](plan/01-architecture.md)）：后端 Python 3.11+/FastAPI/Uvicorn/SQLAlchemy 2.0/SQLite/Alembic/pydantic v2/pydantic-settings/anthropic SDK；前端 React 18+TS+Vite+Tailwind+TanStack Query+React Router+Zustand+react-markdown；部署 docker-compose（nginx + uvicorn + 卷）。
- **git commit**：细粒度、每逻辑变更独立 commit；commit 信息中文；**严禁写 co-author / 协作者**（用户全局规范）。
- **不编造**：引用计划/技能前先读原文；不臆测接口与字段。
- **问而不猜**：遇歧义先问用户，除非用户已明确表示自行决定。
- **文字模型**：不读取/不处理图片等多模态内容。
- **当前分支**：`main`（逐步 commit）。
- **环境**：Windows 11 + Git Bash；Python 3.12.10、Node v26.3.0、npm 11.16.0、git 2.55。注意：Windows 上 `/tmp` 在 Git Bash 与 Python 解析不同，跨工具传 DB 路径时用相对或 Windows 绝对路径。

### 计划的 6 条假设（视为已确认，因用户指示"执行计划"）
1. LLM 以 Anthropic Claude 为主，Provider 抽象便于后续兼容 DeepSeek。
2. 本地单用户优先，认证最简（简单 token），预留多用户（JWT）。
3. MVP 持久化用 SQLite。
4. API Key 本地配置（.env），不入库。
5. MVP 解析范围：PDF/Word/Markdown/TXT/网页（OCR/音视频在 P3）。
6. 方法论密集 AI 工作流以 skill-for-skills 生成的 Skill 承载，基础设施类工具纯 Python。

### 已落地的实现期决策（偏离计划处已记录）
- **启动校验放宽**：`ANTHROPIC_API_KEY` / `BING_API_KEY` 仅功能级必填（调用生成/搜索前校验 + 启动告警），不强制启动退出；仅 `AUTH_TOKEN` 启动硬必填。原因：便于骨架/开发期先跑通非生成端点（health 等）。计划原意（R27）保留，后续可收紧。见 [backend/app/core/settings.py](backend/app/core/settings.py) `validate_required` / `validate_feature_keys`。
- **前端 tsconfig**：弃用项目引用（`tsc -b` + `tsconfig.node.json`），改单一 `tsconfig.json`（`noEmit` + include src 与 vite.config.ts）。原因：该 TS 版本 `tsc -b` 不允许被引用项目 `noEmit`（TS6310），且会产出 `vite.config.js` 被 Vite 误读。

---

## 三、计划文档索引（实现时参考）

| 文件 | 内容 |
|---|---|
| [01-architecture.md](plan/01-architecture.md) | 技术栈、系统架构、两层执行模型、Agent 循环（三支柱）、数据模型（全部表与字段）、存储策略 |
| [02-tools.md](plan/02-tools.md) | 13 个工具契约（输入/输出/错误/LLM/幂等）+ 资源验证规则 |
| [03-api-frontend.md](plan/03-api-frontend.md) | REST/SSE API、前端概览 |
| [04-mvp-risk.md](plan/04-mvp-risk.md) | P0/P1/P2 任务、路线图、风险表 R1-R35、第一步动作 |
| [05-skills.md](plan/05-skills.md) | Skill 层：双轨、Skill↔工具映射、运行时加载、复用既有技能 |
| [06-frontend.md](plan/06-frontend.md) | 设计系统、8 页面线框、14 组件、状态管理、SSE、无障碍 |
| [07-learning-workflows.md](plan/07-learning-workflows.md) | study-planner 7 步映射、self-study-guide generate_study_materials |
| [08-data-exchange-format.md](plan/08-data-exchange-format.md) | 类型系统、ToolInput/ToolOutput 信封、契约模板、流转契约 |
| [09-audit.md](plan/09-audit.md) | 存储策略、上下文溢出、安全、解析边界、降级矩阵、错误码、配置、部署 |
| [10-integration.md](plan/10-integration.md) | 两层执行模型、端到端序列、SSE 续接、澄清续接、反馈循环、状态机 |
| [11-extreme-cases.md](plan/11-extreme-cases.md) | 输入极端、资源耗尽、Pipeline 逻辑、降级默认、并发/状态、安全 |

---

## 四、Phase 1 实施步骤（来源 04-mvp-risk.md §四 第一步动作）

| # | 步骤 | 目标 commit message | 状态 | 进度 |
|---|---|---|---|---|
| 1 | 建骨架 | `chore: scaffold project skeleton` | ✅ 完成 | 见 §4.1（6 commits） |
| 2 | LLM Provider 抽象 + Anthropic 实现 | `feat(llm): provider abstraction + anthropic` | ✅ 完成 | 见 §4.2（commit `44c110b`） |
| 3 | Agent 循环骨架（工具注册+调用修复+成本计量+预算守卫） | `feat(agent): core loop with tool-call repair and cost tracking` | ⬜ | - |
| 4 | P0 工具链（每工具独立 commit） | `feat(tools): ...` | ⬜ | - |
| 5 | Skill 层（skill-chain-planner 规划 + skill-for-skills 生成 + Python 加载器） | `feat(skills): deep-tutor-* skills and loader` | ⬜ | - |
| 6 | API 层（materials/sessions/plans/export） | `feat(api): materials/sessions/plans endpoints` | ⬜ | - |
| 7 | 前端 P0 页面 | `feat(frontend): generate flow and plan detail` | ⬜ | - |
| 8 | 端到端联调 + 冒烟测试 | `test: e2e smoke for plan generation` | ⬜ | - |

### 4.1 步骤 1 - 建骨架（✅ 完成）

**目标**：可启动的后端 FastAPI 骨架 + 前端 Vite/React 骨架 + docker-compose + README + .env.example + core(settings/logging) + 数据层（SQLAlchemy 全表模型 + SQLite 初始化 + Alembic 脚手架 + 初始迁移）。

**产出目录结构**：
```
Deep-tutor/
  backend/
    app/{__init__, main}.py
    app/core/{__init__, settings, logging}.py
    app/db/{__init__, base, models}.py            # 15 表
    app/llm/{__init__, base}.py                    # LLMProvider 接口（step 2 实现）
    app/agent/__init__.py                          # 占位（step 3）
    app/tools/{__init__, base}.py                  # ToolInput/ToolOutput 信封
    app/skills/__init__.py                         # 占位（step 5）
    app/api/{__init__, deps, health}.py            # health + 认证依赖
    alembic/{env, script.py.mako, versions/}      # + 初始迁移 beba50db3cc1
    alembic.ini, requirements.txt, Dockerfile, .dockerignore
  frontend/
    src/{main, App, index.css, vite-env.d.ts}
    src/components/Layout.tsx, src/pages/Home.tsx
    src/lib/{api, sse}.ts, src/store/usage.ts
    package.json, vite.config.ts, tsconfig.json, tailwind.config.js, postcss.config.js
    index.html, Dockerfile, nginx.conf, .gitignore
  skills/README.md
  docker-compose.yml, README.md, .env.example, .gitignore
```

**进度记录（全部完成）**：
- [x] 后端骨架（main.py / CORS / 统一异常处理 / 路由注册 / lifespan 启动校验+建表）
- [x] core：settings（pydantic-settings，对齐 plan/09 §十七）+ logging（UTF-8 修复）
- [x] 数据层：SQLAlchemy 2.0 全表模型（15 表）+ SQLite WAL + init_db
- [x] Alembic 脚手架 + 初始迁移（autogenerate 15 表，upgrade 验证通过）
- [x] requirements.txt（P0 解析依赖）
- [x] 前端骨架（Vite+React+TS+Tailwind+Router+TanStack Query+Zustand+react-markdown）
- [x] docker-compose（backend uvicorn + frontend nginx + data 卷 + skills 只读挂载 + healthcheck）
- [x] README + .env.example + .gitignore
- [x] 自检：后端 import + 15 表建表 + /api/health 200；前端 npm install + build（tsc 类型检查 + vite 产物）
- [x] git commit（6 个）

**验证结果**：
- 后端：`from app.main import app` 通过；`init_db()` 建 15 表；TestClient `GET /api/health` -> 200；lifespan 启动校验生效。
- Alembic：`alembic current` 加载 env.py；`alembic revision --autogenerate` 检测 15 表；`alembic upgrade head` 建 15 表。
- 前端：`npm install`（241 包）+ `npm run build`（tsc --noEmit 通过 + vite 产物 dist/）。

**Commit 列表**：
- `e70e038` docs: 新增 stage.md 进度跟踪文件
- `4f07351` chore(backend): scaffold FastAPI app with models, settings, alembic
- `b7b695c` chore(frontend): scaffold Vite + React + TS + Tailwind app
- `980226f` fix(frontend): simplify tsconfig (no project refs), drop build artifacts
- `5928646` chore: add docker-compose, Dockerfiles, README, env example, gitignore
- `1f2535e` fix(db): alembic.ini 改 ASCII 注释 + 生成初始 schema 迁移

### 4.2 步骤 2 - LLM Provider 抽象 + Anthropic 实现（✅ 完成）

**目标**：在 [backend/app/llm/](backend/app/llm/) 实现 `AnthropicProvider`（继承已建的 `LLMProvider` 接口），落地三支柱中的「缓存优先」与「成本计量」基础。

**计划产出**：
- `app/llm/anthropic_provider.py`：`AnthropicProvider.chat()` —— Anthropic Messages API；`system` 作稳定前缀 + `cache_control`（Pillar 1）；解析 `usage`（input/output/cache_read/cache_creation）填 `LLMUsage`（Pillar 3）；`estimate_cost()` 按模型价目表。
- `app/llm/pricing.py`：模型 -> 单价（per 1M tokens：input/output/cache_read/cache_write）。
- `app/llm/__init__.py`：导出 `AnthropicProvider`；`get_provider()` 工厂（按 settings）。
- 单元测试（mock Anthropic 响应）：usage 解析、cost 估算、cache_control 标记。

**关键对齐**：[plan/01 §三 缓存优先策略 / 成本计量](plan/01-architecture.md)、[plan/10 内层工具调用循环](plan/10-integration.md)。`cache_system=True` 时给 system 块打 `cache_control`。

**未决（已解决）**：价目表用模型族前缀匹配（Sonnet $3/$15、Opus $15/$75、Haiku $0.8/$4，cache_read≈0.1×input、cache_write≈1.25×input），未知模型记 0（不编造），文件内标注核对链接 https://www.anthropic.com/pricing。可后续改为 settings 可配置。

**结果**：commit `44c110b`。9 项单测全过（usage 解析 / 文本拼接 / cache_control 开关 / 空 system / 成本估算含 cache / 未知模型记 0 / 前缀匹配 / get_provider 守卫）。`AsyncAnthropic` 用法已对齐 SDK 0.76.0（Usage 字段：input/output/cache_read_input/cache_creation_input_tokens）。

### 4.3 步骤 3 - Agent 循环骨架（下一步）

**目标**：在 [backend/app/agent/](backend/app/agent/) 落地两层执行模型的「内层工具调用循环」骨架 + 编排器骨架，集成 step 2 的 LLM Provider，实现三支柱完整闭环。

**计划产出**：
- `app/agent/registry.py`：工具注册表（tool_name -> Tool 可调用 + pydantic 输入/输出 schema）。
- `app/agent/loop.py`：内层工具调用循环 -- 组装 system(Skill) + user(args+upstream) -> LLM -> pydantic 校验 -> **工具调用修复**（schema 不符反馈重试≤3 -> 降级默认 + 告警 status=repaired）-> 产出 ToolOutput。
- `app/agent/cost.py`：usage -> api_usage 表 + tool_calls 成本归集。
- `app/agent/budget.py`：预算守卫（累计 cost 超 BUDGET_PER_SESSION_USD 中断 + 保留部分）。
- `app/agent/orchestrator.py`：外层确定性编排器（固定顺序 parse->understand->map->stage->resource->schedule->validate->export），字段覆盖检查，SSE 事件推送 hook，状态机持久化（orchestrator_state）。
- 测试：mock LLM，验证调用修复重试、降级、预算中断、字段覆盖检查。

**关键对齐**：[plan/01 §三 两层执行模型](plan/01-architecture.md)、[plan/10 端到端序列/状态机/反馈循环](plan/10-integration.md)、[plan/08 字段覆盖/工具调用修复](plan/08-data-exchange-format.md)、[plan/11 工具调用死循环上限/降级默认](plan/11-extreme-cases.md)。

**注意**：编排器与 SSE/持久化的完整集成留到 step 6（API 层）联调；step 3 先把可单测的循环核心做扎实。

---

## 五、进度日志（按时间倒序追加）

> 每条：日期 + 步骤 + 动作 + 结果 + commit hash（如有）。

- **2026-07-16 步骤 2 完成**：LLM Provider 抽象 + Anthropic 实现。`anthropic_provider.py`（AsyncAnthropic Messages API，system cache_control 缓存优先，usage 解析成本计量）、`pricing.py`（模型族前缀匹配价目，未知记 0）、`get_provider()` 工厂、`tests/test_llm.py`（9 项单测全过）。commit `44c110b`。
- **2026-07-16 步骤 1 完成**：建骨架（后端 FastAPI + 15 表模型 + Alembic 初始迁移；前端 Vite/React/TS/Tailwind；docker-compose + README + .env.example）。验证：后端 import/建表/health 200、alembic autogenerate+upgrade 15 表、前端 install+build 通过。6 commits：`e70e038` `4f07351` `b7b695c` `980226f` `5928646` `1f2535e`。
- **2026-07-16**：创建 `stage.md`，建立进度跟踪框架；完成环境检查（Python 3.12.10 / Node v26.3.0 / npm 11.16.0 / git 2.55）。准备开始步骤 1 建骨架。
