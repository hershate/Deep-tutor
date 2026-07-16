# 05 - Skill 层设计（基于 skill-for-skills）

> 决策：Deep-tutor 的方法论密集型 AI 工作流，用 `skill-for-skills` 编写为标准 Claude Code Skill，作为该工作流的**权威方法论与 LLM 指令源**；Python 运行时加载 Skill 内容驱动对应工具。基础设施类工具保持纯 Python。Skill 也可在 Claude Code 中独立复用。

## 一、双轨架构

| 轨 | 承载内容 | 实现 |
|---|---|---|
| **Skill 层** | 方法论密集的 AI 工作流（需求理解/知识图谱/阶段设计/资源检索与验证/方案校验/出题） | `skill-for-skills` 生成的 SKILL.md + README.md |
| **Python 运行时** | Agent 循环（缓存优先/工具调用修复/成本计量）+ 基础设施工具（解析/排期/导出/进度/持久化）+ Web API | FastAPI + anthropic SDK |

Skill 是"做什么、怎么做"的权威定义（含 Workflow/Constraints/Do-Not 示例/审查校验）；Python 是"跑起来"的运行时（调度、缓存、修复、计费、存库、渲染）。

## 二、Skill 与工具映射

| Skill（skill-for-skills 生成） | 对应 Python 工具 | 方法论来源 | 阶段 |
|---|---|---|---|
| `deep-tutor-intent` | understand_intent | study-planner Step 1 | P0 |
| `deep-tutor-knowledge-map` | build_knowledge_map | study-planner Step 2 | P0 |
| `deep-tutor-stages` | design_stages | study-planner Step 3 | P0 |
| `deep-tutor-resources` | search_resources + verify_url | study-planner Step 4（含链接验证 ✅⚠️❌） | P0 |
| `deep-tutor-validate` | validate_plan | study-planner Step 6（5 项检查） | P0 |
| `deep-tutor-quiz` | generate_quiz | 按知识点多题型 | P2 |

**纯 Python 工具（不建 Skill，确定性/方法论轻）**
- `parse_material`：文档解析，按后缀分发，无 LLM 方法论。
- `schedule_plan`：LLM 草案 + 确定性总量校验；方法论轻，暂不建 Skill（后续可视情况补）。
- `record_progress`：掌握度计算 + 间隔重复，确定性。
- `export_plan`：md/pdf 渲染，确定性。

**复用既有 Skill（非 skill-for-skills 生成）**
- `build_plan_slides`：复用 `ppt-slide-builder`（HTML 幻灯片），见第六节。
- `generate_study_materials`：复用 `self-study-guide`（逐单元自学材料），见第六节与 [07](./07-learning-workflows.md)。

## 三、运行时加载机制

1. Skill 文件存放：项目根 `skills/<skill-name>/SKILL.md`（开发期随仓库版本管理；部署时分发）。
2. Python 启动时扫描 `skills/`，建立注册表 `skill_name -> { frontmatter, body }`。
3. Agent 调用某方法论工具时：
   - 取该工具对应 Skill 的 `Purpose / When to Use / Workflow / Constraints / Examples` 作为本次 LLM 调用的 **system prompt 指令**（稳定前缀，缓存友好，符合 Pillar 1）。
   - 上游数据/用户输入作为 `user message`。
   - Skill 中要求的输出 schema 在 Python 端用 pydantic 校验；不符则走**工具调用修复**（Pillar 2）。
4. Skill 升级（skill-for-skills UPGRADE）后，Python 无需改代码，重新加载即生效。
5. Skill 的 `allowed-tools` 映射到 Python 端该工具可调用的子能力（如 `deep-tutor-resources` 的 `WebSearch`/`verify_url`）。

```
Agent 循环 ──调用工具──> Python 工具包装器
                          │ 注入 system = Skill SKILL.md 指令
                          ▼
                       Anthropic Claude ──返回结构化结果──> pydantic 校验
                          │                                    │ 不符 -> 工具调用修复
                          ▼                                    ▼
                       api_usage 记账                       工具结果回填
```

## 四、skill-for-skills 的角色（工厂）

- **CREATE**：按需求生成 Deep-tutor 各 Skill，严格遵循 `skill-for-skills/sum.md`（frontmatter、目录结构、体量 ≤500 行/2000 词、Do/Not 示例、Step 8-9 审查校验）。
- **UPGRADE**：方法论迭代时升级 Skill（版本号递增，`Edit` 精确修改，回归审查）。
- **UPDATE_SUM**：官方 Skill 规范变更时更新 `sum.md`。
- 生成流程内置：需求推断补全 + 联网核实（API/工具版本）+ 生成 SKILL.md/README.md + 逻辑/清晰度/安全/可行性/一致性审查。

## 五、skill-chain-planner 的角色（链路规划）

