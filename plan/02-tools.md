# 02 - 自研工具契约

每个工具遵循单一职责，定义 **输入 / 输出 / 错误 / 是否调用 LLM / 幂等性**。Agent 循环按 pipeline 调度，`validate_plan` 可回溯触发上游工具重生成（反馈循环）。所有工具的输入/输出遵循 [08-data-exchange-format.md](./08-data-exchange-format.md) 的统一信封（ToolInput/ToolOutput）与类型系统；下表为概览，完整 typed 契约按 08 第四节模板逐步补全。

## 工具总览

| 工具 | 职责 | LLM | 幂等 |
|---|---|---|---|
| parse_material | 多模态资料 -> 结构化文本+元数据 | 否 | 是（相同输入相同输出） |
| understand_intent | 抽取主题/基础/目标/时长 | 是 | 否（LLM 产出） |
| build_knowledge_map | 拆解知识点与依赖 | 是 | 否 |
| design_stages | 3-5 阶段、目标、里程碑 | 是 | 否 |
| search_resources | WebSearch + 链接验证 | 检索否+筛选是 | 否（网络） |
| verify_url | 校验链接可达性与适配性 | 否 | 条件（URL 不变则稳定） |
| schedule_plan | 按时长/截止日排期 | 是 | 否 |
| generate_quiz | 按知识点多题型出题（P2） | 是 | 否 |
| record_progress | 记录进度、掌握度（P2） | 否 | 是（覆盖写） |
| validate_plan | 5 项校验 + 修复建议 | 是 | 否 |
| export_plan | 导出 md/pdf | 否 | 是 |
| build_plan_slides | 学习方案 -> PPT 风格 HTML 幻灯片（复用 ppt-slide-builder） | 是 | 否 |
| generate_study_materials | 方案 -> 逐单元自学材料（复用 self-study-guide） | 是 | 否 |

> **Skill 承载**：方法论密集工具（understand_intent / build_knowledge_map / design_stages / search_resources / validate_plan / generate_quiz）由 skill-for-skills 生成的 Skill 承载其方法论与 LLM 指令（详见 [05-skills.md](./05-skills.md)）；parse_material / schedule_plan / record_progress / export_plan 为纯 Python。`build_plan_slides` 复用既有 `ppt-slide-builder`（非 skill-for-skills 生成，产出 HTML 幻灯片，非 .pptx）。`generate_study_materials` 复用既有 `self-study-guide`（非 skill-for-skills 生成，产出逐单元自学材料）。

---

## 1. parse_material
- **input**: `{ kind: file|text|url|image|audio|video, source: str }`
- **output**: `{ text: str, meta: { pages?, chars, source_type, title? }, chunks: [str] }`
- **errors**: 格式不支持 -> 返回 `unsupported_format` + 支持列表；解析失败 -> 降级为纯文本提取并标记 `degraded`；空文本 -> 明确 `empty_content` 错误（防静默错误）。
- **实现**: PyMuPDF/python-docx/python-pptx/markdown/trafilatura 按后缀分发。
- **MVP 范围**: file(PDF/DOCX/PPTX/TXT/MD) + text + url。

## 2. understand_intent
- **input**: `{ raw_text: str?, parsed: [material] }`
- **output**: `{ topic: str, level: str?, goal: str?, daily_minutes: int?, deadline: date?, needs_clarify: bool, clarify_questions: [str] }`
- **errors**: 信息不足 -> `needs_clarify=true` + 追问问题（基础/目标/时长），交前端对话式澄清。
- **prompt 要点**: 移植 study-planner 的 Step 1（解析目标、当前基础、学习目标、时间投入）。

## 3. build_knowledge_map
- **input**: `{ intent, parsed: [material] }`
- **output**: `{ nodes: [{ id, name, difficulty, must_learn, parent_id? }], edges: [{ from, to, relation }] }`
- **errors**: 节点为空 -> 回退按主题通用拆解；环检测（DFS）发现环 -> 断开成 DAG；`edge.from/to` 不在 nodes -> 丢弃该边；节点数超 `MAX_NODES` -> 截断+标注。
- **prompt 要点**: 移植 study-planner Step 2（核心前置/核心知识/进阶扩展/实战项目 + 依赖关系）。

