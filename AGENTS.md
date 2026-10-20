# AGENTS.md — Deep-tutor 仓库工作指引

> 给在本仓库工作的 AI agent（与人类协作者）。改造期（Reasonix 架构）生效。

## 必读顺序（新会话开工前）

1. [`stage.md`](./stage.md) — 进度跟踪：先读该文件确定"现在做到哪、下一步做什么"。
2. [`note/12-Reasonix改造决策.md`](./note/12-Reasonix改造决策.md) — **改造宪法**：61 项用户逐条确认的决策。任何实现与该决策冲突 → 停下问用户，不得自行变更；答后把修订记回 note/12。
3. [`plan/reasonix/`](./plan/README.md) — 完整改造方案（六份），实施细节以该方案为准。
4. 领域语义基准：[`note/00-INDEX.md`](./note/00-INDEX.md)（01–11 分析笔记）；v1 API 契约：[`API.md`](./API.md)（= shim 验收基准）。

## 工作方式（用户常设要求）

- **进度**：每完成一个步骤、每次 git commit、每次关键决策或阻塞，立即更新 `stage.md`。
- **分支与提交**：改造工作在 `refactor/reasonix` 分支；细粒度 commit 由 agent 自主提交（**不 push**，由用户合并 main）。
- **提问**：出现任何产品级/方向级未明确内容，及时问用户（AskUserQuestion），不要猜。
- **节奏**：全自动推进，但任务拆分尽可能细、每一步谨慎（小步可验证增量）。
- **内核引入后**（M1 起）：工程规约待落 `DEEPTUTOR.md`（M1.4 交付项，**当前文件不存在**）。在此之前，Go 侧改动遵循上游 `docs/kernel/` 的既有规约与 `Makefile` 门禁命令；建档后以 `DEEPTUTOR.md` 为准。

## 仓库状态速览（2026-10-21）

- **当前可用实现**：Python v0.1.x（`backend/` + `frontend/`，端口 41173）——保留至改造阶段① M5 验收后停用，阶段④删除。实测规模：后端 28 个 `api/` 文件（24 个路由模块）、OpenAPI 145 operations / 119 paths、29 张 ORM 表、26 个 alembic 迁移、180 个测试文件（pytest 收集 1521 用例，iter 393 实测 1520 passed / 1 skipped）；前端 19 页 SPA。
- **进行中**：Reasonix 内核改造。**阶段① M1.1 吸收已完成（2026-10-17）**：R-Tutor studio 快照 8289 文件全量入库（内核 `internal/` `cmd/` `tools/` `sdk/`、桌面端 `desktop/`、上游文档 `docs/kernel/` 等），构建绿。**M1 余项全部未完成**（M1.2 模块改名 `reasonix`→`deep-tutor`；M1.3 多实例 spike；M1.4 品牌化与 `DEEPTUTOR.md` 建档；M1.5 门禁基线重建 + CI；M1.6 `bin/deep-tutor.exe` 冒烟），见 `plan/reasonix/05` 与 `stage.md`。
- **门禁现状**：`.github/workflows/ci.yml` 当前只跑 Python v1 四步门禁（ruff / pytest 覆盖率≥80% / OpenAPI 契约 / 前端 build），**未纳入任何 Go 门禁**。Go 门禁命令在 `Makefile`（`make lint` / `make check` / `make test` / `make coverage-gate` / `make pricecheck`）。`tools/repolint` 基线尚未按本仓树重建（现存 2429 行报告，含上游 `REF/` 副本扫描与本仓根文件），M1.5 处理。`go test ./...` 在本机固定失败的用例如下，均为环境或工作树依赖，**非代码回归**（2026-10-20 逐包串行复跑核实）：

