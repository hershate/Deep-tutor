# deep-tutor-stages

## 资产状态

渐进学习阶段设计的历史方法论资产。`SKILL.md` 由 `plugins/mod.rs` 通过 `include_str!` 嵌入，作为 `legacy-skill` 经 `get_plugin_overview` 和 `get_developer_asset` 展示。当前不执行 Python 或将该文件用作 system prompt；修改后必须重新编译 Rust。

## 当前实现

- `learning/pipeline.rs` 的步骤 3 生成 `Stage { id,title,nodeIds,difficulty }`。
- `learning/validate.rs` 先执行 1–20 阶段的结构校验，再在五维校验中检查 3–6 阶段、每阶段 3–6 节点和难度梯度。
- 步骤 5 通过确定性排期增加 minutes、startDate、endDate，不使用模型估计周数。
- 校验失败不生成默认三阶段。

## 方法论来源

历史 study-planner Step 3。旧 Python `StagesData`、`topics`、`estimated_weeks` 和阶段内 milestone 字段已删除。
