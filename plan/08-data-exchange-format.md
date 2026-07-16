# 08 - 统一数据交换格式（参考 skill-chain-planner）

> 目标：为 Deep-tutor 所有 AI 工作流（工具/Skill 之间）定义**统一数据交换格式**，提高协作效率（一致、可校验、可版本化、显式）。
> 参考：[skill-chain-planner/templates/data-exchange-format.md](file:///C:/Users/Z/.claude/skills/skill-chain-planner/templates/data-exchange-format.md) 的类型系统、接口契约与版本演进公约，适配 Deep-tutor 的工具流水线。
> 权威性：本文件是 Deep-tutor 工具/Skill 间数据契约的唯一权威；[02-tools.md](./02-tools.md) 的工具契约须对齐本格式。

## 一、设计目标与原则

- **一致**：所有 AI 工作流工具用同一套类型系统与契约模板，不再各写各的 JSON。
- **可校验**：契约即 schema，Python 端用 pydantic 校验；不符走工具调用修复（Pillar 2）。
- **可版本化**：`schema_version` + 稳定性级别，向前兼容，平滑演进。
- **显式**：上下游字段覆盖、错误码、稳定性都在契约里写清，消除隐式状态（参考 skill-chain-planner Step 3.6/5.5）。
- **复用**：直接采用 skill-chain-planner 的类型系统与全局枚举，新增 Deep-tutor 专属枚举。

## 二、类型系统（复用 skill-chain-planner + 扩展）

### 2.1 基础类型（复用）
| 类型 | 定义 | 示例 |
|---|---|---|
| `text` | 自由文本 | `"React 入门"` |
| `int` | 整数 | `90` |
| `bool` | 布尔 | `true` |
| `float` | 浮点 | `0.12` |

### 2.2 复合类型（复用）
| 类型 | 定义 | 示例 |
|---|---|---|
| `ref(name)` | 交叉引用（工具/Skill/节点） | `ref(parse_material)` |
| `ref(null)` | 空引用（无上游） | `null` |
| `path` | 相对路径 | `./uploads/abc.pdf` |
| `list<T>` | 有序列表 | `list<text>` |
| `object` | 键值映射 | `{ ... }` |
| `enum(A\|B)` | 枚举 | `enum(ok\|uncertain\|invalid)` |

### 2.3 扩展基础类型（Deep-tutor 新增）
| 类型 | 定义 | 用途 |
|---|---|---|
| `json` | 嵌套 JSON 对象/数组 | plan schema 等复杂结构 |
| `markdown` | Markdown 文本 | 单元材料/导出 |
| `url` | URL（带可达性语义） | 资源链接/导出文件 |
| `timestamp` | ISO8601 时间戳 | 审计/进度 |

### 2.4 全局枚举（复用 + 新增）
**复用 skill-chain-planner**：`Stability`(stable/candidate/experimental)、`DataSource`(upstream/user/filesystem)、`FormatType`、`Protocol`、`Idempotency`(full/conditional/none)、`RecoveryStrategy`、`RiskCategory`、`NodeType`(转换/分析/生成/操作)。

**Deep-tutor 新增**：
| 枚举名 | 可选值 | 适用场景 |
|---|---|---|
| `ToolStatus` | pending/running/ok/error/repaired | 工具执行状态（tool_calls.status） |
| `VerifyLabel` | ok/uncertain/invalid | 资源链接验证 ✅⚠️❌ |
| `MaterialKind` | file/text/url/image/audio/video | 资料类型 |
| `PlanNodeType` | prerequisite/core/advanced/project | 知识节点分类 |
| `QuizType` | choice/fill/short/essay | 题型 |
| `ProgressStatus` | not_started/learning/done | 进度状态 |
| `ClarifyNeed` | none/level/goal/time | 澄清需求维度 |

## 三、统一工具 I/O 信封（Envelope）

所有 AI 工作流工具的输入/输出统一包络，Agent 循环与前端只处理信封，工具内部按 `args`/`data` 契约。

### ToolInput
```jsonc
{
  "schema_version": "1.0",          // [text] 契约版本
  "tool": "<tool-name>",            // [text] 工具名
  "session_id": "<id>",             // [text] 会话 id
  "args": { /* 工具特定参数，按该工具契约 */ },   // [object]
  "upstream": { /* 上游工具输出，按数据流转 */ }  // [object] (optional)
}
```

### ToolOutput
```jsonc
{
  "schema_version": "1.0",
  "tool": "<tool-name>",
  "status": "enum(ToolStatus)",     // ok / error / repaired
  "data": { /* 工具特定输出，按契约 */ },          // [object]
  "errors": [                       // [list<object>] (optional)
    { "code": "empty_content", "message": "...", "details": {} }
  ],
  "metadata": {                     // [object] 成本与审计（写 tool_calls + api_usage）
    "tokens_in": 0, "tokens_out": 0, "cache_read": 0, "cost": 0.0,
    "duration_ms": 0, "model": ""
  },
  "stability": "enum(Stability)"    // 本工具输出稳定性
}
```

## 四、工具契约模板（每个 AI 工作流工具遵循）

适配 skill-chain-planner 接口层（输入/输出/错误/稳定性），统一格式：

```markdown
## Tool: <tool-name>
- schema_version: "1.0"
- node_type: enum(NodeType)
- llm: bool
- idempotency: enum(Idempotency)
- stability: enum(Stability)

### 输入契约 (args)
| 字段 | 类型 | 必填 | 校验规则 | 缺失/非法处理 |
|---|---|---|---|---|
| <field> | <type> | 是/否 | <rule> | <behavior> |

### 上游依赖 (upstream)
| 上游工具 | 用到的字段 | 字段覆盖 |
|---|---|---|
| <tool> | <fields> | ✅ / ❌ |

### 输出契约 (data)
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| <field> | <type> | 是/否 | ... |

### 错误码
| code | 触发场景 | 处理（降级/重试 N 次/中断） |
|---|---|---|
| <code> | ... | ... |
```

## 五、流水线数据流转契约（统一格式）

用统一格式重述 [02](./02-tools.md) 数据流转，含字段类型与覆盖：

| Step | Tool | 输入来源 | 输出关键字段(类型) | 下游消费者 | 覆盖 |
|---|---|---|---|---|---|
| 1 | parse_material | user/filesystem | `text`(text), `meta`(object), `chunks`(list<text>) | understand_intent | - |
| 2 | understand_intent | upstream(parse_material)+user | `topic`(text), `level`(text?), `goal`(text?), `daily_minutes`(int?), `deadline`(timestamp?), `needs_clarify`(bool), `clarify_questions`(list<text>) | build_knowledge_map | ✅ |
| 3 | build_knowledge_map | upstream(understand_intent+parse_material) | `nodes`(list<object>), `edges`(list<object>) | design_stages | ✅ |
| 4 | design_stages | upstream(build_knowledge_map+understand_intent) | `stages`(list<object>) | search_resources, schedule_plan | ✅ |
| 5 | search_resources | upstream(design_stages) | `resources`(list<object>, 含 `verified`:enum(VerifyLabel)) | schedule_plan | ✅ |
| 6 | schedule_plan | upstream(design_stages+search_resources+understand_intent) | `schedule`(list<object>) | validate_plan | ✅ |
| 7 | validate_plan | upstream(以上聚合) | `report`(object), `passed`(bool), `fixes`(list<text>) | (反馈循环 -> design_stages/build_knowledge_map) | ✅ |
| 8 | export_plan | upstream(plan) | `file_url`(url) | - | - |
| 9 | build_plan_slides | upstream(plan) | `slides_url`(url), `slide_count`(int) | - | - |
| 10 | generate_study_materials | upstream(plan, validate 后) | `units`(list<object>), `index_url`(url) | generate_quiz(P2) | ✅ |

**字段覆盖规则**（移植 skill-chain-planner L4）：下游 `args` 必填字段须全部存在于上游 `data`；否则契约不通过 -> 工具调用修复或补默认值或触发上游重生成。

## 六、稳定性与版本演进（移植 skill-chain-planner 第十节）

- **稳定性级别**：`stable`（下游可依赖）/ `candidate`（防御性读取）/ `experimental`（不强依赖）。
- **向前兼容**：新增字段标 optional；废弃字段先 `[deprecated]` 一版再移除。
- **schema_version 递增**：新增可选字段 -> 次版本（`1.0`->`1.1`）；新增必需/移除字段 -> 主版本（`1.0`->`2.0`）。
- **同步更新**：Skill 升级（skill-for-skills UPGRADE）后，同步更新对应工具契约与本文件。

## 七、校验机制（协作效率落点）

- **Python 端**：每个工具契约编译为 pydantic 模型；`ToolInput`/`ToolOutput` 信封统一校验。
- **工具调用修复**（Pillar 2）：schema 不符 -> 校验错误以 `tool_result(is_error=true)` 反馈，重试 3 次 -> 降级默认参数 + 告警，`status=repaired`。
- **字段覆盖检查**：调度工具前校验 `upstream.data` 覆盖 `downstream.args` 必填字段；缺失则报错或触发上游重生成。
- **静默错误防护**（参考 skill-chain-planner Step 5.5）：空输出/格式偏移/截断均有显式状态标记（`errors` 非空 + `status=error`），不传"看似正常的空文件"。

## 八、迁移与落地

- 本文件为权威数据交换格式；[02](./02-tools.md) 工具契约逐步迁移为第四节模板（后续迭代逐工具补全 typed 字段表）。
- 实现阶段：每个工具先写 pydantic schema（= 契约），再写逻辑；契约即文档。
- skill-chain-planner 规划 Deep-tutor 主链路时，其产出的接口契约与本文件互校（[05](./05-skills.md) 第五节已约定）。
- 前端 SSE 事件（[03](./03-api-frontend.md)）的 `tool_end` 携带 `ToolOutput.metadata`，用量与状态统一来自信封。
