# deep-tutor-resources

## 简介
Deep-tutor「资源检索与适配筛选」工具的方法论指令源。源自已删除的 Python 后端；`SKILL.md` 现作为 legacy-skill 静态资产编译期嵌入二进制、仅经插件面板展示，不再作运行时 system prompt，对应能力由 Rust 原生流水线实现。候选资源经 DuckDuckGo 检索 + Rust 原生抓取链可达性校验后，批量判定与阶段主题/学习者水平的适配性（不相关丢弃、存疑降 ⚠️、附说明）。

## 目录结构
```
deep-tutor-resources/
├── SKILL.md     # 方法论指令（历史资产，仅静态展示）
└── README.md    # 本文件
```

## 运行时集成

> 30#1 事实修正：本节原描述的 Python Skill 加载器（`app/skills/loader.py`、
> `app/agent/loop.py`、pydantic 校验链）属已删除的历史 Python 后端，当前应用
> 不存在任何 Python 运行时。当前实现见下方「当前资产状态」。

## 当前资产状态
- **嵌入方式**：`SKILL.md` 由 `desktop/src-tauri/src/backend/plugins/mod.rs` 的
  `assets()` 以 `include_str!` **编译期嵌入**二进制，作为「legacy-skill（历史
  Skill 资产）」静态条目经 `get_plugin_overview`（清单）与 `get_developer_asset`（正文）命令展示（`asset` 为 Rust 内部函数，非 IPC 命令）。
- **生效方式**：修改 `SKILL.md` 后须**重新编译 Rust**（重启应用不生效）。
- **执行边界**：当前应用不执行 Python；内联技能 `native-operations` 明示
  「不执行 Python」。

## 方法论来源
移植自 study-planner Step 4（链接验证 ✅⚠️❌ + 内容难度适配判定，plan/02 §5）；本 Skill 负责「内容适配」一半，可达性判定现由 Rust 原生抓取链（backend/web）完成。

## 注意事项
- 本 Skill 不是 Claude Code 用户态技能，而是历史 Python 后端的方法论指令，现作为 legacy-skill 静态资产展示。
- 修改 `SKILL.md` 须重新编译 Rust（`include_str!` 编译期嵌入，重启应用不生效）。
- 原 `_parse_screen_verdicts` 与 inline `SCREEN_PROMPT` 属 Python 后端；筛选契约现由 Rust 流水线承接（backend/learning）。
- `suitable=false` 仅限内容不相关（将被丢弃）；相关但过深/存疑用 `label="uncertain"` 保留。
