# docs/kernel/ — Reasonix 内核工程文档（上游吸收）

> **本目录内容源自 [DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)（`studio` 分支，快照 `R-Tutor-src-studio-20261017.zip`），MIT License，Copyright (c) 2026 Reasonix Contributors。**
> 引用声明、路径映射与修改披露见仓库根目录 [`NOTICE`](../../NOTICE) 与 [`LICENSE-REASONIX`](../../LICENSE-REASONIX)。

## 阅读须知

- 文档按上游 `docs/` 收录，整体从 `docs/` 迁移到 `docs/kernel/`（`internal/tools/productdocs` 的 embed 路径相应改为 `reasonix/docs/kernel`）。
- 文中的 "Reasonix" 产品名、`reasonix` 命令名、上游仓库路径、社区链接（Discord / QQ / 赞助码等）均为**上游语境**。本仓的本地化工作按 note/12 决策执行：命令改名 `deep-tutor` 属阶段① M1.2，规约本地化 `DEEPTUTOR.md` 属 M1.4，两者均未完成。
- 与本仓根目录同名冲突的上游原件（README、AGENTS、CLAUDE、CONTRIBUTING、SECURITY、REASONIX、design-qa 等）存放在 [`upstream/`](./upstream/)，仅作存档对照，**不是本仓生效文档**；本仓生效的是根目录同名文件。
- 上游 CI 工作流与模板存档在 [`upstream/github/`](./upstream/github/)，不在本仓生效。

## 本仓已移除的上游资产

以下上游目录树与本仓目标形态无关，已在 2026-10-20 清理（内容保留于 git 历史，commit `bec5525`）：

| 上游目录 | 内容 | 移除后受影响的上游文档 |
|---|---|---|
| `release-notes/` | 上游 65 份版本发布说明 | `DOCS_STANDARD.md`（发布说明范围与 owner 约定）、`STUDIO_RELEASE.md`（上游发布流程） |
| `benchmarks/` | 上游 15 组评测夹具与 129 个 Go 包 | `design/INTELLIGENT_UI.md`（固定任务评测协议） |
| `examples/` | 上游 9 个 skill / 命令 / MCP 套件 | `PLUGIN_PACKAGES.md`（示例包引用） |
| `workers/` | 上游 accounts / forum / crash-report 云服务 | `DESKTOP_CRASH_DIAGNOSTICS_RUNBOOK.md`（诊断迁移脚本） |
| `npm/` | 上游 CLI 的 npm 分发包 | — |
| `output/` | 上游 agent-workbench v4–v6 原型产物 | — |

对应处置：

- 上游文档中指向已移除目录的链接改为**无链接的说明性表述**，保留被引用内容所描述的能力边界，不再指向不存在的文件。
- 上游评测/示例/发布说明的等价物在 Deep-tutor 侧另有归属：评测与门禁见 [`../../tools/`](../../tools/)，扩展示例见 [`../../sdk/go/examples/`](../../sdk/go/examples/)，前端插件示例见 [`../../plugin-examples/`](../../plugin-examples/)，方法论资产见 [`../../skills/`](../../skills/)。
- 保留的上游资产：`internal/`、`cmd/`、`tools/`、`sdk/`、`desktop/`、`scripts/reasonix/`、`docs/kernel/`（本文档树）、`benchmark/`（上游 Go 基准测试包）。

## 链接适配

上游文档的相对链接在 `docs/` → `docs/kernel/` 迁移后层级失配，已按新位置逐条修正：

| 位置 | 修正形态 |
|---|---|
| `docs/kernel/*.md` 指向仓库根资源 | `../<dir>/` → `../../<dir>/` |
| `desktop/README.md`、`sdk/go/README.md` 指向内核文档 | `docs/X.md` → `docs/kernel/X.md` |
| `upstream/README*.md` 指向内核文档与资源 | `./docs/X.md` → `../X.md`；`desktop/`、`sdk/go/` → `../../../` |
| `upstream/design-qa.md` 指向调色板图 | `docs/design/theme-palettes/` → `../design/theme-palettes/` |

修改披露见根目录 [`NOTICE`](../../NOTICE)。

## 目录速览

- `SPEC.md` / `TOOL_CONTRACT.md` / `TASK_CONTRACT.md` — 内核规格与工具/任务合约（内核改造的权威依据）
- `GUIDE(.zh-CN).md` / `CLI(.zh-CN).md` / `TUTOR.md` — 用户指南 / 命令参考 / 学习导师（Deep-tutor 域）
- `EXTENSIONS*.md` / `PLUGIN_PACKAGES.md` / `EXTENSION_PROTOCOL*.md` — 扩展与插件体系
- `STUDIO_*.md` / `DESKTOP_*` — Studio 桌面端与前端
- `SUBAGENT_PROFILES.md` / `SESSION_MEMORY_RETRIEVAL.md` / `CAPABILITY_DIAGNOSTICS.md` / `RECOVERY.md` / `CHECKPOINTS.md` / `BILLING.md` 等专题
