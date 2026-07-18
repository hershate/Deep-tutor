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
- **加载**：`app/skills/loader.py` 启动扫描 `skills/deep-tutor-resources/SKILL.md`，按 frontmatter `name` 注册。
- **取用**：`app/tools/search_resources.py` `_screen_resources` 从 `ctx.skill_registry` 取本 Skill 正文作筛选 LLM 调用的 system prompt（缺失 -> inline `SCREEN_PROMPT` 回退，R13；两者输出 schema 必须一致）。
- **解析**：筛选输出由 `_parse_screen_verdicts` 解析（JSON 数组，容忍代码块包裹，单项非法跳过）。
- **降级**：无 provider / LLM 失败 / 输出不可解析 -> 跳过筛选（保持 verify 可达性判定，行为同 iter 62 前）。

## 方法论来源
移植自 study-planner Step 4（链接验证 ✅⚠️❌ + 内容难度适配判定，plan/02 §5）；本 Skill 负责「内容适配」一半，可达性判定由 verify_url（纯 Python）完成。

## 注意事项
- 本 Skill 不是 Claude Code 用户态技能，而是 Deep-tutor 运行时确定性加载的方法论指令。
- 修改 `SKILL.md` 后重启后端即生效。
- 筛选输出 schema 变更须同步 `_parse_screen_verdicts` 与 inline `SCREEN_PROMPT`。
- `suitable=false` 仅限内容不相关（将被丢弃）；相关但过深/存疑用 `label="uncertain"` 保留。
