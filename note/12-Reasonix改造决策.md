# Reasonix 架构改造决策记录

> 决策时间：2026-10-15
> 依据：REF/extracted（R-Tutor-src-studio-20261015.zip 解压，Reasonix studio 分支源码 + `internal/runtime/tutor` 移植先例）与本仓库 note/01–11 分析笔记的双向深度调研；61 项决策经与用户五轮逐条确认。
> 状态：**完整改造方案已定稿 → [plan/reasonix/](../plan/README.md)（六份）**；实施分支 `refactor/reasonix`；进度见根目录 stage.md。

## 一、改造蓝本关键事实（来自 REF 调研）

1. **REF 里的代码不是 Reasonix 原版，而是 R-Tutor**——Reasonix studio 分支 + `internal/runtime/tutor` 包（67 个 `.go` 文件）。该包 doc.go 明确写明 "Domain semantics are ported from Deep-tutor"，是 Deep-tutor 领域功能移植到 Reasonix 内核的**完整先例**：
   - 数据模型逐条镜像：Material 三段制（pending→confirmed→parsed）、Intent、KnowledgeMap、Stage 3–6 契约（ValidateStages 强制）、五维 ValidationReport、OrchestratorStatus 状态机（idle/running/paused_clarify/done/error/interrupted）。
   - 8 步流水线同构：`parse_material → understand_intent → build_knowledge_map → design_stages → {search_resources ∥ schedule_plan} → validate_plan → export_plan`，反馈循环 ≤2 轮、regenerate 配额默认 2。
   - SSE 事件词表与 Deep-tutor 完全一致：tool_start/tool_end/unit_start/unit_done/plan_chunk/doc/warn/clarify_needed/budget_exceeded/error/done。
   - 导出已实现：Markdown / ICS / Anki .apkg（schema-11，稳定 guid 原地更新）/ HTML slides（LLM 失败降级确定性 deck）；PDF 为降级 Markdown + 警告头。
   - 材料解析为 Go 原生：text/md/docx/pptx/epub/pdf 文本层（含 CJK CMap）+ URL 抓取（SSRF 防护）；无 OCR/音视频转写/marker 深度解析。
   - 数据落盘为 state root 下 JSON 文件树（sessions/plans/quizzes/notes/kb/shares/usage），**无 SQL**。
2. **暴露方式**：tutor 域挂在 serve 服务器的 `/tutor/*` 命名空间（约 60 端点）+ Studio 侧栏面板（`#tutor` hash）；tutor.Service 是 transport 无关驱动层（`ServiceOptions{Provider func() (LLM, error), Searcher, Pricer, …}`），LLM 依赖收窄为 `interface { Name; Stream }`，由 boot 注入——**这就是"领域功能复用内核 provider"的样板**。
3. **Reasonix 内核结构**：依赖方向 `frontend → assembly(boot.Build) → session(control.Controller) → runtime → {tools, ext, model, platform} → {contract, base}`；safety/state 为横切层。全部前端（TUI / serve HTTP+SSE / ACP / Electron Studio）只驱动同一个 control.Controller（命令方法 + 单一 event.Sink）；Studio renderer 走 loopback HTTP 与浏览器同路由，Electron IPC 只承载 OS 能力。
4. **provider 体系**：三种原生 kind（openai 兼容含 DeepSeek 特化 / anthropic 原生 Messages / responses），配置驱动（reasonix.toml `[[providers]]`），密钥经 api_key_env；官方费率表 append-only（internal/contract/pricing）。
5. **内核能力清单**（改造后要用的）：权限三档（ask/auto/yolo）+ 规则语法、沙箱（Windows 无 OS 级沙箱，bash fail-closed）、checkpoint/rewind（git-free 快照，两阶段 prepare/commit + undo）、子代理五概念（Profile/TaskSpec/CapabilityGrant/ContextRequest/SchedulerPolicy，fleet 依赖图）、MCP（stdio/http/sse + OAuth）、Extension Protocol v2 sidecar（17 hook 点、replacement slots、sidecar provider、结构化 UI）、skills（SKILL.md，runAs:subagent）、Context Engine v2（standing instructions + BM25 记忆召回）、compaction（cache-first、compact_ratio 0.85）。
6. **与 Deep-tutor 的天然落差**：Reasonix 是单用户本地工具（state root 单根、无用户概念）；会话存储是 JSONL；Deep-tutor 是多用户 JWT + SQLite 26 表 + Python 进程内插件。

