# 10 - 端到端协作与交互细节

> 目标：明确各计划部分（01 架构/Agent、02 工具、03 API、05 Skill、06 前端、07 工作流、08 数据交换、09 审计）之间的交互，确保整体可行，无不确定/风险。本轮解决核心协作缺口：**执行模型**、端到端序列、SSE 一致、澄清续接、持久化选择性、反馈循环机制、Skill↔工具映射。

## 一、执行模型（关键澄清，修订 01）

**决策：确定性流水线编排器（Orchestrator）+ 每工具一次 LLM 调用（Skill 作 system prompt）+ 工具调用修复。**

- **不采用**"自由 Agent 让 Claude 决定调哪个工具"（易跑偏、难复现、成本高）。
- **外层 编排器（Orchestrator，Python）**：按固定顺序调度工具 pipeline：`parse -> understand -> map -> stage -> resource -> schedule -> validate（-> 反馈循环）-> export`（可选 `build_plan_slides` / `generate_study_materials`）。
- **内层 工具调用循环（每 LLM 工具）**：一次 Claude 调用，`system` = 对应 Skill 的 SKILL.md 指令（稳定前缀，`cache_control`），`user` = `ToolInput.args` + `upstream.data`；输出 pydantic 校验，不符走工具调用修复（重试≤3 -> 降级）。
- **纯 Python 工具**（parse / schedule 的确定性部分 / record / export / slides 渲染）无 LLM 调用。
- 01 的"Agent 循环"语义收窄为**工具内 LLM 调用循环**（call -> validate -> repair -> return）；编排器是外层确定性调度。原"收到 tool_use / 重复到 end_turn"的自由 Agent 语义废弃。

> 修订 01 第三节为两层模型；完整序列见下。

## 二、端到端交互序列（生成方案）

1. 前端 `POST /plans/generate {session_id, raw_text?}` -> 后端创建生成任务，返回 SSE 流。
2. 编排器启动，按序执行工具；每工具：
   1. SSE `tool_start {tool}`。
   2. 编排器组装 `ToolInput`（`args` + `upstream.data`），调工具包装器。
   3. 工具包装器：LLM 工具 -> 加载 Skill 为 system + Claude 调用 + pydantic 校验（不符 -> 修复重试≤3）；Python 工具 -> 确定性逻辑。
   4. 产出 `ToolOutput {status, data, errors, metadata}`。
   5. SSE `tool_end {tool, status, metadata}`。
   6. 字段覆盖检查（`upstream.data` 覆盖下游 `args` 必填）-> 不符 -> 触发上游重生成或 `error`。
   7. 写 `tool_calls` 审计 + `api_usage`；预算守卫检查。
3. `understand_intent` 若 `needs_clarify=true`：
   1. SSE `clarify_needed {questions}`；编排器状态 -> `paused_clarify`，持久化已产出。
   2. 前端 `ChatClarify` 收集答案 -> `POST /plans/{id}/clarify {answers}`。
   3. 编排器以 `answers` 重跑 `understand_intent`（`upstream` 带答案）-> 继续 pipeline。
4. `validate_plan` 若 `passed=false`：编排器据 `fixes` 重跑上游工具 -> 再 validate，≤2 轮（见六）。
5. 预算超限：SSE `budget_exceeded` + 中断保留部分。
6. 完成：持久化 plan（`content` 大则溢出文件，DB 存路径/小 JSON）-> SSE `done {plan_id, metadata}`。
7. 前端跳 `GET /plans/{id}` 渲染详情。

## 三、SSE 事件一致性（修订 03，对齐 08 信封）

| event | data |
|---|---|
| `tool_start` | `{tool}` |
| `tool_progress` | `{tool, message}` |
| `tool_end` | `{tool, status, metadata:{tokens_in,tokens_out,cache_read,cost,duration_ms,model}}` |
| `clarify_needed` | `{questions:[...]}` |
| `plan_chunk` | `{partial_plan}` |
| `warn` | `{code, message}`（校验未过/降级） |
| `budget_exceeded` | `{spent, limit}` |
| `done` | `{plan_id, metadata}` |
| `error` | `{code, message, stage}` |

