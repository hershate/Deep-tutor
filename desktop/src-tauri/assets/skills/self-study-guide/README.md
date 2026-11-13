# Self-Study Guide

## 资产状态

自学材料组织方法的历史资产。`SKILL.md` 由 `plugins/mod.rs` 通过 `include_str!` 嵌入，作为 `legacy-skill` 经 `get_plugin_overview` 和 `get_developer_asset` 展示。修改后必须重新编译 Rust。

当前应用不提供 `/self-study-guide`、关键词触发或 Read/Write/WebSearch 工具执行，不创建 `study-materials/`。历史 `references/` 和 `study-materials/` 从未随本仓库资产分发。

## 当前实现

- `learning/pipeline.rs`：目标、知识图谱、阶段和确定性排期。
- `learning/lecture.rs`：按单元生成 sections、examples、checkpoints 和 citations。
- `practice/`：多题型练习、主观评分、错题变式和考试。
- `review/`：四档反馈、到期排程和复习日历。
- `web/`：公开检索和实际正文抓取；只采纳验证成功的网页。
- SQLite、导出、备份和分享负责持久化与交付，不生成历史目录结构。

## 方法论内容

`SKILL.md` 保留输入解析、30–60 分钟单元、模块分组、资料检索、单元模板、索引和结果报告的历史规则，并逐项标明当前原生对应关系。当前节点预计时长以 5–600 分钟的图谱契约为准，不沿用历史 30–60 分钟作为硬限制。
