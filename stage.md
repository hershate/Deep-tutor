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

**当前下一步**：Step 5 Skill 层进行中（iter 16 加载器基础设施落地，128 测试）；待做：生成 `deep-tutor-*` Skill 内容（iter 17+，待决 skill-for-skills 调用方式）、vendor `ppt-slide-builder`/`self-study-guide`、真机 Anthropic 冒烟（需 key）、slides。详见 §五 2026-07-17 iter 16 条。

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
- **PDF 解析用 Marker**（用户指定，2026-07-16）：parse_material 的 PDF 路径改用 `marker_single <文件> --force_ocr --output_dir <输出目录>`（默认参数），替代原计划的 PyMuPDF。Marker 输出 `<out>/<stem>/<stem>.md` + `<stem>_meta.json`（页数取自 `page_stats`）；subprocess 调用，超时 600s（OCR 较慢）；requirements 以 `marker-pdf` 替换 PyMuPDF。见 [backend/app/tools/parse_material.py](backend/app/tools/parse_material.py) `_parse_pdf`。

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
| 3 | Agent 循环骨架（工具注册+调用修复+成本计量+预算守卫） | `feat(agent): core loop with tool-call repair and cost tracking` | ✅ 完成 | 见 §4.3（commit `a46e1e0`） |
| 4 | P0 工具链（每工具独立 commit） | `feat(tools): ...` | ✅ 完成 | 见 §4.4（8 工具，commit 45d194c…9de452e） |
| 5 | Skill 层（skill-chain-planner 规划 + skill-for-skills 生成 + Python 加载器） | `feat(skills): deep-tutor-* skills and loader` | 🟡 进行中 | 见 §4.5（iter 16 加载器落地，128 测试；待生成 Skill 内容） |
| 6 | API 层（materials/sessions/plans/export） | `feat(api): materials/sessions/plans endpoints` | ✅ 完成 | 见 §4.6（P0 API 全完成，5 次迭代，91 测试） |
| 7 | 前端 P0 页面 | `feat(frontend): generate flow and plan detail` | ✅ 完成 | 见 §五（P0 页面 + SSE 续接 + Usage/Settings + a11y） |
| 8 | 端到端联调 + 冒烟测试 | `test: e2e smoke for plan generation` | 🟡 进行中 | 见 §五（mock 冒烟完成，110 测试；真机 Anthropic 冒烟待 key） |

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

### 4.3 步骤 3 - Agent 循环骨架（✅ 完成）

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

**结果**：commit `a46e1e0`。产出 `registry / context / budget / cost / loop / orchestrator` 6 模块。17 项单测全过（Python 工具 / LLM 校验 / 修复重试累加 usage / JSON 代码块提取 / 降级默认 repaired / 无默认 error / Skill 缺失降级默认 prompt / 无 provider 降级 / 字段覆盖 / 预算超限中断 / pipeline 顺序 / 反馈循环重跑 design_stages / 澄清暂停）。全量 26 测试通过（LLM 9 + agent 17）。

**实现期决策（记录）**：
- 工具调用修复用「文本 JSON + 反馈重试」实现（对齐 08 worked example：Claude 返回 JSON 经 pydantic 校验，不符反馈错误重试≤3，耗尽降级 default_data→repaired / 无 default→error）。plan 提及的 `tool_result(is_error=true)` 是 Anthropic tool_use 流程的等价语义，此处用反馈消息近似；后续如改用 tool_use 结构化输出可在 provider 层扩展。
- 工具调用上限 20、反馈循环≤2、修复重试≤3 已落地常量。
- SSE 事件回调 `event_cb(event, data)` 已留接口；DB 持久化（tool_calls/api_usage/orchestrator_state）与 SSE 续接留 step 6 接入。

### 4.4 步骤 4 - P0 工具链（✅ 完成）

**目标**：在 [backend/app/tools/](backend/app/tools/) 逐工具实现 P0 工具，注册到 ToolRegistry，每工具独立 commit。契约对齐 [plan/02](plan/02-tools.md)、信封对齐 [plan/08](plan/08-data-exchange-format.md)。