## 四、澄清续接（新增 API 与状态机）

- `POST /plans/{id}/clarify {answers:{level,goal,time,...}}` -> SSE 流（续接生成）。
- 编排器状态机：`idle -> running -> paused_clarify -> running -> done|error`。
- `paused_clarify` 持久化（session 级），页面刷新后可继续；超时（默认 30min）自动取消并保留部分。

## 五、持久化选择性（明确，呼应 09）

- **持久化**：最终 plan（`plans` 表，`content` 大则溢出文件）、study_materials（P2，content 文件）、materials 解析文本（文件+路径）、`tool_calls`、`api_usage`、progress（P2）。
- **不持久化（流水线中间态，内存/信封传递）**：understand_intent/map/stages/resource/schedule 的中间输出，仅作下游输入；可记入 `tool_calls.output` 供审计，不单独建表。
- `validate_plan.report` 记入 `tool_calls`；`fixes` 用于反馈循环。
- 导出/幻灯片/材料文件存 `data/sessions/<sid>/{exports,materials}/`，DB 记 url。

## 六、反馈循环机制（明确）

`validate_plan.passed=false` -> 编排器读 `fixes` 决定重跑哪个上游：
- `difficulty_step` 失败 -> 重跑 `design_stages`（`upstream` 带 `fixes` + 原 map/intent）
- `dependency_closure` 失败 -> 重跑 `build_knowledge_map`
- `cognitive_load` 失败 -> 重跑 `design_stages`
- `milestone_feasibility` / `gap_analysis` -> 重跑 `design_stages` 或 `build_knowledge_map`
- 重跑后再 validate，≤2 轮；仍失败 -> SSE `warn` + 交付当前 plan + 校验报告标未过（R7）。

## 七、Skill 加载 ↔ 工具执行（明确，呼应 05）

- 启动：`SkillLoader` 扫描 `SKILLS_DIR` -> registry `{skill_name:{frontmatter,body}}`。
- 工具包装器按 tool->skill 映射取 `body` 作 system prompt（Purpose/Workflow/Constraints/Examples）。
- system 固定前缀 + `cache_control`；user = `ToolInput.args` + `upstream.data` 序列化。
- Skill 缺失 -> 降级内置默认 prompt + 告警（R13）。
- **映射表**：

| 工具 | Skill | 类型 |
|---|---|---|
| understand_intent | deep-tutor-intent | skill-for-skills 生成 |
| build_knowledge_map | deep-tutor-knowledge-map | 生成 |
| design_stages | deep-tutor-stages | 生成 |
| search_resources (+verify_url) | deep-tutor-resources | 生成 |
| validate_plan | deep-tutor-validate | 生成 |
| generate_quiz (P2) | deep-tutor-quiz | 生成 |
| generate_study_materials (P2) | self-study-guide | 复用既有 |
| build_plan_slides (P1) | ppt-slide-builder | 复用既有 |
| parse_material / schedule_plan / record_progress / export_plan | 无 | 纯 Python |

## 八、跨部分一致性核对表

| 交互 | 涉及文件 | 状态 |
|---|---|---|
| 编排器 -> 工具 -> Skill -> LLM | 01/02/05/10 | 本轮明确（两层模型） |
| SSE 事件 <-> 前端 | 03/06/08/10 | 本轮统一载荷 |
| 澄清续接 | 02/03/06/10 | 本轮新增 API+状态机 |
| 持久化选择性 | 01/02/08/09/10 | 本轮明确 |
| 反馈循环 | 01/02/07/10 | 本轮明确机制 |
| 字段覆盖检查 | 02/08/10 | 已有 + 本轮强化 |
| 预算守卫 | 01/04/10 | 已有 |
| 存储分层（内存/文件/DB） | 01/08/09/10 | 已有 |

## 九、待后续轮次深化的协作点
- 工具包装器的统一接口签名（async/sync、超时传递、trace_id 贯穿）。
- 编排器状态机的持久化与并发会话隔离。
- SSE 断线续接的 token/游标协议（前端从 last tool_calls 续）。
- 局部重生成（/plans/{id}/regenerate）与全量生成的编排复用。
