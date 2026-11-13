# deep-tutor-resources

## 资产状态

学习资源检索与验证的历史方法论资产。`SKILL.md` 由 `plugins/mod.rs` 通过 `include_str!` 嵌入，作为 `legacy-skill` 经 `get_plugin_overview` 和 `get_developer_asset` 展示。当前不执行 Python 或将该文件用作 system prompt；修改后必须重新编译 Rust。

## 当前实现

- `web/mod.rs` 使用 DuckDuckGo HTML 检索并实际抓取候选。
- 仅抓取成功的结果标记 verified；状态为 done、partial 或 unavailable。
- 抓取链执行 SSRF、DNS 钉扎、重定向、编码、大小和内容类型检查。
- `learning/pipeline.rs` 的步骤 4 将真实结果写入方案，失败时保留警告。
- 当前没有 LLM 内容适配评分或三级验证标记。

## 方法论来源

历史 study-planner Step 4。旧 Bing API、`verify_url`、`suitable/label/note` 和 Python 适配筛选器已删除。
