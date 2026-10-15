# Skills 目录

> **改造期去向（note/12 资产处置表）**：本目录 SKILL.md 为**纯文本资产，随改造复用**——迁移时与 R-Tutor `internal/runtime/tutor/skills/` 移植版语义对照、缺漏补齐，进入 Reasonix 内核技能体系（管理面为全局 admin，决策 #55）。

vendor 复用的既有 Skill（自包含）与本项目的 `deep-tutor-*` Skill 放此目录。

- 由 skill-for-skills 生成的 Skill（step 5）：`deep-tutor-intent` / `-knowledge-map` / `-stages` / `-resources` / `-validate` / `-quiz`(P2)。
- 复用的既有技能（vendor，复制进此目录）：`ppt-slide-builder`（P1 build_plan_slides）、`self-study-guide`（P2 generate_study_materials）。
- Python Skill 加载器（`backend/app/skills/loader.py`）启动时扫描此目录，建立 `skill_name -> SKILL.md` 注册表（v1；改造后由内核技能加载器接管）。

详见 [docs/archive/plan-v1/05-skills.md](../docs/archive/plan-v1/05-skills.md)（v1 设计归档）。