## 二、30 项决策（用户已逐条确认）

### A. 总体形态
| # | 决策点 | 决策 |
|---|--------|------|
| 1 | 仓库起点 | **当前仓库内改造**：Deep-tutor 仓库新建 Go 后端逐步替换 Python，保留 git 历史 |
| 2 | Web 特性不变的严格度 | **前端可重做**（只保留产品能力清单）；后端 /api 契约不再是逐字节硬约束 |
| 3 | /tutor/* 与 /api 关系 | **tutor.Service 不动 + 新增 /api 兼容路由层复用它**；/tutor/* 保留给 Studio |
| 4 | 前端范围 | **四前端全套**：Web / Electron Studio / CLI / TUI / ACP |

### B. 前端
| # | 决策点 | 决策 |
|---|--------|------|
| 5 | 新前端底座 | **以 Studio frontend-next（React+Vite）为底座**，把 Deep-tutor 19 页面产品能力重做进去，一套前端服务浏览器与桌面 |
| 6 | 前端插件宿主 | window.DeepTutor **废弃**，前端扩展能力改接 Reasonix 插件包 / Extension 结构化 UI |
| 7 | 浏览器扩展 | MV3 剪藏扩展**随新架构重构**（不做零改动兼容） |
| 8 | UI 语言 | **中英双语**（接入 Reasonix i18n） |

### C. 认证与用户
| # | 决策点 | 决策 |
|---|--------|------|
| 9 | 用户模型 | **保留多用户**：JWT 登录 / PAT / admin CRUD / 用户级偏好预算在 Go 层重新实现（最大架构分叉，需与 Reasonix 单用户内核调和） |
| 10 | 归属隔离 | 随多用户**保留**（跨用户 404 防枚举语义） |
| 11 | 首次设置向导 | /api/setup 废弃，**用 Reasonix Studio 首次引导**（provider/密钥） |
| 12 | 遥测/云服务 | Reasonix telemetry / crash-report / workers（accounts/forum/crash-report）**全部禁用** |

### D. 数据层
| # | 决策点 | 决策 |
|---|--------|------|
| 13 | 存储引擎 | **全 SQL**：Go 侧重建 26 表等价 schema，tutor.Service 的 JSON 存储层改写为 SQL 实现 |
| 14 | 存量数据 | **全新开始**，不写旧库迁移器 |
| 15 | schema 演进 | **建 Go 侧迁移框架**（Alembic 史终止） |
| 16 | 数据落点 | state root **沿用 data/ 目录**（覆盖 Reasonix 默认 %APPDATA%\reasonix） |

### E. LLM / Provider / 成本
| # | 决策点 | 决策 |
|---|--------|------|
| 17 | 模型配置权威 | **每用户 DB 档案为权威**（llm_provider_profiles/credential_profiles 语义保留），启动时生成 Reasonix provider 配置动态注入内核 |
| 18 | LLM 协议 | **仅三种原生 kind**（openai/anthropic/responses），自定义协议（PluginProvider）废弃 |
| 19 | 成本计量 | **SQL 台账**（api_usage 等价表，多用户可归属）；三层预算防线（会话/日/重生成次数）保留；Reasonix usage catalog 同步入库 |
| 20 | 计价权威 | **Reasonix 官方费率表**（append-only + superseded），Deep-tutor 用户计价表仅作默认值/回退 |

### F. Agent 对话与内核能力
| # | 决策点 | 决策 |
|---|--------|------|
| 21 | Chat 基座 | **control.Controller 为基座**（每用户/线程一个 controller 实例），复用审批/checkpoint/子代理；workspace 三段生命周期（scratch→committed→promoted + awaiting_approval）桥接到 checkpoint+审批体系；threads/runs/steps/memory 概念在新 schema 重建 |
| 22 | 内核能力暴露 | **全部进 Web UI**：工具审批、Rewind、MCP/技能/插件管理、子代理 fleet、计划模式、Goal、沙箱配置、后台作业 |
| 23 | Agent 工具面 | **用 Reasonix 内建工具**（bash/read/write/edit/web_fetch/recall/memory…），run_steps 审计映射到内核工具事件 |
| 24 | 联网搜索 | **Bing 实现迁移到 Go，与 Reasonix websearch 并存、可配置切换** |

### G. 插件 / 解析 / 导出
| # | 决策点 | 决策 |
|---|--------|------|
| 25 | Python 插件体系 | 进程内动态导入终结；**9 个内置插件功能全部在新架构找到等价实现**（一个不丢），扩展生态转向 Reasonix 插件包/MCP/skill |
| 26 | 插件管理页 | 新 Web UI 插件管理页**对接 Reasonix 插件包安装态**（install/enable/disable/reload） |
| 27 | 材料解析 | **接受砍掉 OCR（tesseract）与音视频转写（faster-whisper）**，用 Go 原生解析链（text/md/docx/pptx/epub/pdf 文本层 + URL SSRF） |
| 28 | PDF 导出 | **Go 真实现**（引入 Go PDF 库，不用 R-Tutor 的降级策略） |

### H. 工程交付
| # | 决策点 | 决策 |
|---|--------|------|
| 29 | 交付形态 | **主二进制**（内嵌新 SPA + tutor + serve/CLI/TUI/ACP，多用户 Web 服务形态，端口 41173，Windows 优先）+ **Studio 安装包单独构建** |
| 30 | 阶段/验收/上游 | **四阶段递进**；验收以 Deep-tutor 现有功能清单回归（逐项 + SSE 事件序列对拍）；Reasonix 源码作一次性基线搬入，**自由修改不跟上游，以本仓库为中心，但仓库内明写引用 Reasonix 代码并遵守其 MIT 协议** |

## 三、四阶段划分（已确认骨架）

1. **阶段①**：引入 Reasonix 内核 + 新 SQL schema（多用户）+ 生成流水线/学习闭环（tutor.Service 接 SQL 存储）+ /api 兼容层 —— 旧 SPA 对兼容层回归验收。
2. **阶段②**：Chat 接 control.Controller（每用户/线程实例）+ 审批/Rewind 暴露 + workspace 三段生命周期桥接。
3. **阶段③**：9 插件等价物 + MCP/技能/插件管理页 + 进阶能力面板（fleet/计划模式/Goal/沙箱/后台作业）。
4. **阶段④**：Studio/CLI/ACP 打磨 + 新前端（frontend-next 底座）全量覆盖 + Python 后端退役 + 浏览器扩展重构。

> 验收前端衔接：①②阶段可用旧 SPA 对 /api 兼容层回归；③④切换到新前端。

## 四、已识别的四个张力点（方案必须正面处理）

1. **多用户 × Reasonix 单用户内核**：state root、JSONL sessionstore、serve hub 均按单用户设计。全 SQL + 多用户要求重写 Controller 会话持久层、tutor.Service JSON store、usage catalog 为带用户归属的 SQL 实现——**全改造最大的深层工程，阶段①必须打牢**。
2. **每用户 DB 档案注入内核**：Reasonix provider 注册表来自 reasonix.toml；需设计"DB 档案 → 运行时 provider 注册"动态装配层（每用户 Controller 实例各自解析），本质是新的 assembly 路径。
3. **插件等价物清单要先行**：逐插件映射表（provider_bridge→原生多 kind；mcp_bridge→原生 MCP；epub_reader→Go 原生 epub；webhook_notify→hooks/sidecar 重写；study_export_pack→Go csv/txt/opml 导出器；study_stats→统计端点；ai_tutor/security_guard→逐个评估），阶段③按表施工。
4. **四阶段验收 × 新前端重做**：各阶段验收用哪个前端要明确（①②旧 SPA 回归兼容层，③④新前端）。

## 五、补充决策（第二轮澄清 12 问，2026-10-15）

### 存储与运行模式
| # | 决策点 | 决策 |
|---|--------|------|
| 31 | 全 SQL 边界 | **领域数据 SQL + 内核文件态**：26 表等价物入 SQL；Reasonix 内核自身的会话 transcript/checkpoint/记忆保留文件形态，按用户分 root（data/users/<uid>/kernel/…） |
| 32 | DB 引擎 | **SQLite 单库 WAL 默认 + 预留 Postgres**：建表/迁移层从第一天抽象方言 |
| 33 | 运行模式 | **全登录服务**：唯一形态是多用户服务（端口 41173）；Studio/CLI/TUI/ACP 一切客户端登录服务取数，**无独立本地模式** |
| 34 | 新前端 API 面 | **/api 为长期统一面**：新前端（frontend-next 底座）也走 /api；Reasonix 原生端点仅作内部/过渡 |

