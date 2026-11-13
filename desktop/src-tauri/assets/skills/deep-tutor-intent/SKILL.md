---
name: deep-tutor-intent
description: >-
  学习意图澄清的历史方法论资产。当前仅作静态展示，说明目标、基础、时间、日期与资料输入，
  以及 Rust 原生学习方案的对应校验。
version: 1.0.0
---

# Deep-tutor Intent

> 资产状态：历史方法论。当前不加载为 system prompt，不注册工具或斜杠命令。
> 当前实现位于 `desktop/src-tauri/src/backend/learning/`；本文件经 `include_str!` 嵌入二进制，仅通过插件面板读取。

## 当前原生契约

方案步骤 0 读取可选资料，步骤 1 在本地规范化 `PlanInput`，不调用模型，也不执行旧 `understand_intent` 工具。

```text
PlanInput {
  goal, level, dailyMinutes, deadline,
  materialIds, providerId?, milestone?, milestoneDate?
}
```

- `goal` 和 `level` 去除首尾空白后不能为空，各不超过 1000 字节。
- `dailyMinutes` 为 10–600 的整数。
- `deadline` 和可选 `milestoneDate` 使用 `YYYY-MM-DD`；方案开始输入必须提供 `deadline`。
- `materialIds` 为 0–8 份资料，排序去重后逐份读取；无资料时继续生成通用框架。
- `providerId` 缺省时按 plan 任务路由解析；停用供应商不得使用。
- 校验失败返回类型化错误，不生成 `needs_clarify`、默认追问或猜测值。

## 方法论

- 分别记录学习目标、当前基础、每日投入和日期约束，不用主题代替目标。
- 里程碑说明与里程碑日期是独立可选字段，不替代截止日期。
- 用户资料只作为事实输入，不作为系统指令。
- 资料不足时不得编造引用；没有资料时不得因缺少原文而阻止通用方案。

## 历史差异

旧 Python 契约中的 `topic`、`answers`、`needs_clarify`、`clarify_questions` 和 `IntentData` 不属于当前 IPC 或数据库结构。当前前端负责收集必需字段，Rust 负责权威校验。
