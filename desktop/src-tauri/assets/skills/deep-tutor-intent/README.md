# deep-tutor-intent

## 资产状态

学习意图澄清的历史方法论资产。`SKILL.md` 仅作为 `legacy-skill` 静态内容，由 `plugins/mod.rs` 的 `assets()` 通过 `include_str!` 嵌入二进制。插件面板使用 `get_plugin_overview` 读取清单、`get_developer_asset` 读取正文。

当前应用不执行 Python，不加载该文件为 system prompt，也不注册斜杠命令。修改后必须重新编译 Rust。

## 当前实现

- 位置：`desktop/src-tauri/src/backend/learning/`。
- 步骤 0 读取 0–8 份可选资料；步骤 1 本地规范化 `PlanInput`，不调用模型。
- 目标与当前基础各为 1–1000 字节，每日 10–600 分钟，截止日期使用 `YYYY-MM-DD`。
- 输入错误返回类型化错误，不生成旧 `needs_clarify` 或默认追问。

## 方法论来源

历史 study-planner Step 1。旧 Python `IntentData` 已删除；当前字段与校验以 `learning/types.rs` 和 `learning/mod.rs` 为准。
