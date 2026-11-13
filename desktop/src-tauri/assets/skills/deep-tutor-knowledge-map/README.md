# deep-tutor-knowledge-map

## 资产状态

知识依赖图构建的历史方法论资产。`SKILL.md` 由 `plugins/mod.rs` 通过 `include_str!` 嵌入，作为 `legacy-skill` 经 `get_plugin_overview` 和 `get_developer_asset` 展示。当前不执行 Python 或将该文件用作 system prompt；修改后必须重新编译 Rust。

## 当前实现

- 生成：`learning/pipeline.rs` 的步骤 2。
- 校验：`learning/validate.rs` 的 `validate_graph`。
- 节点包含标题、摘要、难度、required、预计分钟和引用；边只包含 from/to。
- 校验覆盖 1–100 个节点、ID、字段边界、引用、悬空边、重复边、自环和拓扑环。
- 无资料图谱引用为空；有资料时逐字核对连续原文。

## 方法论来源

历史 study-planner Step 2。旧 Python `KnowledgeMapData`、`parent_id`、`must_learn` 和自动断环逻辑已删除。