## 4. design_stages
- **input**: `{ map, intent }`
- **output**: `{ stages: [{ index, name, goal, difficulty(1-5), estimated_weeks, topics:[{name,why,must_learn}], milestone }] }`
- **errors**: 阶段数 < 3 -> 重生成。
- **prompt 要点**: 移植 study-planner Step 3（3-5 渐进阶段，每阶段目标/3-6 知识点/里程碑）。

## 5. search_resources
- **input**: `{ stage_topics: [[str]] }`（可选；缺省时从 upstream.design_stages 派生，err.md B21）
- **output**: `{ resources: [{ stage_index, title, url, type, verified, note }], engine: "bing_api"|"bing_web" }`（engine 键为 iter 222 新增，只增不改）
- **errors**: WebSearch 失败 -> 返回搜索关键词建议，不编造 URL；免 Key 网页引擎全面失败 -> `search_unavailable` + 关键词建议；`SEARCH_ENGINE=bing` 未配 key -> `no_search_key`。
- **实现**: 双引擎（iter 222，`BING_API_KEY` 可选化）：`bing_api` = Bing Search API（付费，有 key 质量更高）；`bing_web` = Bing 网页版抓取（cn.bing.com，免 Key，国内可达）。`SEARCH_ENGINE=auto|bing|bing_web`（默认 auto：有 key 用 API，无 key 用网页版），决策见 `app.core.settings.effective_search_engine`；引擎注册表 dispatch（新增引擎 = 实现函数 + 注册一行）。候选均过 `verify_url`。
- **规则**: 严格移植 study-planner Step 4 链接验证，判定如下：

| 标记 | 判定 | 处理 |
|---|---|---|
| ✅ 已验证 | HTTP 200 + 内容难度适配该阶段 | 嵌入方案 |
| ⚠️ 存疑 | 可达但内容级别/质量不确定 | 带说明嵌入，提示人工确认 |
| ❌ 无效 | 404/403/500/超时/被反爬拦截 | 丢弃并换源 |

补充：重定向记最终 URL；绝不编造或外推 URL；WebSearch 无结果给搜索关键词建议而非空占位。

## 6. verify_url
- **input**: `{ url: str, stage_level: int }`
- **output**: `{ ok: bool, status: int?, suitable: bool, final_url: str, label: ✅|⚠️|❌ }`
- **errors**: 抓取失败 -> `label=⚠️`，提示人工确认；超时重试 1 次再标记。
- **实现**: httpx GET，检查 HTTP 200 + 扫描标题/首段判断难度适配。

## 7. schedule_plan
- **input**: `{ stages, daily_minutes, deadline? }`
- **output**: `{ schedule: [{ date, stage_index, tasks:[str], est_minutes }] }`
- **errors**: 时长不足覆盖所有阶段 -> 返回 `insufficient_time` + 建议（压缩/延期/降级）。
- **实现**: LLM 生成草案 + 确定性算法校验总量 <= 可用时长。

## 8. generate_quiz（P2）
- **input**: `{ nodes: [{ id, name }] }`
- **output**: `{ quizzes: [{ node_id, type, question, options?, answer, explanation }] }`
- **errors**: 题型不支持 -> 降级为选择题。

## 9. record_progress（P2）
- **input**: `{ plan_id, node_id, status, quiz_result? }`
- **output**: `{ mastery: float, next_review: date }`
- **errors**: 节点不存在 -> 忽略并记录 warning。
- **实现**: 掌握度 = 答题正确率加权；next_review 按间隔重复算法。