- 用 `skill-chain-planner` 对 Deep-tutor 主链路做链式规划：5W1H+C 分析 -> 复杂度判定 -> 任务分解 -> **接口契约（输入/输出/错误）** -> 架构模式 -> 风险登记 -> 各 Skill 创建规格（P0/P1/P2）。
- 产出规划文档固化 Skill 间的数据流转契约（如 `understand_intent.output` -> `build_knowledge_map.input`），与 [02-tools.md](./02-tools.md) 的契约一致性互校；统一数据交换格式（类型系统/信封/契约模板）见 [08-data-exchange-format.md](./08-data-exchange-format.md)。
- Python Agent 按该链路编排：parse -> understand -> map -> stage -> resource -> schedule -> validate -> export（validate 可回溯触发上游重生成）。
- 可选参考 `skill-chain-executor` 的执行思路，但运行时编排由 Python Agent 承担（不依赖 Claude Code 运行时）。

## 六、复用既有技能生态

- **study-planner** 是 Deep-tutor Skill 的方法论蓝本（Step 1-6 直接映射），但不直接调用它：study-planner 面向 Claude Code 终端用户、输出到 `plans/*.md`；Deep-tutor Skill 面向 Web 应用、输出结构化 JSON 供前端渲染与存库。完整 7 步工作流映射见 [07-learning-workflows.md](./07-learning-workflows.md)。
- 可参考 `self-study-guide`（已有大纲场景）、`course-material-organizer`、`outline-extractor` 的思路融入资料解析与大纲提取。
- **ppt-slide-builder**（既有，直接复用）：作为 `build_plan_slides` 工具的方法论与模板源（`templates/base.html` + 主题 CSS 变量 + 动画/过渡），把学习方案导出为自包含 HTML 幻灯片（非 .pptx）。Deep-tutor 以非交互方式调用（plan 已有内容，跳过其逐轮提问，直接生成幻灯片规约并渲染）。
- **self-study-guide**（既有，直接复用）：作为 `generate_study_materials` 工具的方法论与单元模板源，把学习方案转化为逐单元自学材料（概述/核心概念/实践练习/检查理解/总结/扩展阅读 + 索引与复习节点）。上游为 study-planner 工作流产出的 plan。详见 [07-learning-workflows.md](./07-learning-workflows.md)。
- Deep-tutor 生成的 Skill 命名统一前缀 `deep-tutor-*`，与既有通用 Skill 区分。

## 七、Skill 目录与规范

```
Deep-tutor/
└── skills/
    ├── deep-tutor-intent/
    │   ├── SKILL.md
    │   └── README.md
    ├── deep-tutor-knowledge-map/
    ├── deep-tutor-stages/
    ├── deep-tutor-resources/
    │   └── references/   # 链接验证规则等补充
    ├── deep-tutor-validate/
    └── deep-tutor-quiz/   (P2)
```
- 规范：遵循 `skill-for-skills/sum.md`（frontmatter 必需字段、目录结构、体量控制、Do/Not 示例、审查校验）。
- 每个 Skill 必含 `SKILL.md` + `README.md`，可选 `references/`、`scripts/`、`templates/`。
- 输出 schema 在 SKILL.md 的 Constraints 中固化（JSON 字段列表），Python 端 pydantic 对齐。

## 八、MVP Skill 范围（P0）

用 skill-for-skills 生成以下 Skill（每个独立版本管理）：
- [ ] `deep-tutor-intent`
- [ ] `deep-tutor-knowledge-map`
- [ ] `deep-tutor-stages`
- [ ] `deep-tutor-resources`（含 verify 方法论 + ✅⚠️❌ 规则）
- [ ] `deep-tutor-validate`（5 项检查 + 修复建议）

加 Python 侧：
- [ ] Skill 加载器（扫描 `skills/` -> 注册表 -> 工具调用时注入 system prompt + pydantic 校验）

`deep-tutor-quiz` 在 P2。`build_plan_slides`（P1）复用既有 `ppt-slide-builder`，无需 skill-for-skills 生成。

## 九、风险（Skill 相关）

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| Skill 输出 schema 与 Python 期望漂移 | 中 | 高 | Skill Constraints 固化输出 schema；Python pydantic 校验；工具调用修复兜底 |
| Skill 体量超限（>500 行） | 中 | 低 | 遵循 sum.md，超出部分移入 `references/` |
| Skill 升级引入回归 | 中 | 中 | skill-for-skills UPGRADE 回归审查 + 版本号 + Python 端 schema 校验 + 冒烟测试 |
| Skill 方法论与 study-planner 蓝本偏差 | 低 | 中 | 生成时显式引用 study-planner 对应 Step 作为依据 |
| Skill 加载失败/缺失 | 低 | 高 | 启动时检测必需 Skill 缺失则报错并提示；降级为内置默认 prompt |
