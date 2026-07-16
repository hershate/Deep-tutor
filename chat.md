# Deep-tutor 项目规划对话记录

> 本文件整理本次对话的**全部内容与模式**：目标澄清、计划创建、多轮 `/loop` 迭代细化、关键决策、工作/架构/方法论模式、最终计划结构与提交历史。
> 对应仓库：`f:/Github/repo/Deep-tutor`，计划文档位于 `plan/`（11 份）。

---

## 一、对话概览

- **项目**：Deep-tutor —— AI 驱动的学习方案生成 Web 应用。
- **本次对话产出**：从零完成完整实施计划（`plan/` 11 份文档），经 7 个 `/loop` 主题、共 18 轮迭代细化，覆盖架构、工具、API、前端、Skill 层、学习工作流、数据交换、审计、端到端协作、极端情况。
- **核心方式**：`/loop`（1 分钟间隔）驱动反复细化 + `AskUserQuestion` 对关键决策征询 + 细粒度 git commit + 复用既有技能生态。
- **当前状态**：计划已收敛，待用户确认 6 条假设与 MVP 范围后退出计划模式、开始 Phase 1 实现。

---

## 二、对话阶段与关键决策

### 阶段 0：目标澄清（问答）

通过两轮 `AskUserQuestion` 明确：

| 维度 | 决策 |
|---|---|
| 产品形态 | Web 应用 |
| 方案内容 | 学习路径+知识大纲、时间安排与学习计划、配套资源推荐、练习/测验与进度跟踪（尽可能细化、形式多样） |
| 技术栈 | 混合栈（前后端按最合适组合搭配） |
| AI 来源 | 兼容 Anthropic API，工具自研，参考 Reasonix |
| Reasonix | [esengine/DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix) —— DeepSeek 原生终端编程 Agent（借鉴其缓存优先/工具调用修复/成本控制三支柱，但换 Anthropic、换学习领域、换 Web 形态） |
| 部署规模 | 先本地，可扩展 |
| 资料形式 | 文档文件 / 文本/网页链接 / 图片音视频 / 纯描述需求（不拘泥形式） |

### 阶段 1：计划创建

新建 `plan/` 并产出细化方案：README + 01 架构 + 02 工具 + 03 API/前端 + 04 MVP/风险。技术栈定为 **Python FastAPI 后端 + React/Vite/TS 前端 + SQLite**，借鉴 Reasonix 三支柱自研 Agent 循环。

### 阶段 2：Skill 层接入（用户指示）

用户指示"相当一部分 AI 工作流可以用 skill-for-skills 构建"。读 `skill-for-skills` 后确认采用 **Skill 作方法论与指令源**（非子 Agent、非纯开发期产物）：方法论密集工具由 skill-for-skills 生成的 Skill 承载，Python 运行时加载 SKILL.md 为 system prompt。新增 `05-skills.md`。

### 阶段 3：PPT 生成接入

用户指示 PPT 生成用 `ppt-slide-builder`。读后确认它产出 **自包含 HTML 幻灯片（非 .pptx）**，新增工具 `build_plan_slides` 复用之（P1）。

### 阶段 4：前端细化

用户要求细化前端 UI。新建 `06-frontend.md`：设计系统、7 页面线框、组件规格、状态管理、SSE、用户流程、无障碍。

### 阶段 5-11：`/loop` 迭代细化（见第六节）

7 个主题循环：study-planner/self-study-guide 工作流、统一数据交换格式、审计、端到端协作、整体检查、极端情况。

### 关键决策汇总（经用户确认）

1. **Skill 运行时角色**：Skill 作方法论与指令源（推荐项）。
2. **复用 Skill 来源**：打包进项目 `skills/`（vendor，自包含）。
3. **搜索机制**：search_resources 用 **Bing Search API**（WebSearch 是 Claude Code 工具，Python 后端不能直接用）。
4. **SSE 鉴权**：参考 Claude Code/Anthropic SDK 的 Bearer 头流式，前端用 **fetch+ReadableStream**（EventSource 不能带自定义头）。

---

## 三、工作模式（Working Patterns）