### 能力边界
| # | 决策点 | 决策 |
|---|--------|------|
| 35 | Chat 工具白名单 | **学习域白名单**：web_fetch/websearch/recall/memory/笔记/方案/KB 工具 + 只读文件；默认不含 bash/写文件/子代理；管理员可开全量 |
| 36 | Windows bash | **跟随 Reasonix 原生**（沙箱 enforce 时 fail-closed，非 enforce 不限制）——与白名单叠加后默认不可见 |
| 37 | 内核裁剪 | ⚠️ 用户跳过未答，暂按以下默认执行（可随时修正）：**browser/computer 工具保留**（进阶全套能力之一）、**插件市场 market 保留**（插件包安装来源）；**appupdate 自动更新剔除**（自建 Deep-tutor 品牌发布）、**remote/remotecloud 剔除**（全登录服务已覆盖远程使用场景） |
| 38 | 双模型协作 | **全部启用**：方案生成与 Chat 均可在设置页配置 planner+executor 双模型 |

### 工程
| # | 决策点 | 决策 |
|---|--------|------|
| 39 | 阶段①② /api 覆盖 | **旧 SPA 全部 19 页面**（含 Plugins/Users/Settings 管理页）阶段①②即完整可用 |
| 40 | 共存/分支 | **Go 从阶段①直接接管 41173**，旧 Python 后端即停用，无并行过渡期 |
| 41 | 工程门禁 | **全量保留** Reasonix 门禁体系（repolint 预算棘轮、coverage-gate、effect test、golangci 配置一并搬入） |
| 42 | 命名品牌 | **保留 Deep-tutor**：二进制 deep-tutor(.exe)、桌面端 Deep-tutor Studio；对 Reasonix 开发者保持尊重——遵守 MIT，仓库显著位置署名来源（README/关于页/版权声明保留原版权行） |

