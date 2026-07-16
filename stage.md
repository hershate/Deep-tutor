# stage.md — Deep-tutor 实施进度跟踪

> **本文件用途（用户原始要求，须始终遵循）**：
> 分阶段执行计划，不需要在本次会话全部完成，但是注意，你的所有操作进度都应该写入目录下的 `stage.md` 以便于后期在其他会话继续工作，或者中断后随时继续（请把这一段要求也直接写入 stage.md）。
>
> 因此：**每完成一个步骤、每次 git commit、每次关键决策或阻塞，都必须更新本文件**。新会话或中断恢复时，先读本文件确定"下一步做什么"，再继续。

---

## 〇、如何续接（给未来会话的自己）

1. 读本文件"四、进度日志"确定当前进度与下一步。
2. 读 `plan/` 下相关计划文档确认实现细节（见"三、计划文档索引"）。
3. 检查 `git log --oneline` 与本文件记录的 commit hash 是否一致。
4. 遵守"二、执行约定"。
5. 继续执行下一个未完成步骤，完成后更新本文件并 commit。

---

## 一、总体阶段路线图

来源：[plan/README.md](plan/README.md) 第五节、[plan/04-mvp-risk.md](plan/04-mvp-risk.md)。

| Phase | 目标 | 状态 |
|---|---|---|
| **P1 MVP** | 资料/需求 -> 完整学习方案主线（大纲+路径+时间+资源+校验） | ⏳ 进行中 |
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
- **当前分支**：`main`（计划阶段均在 main；实现阶段如需可另开分支，但目前按 main 推进，逐步 commit）。

### 计划的 6 条假设（视为已确认，因用户指示"执行计划"）
1. LLM 以 Anthropic Claude 为主，Provider 抽象便于后续兼容 DeepSeek。
2. 本地单用户优先，认证最简（简单 token），预留多用户（JWT）。
3. MVP 持久化用 SQLite。
4. API Key 本地配置（.env），不入库。
5. MVP 解析范围：PDF/Word/Markdown/TXT/网页（OCR/音视频在 P3）。
6. 方法论密集 AI 工作流以 skill-for-skills 生成的 Skill 承载，基础设施类工具纯 Python。

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
| 1 | 建骨架 | `chore: scaffold project skeleton` | 🟡 进行中 | 见 §4.1 |
| 2 | LLM Provider 抽象 + Anthropic 实现 | `feat(llm): provider abstraction + anthropic` | ⬜ | — |
| 3 | Agent 循环骨架（工具注册+调用修复+成本计量+预算守卫） | `feat(agent): core loop with tool-call repair and cost tracking` | ⬜ | — |
| 4 | P0 工具链（每工具独立 commit） | `feat(tools): ...` | ⬜ | — |
| 5 | Skill 层（skill-chain-planner 规划 + skill-for-skills 生成 + Python 加载器） | `feat(skills): deep-tutor-* skills and loader` | ⬜ | — |
| 6 | API 层（materials/sessions/plans/export） | `feat(api): materials/sessions/plans endpoints` | ⬜ | — |
| 7 | 前端 P0 页面 | `feat(frontend): generate flow and plan detail` | ⬜ | — |
| 8 | 端到端联调 + 冒烟测试 | `test: e2e smoke for plan generation` | ⬜ | — |

### 4.1 步骤 1 — 建骨架（进行中）

**目标**：可启动的后端 FastAPI 骨架 + 前端 Vite/React 骨架 + docker-compose + README + .env.example + core(settings/logging) + 数据层（SQLAlchemy 全表模型 + SQLite 初始化 + Alembic 脚手架）。

**计划产出目录结构**：
```
Deep-tutor/
  backend/
    app/{__init__.py, main.py, core/{settings,logging}, db/{base,models}, llm/, agent/, tools/, skills/, api/}
    alembic/{env.py, versions/}, alembic.ini
    requirements.txt
  frontend/
    src/{...}, package.json, vite.config.ts, tailwind.config.js, index.html
  skills/                # vendor skills（后续步骤填充）
  data/                  # 运行时数据（.gitignore）
  docker-compose.yml
  README.md
  .env.example
  .gitignore
```

**进度记录**：
- [ ] 后端骨架（main.py / CORS / 异常处理 / 路由注册）
- [ ] core：settings（pydantic-settings）+ logging
- [ ] 数据层：SQLAlchemy 模型（全部表）+ SQLite 初始化
- [ ] Alembic 脚手架
- [ ] requirements.txt
- [ ] 前端骨架（Vite+React+TS+Tailwind+路由+TanStack Query+Zustand）
- [ ] docker-compose
- [ ] README + .env.example + .gitignore
- [ ] 自检（后端可 import / 前端可构建或至少依赖安装）
- [ ] git commit

---

## 五、进度日志（按时间倒序追加）

> 每条：日期 + 步骤 + 动作 + 结果 + commit hash（如有）。

- **2026-07-16**：创建 `stage.md`，建立进度跟踪框架；完成环境检查（Python 3.12.10 / Node v26.3.0 / npm 11.16.0 / git 2.55）。准备开始步骤 1 建骨架。