**工具清单与顺序**（每工具一个 commit）：
1. `parse_material`（Python）：PDF/DOCX/PPTX/TXT/MD + text + url -> {text, meta, chunks}；空文本 `empty_content`；不支持 `unsupported_format`；降级 `degraded`。
2. `understand_intent`（LLM，Skill=deep-tutor-intent）：{topic, level?, goal?, daily_minutes?, deadline?, needs_clarify, clarify_questions}。
3. `build_knowledge_map`（LLM，Skill=deep-tutor-knowledge-map）：{nodes, edges}；环检测（DFS）-> 断 DAG；悬空边丢弃；节点上限。
4. `design_stages`（LLM，Skill=deep-tutor-stages）：{stages:[{index,name,goal,difficulty,estimated_weeks,topics,milestone}]}；阶段数<3 重生成。
5. `search_resources` + `verify_url`（检索 Python Bing + 筛选 LLM，Skill=deep-tutor-resources）：{resources:[{stage_index,title,url,type,verified,note}]}；✅⚠️❌ 规则；不编造 URL。
6. `schedule_plan`（LLM 无 Skill，内联 prompt + 确定性总量校验）：{schedule}；时长不足 `insufficient_time`。
7. `validate_plan`（LLM，Skill=deep-tutor-validate）：{report(5 项), passed, fixes}。
8. `export_plan`（Python）：md 导出 -> {file_url}。

**前置依赖**：步骤 5 的 Skill 加载器为 LLM 工具提供 system prompt。步骤 4 可先用占位 prompt / 内联 prompt 推进 Python 工具与契约，LLM 工具的 Skill 在步骤 5 接入后替换。建议顺序：先 parse_material（纯 Python，可立即单测），再 understand_intent（需 LLM，先内联 prompt）。

**关键对齐**：[plan/02 工具契约](plan/02-tools.md)、[plan/07 study-planner 7 步映射](plan/07-learning-workflows.md)、[plan/11 降级默认值/上限](plan/11-extreme-cases.md)。

**结果（8 工具全部完成，每工具独立 commit）**：
- `45d194c` parse_material（Python）：PDF via Marker（marker_single --force_ocr）+ docx/pptx/txt/md/text/url；empty_content/unsupported_format/degraded；500k 截断；分块。
- `2d8aa22` understand_intent（LLM，deep-tutor-intent）：{topic,level?,goal?,daily_minutes?,deadline?,needs_clarify,clarify_questions}；缺失->澄清。
- `0bdd18b` build_knowledge_map（LLM，deep-tutor-knowledge-map）：{nodes,edges}；post_process 断环(DFS)->DAG、去悬空边、节点>100 截断。
- `d33b1be` design_stages（LLM，deep-tutor-stages）：{stages}；pydantic validator 强制 3-6 阶段（不符->修复重试，实现「阶段数<3 重生成」）。
- `1be7cb1` validate_plan（LLM，deep-tutor-validate）：{report(5 项),passed,fixes}；驱动反馈循环。
- `5a7b4a4` export_plan（Python）：MVP md；从 args.plan 或 upstream 组装；render_markdown；不支持格式降级 md。
- `568a6c2` schedule_plan（LLM 内联 prompt + 确定性）：{schedule}；post_process 校验总量<=跨度×每日时长，不足->insufficient_time 警告。
- `9de452e` search_resources + verify_url（Python）：Bing 检索 + verify_url（SSRF 防护 R19，✅⚠️❌）；无 key 降级不编造 URL；每阶段≤5、总≤40。

**关键机制落地**：Tool 加 `inline_prompt`（step 5 Skill 未接入兜底）+ `post_process(data, inp)`（LLM 输出清洗）；loop `model_dump(by_alias=True)` 保 `from` 字段名；pydantic validator 触发修复重试（业务规则）。全量 70 快速测试通过 + 1 marker 集成测试。

### 4.5 步骤 5 - Skill 层（下一步）

**目标**：将 LLM 工具的 `inline_prompt` 替换为 skill-for-skills 生成的 `deep-tutor-*` Skill（方法论指令源），并实现 Python Skill 加载器。

**计划产出**：
- 用 skill-chain-planner 规划主链路（产出接口契约与创建顺序，与 plan/08 互校）。
- skill-for-skills 生成 Skill：`deep-tutor-intent` / `-knowledge-map` / `-stages` / `-resources`（含 verify 方法论 + ✅⚠️❌ 规则）/ `-validate`（5 项检查）。放入 `skills/`。
- `app/skills/loader.py`：启动扫描 `SKILLS_DIR` -> `{skill_name: SKILL.md body}` 注册表 -> 注入 `ctx.skill_registry`（loop 已支持 skill->inline->默认 三级回退）。
- vendor 复用：`ppt-slide-builder`（P1）/ `self-study-guide`（P2）复制进 `skills/`（自 `~/.claude/skills/`）。
- 接入后 LLM 工具的 system prompt 由 Skill 提供，`inline_prompt` 降为兜底；search_resources 的内容适配性筛选由 deep-tutor-resources LLM 提升/下调 ✅⚠️。
- 测试：Skill 加载器（扫描/缺失降级 R13）；LLM 工具用 Skill prompt（替换 inline）。

