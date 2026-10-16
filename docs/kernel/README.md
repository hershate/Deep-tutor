# docs/kernel/ — Reasonix 内核工程文档（上游原样吸收）

> **本目录全部内容源自 [DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)（`studio` 分支，快照 `R-Tutor-src-studio-20261017.zip`），MIT License，Copyright (c) 2026 Reasonix Contributors。**
> 完整的引用声明、路径映射与修改披露见仓库根目录 [`NOTICE`](../../NOTICE) 与 [`LICENSE-Reasonix`](../../LICENSE-REASONIX)。

## 阅读须知

- 文档按上游 `docs/` 原样收录，仅整体从 `docs/` 迁移到 `docs/kernel/`（因此 `internal/tools/productdocs` 的 embed 路径改为 `reasonix/docs/kernel`）。
- 文中出现的 "Reasonix" 产品名、`reasonix` 命令名、上游仓库路径、社区链接（Discord / QQ / 赞助码等）均为**上游语境**，在 Deep-tutor 仓库中按 note/12 决策逐步本地化（改名 `deep-tutor` 计划见 `plan/reasonix/05` M1.2，规约本地化见 M1.4 `DEEPTUTOR.md`）。
- 与本仓根目录同名冲突的上游原件（README、AGENTS、CLAUDE、CONTRIBUTING、SECURITY、REASONIX、design-qa 等）存放在 [`upstream/`](./upstream/)，仅作存档对照，**不是本仓生效文档**；本仓生效的是根目录同名文件。
- 上游 CI 工作流与模板存档在 [`upstream/github/`](./upstream/github/)，不在本仓生效。

## 目录速览（与上游 docs/ 一致）

- `SPEC.md` / `TOOL_CONTRACT.md` / `TASK_CONTRACT.md` — 内核规格与工具/任务合约（内核改造的权威依据）
- `GUIDE(.zh-CN).md` / `CLI(.zh-CN).md` / `TUTOR.md` — 用户指南 / 命令参考 / 学习导师（Deep-tutor 域）文档
- `EXTENSIONS*.md` / `PLUGIN_PACKAGES.md` / `EXTENSION_PROTOCOL*.md` — 扩展与插件体系
- `STUDIO_*.md` / `DESKTOP_*` — Studio 桌面端与前端
- `SUBAGENT_PROFILES.md` / `SESSION_MEMORY_RETRIEVAL.md` / `CAPABILITY_DIAGNOSTICS.md` / `RECOVERY.md` / `CHECKPOINTS.md` / `BILLING.md` 等专题
