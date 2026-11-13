---
name: deep-tutor-stages
description: >-
  渐进学习阶段设计的历史方法论资产。当前仅作静态展示，说明结构校验、认知负载与排期的分工。
version: 1.0.0
---

# Deep Tutor Stages

> 资产状态：历史方法论。当前不加载为 system prompt，不注册工具或斜杠命令。
> 当前实现位于 `desktop/src-tauri/src/backend/learning/pipeline.rs` 和 `validate.rs`。

## 当前原生契约

方案步骤 3 由模型生成 `Stage { id, title, nodeIds, difficulty }`。步骤 5 另行生成 `ScheduledStage`，增加 minutes、startDate 和 endDate。

### 结构校验

- 阶段数为 1–20，每阶段为 1–100 个节点；ID 和标题有效且阶段 ID 不重复。
- 节点必须来自图谱，不得重复分配；全部 required 节点必须被分配。
- 前置节点必须位于更早阶段；如果依赖目标已分配，其前置也必须纳入阶段。
- 难度为 1–5。

### 认知负载校验

五维校验要求 3–6 个阶段、每阶段 3–6 个节点，并要求难度不下降、相邻跳级不超过 2。违反这些条件会使对应检查为 fail；它们不是结构解析阶段的自动修复项。

## 方法论

- 阶段按依赖和难度组织，不用旧 `estimated_weeks` 代替实际日期。
- 阶段只引用图谱节点，不新增知识点或引用。
- 排期根据节点预计分钟和每日容量确定，不由模型估计周数。
- 校验失败不生成默认的“入门/进阶/实战”三阶段。

## 历史差异

旧 `index`、`name`、`goal`、`estimated_weeks`、`topics`、`milestone` 和 Python `StagesData` 不属于当前 `Stage`。阶段里程碑能力由排期与项目里程碑分别实现。
