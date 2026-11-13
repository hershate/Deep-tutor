# deep-tutor-quiz

## 资产状态

学习测评生成的历史方法论资产。`SKILL.md` 由 `plugins/mod.rs` 通过 `include_str!` 嵌入，作为 `legacy-skill` 经 `get_plugin_overview` 和 `get_developer_asset` 展示。当前不执行 Python、旧 HTTP 端点或将该文件用作 system prompt；修改后必须重新编译 Rust。

## 当前实现

- `practice/mod.rs` 负责 1–20 题生成、范围、引用和判分。
- 支持 choice、fill、short、essay；choice 答案为从 0 开始的下标。
- `practice/subjective.rs` 负责量规评分，`variants.rs` 负责错题变式，`exams.rs` 负责考试与交卷。
- 资料预算为单份 12000、合计 64000 字符；失败不生成默认题。
- 任务、答案、错题、证据和考试状态真实持久化。

## 方法论来源

历史按知识点多题型出题方法。旧 `QuizSpec`、原文选项答案、题型自动降级和默认自评题已删除。
