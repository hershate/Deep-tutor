# deep-tutor-validate

## 资产状态

学习方案五维校验的历史方法论资产。`SKILL.md` 由 `plugins/mod.rs` 通过 `include_str!` 嵌入，作为 `legacy-skill` 经 `get_plugin_overview` 和 `get_developer_asset` 展示。当前不执行 Python 或将该文件用作 system prompt；修改后必须重新编译 Rust。

## 当前实现

- `learning/validate.rs` 合并模型报告、原生结构检查和确定性排期结果。
- 五项检查为依赖闭包、难度阶梯、认知负载、里程碑可行性和知识缺口。
- 只有五项全部 pass 才通过；warn 不等于 pass。
- 模型不可用时返回未执行校验的警告，方案降级，不自动重跑上游。
- 局部再生成由用户选择步骤 2–6 触发。

## 方法论来源

历史 study-planner Step 6。旧 Python `ValidatePlanData` 和自动反馈循环已删除；内部 snake_case 与 IPC camelCase 是有意区分的线缆契约。