### 第二轮决策带来的阶段①体量提示

决策 33（全登录服务）+ 39（全 19 页面）+ 40（直接接管端口）叠加意味着：**阶段①没有轻量过渡态**——多用户服务、认证链、完整 /api 面、SQL schema、内核落位须一次性到位，旧后端在阶段①完成前即停用。完整方案文档需把阶段①内部再细化为可串行验证的里程碑（如 M1 内核落位+构建链 → M2 SQL schema+认证 → M3 生成流水线+/api → M4 学习闭环全页 → M5 Chat 基座…），并在阶段①期间提供数据全新开始的预期管理。

## 六、补充决策（第三轮澄清 12 问，2026-10-15）

### 认证与 API 演进
| # | 决策点 | 决策 |
|---|--------|------|
| 43 | 首个管理员初始化 | **Web 初始化页 + CLI 双通道**：首次访问出现仅建首个 admin 的初始化页；同时提供 `deep-tutor admin create` 命令 |
| 44 | 注册策略 | **仅 admin 创建用户**（不开放注册） |
| 45 | /api 契约演进 | **直接重设计 /api + 旧 SPA 走适配 shim**：新契约一步到位；阶段①②旧 SPA 通过 shim 层消费新契约完成全 19 页回归（shim 阶段④随旧 SPA 退役） |
| 46 | Schema 自由度 | **语义等价、结构自由设计**：充分利用内核文件态承担（消息历史归内核会话存储后领域表可瘦身为索引），不追求与旧 26 表字段级对照 |

