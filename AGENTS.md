# AGENTS.md — Deep-tutor 仓库工作指引

> 给在本仓库工作的 AI agent（与人类协作者）。改造期（Reasonix 架构）生效。

## 必读顺序（新会话开工前）

1. [`stage.md`](./stage.md) — 进度跟踪：先读它确定"现在做到哪、下一步做什么"。
2. [`note/12-Reasonix改造决策.md`](./note/12-Reasonix改造决策.md) — **改造宪法**：61 项用户逐条确认的决策。任何实现与它冲突 → 停下问用户，不得自行变更；答后把修订记回 note/12。
3. [`plan/reasonix/`](./plan/README.md) — 完整改造方案（六份），实施细节以它为准。
4. 领域语义基准：[`note/00-INDEX.md`](./note/00-INDEX.md)（01–11 分析笔记）；v1 API 契约：[`API.md`](./API.md)（= shim 验收基准）。

## 工作方式（用户常设要求）

- **进度**：每完成一个步骤、每次 git commit、每次关键决策或阻塞，立即更新 `stage.md`。
- **分支与提交**：改造工作在 `refactor/reasonix` 分支；细粒度 commit 由 agent 自主提交（**不 push**，由用户合并 main）。
- **提问**：出现任何产品级/方向级未明确内容，及时问用户（AskUserQuestion），不要猜。
- **节奏**：全自动推进，但任务拆分尽可能细、每一步谨慎（小步可验证增量）。
- **内核引入后**（M1 起）：遵守 `DEEPTUTOR.md` 工程规约（分层依赖、缓存稳定前缀、注释规范、预提交清单 gofmt/vet/lint/test）。

## 仓库状态速览（2026-10-17）

- **当前可用实现**：Python v0.1.x（`backend/` + `frontend/`，端口 41173）——保留至改造阶段① M5 验收后停用，阶段④删除。
- **进行中**：Reasonix 内核改造。**阶段① M1.1 吸收已完成（2026-10-17）**：R-Tutor studio 快照 8289 文件全量入库（内核 `internal/` `cmd/` `tools/` `sdk/`、桌面端 `desktop/`、上游文档 `docs/kernel/` 等），构建绿；M1 余项（多实例 spike → 模块改名 `deep-tutor` → DEEPTUTOR.md → 门禁基线）见 `plan/reasonix/05` 与 `stage.md`。
- **引用合规**：上游 https://github.com/esengine/DeepSeek-Reasonix （MIT）。声明与修改披露见根 [`NOTICE`](./NOTICE)，许可全文 [`LICENSE-REASONIX`](./LICENSE-REASONIX)；修改上游代码须同步更新 NOTICE 修改披露。
- **参考副本**：`REF/`（gitignore，不入库）= 吸收用源快照（zip 为备份），仅供对照。
- **归档**：Python 时代计划与工作档案在 `docs/archive/`（git 历史完整）。