- **`/loop` 驱动迭代细化**：用 1 分钟间隔 cron 反复触发同一主题，每轮做实质性细化 + commit，收敛后 `CronDelete` 停止。共 7 主题、18 轮。
- **问而不猜**：遇架构分叉（如 Skill 运行时角色、搜索机制、SSE 鉴权）用 `AskUserQuestion` 征询，不臆测（遵循用户全局规范）。
- **细粒度 git commit**：每个逻辑变更独立 commit，便于回滚；commit 信息中文、**不含 co-author**（遵循用户全局规范）。
- **复用既有技能生态**：优先复用 `~/.claude/skills/` 下的 study-planner、self-study-guide、ppt-slide-builder、skill-for-skills、skill-chain-planner，而非重造。
- **计划分层文档**：按主题拆多文件 + README 索引 + 交叉引用，避免单文件臃肿；每文件内部一致风格。
- **先读后写、不编造**：引用任何技能/外部内容前先 Read 原文（如 Reasonix README、各 SKILL.md、data-exchange-format.md），不凭名字猜测。
- **收敛即停**：每个 `/loop` 主题达到收敛（主要类别覆盖）后主动停止循环，避免 churn。

---

## 四、架构模式（Architectural Patterns）

- **两层执行模型**（核心，解决"自由 Agent vs 固定流水线"冲突）：外层确定性编排器（Orchestrator）按固定顺序调度 `parse→understand→map→stage→resource→schedule→validate(→反馈循环)→export`；内层每 LLM 工具一次 Claude 调用（system=Skill SKILL.md，user=args+upstream.data）+ pydantic 校验 + 工具调用修复。废弃原"自由 Agent tool_use 决策"语义。
- **Skill 作方法论与指令源**：Skill 是"做什么/怎么做"的权威定义（Workflow/Constraints/Do-Not 示例），Python 是"跑起来"的运行时；Skill 升级后重新加载即生效，不改代码。
- **统一数据交换格式**（参考 skill-chain-planner）：类型系统 + `ToolInput`/`ToolOutput` 信封 + 工具契约模板 + 流水线流转契约 + 字段覆盖检查 + schema_version 版本演进。
- **存储分层**：大内容（解析文本/自学材料/导出/幻灯片/大方案 JSON）入文件系统 `data/sessions/<sid>/`，SQLite 存路径+元数据；WAL + 定期 VACUUM + 删 session 级联清理；P4 迁 Postgres+对象存储。
- **Reasonix 三支柱**：缓存优先（per-tool system 稳定前缀 + cache_control）、工具调用修复（schema 不符反馈重试≤3→降级）、成本计量（usage→api_usage + 预算守卫）。
- **反馈循环**：validate_plan.passed=false → 据 fixes 重跑上游工具 → 再 validate，≤2 轮。
- **状态机**：`orchestrator_state`（idle/running/paused_clarify/done/error/interrupted），支持崩溃恢复、澄清续接、SSE 断线续接（Last-Event-ID）。
- **SSE 头鉴权流式**：fetch+ReadableStream 携带 Bearer 头（参考 Claude Code）。
- **分层降级**：每工具有降级默认值；降级矩阵规定 13 类工具失败的降级行为与用户感知。

---

## 五、方法论模式（Methodology Patterns，复用的技能）

| 技能 | 在 Deep-tutor 中的角色 |
|---|---|
| `study-planner` | 学习规划方法论蓝本：7 步工作流（解析需求→知识图谱→阶段设计→资源检索+验证→生成方案→5 项校验→呈现）映射到主线工具与 `deep-tutor-*` Skill |
| `self-study-guide` | 自学材料生成：`generate_study_materials` 工具复用其单元模板（概述/核心概念/实践练习/检查理解/总结/扩展阅读） |
| `ppt-slide-builder` | PPT 生成：`build_plan_slides` 复用其 `templates/base.html` + 主题/动画，产出 HTML 幻灯片 |
| `skill-for-skills` | Skill 工厂：CREATE/UPGRADE/UPDATE_SUM 生成与维护 `deep-tutor-*` Skill（遵循 sum.md 规范） |
| `skill-chain-planner` | 链路规划与数据交换格式：5W1H+C、接口契约、类型系统、风险登记、L4 字段覆盖检查；其 `templates/data-exchange-format.md` 为 08 的参考 |
| `skill-chain-executor` | 执行思路参考（运行时编排由 Python Agent 承担，不依赖 Claude Code 运行时） |

---

