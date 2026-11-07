# deep-tutor-validate

## 简介
Deep-tutor「方案校验」工具的方法论指令源。运行时由 Python Skill 加载器读取 `SKILL.md` 正文，作为 `validate_plan` 工具调用 LLM 时的 system prompt。对完整学习方案做 5 项校验并给修复建议，驱动反馈循环。

## 目录结构
```
deep-tutor-validate/
├── SKILL.md     # 方法论指令（运行时作 system prompt）
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
移植自 study-planner Step 6（5 项校验：依赖闭包 / 难度阶梯 / 认知负荷 / 里程碑可行性 / 新手盲点）。

## 注意事项
- 本 Skill 不是 Claude Code 用户态技能，而是 Deep-tutor 运行时确定性加载的方法论指令。
- 修改 `SKILL.md` 须重新编译 Rust（`include_str!` 编译期嵌入，重启应用不生效）。
- 输出 schema 变更须同步 `ValidatePlanData`/`ValidationReport`/`CheckResult` pydantic 模型。
- `fixes` 须可执行（指明阶段与动作），供反馈循环重跑上游；笼统建议会导致循环无效。
