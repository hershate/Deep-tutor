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
plan/02 §8（按知识点多题型出题）+ iter 37-49 已验证的 inline PROMPT 规则移植（must_learn 优先、5-20 题、题型混合、难度贴合 difficulty、answer 为 options 原文、不编造知识点）。

## 注意事项
- 本 Skill 不是 Claude Code 用户态技能，而是 Deep-tutor 运行时确定性加载的方法论指令。
- 修改 `SKILL.md` 须重新编译 Rust（`include_str!` 编译期嵌入，重启应用不生效）。
- 输出 schema 变更须同步 `QuizSpec`/`QuizItem` pydantic 模型。
- choice 的 `answer` 必须是 `options` 中正确选项原文（前端/服务器按原文比对判分）。
