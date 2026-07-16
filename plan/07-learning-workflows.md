# 07 - 学习工作流细化（study-planner + self-study-guide）

> 依据两个既有技能细化 Deep-tutor 的学习工作流：**学习规划**采用 [study-planner](file:///C:/Users/Z/.claude/skills/study-planner/SKILL.md) 的 7 步工作流；**自学材料生成**采用 [self-study-guide](file:///C:/Users/Z/.claude/skills/self-study-guide/SKILL.md)。前者产出"学什么、怎么走"的方案，后者产出每个知识单元"具体怎么学"的材料。

## 一、两个工作流的分工

| 维度 | study-planner（学习规划） | self-study-guide（自学材料） |
|---|---|---|
| 输入 | 需求描述 + 资料 | 学习方案/大纲/知识点列表（来自 study-planner 产出） |
| 产出 | 学习路径 + 阶段 + 资源 + 时间 + 校验 | 每单元自学材料（概述/概念/练习/检查/扩展）+ 索引 |
| 粒度 | 阶段/知识点级 | 单元级（30-60min/单元） |
| Deep-tutor 阶段 | P0（主线） | P2（增强） |
| 关系 | 上游：定路径 | 下游：填内容 |

## 二、学习规划：采用 study-planner 7 步工作流

Deep-tutor 的学习规划主线严格对齐 study-planner 的 Step 1-7，由 skill-for-skills 生成的 `deep-tutor-*` Skill 承载方法论，Python 工具执行：

| study-planner Step | 内容 | Deep-tutor 工具 | Skill | 适配说明 |
|---|---|---|---|---|
| Step 1 解析需求 | 主题/基础/目标/时长 | understand_intent | deep-tutor-intent | 需澄清时走前端 ChatClarify 而非终端问答；输出 JSON |
| Step 2 知识图谱 | 前置/核心/进阶/项目 + 依赖 | build_knowledge_map | deep-tutor-knowledge-map | 输出 nodes+edges 供前端渲染 |
| Step 3 阶段设计 | 3-5 渐进阶段 + 目标/里程碑 | design_stages | deep-tutor-stages | 每阶段 3-6 知识点 |
| Step 4 资源检索+验证 | WebSearch + 链接验证 ✅⚠️❌ | search_resources + verify_url | deep-tutor-resources | 非交互；绝不编造 URL |
| Step 5 生成方案文件 | plans/<topic>.md | （plan schema + export_plan） | - | Deep-tutor 输出结构化 plan JSON + 可导出 md，而非固定 md 文件 |
| Step 6 方案校验 | 5 项检查 + 修复 | validate_plan | deep-tutor-validate | 严重项触发上游重生成（反馈循环） |
| Step 7 呈现用户 | 总览 + 阶段 + 资源 + 调整 | （前端方案详情页） | - | Web 渲染 + 可重生成 |

**适配原则**
- study-planner 面向 Claude Code 终端、输出 md 文件；Deep-tutor 面向 Web、输出 JSON 供前端渲染与存库。方法论（怎么拆、怎么验）一致，产出形态不同。
- study-planner 的"逐轮提问澄清"对应 Deep-tutor 前端的 ChatClarify；"可调整"对应重生成。
- 5 项校验（依赖闭包 / 难度阶梯 / 认知负荷 / 里程碑可行性 / 新手盲点）原样移植到 deep-tutor-validate。
- study-planner 的资源链接验证规则（✅ 已验证 / ⚠️ 存疑 / ❌ 无效丢弃）原样移植到 deep-tutor-resources + verify_url。

## 三、自学材料生成：采用 self-study-guide

方案生成后，可基于方案的阶段/知识点生成**逐单元自学材料**，方法论来自 self-study-guide。

### 新工具 `generate_study_materials`
- **input**: `{ plan_id, scope?: { stage_indexes?: [int], node_ids?: [str] }, depth?: brief|full }`（scope 省略 = 全方案）
- **output**: `{ units: [{ id, title, prerequisites:[str], objectives:[str], estimated_time, material_url }], index_url }`
- **LLM**: 是（每单元一次，按 self-study-guide 模板生成）
- **Skill**: 复用既有 `self-study-guide`（非 skill-for-skills 生成）
- **errors**: 单元过多(>10) -> 分模块(module/unit)；WebSearch 无结果 -> 标注"无外部资料参考"；单文件>500 行 -> 拆子文件；LLM 失败 -> 该单元降级为简要大纲。
- **实现**:
  1. 从 plan 的 stages/nodes 拆分学习单元（每单元 30-60min，标前置依赖、2-4 目标）；>10 主题分模块。
  2. 每单元 WebSearch 补充资料（教程/文档/示例）。
  3. 加载 self-study-guide SKILL.md 为指令，LLM 按模板生成：YAML frontmatter(unit/prerequisites/objectives/estimated_time) + 概述/核心概念/实践练习(≥2)/检查理解/总结/扩展阅读。
  4. 生成 index（学习路线图 + 计划表 + 复习节点，每 3-4 单元一次复习）。
  5. 渲染 md/html 供前端阅读；存库。
- **MVP 范围**: P2。

### 与 study-planner 的衔接
- `validate_plan.output`(plan) -> `generate_study_materials.input`
- 单元来源于 plan 的 knowledge_nodes / stages.topics；前置依赖复用 knowledge_map.edges。
- self-study-guide 的"检查理解"题可复用为 `generate_quiz` 的题源（P2 联动）。
- self-study-guide 的"扩展阅读"与 deep-tutor-resources 的资源可互为补充（前者偏单元内教学，后者偏阶段级参考）。

## 四、数据模型补充（见 [01](./01-architecture.md)）
- `study_units`：id, plan_id, node_id?, stage_index?, title, prerequisites(json), objectives(json), estimated_time, order
- `study_materials`：id, unit_id(FK), content_md, content_html, resources(json), created_at

## 五、API 补充（见 [03](./03-api-frontend.md)）
- `POST /plans/{id}/materials` 生成自学材料（SSE/异步）
- `GET /plans/{id}/materials` 单元列表
- `GET /materials/{unit_id}` 单元内容

## 六、前端补充（见 [06](./06-frontend.md)）
- 方案详情新增 Tab"自学材料"或独立页 `/plans/:id/materials`
- 组件 `StudyMaterialReader`：单元导航 + 内容渲染 + 上一/下一单元 + 标记已学；"检查理解"可跳测验。

## 七、任务与风险（见 [04](./04-mvp-risk.md)）
- P2 任务：generate_study_materials 工具 + self-study-guide 复用 + study_units/materials 表 + API + StudyMaterialReader。
- 风险：材料生成成本（多单元多 LLM 调用，R16）、单文件过长/质量不均（R17）。
