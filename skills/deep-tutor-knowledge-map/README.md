# deep-tutor-knowledge-map

## 简介
Deep-tutor「知识图谱构建」工具的方法论指令源。运行时由 Python Skill 加载器读取 `SKILL.md` 正文，作为 `build_knowledge_map` 工具调用 LLM 时的 system prompt。依据学习意图与资料拆解知识点依赖图谱（nodes/edges）。

## 目录结构
```
deep-tutor-knowledge-map/
├── SKILL.md     # 方法论指令（运行时作 system prompt）
└── README.md    # 本文件
```

## 运行时集成
- **加载**：`app/skills/loader.py` 启动扫描 `skills/deep-tutor-knowledge-map/SKILL.md`，按 frontmatter `name` 注册。
- **取用**：`app/agent/loop.py` `_get_system_prompt` 取本 Skill 正文作 `build_knowledge_map` 的 system prompt（三级回退：skill -> inline_prompt -> 默认）。
- **校验**：输出由 `app/tools/build_knowledge_map.py` 的 `KnowledgeMapData` pydantic 模型校验。
- **后处理**：`clean_map` 兜底丢弃悬空边、断环成 DAG、节点 >100 截断并加 `_note`。

## 方法论来源
移植自 study-planner Step 2（核心前置 / 核心知识 / 进阶扩展 / 实战项目）。

## 注意事项
- 本 Skill 不是 Claude Code 用户态技能，而是 Deep-tutor 运行时确定性加载的方法论指令。
- 修改 `SKILL.md` 后重启后端即生效。
- 输出 schema 变更须同步 `KnowledgeMapData` pydantic 模型。
