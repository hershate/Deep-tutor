---
name: deep-tutor-validate
description: >-
  学习方案五维校验的历史方法论资产。当前仅作静态展示，说明模型报告、原生复核和降级规则。
version: 1.0.0
---

# Deep Tutor Validate

> 资产状态：历史方法论。当前不加载为 system prompt，不注册工具或斜杠命令。
> 当前实现位于 `desktop/src-tauri/src/backend/learning/validate.rs` 和 `pipeline.rs`。

## 当前原生契约

方案步骤 6 先获取模型 `ModelValidation`，再由 `validation_report` 生成 IPC 使用的 `ValidationReport`。内部模型报告使用 snake_case，IPC 报告使用 camelCase。

| 检查项 | 当前判定 |
|---|---|
| dependencyClosure | 原生检查 required 覆盖、重复分配、未知节点和前置顺序 |
| difficultyStep | 原生检查难度不下降且相邻跳级不超过 2 |
| cognitiveLoad | 原生检查 3–6 个阶段、每阶段 3–6 个节点 |
| milestoneFeasibility | 使用模型判断，并用原生排期的截止日期和硬里程碑结果修正 |
| gapAnalysis | 使用模型检查未解释术语和学习跳步 |

- 五项状态均为 pass、warn 或 fail；只有五项全部 pass 时 `passed=true`。
- 每项 details 不超过 4000 字节；每条 fix 非空且不超过 500 字节，fixes 最多 20 项。
- 模型不可用或输出无效时使用 `unavailable_model`：五项为 warn、passed=false，并提示人工复核；方案最终为 degraded，不伪造通过。
- 原生依赖、难度或认知负载问题会追加明确修复建议，去重、排序并截到 20 项。
- 当前没有按失败字段自动重跑上游的反馈循环；用户通过局部再生成选择步骤 2–6。

## 方法论

- 区分模型判断和可确定性复核的规则，不让模型宣布与排期冲突的日期可行性。
- 修复建议必须指明对象和动作，不能只写“优化方案”。
- 警告与通过不得混用，未执行模型校验不得记为 pass。

## 历史差异

旧“任一 fail 才 passed=false”“全 warn 仍 passed=true”和 Python 自动反馈映射不属于当前实现。
