---
name: deep-tutor-quiz
description: >-
  学习测评生成的历史方法论资产。当前仅作静态展示，说明题型、范围、引用、判分和变式约束。
version: 1.0.0
---

# Deep Tutor Quiz

> 资产状态：历史方法论。当前不加载为 system prompt，不注册 HTTP 端点、工具或斜杠命令。
> 当前实现位于 `desktop/src-tauri/src/backend/practice/`。

## 当前原生契约

`generate_practice` 创建持久化练习集，支持 targeted 和 exam。题量为 1–20，默认 5；题型为 choice、fill、short、essay，默认 choice+fill。

```text
QuestionDraft {
  kind, prompt, options, answer, hint, explanation,
  citations, nodeIds, difficulty, rubric
}
```

- choice 提供 2–6 个不同选项，answer 是从 0 开始的正确选项下标，不是选项原文。
- fill 的 answer 是用于文字核对的简洁术语；short/essay 提供参考答案和 2–6 项 rubric，权重总和为 1。
- 客观题由 Rust 判分；主观题由模型按量规评分。反馈模式为 immediate、deferred、hints。
- 可按方案、阶段或单个 nodeId 限定范围；变式必须来自同方案、同题型的真实错题，且不得用于 exam。
- 有资料时每题至少一条连续原文引用；无资料时 citations 为空。资料每份最多 12000 字符，合计最多 64000 字符。
- 提示词排除历史近似题干并加入随机批次标识；写入事务再次查重。数量、题型、引用或节点范围不符时整批失败，不生成默认题。

## 判分与证据

- 选择题为合法下标且等于期望；填空题去空白并转小写后比较。
- hints 遮蔽分数、对错和解析，不写错题与掌握度证据。
- deferred 交卷后统一反馈；未答题记 0 分但不写错题。
- 变式答对沿链更新中间错题和原错题的掌握状态。

## 历史差异

旧 5–20 题范围、choice 原文答案、只允许 choice/fill/short、未知题型自动改 choice、失败时默认自评题和 `POST /plans/{id}/quiz` 均不属于当前实现。
