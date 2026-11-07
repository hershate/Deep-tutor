# deep-tutor-resources

## 简介
Deep-tutor「资源检索与适配筛选」工具的方法论指令源。运行时由 Python Skill 加载器读取 `SKILL.md` 正文，作为 `search_resources` 工具**内容适配性筛选** LLM 调用的 system prompt：候选资源经 Bing 检索 + verify_url 可达性校验后，批量判定与阶段主题/学习者水平的适配性（不相关丢弃、存疑降 ⚠️、附说明）。

## 目录结构
```
deep-tutor-resources/
├── SKILL.md     # 方法论指令（运行时作筛选 system prompt）
└── README.md    # 本文件
```

## 运行时集成

> 30#1 事实修正：本节原描述的 Python Skill 加载器（`app/skills/loader.py`、
> `app/agent/loop.py`、pydantic 校验链）属已删除的历史 Python 后端，当前应用
> 不存在任何 Python 运行时。当前实现见下方「当前资产状态」。

## 当前资产状态
- **嵌入方式**：`SKILL.md` 由 `desktop/src-tauri/src/backend/plugins/mod.rs` 的
  `assets()` 以 `include_str!` **编译期嵌入**二进制，作为「legacy-skill（历史
  Skill 资产）」静态条目经 `get_plugin_overview`/`asset` 命令展示。
- **生效方式**：修改 `SKILL.md` 后须**重新编译 Rust**（重启应用不生效）。
- **执行边界**：当前应用不执行 Python；内联技能 `native-operations` 明示
  「不执行 Python」。

## 方法论来源
移植自 study-planner Step 4（链接验证 ✅⚠️❌ + 内容难度适配判定，plan/02 §5）；本 Skill 负责「内容适配」一半，可达性判定由 verify_url（纯 Python）完成。

## 注意事项
- 本 Skill 不是 Claude Code 用户态技能，而是 Deep-tutor 运行时确定性加载的方法论指令。
- 修改 `SKILL.md` 须重新编译 Rust（`include_str!` 编译期嵌入，重启应用不生效）。
- 筛选输出 schema 变更须同步 `_parse_screen_verdicts` 与 inline `SCREEN_PROMPT`。
- `suitable=false` 仅限内容不相关（将被丢弃）；相关但过深/存疑用 `label="uncertain"` 保留。