## 六、`/loop` 迭代细化记录

每个主题：建立/扩充对应文档 + 交叉引用更新 + commit，收敛后停止。

### 主题 A：study-planner + self-study-guide 工作流（3 轮）
- 第 1 轮 `4c766eb`：新建 `07-learning-workflows.md`，study-planner 7 步映射 + `generate_study_materials`（self-study-guide）工具 + 上下游衔接。
- 第 2 轮 `2adb8fb`：07 补单元材料模板 + 端到端示例流；06 补自学材料页线框。
- 第 3 轮 `9834304`：闭合一致性缺口（06 组件表、01 关系图、02 资源验证规则表）。

### 主题 B：统一数据交换格式（2 轮）
- 第 1 轮 `dec6cbc`：新建 `08-data-exchange-format.md`，复用 skill-chain-planner 类型系统 + 信封 + 契约模板 + 流转契约 + 版本演进。
- 第 2 轮 `25e6895`：补 worked example（5 种场景 JSON 实例）。

### 主题 C：审计（风险/边界）（3 轮）
- 第 1 轮 `044ddea`：新建 `09-audit.md`，存储策略（回应"SQLite 内容过长→文件夹管理"）+ 上下文溢出 + 安全 + 解析边界 + Agent 守卫 + 数据运维 + 前端边界；01 加存储策略节；04 加 R18-R26。
- 第 2 轮 `1a059e7`：输入校验全集 + 并发事务 + 国际化 + 测试 QA + 可观测性 + 降级矩阵。
- 第 3 轮 `e7f179b`：API 错误码目录 + 配置项 + CORS/限额 + 部署 + 容量配额 + 收敛声明；04 加 R27/R28。

### 主题 D：端到端协作（2 轮）
- 第 1 轮 `1120de8`：新建 `10-integration.md`，**两层执行模型**（核心）+ 端到端序列 + SSE 统一 + 澄清续接 + 持久化选择性 + 反馈循环 + Skill↔工具映射；修订 01/03。
- 第 2 轮 `95ce80a`：工具包装器统一接口 + 编排器状态机持久化与并发隔离 + SSE 续接游标 + 重生成复用 + 错误/取消传播 + 前端状态机；01 加 `orchestrator_state` 表。

### 主题 E：整体检查（协作+稳定性+逻辑）（2 轮 + 决策）
- `1878f08`：修复 4 处逻辑不一致（01 `parsed_text`/`study_materials` 字段改路径；`schedule_plan` 改归"LLM 无 Skill"）。
- `58d43db`：落实用户 3 项决策（vendor 复用 Skill / Bing 搜索 / SSE fetch+ReadableStream 头鉴权）。
- `3c0328e`：次要改进（DELETE 会话级联、分页、R14 类别、CORS 头、模型注记、缓存澄清、plans.content_path）+ 最终核验。

### 主题 F：极端情况逻辑稳固性（2 轮）
- 第 1 轮 `2313b72`：新建 `11-extreme-cases.md`，输入极端/资源耗尽失控/Pipeline 逻辑/降级默认/并发状态/安全；补 prompt 注入、数量上限、图环检测、running 态冲突、状态损坏；04 加 R29-R35。
- 第 2 轮 `68bb10e`：PDF 导出依赖、Skill 解析失败处理、次要极端场景 + 收敛声明。

---

## 七、最终计划结构（`plan/` 11 份）

