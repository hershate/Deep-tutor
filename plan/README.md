# plan/ — Deep-tutor 改造方案

> 当前方案：**基于 Reasonix 内核的完全改造**（`reasonix/`，2026-10-15 定稿方向层）。
> Python 时代实施计划已归档至 [docs/archive/plan-v1/](../docs/archive/plan-v1/)。

## reasonix/ 文档索引

| 文件 | 内容 |
|---|---|
| [00-总方案.md](reasonix/00-总方案.md) | 目标/决策摘要（61 项）/目标架构图/资产处置/四阶段/风险登记 |
| [01-仓库布局与内核落位.md](reasonix/01-仓库布局与内核落位.md) | 顶层 go.mod 布局、内核拷贝/裁剪/重命名流程、品牌化与 MIT 署名、门禁搬入 |
| [02-数据层设计.md](reasonix/02-数据层设计.md) | SQLite+sqlc+迁移、领域 schema 草案、data/ 目录总图、预算口径 |
| [03-API设计与shim映射.md](reasonix/03-API设计与shim映射.md) | /api v2 端点清单、SSE 协议、全局事件流、shim 翻译面与验收 |
| [04-多用户与内核集成.md](reasonix/04-多用户与内核集成.md) | 用户装配层、per-user 装配、内核多实例化清单、Chat/审批/Rewind/planner 审查者 |
| [05-四阶段任务分解与验收.md](reasonix/05-四阶段任务分解与验收.md) | M1–M5 细粒度任务、阶段②③④、9 插件等价表、19 页回归清单 |

## 执行状态（2026-10-20）

阶段① 进度：

| 任务 | 状态 | 说明 |
|---|---|---|
| M1.1 内核吸收与裁剪 | 完成 | 8289 文件入库；上游无关资产树已按 01 §二 原表剔除（commit `bec5525`） |
| M1.2 模块/import 重命名 | 未开始 | `go.mod` 仍为 `module reasonix`，全仓 8629 处 `reasonix/` import |
| M1.3 多实例 spike | 未开始 | 04 §四矩阵待回填 |
| M1.4 品牌化 + `DEEPTUTOR.md` | 未开始 | `DEEPTUTOR.md` 尚不存在 |
| M1.5 门禁基线重建 + CI | 未开始 | `tools/repolint/baseline.json` 仍为上游基线；CI 只跑 Python v1 门禁 |
| M1.6 `bin/deep-tutor.exe` | 未开始 | `cmd/` 下仍为 `reasonix` 系列入口 |
| M2 / M3 / M4 / M5 | 未开始 | M3.5 shim 层缺位，旧 SPA 暂不能对接 Go 服务 |

偏差登记：上游资产剔除范围与 note/12 修订记录 #37 的差异记于该文件修订记录及 01 执行状态注记。

## 必读配套

- [note/12-Reasonix改造决策.md](../note/12-Reasonix改造决策.md) — **改造宪法**：61 项已确认决策（五轮），实施遇分歧先对照
- [stage.md](../stage.md) — 进度跟踪（每步骤/commit/决策更新，用户常设要求）
- [API.md](../API.md) — v1 契约 = shim 兼容基准
- [note/01–11](../note/00-INDEX.md) — 项目语义基准（架构/数据/流水线/Agent/LLM/API/插件/基础设施/工具/前端/周边）