| 包 / 用例 | 失败条件 |
|---|---|
| `internal/safety/sandbox` `TestIsWindowsWSLBash` | 按文件身份判定 WSL 启动器（`internal/safety/sandbox/shell.go:333`），需本机存在 `C:\Windows\System32\bash.exe` |
| `internal/base/testenv` `TestWindowsCILegsBypassTheTestResultCache` | 要求 CI 工作流含 Windows 上的根模块 `go test` 步骤；本仓 `ci.yml` 无 Go job |
| `internal/ext/skill/builtincontent` | 本机 AV 占用 `go-build` 临时 `.exe`，报 `Access is denied` |
| `internal/runtime/agent` `TestChildConstructionForksStayEnumerated` | 遍历工作树全部 `.go` 文件（未过滤 `REF/`），本地 `REF/extracted/` 副本被判为未登记的子代理构造点 |
| `internal/runtime/schedrun` `TestKillingTheRunEndsItsWholeProcessTree` | Windows 子进程树取锁时序 |
| `internal/tools/builtin` `TestPipeStatusProbePreservesExitStatusAndReports` | Windows 管道退出码探针 |

另：`internal/assembly/boot` 单包本机耗时 **974.9 秒**，超过 Go 默认 600 秒超时上限，`go test ./...` 一次性执行必然报 timeout panic（`make test` 同）；该包单独执行（`-timeout 25m`）通过，`internal/frontend/serve` 单独执行亦通过。Go 侧门禁的实际执行方式（分批 + 超时上限）属 M1.5 待定项。
- **引用合规**：上游 https://github.com/esengine/DeepSeek-Reasonix （MIT）。声明与修改披露见根 [`NOTICE`](./NOTICE)，许可全文 [`LICENSE-REASONIX`](./LICENSE-REASONIX)；修改上游代码须同步更新 NOTICE 修改披露。上游无关资产树（`release-notes/` `benchmarks/` `examples/` `workers/` `npm/` `output/`）已于 2026-10-20 移除，范围偏差登记于 note/12 修订记录。
- **参考副本**：`REF/`（gitignore，不入库）= 吸收用源快照（zip 为备份），仅供对照。**注意**：两个门禁会扫工作树全集，结果依赖本地副本状态——`tools/repolint`（报告含 `REF/` 副本）与 `internal/runtime/agent` 的 `TestChildConstructionForksStayEnumerated`（遍历全部 `.go`，未过滤 `REF/`）。
- **归档**：Python 时代计划与工作档案在 `docs/archive/`（git 历史完整）。

## Reasonix host checks

下列路径决定运行时允许执行的行为，路径下的改动需要 `review` + `security_review` 才能结束一轮。敏感性为显式声明，不由路径名推断。同一份清单同时是覆盖率门禁（`make coverage-gate`）的对象：每条路径被锁定在既有覆盖率下限，下调需 `make coverage-gate-update -allow-drop` 并说明理由。清单原文见 [`docs/kernel/upstream/REASONIX.md`](./docs/kernel/upstream/REASONIX.md)。

- sensitive: internal/safety/permission/**
- sensitive: internal/safety/sandbox/**
- sensitive: internal/safety/shellsafe/**
- sensitive: internal/base/shellparse/**
- sensitive: internal/session/control/approval.go
- sensitive: internal/session/control/approval_bridge.go
- sensitive: internal/session/control/approval_orchestration.go
- sensitive: internal/model/providerbroker/**
- sensitive: internal/ext/installsource/**
- sensitive: internal/ext/plugin/**
- sensitive: internal/ext/pluginpkg/**
- sensitive: internal/base/netclient/**
- sensitive: internal/safety/redirectguard/**
- sensitive: internal/safety/endpointclient/**
- sensitive: internal/safety/egress/**
- sensitive: internal/platform/browser/**
- sensitive: internal/platform/computer/**

`internal/web/auth/**`、`internal/web/session/**`、`internal/web/shim/**` 三条待阶段① M2/M3 建成 `internal/web` 服务层后追加（见 `plan/reasonix/01` §五）。