**关键对齐**：[plan/05 Skill 层](plan/05-skills.md)、[plan/10 §七 Skill↔工具映射](plan/10-integration.md)。**前置**：需确认本机 `~/.claude/skills/` 下 skill-for-skills / skill-chain-planner 可用（chat.md 记录复用既有技能）。

---

## 五、进度日志（按时间倒序追加）

> 每条：日期 + 步骤 + 动作 + 结果 + commit hash（如有）。

- **2026-07-17 Step 5 进行中（iter 16，Skill 加载器基础设施）**：落地 plan/05 §三 Skill 层基础设施（**不生成 Skill 内容**，向后兼容）。新增 [loader.py](backend/app/skills/loader.py)：`load_skills(skills_dir)->dict` 纯函数（扫描 `<name>/SKILL.md`，逐行剥 frontmatter，name 取 frontmatter `name:` 或目录名，BOM/CRLF 容忍，单文件失败跳过+告警，缺目录返 `{}`，**永不抛**）+ `get_skill_registry()` 模块缓存 + `reset_skill_registry()` 测试隔离。[main.py](backend/app/main.py) lifespan 启动扫描 + 日志加载数。[plans.py](backend/app/api/plans.py) `_run_start` 注入 `skill_registry=get_skill_registry() or None`（空->None->loop 走 inline_prompt，**零行为变更**；loop 三级回退 skill->inline->默认 不动）。[conftest.py](backend/tests/conftest.py) `isolate` 每测 `reset_skill_registry()`。[test_skill_loader.py](backend/tests/test_skill_loader.py) +10（frontmatter/无/回退/空正文/缺目录/无 SKILL.md/重名/剥 frontmatter/BOM/CRLF）。全量 128 测试通过（118+10），零回归。风险已逐项排查：路径解析沿用 CWD 约定（Docker 绝对/测试 temp/dev 方式1 项目根；dev 方式2 优雅降级不崩）；缓存事件循环单线程读写无竞态；仅 `_run_start` 注入（exports/materials 纯 Python 不需）。commit `7d1a0d0`。待做：生成 `deep-tutor-*` Skill 内容（待决 skill-for-skills 会话内调用 vs 手工编写）。
- **2026-07-17 稳定性修复（iter 15，regenerate 局部重生成 scope 失效）**：审计发现 [plans.py](backend/app/api/plans.py) `_run_start` 硬编码 `start_tool="understand_intent"`，致 `/regenerate` 的 scope 被忽略 -- 无论 scope 为何都从 understand_intent 全量重跑（5 次 LLM 调用，应为 3 次），违反 plan/10 §十三「重跑范围最小化：从受影响工具起级联重跑其下游」，且无谓重跑 understand_intent/build_knowledge_map（本应保留的上游可能被 LLM 改写）+ 浪费成本。修复：`_run_start` 增 `start_tool: str = "understand_intent"` 参数（generate/clarify 默认值不变），`regenerate` 传 `start_tool=body.scope`。测试 `test_regenerate_partial_rerun` 改用共享 provider 单例（旧 `lambda: RegenProvider()` 每次新建实例，致 5+3 响应分割失效、掩盖 bug）+ 增 `assert provider.i == 8`（5 初始 + 3 重生成）显式验证 scope 起跑；已验证回退修复则测试失败（IndexError 耗尽响应）。全量 118 后端测试通过。
- **2026-07-17 稳定性加固（iter 14，取消线程安全 + DELETE 先取消）**：[plans.py](backend/app/api/plans.py) 抽出 `cancel_running_generation(sid)->bool`，改用 `task.get_loop().call_soon_threadsafe(task.cancel)`（同步调用方如 DELETE /sessions 跑线程池，跨线程/跨循环，旧 `task.cancel()` 仅同事件循环内安全）；`cancel` 端点改用之。[sessions.py](backend/app/api/sessions.py) DELETE /sessions 删行前先 `cancel_running_generation`，防僵尸编排器任务 `_finalize` 撞已删会话 FK（plan/10 §十一 running 态冲突）。测试 +1（`test_cancel_running_generation_schedules_cancel`：无 task/活动/终态/不存在 四情形），全量 118 后端测试通过。commit `dc92b7e`。
- **2026-07-17 P1（iter 13，重生成前端 UI）**：落地 /regenerate 前端，前后端闭环。[api.ts](frontend/src/lib/api.ts) `regenerateStream`（复用 `ssePostWithReconnect` 断线重连）。[PlanDetail.tsx](frontend/src/pages/PlanDetail.tsx) 增重生成区：scope `<select>`（design_stages/search_resources/schedule_plan/validate_plan）+ feedback `<textarea>` + 按钮；`done` -> nav 新 plan；error/budget_exceeded -> `regenError` 内联（**独立于加载 error**，不触发早返回覆盖方案视图）；卸载 `abort` 清理。label/htmlFor 关联 + `role="alert"`。前端 build 通过（91 模块）。
- **2026-07-17 P1（iter 12，/regenerate 端点）**：落地 plan/10 §十三 局部重生成。后端 [plans.py](backend/app/api/plans.py) `POST /plans/{plan_id}/regenerate {feedback, scope}` -- scope 为 PIPELINE 工具名（understand_intent 之后），从 tool_calls 重建 `initial_upstream`（`_reconstruct_upstream` 取每工具最新 tool_call），`start_tool=scope` 级联重跑，feedback 注入 start_tool 的 `fixes`；复用 `_run_start`（SSE + done-callback 持久化）。`_persist_plan` 改为 version = 该会话 max+1（plan/10 §十三 `plans.version+=1`，首版 1）。校验：scope 非法 -> 400；understand_intent 不在上游 -> 400（需原始 raw_text，不支持）；running -> 409。测试：+3（局部重跑 version+1 / scope 非法 400 / understand_intent scope 400），全量 117 后端测试通过。待做：前端重生成 UI。
- **2026-07-17 步骤 7 进行中（iter 11，取消生成）**：落地 plan/06 §三/§八 + plan/10 §十一 取消功能（用户控制 + 预算保护）。后端 [plans.py](backend/app/api/plans.py) `POST /plans/{session_id}/cancel`（async，同事件循环内 `stream.task.cancel()` 安全）-- 取消活动流任务 -> done-callback `_finalize` 已处理 `task.cancelled()`（发 `error{code:cancelled}` + 置 error 态）；无运行任务 -> 409。前端 [api.ts](frontend/src/lib/api.ts) `cancelGeneration`；[GenerateProgress.tsx](frontend/src/pages/GenerateProgress.tsx) [取消] 按钮（仅 running 显示）+ `cancelled` 状态（区分于 error，中性样式，非 role=alert）。取消后 SSE 流收到 `error{cancelled}` -> 终态不重连。测试：+3（无运行 -> 409 / 已完成 -> 409 / `_finalize` cancelled 单元），全量 114 后端测试通过；前端 build 通过。
- **2026-07-17 稳定性加固（iter 10，反馈循环预算守卫）**：审计编排器发现 [orchestrator.py](backend/app/agent/orchestrator.py) `_rerun_from`（反馈循环重跑）累计 cost 但**未检查 `exceeded()`**，致反馈循环期间可超支且不中断（主循环预算检查仅在 `_exec` 后触发，反馈重跑绕过）。修复：`_rerun_from` 每次 `add` 后检查超限即 `break`；主循环反馈段后检查超限 -> 发 `budget_exceeded` + 返回 `interrupted`（先于 `validation_unresolved` 判定）。测试：+1（反馈重跑期间预算超限 -> interrupted，`export_plan` 未执行），全量 111 后端测试通过。审计另确认：LLM/网络调用超时均已落地（`ctx.timeouts.llm`、`MARKER_TIMEOUT`、`URL_TIMEOUT`、`SEARCH_TIMEOUT`），无挂死风险，未加循环级兜底（避免误杀 marker 600s OCR）。
- **2026-07-17 步骤 7 进行中（iter 9，无障碍 a11y）**：落地 plan/06 §九 无障碍基础。[index.css](frontend/src/index.css) 加 `prefers-reduced-motion` 关闭动画/过渡。[Clarify.tsx](frontend/src/pages/Clarify.tsx) label/input `htmlFor`/`id` 关联。[Home.tsx](frontend/src/pages/Home.tsx) 为 textarea/file input 加可见 `<label>`。[GenerateProgress.tsx](frontend/src/pages/GenerateProgress.tsx) 步骤状态图标加 `sr-only` 文字（进行中/失败/完成）。[PlanDetail.tsx](frontend/src/pages/PlanDetail.tsx) 资源 ✅⚠️❌ 加 `sr-only` 文字（已验证/不确定/无效，图标+文字不只靠颜色）。各页错误区加 `role="alert"`（Home/Clarify/GenerateProgress/PlanDetail/Usage/Settings）。前端 build 通过（91 模块）。待做：真机 Anthropic 冒烟（需 key）。**P0 前端基本完成**。
- **2026-07-17 步骤 8 进行中（iter 8，e2e 冒烟测试）**：新增 [test_e2e_smoke.py](backend/tests/test_e2e_smoke.py) -- 完整用户旅程冒烟（TestClient + mock LLM）：建会话 -> 加文本资料（验证 material->生成注入 `initial_upstream`，他测仅 raw_text）-> 生成(SSE `done`) -> 方案详情/列表 -> `/usage`（api_usage 落库，5 调用）-> `/settings`（不泄密）-> 导出 md。全量 110 后端测试通过。真机 Anthropic 冒烟需配置 key 后另跑。待做：无障碍、真机冒烟、R27 启动校验收紧。
- **2026-07-17 步骤 7 进行中（iter 7，Settings 只读页）**：用户定方向为只读展示（避免与 .env 假设 4 冲突）。后端新增 [settings.py](backend/app/api/settings.py) `GET /api/settings` -- 只读返回非敏感配置（模型/会话预算/每日预算/工具调用上限/上传上限/搜索引擎 + Key 是否已设 bool），**不返回任何明文密钥**；注册到 api_router。前端新增 [Settings.tsx](frontend/src/pages/Settings.tsx)（`dl/dt/dd` 列表只读展示，Key 状态绿/琥珀色标注）+ [api.ts](frontend/src/lib/api.ts) `getSettings`/`AppSettings`；[App.tsx](frontend/src/App.tsx) 加 /settings 路由，[Layout.tsx](frontend/src/components/Layout.tsx) 加导航。测试：+2（不泄密 + 需鉴权），全量 109 后端测试通过；前端 build 通过（91 模块）。待做：无障碍、步骤 8 联调。
- **2026-07-17 步骤 7 进行中（iter 6，Usage UI）**：前端用量展示（Pillar 3 成本可见性，plan/06 UsageMeter/用量页）。新增 [UsageMeter.tsx](frontend/src/components/UsageMeter.tsx)（顶栏小窗：总费用+token，TanStack Query 拉 /api/usage，窗口聚焦刷新，aria-label）+ [Usage.tsx](frontend/src/pages/Usage.tsx)（详情页：总 token/费用/缓存命中率/调用数 + 手动刷新，`dl/dt/dd` 语义）。[Layout.tsx](frontend/src/components/Layout.tsx) 加顶栏导航（首页/用量）+ UsageMeter。[App.tsx](frontend/src/App.tsx) 加 /usage 路由。[GenerateProgress.tsx](frontend/src/pages/GenerateProgress.tsx) 生成结束（非取消）后 `invalidateQueries(['usage'])` 刷新用量（api_usage 已在 done-callback 落库）。前端 build 通过（90 模块）。待做：Settings 页（范围待定，见下）、无障碍、步骤 8 联调。
  - **Settings 页范围未决**：plan/06 §四.7 列「API Key/模型/预算/主题」，但计划假设 4「API Key 本地配置（.env），不入库」与之冲突——可编辑 Key/模型/预算需后端运行时配置端点（覆盖 .env 或入库）。安全默认：只读展示当前后端配置（模型/预算/Key 是否已设，掩码）+ 主题切换，需新增 `GET /api/settings`（不泄密）。**需用户确认范围**。