| 文件 | 内容 |
|---|---|
| `README.md` | 定位、Reasonix 借鉴、交互形式全集、方案数据结构、路线图、6 条假设、索引 |
| `01-architecture.md` | 技术栈、系统架构、两层执行模型、Agent 循环（三支柱）、Skill 层、数据模型（含 orchestrator_state）、存储策略 |
| `02-tools.md` | 13 个工具契约（输入/输出/错误/LLM/幂等）+ 数据流转 + 资源验证规则 |
| `03-api-frontend.md` | REST/SSE API（含 /clarify、DELETE 会话、分页）、前端概览 |
| `04-mvp-risk.md` | P0/P1/P2 任务、路线图、风险表 R1-R35、静默错误防护 |
| `05-skills.md` | Skill 层：双轨、Skill↔工具映射、运行时加载、skill-for-skills/skill-chain-planner 角色、复用既有技能、目录规范 |
| `06-frontend.md` | 设计系统、AppShell、8 页面线框、14 组件、状态管理、SSE（fetch+ReadableStream）、用户流程、无障碍 |
| `07-learning-workflows.md` | study-planner 7 步映射、self-study-guide `generate_study_materials`、单元材料模板、端到端示例流 |
| `08-data-exchange-format.md` | 类型系统、ToolInput/ToolOutput 信封、契约模板、流转契约、版本演进、worked example |
| `09-audit.md` | 存储策略、上下文溢出、安全（含 prompt 注入）、解析边界、Agent 守卫、数据运维、前端边界、输入校验、并发、国际化、测试、可观测、降级矩阵、错误码、配置、CORS、部署、容量配额（21 节） |
| `10-integration.md` | 两层执行模型、端到端序列、SSE 一致+续接、澄清续接、持久化选择性、反馈循环、Skill↔工具映射、工具接口、状态机、重生成复用、错误/取消传播、前端状态机（16 节） |
| `11-extreme-cases.md` | 输入极端、资源耗尽/失控、Pipeline 逻辑、降级默认、并发/状态、安全、次要场景、收敛声明（10 节） |

---

## 八、提交历史

```
9bbb0e4 Initial commit
7c593d4 docs(plan): 新增 Deep-tutor 细化实施方案
9f0620c docs(plan): 新增 Skill 层设计（基于 skill-for-skills）
e3176c0 docs(plan): 接入 ppt-slide-builder 作为 PPT 生成能力
a850dd1 docs(plan): 新增前端 UI 细化计划 06-frontend
4c766eb docs(plan): 第1轮细化-接入 study-planner 工作流与 self-study-guide 自学材料
2adb8fb docs(plan): 第2轮细化-07 单元材料模板与端到端示例流、06 自学材料页线框
9834304 docs(plan): 第3轮细化-闭合一致性缺口
dec6cbc docs(plan): 新增统一数据交换格式 08(参考 skill-chain-planner)
25e6895 docs(plan): 08 统一数据交换格式补 worked example
044ddea docs(plan): 第1轮审计-存储策略与风险边界(09)
1a059e7 docs(plan): 第2轮审计-输入校验/并发/国际化/测试/可观测/降级矩阵
e7f179b docs(plan): 第3轮审计-错误码/配置/CORS/部署/配额(最终扫描)
1120de8 docs(plan): 第1轮协作-两层执行模型与端到端交互(10)
95ce80a docs(plan): 第2轮协作-工具接口/状态机/SSE续接/重生成复用(10)
1878f08 docs(plan): 整体检查-修复 4 处逻辑不一致
58d43db docs(plan): 落实 3 项协作决策(vendor复用Skill/Bing搜索/SSE头鉴权)
3c0328e docs(plan): 整体检查-次要改进与最终核验
2313b72 docs(plan): 第1轮极端情况审计-逻辑稳固性(11)
68bb10e docs(plan): 第2轮极端情况-PDF依赖/Skill解析/次要场景/收敛
```

> 另：会话中曾出现 `.gitattributes` 莫名多出 `DeepSeek-Reasonix` 行（非我所为、无活动钩子），用户已清空 git 重建干净初始提交后重新提交计划。

---

## 九、待确认与下一步

**待用户确认（`plan/README.md` 第六节 6 条假设）**：
1. LLM 以 Anthropic Claude 为主，Provider 抽象便于后续兼容 DeepSeek。
2. 本地单用户优先，认证最简，预留多用户。
3. MVP 持久化用 SQLite。
4. API Key 本地配置（.env），不入库。
5. MVP 解析范围：PDF/Word/Markdown/TXT/网页（OCR/音视频在 P3）。
6. 方法论密集 AI 工作流以 skill-for-skills 生成的 Skill 承载，基础设施类工具纯 Python。

**下一步**：确认假设与 MVP 范围后，退出计划模式，开始 Phase 1 实现（建骨架 → LLM Provider 抽象 → Agent 循环 → P0 工具链 → 前端 → 端到端联调，全程细粒度 commit）。

**Phase 路线图**：P1 MVP（资料/需求→完整方案）→ P2（测验+进度+自学材料）→ P3（OCR+音视频）→ P4（多用户+云部署+Skills/MCP）→ P5（缓存优化+Anki/ics+协作分享）。
