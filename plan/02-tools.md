# 02 - 自研工具契约

每个工具遵循单一职责，定义 **输入 / 输出 / 错误 / 是否调用 LLM / 幂等性**。Agent 循环按 pipeline 调度，`validate_plan` 可回溯触发上游工具重生成（反馈循环）。

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
- **errors**: 节点为空 -> 回退按主题通用拆解。
- **prompt 要点**: 移植 study-planner Step 2（核心前置/核心知识/进阶扩展/实战项目 + 依赖关系）。

## 4. design_stages
- **input**: `{ map, intent }`
- **output**: `{ stages: [{ index, name, goal, difficulty(1-5), estimated_weeks, topics:[{name,why,must_learn}], milestone }] }`
- **errors**: 阶段数 < 3 -> 重生成。
- **prompt 要点**: 移植 study-planner Step 3（3-5 渐进阶段，每阶段目标/3-6 知识点/里程碑）。

## 5. search_resources
- **input**: `{ stage_topics: [[str]] }`
- **output**: `{ resources: [{ stage_index, title, url, type, verified, note }] }`
- **errors**: WebSearch 失败 -> 返回搜索关键词建议，不编造 URL。
- **实现**: WebSearch（后端调用搜索 API）-> 对每个候选调用 `verify_url`。
- **规则**: 严格移植 study-planner Step 4 链接验证（✅ 已验证 / ⚠️ 存疑 / ❌ 无效丢弃，绝不编造 URL）。

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
```

格式约定：工具间以 JSON 对象传递，字段名遵循上表；上游 `output` 必须覆盖下游 `input` 的必填字段（参考 skill-chain-planner 的 L4 字段覆盖检查）。