### 代码处置与技术选型
| # | 决策点 | 决策 |
|---|--------|------|
| 47 | 旧代码处置 | **阶段④删除** backend/、frontend/（旧 SPA）、plugins/、plugin-examples/、extension/（git 历史保留） |
| 48 | 数据访问层 | **sqlc**：写 SQL 生成类型安全 Go 代码（SQLite/PG 双方言），配合自建迁移框架 |
| 49 | 仓库布局 | **顶层 go.mod（模块 deep-tutor）+ internal/ + cmd/ + desktop/**，与 Reasonix 同构；旧目录并存至阶段④ |
| 50 | 发布签名 | **不买证书 + minisig 签名文件**：Windows 安装包未签名（接受 SmartScreen 警告），附 minisig 供校验防篡改 |

### 产品行为细节
| # | 决策点 | 决策 |
|---|--------|------|
| 51 | 管理员全量边界 | **全量但 computer/CUA 单独开关且默认关**：管理员可开写文件/编辑/bash/子代理/MCP/技能等一切；CUA（Agent 操作屏幕/浏览器）独立开关 |
| 52 | 记忆召回默认 | **按需召回**（Deep-tutor 现状语义）：记忆仅由工具显式召回，不用 Reasonix 每轮自动 BM25 注入（Context Engine 保留在内核，行为配置关闭） |
| 53 | 对话入口 | **单入口动态工具面**：新前端只有一个对话入口（Deep-tutor 学习助手人格），工具面随角色/配置变化（普通用户学习域白名单，管理员可切全量） |
| 54 | 交付节奏 | **顺序质量优先**：四阶段按序完整验收后再进下一阶段，不设硬时间表 |

## 七、补充决策（第四轮澄清 4 问，2026-10-15）

### 多用户 × 内核全局资产的作用域
| # | 决策点 | 决策 |
|---|--------|------|
| 55 | 扩展资产作用域 | **全局 admin 管理**：插件包、MCP 服务器、技能（SKILL.md）仅管理员安装/启停，全体用户可用；普通用户不可自装 |
| 56 | 权限/沙箱作用域 | **全局底线 + 用户只能收紧**：admin 设全局 Allow/Ask/Deny 规则与沙箱策略（用户不可放宽），用户可在此基础上收紧/细化自己的规则 |

### 生成域与流程
| # | 决策点 | 决策 |
|---|--------|------|
| 57 | 生成域双模型语义 | **planner 作为方案审查者**：流水线产出后 planner 独立审查（对照五维校验 + 用户意图）提修改意见、触发定向重跑——比纯规则校验更聪明的反馈循环 |
| 58 | 交付确认节奏 | **全自动推进，但任务拆分尽可能细、每一步谨慎**：不停下来等确认，以小步可验证增量作业（自动运行 + 细粒度任务 + 谨慎逐步） |

## 八、补充决策（第五轮澄清 3 问，2026-10-15）

| # | 决策点 | 决策 |
|---|--------|------|
| 59 | 前端插件示例功能 | **全部重建**：番茄钟专注计时、浮层笔记、换肤、阅读器四个功能都在新前端等价实现（番茄钟为内置功能；换肤可借 Reasonix 主题体系落地） |
| 60 | 全局事件流 | **保留等价物**：新 /api 设计全局实时事件流端点（语义重设计：领域事件 quiz_ready/plan_created 等 + Reasonix 内核事件桥接），供前端 toast 通知/后台任务完成提醒/实时刷新 |
| 61 | 分支与提交 | **新分支（如 refactor/reasonix）+ 细粒度 commit 由 agent 自主提交（不 push）**：改造全程在新分支进行，完成后由用户合入 main；FIX-Filter 保持干净 |

## 九、待产出（下一步）

- 完整改造方案文档（建议落 `plan/`）：Go 仓内目录结构、Reasonix 内核落位与裁剪清单（遥测/云服务剔除）、新 SQL schema 设计、/api 兼容层端点映射表、四阶段任务分解与里程碑、风险清单。
- 许可合规：仓库显式标注 Reasonix/DeepSeek-Reasonix（MIT）来源与版权声明。

## 修订记录

### 2026-10-20：上游无关资产清除（修订 #37 的处置范围收窄）

用户裁定：「同时清理上游树中与本仓无关的资产（如 release-notes/、benchmarks/、examples/、workers/ 等），仅保留内核必需部分」。据此修订：

- **#37（内核裁剪）处置范围收窄**：2026-10-17 修订的「全部内容入库、禁用项不接线」适用于**内核代码**（Go 源码、构建基座、门禁工具、SDK、桌面端），该项不变；**上游产品周边资产**改为按「与本仓目标形态是否相关」判定，不再无条件入库。
- **已移除的上游资产**（commit `bec5525`，内容保留于 git 历史）：`release-notes/`（上游 65 份版本发布说明）、`benchmarks/`（上游 15 组评测夹具 + 129 个 Go 包）、`examples/`（上游 9 个 skill/命令/MCP 套件）、`workers/`（上游 accounts/forum/crash-report 云服务，即 #12 的禁用项）、`npm/`（上游 CLI npm 分发包）、`output/`（上游 agent-workbench v4–v6 原型产物）。
- **对 #12（遥测/云服务禁用）的影响**：`workers/` 原为「保留但不接线」的禁用项载体，移除后 #12 的禁用语义由装配层不注册/不暴露实现，无需该载体；内核 Go 代码中的对应接线点未作改动。
- **保留的上游资产**：`internal/`、`cmd/`、`tools/`、`sdk/`、`benchmark/`、`desktop/`、`scripts/reasonix/`、`docs/kernel/`（含 `upstream/` 原件存档）。
- **对阶段③的影响**：`examples/` 移除后，原「example_hello → 扩展开发示例（sdk/ 示例+文档）」的等价物落在 `sdk/go/examples/`（starterextension / toolextension / providerextension / fullsidecar）与本仓 `plugin-examples/`、`skills/`。
- **对 M1.5（门禁基线重建）的影响**：`tools/repolint` 的 `release-note` 规则与 `docs.go` 前缀映射随 `release-notes/` 移除而失效；`baseline.json` 仍含被删文件的 23 条记录，需在 M1.5 重建基线时一并清理。移除后 repolint 报告行数 2433→2429，未新增（门禁既有红色源于基线未按本仓树重建，与本次移除无关）。

### 2026-10-17：REF 全部内容吸收（修订 #37 的处置方式）

用户目标指示：「REF 文件夹下的改造实现（R-Tutor 20261017 快照）逐步吸收、细粒度 commit、直至包含全部内容」。据此修订：

- **#37（内核裁剪）处置方式变更**：原"剔除=不拷贝"改为**全部内容入库、禁用项不接线**——telemetry/appupdate/remote/workers 等代码保留在仓库，#12/#37 的禁用语义在 cmd/deep-tutor 与 internal/web 装配层实现（不注册/不暴露），M1 执行时逐项核对。
- **#30（基线搬入）已执行**：基线为 20261017 快照（比写方案时的 1015 快照新增：产品翻转、统一前端、11 轮性能优化、实机验证轮等，见 note/graft/STAGE.md）。
- **对四阶段的实质影响**：上游"产品翻转 + 统一前端 + vitest 全绿基线"意味着阶段④的前端主体工作已在吸收物中就绪，阶段②③的 tutor 侧增量（多用户/SQL 化）仍为本仓工作。
- 吸收落位映射与验证记录见 stage.md iter R1。