## 10. validate_plan
- **input**: `{ plan }`
- **output**: `{ report: { dependency_closure, difficulty_step, cognitive_load, milestone_feasibility, gap_analysis }, passed: bool, fixes: [str] }`
- **errors**: 检查失败 -> 返回具体 fix 列表；严重(critical) -> 触发上游工具重生成。
- **prompt 要点**: 严格移植 study-planner Step 6 五项检查：
  1. 依赖闭包（每阶段所需前置须在更早阶段出现）
  2. 难度阶梯（相邻阶段难度差 ≤ 2）
  3. 认知负荷（每阶段 3-6 知识点）
  4. 里程碑可行性（仅用当前+已学知识可达成）
  5. 新手盲点（无未解释术语/跳步）

## 11. export_plan
- **input**: `{ plan_id, format: md|pdf|ics }`
- **output**: `{ file_url: str }`
- **errors**: 格式不支持 -> 仅返回 md。
- **MVP 范围**: md（pdf 同期，ics 在 P5）。

## 12. build_plan_slides
- **input**: `{ plan_id, theme?: { bg?, accent?, font? }, style?: str }`
- **output**: `{ slides_url: str, slide_count: int, format: "html" }`
- **errors**: 模板缺失 -> 降级为简单 HTML 列表；内容过多 -> 按阶段分多个 deck 或精简要点；LLM 组织失败 -> 回退为"每阶段一页"默认结构。
- **实现**: 加载 `ppt-slide-builder` 的 SKILL.md（方法论）+ `templates/base.html`；调用 LLM（plan + ppt-slide-builder 指令）生成幻灯片规约（每页内容 + 主题 CSS 变量 + 动画/过渡类）；填充 base.html 占位符 -> 自包含 HTML 文件。
- **Skill**: 复用既有 `ppt-slide-builder`（非 skill-for-skills 生成；产出 HTML 幻灯片，非 .pptx；Deep-tutor 以非交互方式调用，跳过其逐轮提问）。
- **MVP 范围**: P1（输出增强）。

## 13. generate_study_materials
- **input**: `{ plan_id, scope?: { stage_indexes?: [int], node_ids?: [str] }, depth?: brief|full }`（scope 省略 = 全方案）
- **output**: `{ units: [{ id, title, prerequisites:[str], objectives:[str], estimated_time, material_url }], index_url }`
- **errors**: 单元过多(>10) -> 分模块；WebSearch 无结果 -> 标注"无外部资料参考"；单文件>500 行 -> 拆子文件；LLM 失败 -> 该单元降级为简要大纲。
- **实现**: 从 plan 的 stages/nodes 拆单元（30-60min/单元，标前置依赖、2-4 目标）；每单元 WebSearch 补充资料；加载 self-study-guide SKILL.md 为指令，LLM 按模板生成（YAML frontmatter + 概述/核心概念/实践练习≥2/检查理解/总结/扩展阅读）；生成 index（路线图+计划表+复习节点）；渲染 md/html 存库。
- **Skill**: 复用既有 `self-study-guide`（非 skill-for-skills 生成；Deep-tutor 非交互调用，单元源自 plan）。
- **MVP 范围**: P2。

---

## 契约一致性（数据流转）

```
parse_material.output ─┐
                       ├─> understand_intent.input
raw_text ──────────────┘
understand_intent.output + parse_material.output ─> build_knowledge_map.input
build_knowledge_map.output + understand_intent.output ─> design_stages.input
design_stages.output ─> search_resources.input
search_resources.output + design_stages.output + understand_intent.output ─> schedule_plan.input
(以上聚合) ─> validate_plan.input
validate_plan.fixes ─(反馈循环)─> 触发 design_stages / build_knowledge_map 重生成
最终 plan ─> export_plan.input
最终 plan ─> build_plan_slides.input
plan(validate 后) ─> generate_study_materials.input
```

格式约定：工具间以 JSON 对象传递，字段名遵循上表；上游 `output` 必须覆盖下游 `input` 的必填字段（参考 skill-chain-planner 的 L4 字段覆盖检查）。