- **2026-07-17 步骤 7 进行中（iter 5，SSE 续接前端重连）**：前端落地断线重连（plan/06 §七），消费后端 attach 能力，SSE 续接前后端闭环。[api.ts](frontend/src/lib/api.ts)：新增 `ssePostWithReconnect` -- 流未到终态（done/error/budget_exceeded/clarify_needed）即带 `Last-Event-ID` 重连，指数退避 1s/2s/4s 最多 3 次（初始+3 重试）；4xx 非瞬态（如 409 stale，408/429 除外）不重连直接抛；网络错误/5xx 重连；超限抛「连接中断，重连失败，请重试」；取消/卸载不重连。`generateStream` 改用之（返回 `Promise<void>`）；`clarifyStream` 保留单次（端点非幂等，续接应走 /generate attach）。[sse.ts](frontend/src/lib/sse.ts)：新增 `SseError`（携 HTTP 状态码）供上层区分瞬态/非瞬态。[GenerateProgress.tsx](frontend/src/pages/GenerateProgress.tsx)：简化 -- 移除 `terminalRef`/`.then()` 静默断连检测（重连由 wrapper 处理），`.catch` 仅捕重连耗尽/4xx。后端按 id>last_event_id 重放 -> 无重复事件/重复计费。前端 build 通过（tsc+vite）。待做：Settings/用量 UI、无障碍、步骤 8 端到端联调。
- **2026-07-17 步骤 7 进行中（iter 4，SSE 续接后端）**：落地 plan/10 §十二 事件缓冲 + 续接 + 持久化解耦（稳定性优先）。后端 [plans.py](backend/app/api/plans.py)：新增 `_SessionStream`（单会话有界事件缓冲 `_MAX_BUFFERED_EVENTS=500` + 订阅扇出；订阅时重放 id>last_event_id 再接实时；终态后保留 `_RETENTION_SECONDS=300s` 供迟到重连）。**持久化移至编排器任务 `done-callback`（`_finalize`）**，与流客户端解耦 -- 修复断连后 stale running / 丢进度（R24）：旧实现客户端断连时 `GeneratorExit` 跳过 `await task`，致 plan/状态不落库、会话永久 `running`（409 死锁）；新实现任务结束即持久化，无论客户端是否在连。`POST /generate` 读 `Last-Event-ID` 头：`running`+活动流 -> 续接（`_relay` 重放+实时+心跳）；`running`+无流（进程重启等 stale）-> 409 引导重生成；终态 -> 快路径（iter 3）。`_stream` 拆为 `_run_start`（建流+启任务+done-callback）/`_relay`（订阅投递+心跳+终态退出）/`_finalize`（持久化+发终态）。测试：+11（`_SessionStream` 语义 + `_relay` 续接/迟到重连，`asyncio.run` 驱动无 pytest-asyncio），conftest `isolate` 清 `_STREAMS` 跨测试隔离；全量 107 后端测试通过。待做：前端指数退避重连≤3（消费 Last-Event-ID 续接）、Settings/用量 UI、无障碍、步骤 8 联调。
- **2026-07-17 步骤 7 进行中（iter 3，SSE 终态快路径）**：落地 plan/10 §十二 终态快路径（稳定性：防重复生成）。后端 [plans.py](backend/app/api/plans.py)：`generate` 端点在 running 冲突检查后增 `_terminal_event` 快路径 -- `done` 查最新 plan 回放 `done {plan_id}`（不重跑，无新 plan）；`paused_clarify` 从 partial_plan 提取 `clarify_questions` 回放 `clarify_needed`；`error`/`interrupted` 回放终态 `error {code:previous_failed}`（不自动重跑，因硬刷新会丢 raw_text）；`idle`/状态不一致 -> 正常生成。新增 `_terminal_stream`（单事件即关闭）、`_clarify_questions_from_state`。前端无需改动（已处理 done/clarify_needed/error 事件）。测试：+3（done/paused_clarify/error 快路径），全量 96 后端测试通过。待做：SSE 续接完整版（事件缓冲 + running 重放 + 前端指数退避重连≤3）、Settings/用量 UI、无障碍、步骤 8 联调。
- **2026-07-17 步骤 7 进行中（iter 2，SSE 稳定性基础）**：落地 plan/10 §十二 断线续接游标基础（稳定性优先）。后端 [plans.py](backend/app/api/plans.py)：`_sse` 增 `event_id` 参数，每事件附递增 `id:` 行；`_stream` 加 `seq` 计数 + `emit` 闭包；空闲 >15s 发 `: keepalive` 心跳防 nginx/代理/浏览器空闲断连；终态持久化 `last_event_id` 到 `orchestrator_state`（DB 已有该列）。前端 [sse.ts](frontend/src/lib/sse.ts)：跟踪并返回 last event id（供续接回传 `Last-Event-ID`）；[GenerateProgress.tsx](frontend/src/pages/GenerateProgress.tsx) / [Clarify.tsx](frontend/src/pages/Clarify.tsx) 增 `terminalRef` 检测静默断连（流结束未收到终态事件且非主动取消 -> 提示「连接中断，请重试」），`.catch` 守护主动取消/卸载。测试：+1（事件 id 递增 + `last_event_id` 持久化），全量 93 后端测试通过；前端 build 通过。待做：SSE 续接完整版（后端事件缓冲 + GET 重放端点 + 终态快路径 + 前端指数退避重连≤3）、Settings/用量 UI、无障碍、步骤 8 联调。
- **2026-07-16 步骤 7 进行中**：前端 P0 页面（iter1）。Home（需求描述+文件上传->建会话）、GenerateProgress（消费 SSE 实时展示工具步骤+token/费用、clarify/done/error 路由）、Clarify（逐问回答->续接）、PlanDetail（意图/阶段卡/资源✅⚠️❌/校验报告+导出）。lib/api.ts Bearer 全端点、lib/sse.ts 支持 POST 流式、lib/types.ts。修复 orchestrator._assemble（组装完整方案组件）。frontend `npm run build` 通过（tsc+产物）。待做：Settings/用量 UI、SSE 续接、无障碍、步骤 8 端到端联调。commit: 见 8f3d…段。
- **2026-07-16 步骤 6 完成**：API 层 P0 全完成（5 次迭代）。errors 统一格式 + storage 路径安全(R20)；sessions/materials（Bearer 鉴权、大小/magic/SSRF 校验）；/plans/generate SSE（编排器+事件回调->SSE、运行态 409 R33、plan 持久化溢出、状态机）；/plans 列表/详情；/export；/clarify 澄清续接（partial_plan 重建 upstream）；/usage + tool_calls/api_usage 持久化（Pillar 3 端到端可见）。Orchestrator.run 加 start_tool+initial_upstream（支持续接/重生成）。全量 91 快速测试通过。commits: 9cb07f6…f4fc3c5 段。待办：/regenerate（P1）。
- **2026-07-16 步骤 4 完成**：P0 工具链 8 工具全部实现（每工具独立 commit）。parse_material（PDF via Marker + docx/pptx/txt/md/text/url）、understand_intent、build_knowledge_map（断环/去悬空/截断）、design_stages（3-6 阶段 validator 触发修复）、validate_plan（5 项校验驱动反馈循环）、export_plan（md）、schedule_plan（确定性时长校验）、search_resources+verify_url（Bing + SSRF 防护）。Tool 加 inline_prompt + post_process(data,inp)；全量 70 快速测试 + 1 marker 集成测试通过。commits: 45d194c 2d8aa22 0bdd18b d33b1be 1be7cb1 5a7b4a4 568a6c2 9de452e。
- **2026-07-16 步骤 3 完成**：Agent 循环骨架。两层执行模型落地：内层 `run_tool`（LLM 工具 system=Skill+cache_control、JSON 解析+pydantic 校验、工具调用修复重试≤3->降级；Python 工具 func；超时/取消/异常统一兜底；跨重试累加 usage），外层 `Orchestrator`（固定 pipeline + 字段覆盖 L4 + 反馈循环≤2 + 预算中断 + 澄清暂停 + SSE 事件回调）。`registry/context/budget/cost` 配套。17 项单测全过（全量 26）。commit `a46e1e0`。
- **2026-07-16 步骤 2 完成**：LLM Provider 抽象 + Anthropic 实现。`anthropic_provider.py`（AsyncAnthropic Messages API，system cache_control 缓存优先，usage 解析成本计量）、`pricing.py`（模型族前缀匹配价目，未知记 0）、`get_provider()` 工厂、`tests/test_llm.py`（9 项单测全过）。commit `44c110b`。
- **2026-07-16 步骤 1 完成**：建骨架（后端 FastAPI + 15 表模型 + Alembic 初始迁移；前端 Vite/React/TS/Tailwind；docker-compose + README + .env.example）。验证：后端 import/建表/health 200、alembic autogenerate+upgrade 15 表、前端 install+build 通过。6 commits：`e70e038` `4f07351` `b7b695c` `980226f` `5928646` `1f2535e`。
- **2026-07-16**：创建 `stage.md`，建立进度跟踪框架；完成环境检查（Python 3.12.10 / Node v26.3.0 / npm 11.16.0 / git 2.55）。准备开始步骤 1 建骨架。
