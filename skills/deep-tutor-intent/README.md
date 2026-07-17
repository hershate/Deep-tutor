# deep-tutor-intent

## 简介
Deep-tutor「学习意图理解」工具的方法论指令源。运行时由 Python Skill 加载器读取 `SKILL.md` 正文，作为 `understand_intent` 工具调用 LLM 时的 system prompt。从用户描述与解析资料中抽取学习意图四维度（topic / level / goal / daily_minutes / deadline），信息不足时触发澄清。

## 目录结构
```
deep-tutor-intent/
├── SKILL.md     # 方法论指令（运行时作 system prompt）
└── README.md    # 本文件
```

## 运行时集成
- **加载**：`app/skills/loader.py` 启动扫描 `skills/deep-tutor-intent/SKILL.md`，按 frontmatter `name` 注册到 `skill_registry`。
- **取用**：`app/agent/loop.py` `_get_system_prompt` 取本 Skill 正文作 `understand_intent` 的 system prompt（三级回退：skill -> inline_prompt -> 默认）。
- **校验**：输出由 `app/tools/understand_intent.py` 的 `IntentData` pydantic 模型校验，不符走工具调用修复（重试 ≤3 -> 降级默认）。

## 方法论来源
移植自 study-planner Step 1（解析目标、当前基础、学习目标、时间投入）。

## 注意事项
- 本 Skill 不是 Claude Code 用户态技能（不通过 `/deep-tutor-intent` 触发），而是 Deep-tutor 运行时确定性加载的方法论指令。
- 修改 `SKILL.md` 后重启后端即生效（加载器启动扫描 + 缓存）。
- 输出 schema 变更须同步 `IntentData` pydantic 模型，否则触发工具调用修复。
