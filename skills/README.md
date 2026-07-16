# Skills 目录

vendor 复用的既有 Skill（自包含）与本项目的 `deep-tutor-*` Skill 放此目录。

- 由 skill-for-skills 生成的 Skill（step 5）：`deep-tutor-intent` / `-knowledge-map` / `-stages` / `-resources` / `-validate` / `-quiz`(P2)。
- 复用的既有技能（vendor，复制进此目录）：`ppt-slide-builder`（P1 build_plan_slides）、`self-study-guide`（P2 generate_study_materials）。
- Python Skill 加载器（`backend/app/skills/loader.py`）启动时扫描此目录，建立 `skill_name -> SKILL.md` 注册表。

详见 [plan/05-skills.md](../plan/05-skills.md)。
