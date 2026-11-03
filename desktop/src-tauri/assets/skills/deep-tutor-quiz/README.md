# deep-tutor-quiz

## 简介
Deep-tutor「测验题生成」工具的方法论指令源。运行时由 Python Skill 加载器读取 `SKILL.md` 正文，作为 `generate_quiz` 工具调用 LLM 时的 system prompt。从学习方案按知识点多题型出题（choice/fill/short），支持以自学单元「检查理解」自测题为题源改编（iter 49 题源联动）。

## 目录结构
```
deep-tutor-quiz/
├── SKILL.md     # 方法论指令（运行时作 system prompt）
└── README.md    # 本文件
```

## 运行时集成
- **加载**：`app/skills/loader.py` 启动扫描 `skills/deep-tutor-quiz/SKILL.md`，按 frontmatter `name` 注册。
- **取用**：`app/agent/loop.py` `_get_system_prompt` 取本 Skill 正文作 `generate_quiz` 的 system prompt（三级回退：skill -> inline_prompt -> 默认）。quiz 端点（`POST /plans/{id}/quiz`）的 ctx 注入 `skill_registry` 后生效。
- **校验**：输出由 `app/tools/generate_quiz.py` 的 `QuizSpec`/`QuizItem` pydantic 模型校验；未知题型经 validator 降级 `choice`。
- **题源联动**：端点在已持久化自学单元存在时提取「### 检查理解」小节注入 `args.unit_check_questions`（见 `extract_check_questions`）。

## 方法论来源
plan/02 §8（按知识点多题型出题）+ iter 37-49 已验证的 inline PROMPT 规则移植（must_learn 优先、5-20 题、题型混合、难度贴合 difficulty、answer 为 options 原文、不编造知识点）。

## 注意事项
- 本 Skill 不是 Claude Code 用户态技能，而是 Deep-tutor 运行时确定性加载的方法论指令。
- 修改 `SKILL.md` 后重启后端即生效。
- 输出 schema 变更须同步 `QuizSpec`/`QuizItem` pydantic 模型。
- choice 的 `answer` 必须是 `options` 中正确选项原文（前端/服务器按原文比对判分）。
