# deep-tutor-stages

## 简介
Deep-tutor「学习阶段设计」工具的方法论指令源。运行时由 Python Skill 加载器读取 `SKILL.md` 正文，作为 `design_stages` 工具调用 LLM 时的 system prompt。依据知识图谱与意图设计 3-6 个渐进难度学习阶段。

## 目录结构
```
deep-tutor-stages/
├── SKILL.md     # 方法论指令（运行时作 system prompt）
└── README.md    # 本文件
```

## 运行时集成
- **加载**：`app/skills/loader.py` 启动扫描 `skills/deep-tutor-stages/SKILL.md`，按 frontmatter `name` 注册。
- **取用**：`app/agent/loop.py` `_get_system_prompt` 取本 Skill 正文作 `design_stages` 的 system prompt（三级回退：skill -> inline_prompt -> 默认）。
- **校验**：输出由 `app/tools/design_stages.py` 的 `StagesData` pydantic 模型校验；`model_validator` 强制阶段数 3-6，不符走工具调用修复（重试 ≤3 -> 降级默认 3 阶段）。

## 方法论来源
移植自 study-planner Step 3（渐进阶段、每阶段目标 / 3-6 知识点 / 里程碑）。

## 注意事项
- 本 Skill 不是 Claude Code 用户态技能，而是 Deep-tutor 运行时确定性加载的方法论指令。
- 修改 `SKILL.md` 后重启后端即生效。
- 输出 schema 变更须同步 `StagesData`/`Stage`/`StageTopic` pydantic 模型。
