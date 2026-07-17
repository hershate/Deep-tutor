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
- **加载**：`app/skills/loader.py` 启动扫描 `skills/deep-tutor-validate/SKILL.md`，按 frontmatter `name` 注册。
- **取用**：`app/agent/loop.py` `_get_system_prompt` 取本 Skill 正文作 `validate_plan` 的 system prompt（三级回退：skill -> inline_prompt -> 默认）。
- **校验**：输出由 `app/tools/validate_plan.py` 的 `ValidatePlanData` pydantic 模型校验。
- **反馈循环**：`passed=false` 时编排器据 `report` 失败项重跑上游（plan/10 §六，≤2 轮）。

## 方法论来源
移植自 study-planner Step 6（5 项校验：依赖闭包 / 难度阶梯 / 认知负荷 / 里程碑可行性 / 新手盲点）。

## 注意事项
- 本 Skill 不是 Claude Code 用户态技能，而是 Deep-tutor 运行时确定性加载的方法论指令。
- 修改 `SKILL.md` 后重启后端即生效。
- 输出 schema 变更须同步 `ValidatePlanData`/`ValidationReport`/`CheckResult` pydantic 模型。
- `fixes` 须可执行（指明阶段与动作），供反馈循环重跑上游；笼统建议会导致循环无效。
