# 03 · API 设计与 shim 映射

> 决策依据：#34（/api 长期统一面）、#45（重设计+旧 SPA 走 shim）、#39（阶段①②覆盖旧 SPA 全 19 页）、#60（全局事件流等价物）、#9/#44（认证）、#19（预算/配额语义）、#22（内核能力进 Web UI）。
> 兼容基准：API.md（v1 契约全文）+ note/06（横切守卫/错误码/SSE 协议）。shim 的验收 = 旧 SPA 19 页回归 + SSE 事件序列对拍。

## 一、设计原则

1. **/api 是唯一对外承诺契约**（#34）：新前端、浏览器扩展、Studio webview、CLI/TUI/ACP 的 Web 通道都走它；Reasonix 原生端点（/events、/providers 等 serve 路由）不对外承诺，仅内部与 /tutor/* Studio 面板使用（#3）。
2. **v2 形态一步到位**（#45）：不背 v1 历史包袱（如 Anthropic block 原生形态的消息结构）；事件名沿用 snake_case 但按新域重定义。
3. **统一中间件链**：JWT/PAT 认证 → 归属注入（ctx user_id）→ 配额/限流 → 错误信封。错误信封保持 `{error:{code,message,details}}` 结构（好设计保留），码表重定义（§四）。
4. **SSE 三用保持**：生成流（POST + fetch ReadableStream 携带 Bearer）、Chat run 流、全局事件流（GET 常驻）；全部支持 `Last-Event-ID` 续接与 15s 心跳。

## 二、新 /api 端点清单（v2）

> 分组列出；详细请求/响应 schema 在 M3/M4 落地时按本清单逐端点补 `docs/api-v2.md`。`[SSE]`=流式端点，`[admin]`=仅管理员。

### 认证与用户
| 端点 | 说明 |
|---|---|
| POST /api/auth/login · GET /api/auth/me | JWT（7 天，tokens_valid_after 吊销水位） |
| GET/POST/PUT/DELETE /api/users · /api/users/{id} `[admin]` | 仅 admin 建用户（#44） |
| GET/POST /api/admin/init · CLI `deep-tutor admin create` | 首管理员双通道（#43）；仅零用户时可用 |

### 生成域（会话/材料/方案）
| 端点 | 说明 |
|---|---|
| POST/GET /api/sessions · GET/DELETE /api/sessions/{id} | GET {id} 含 live 进度（current_tool/step/cost） |
| GET /api/materials/formats · POST /api/materials/upload|url|text|clip | 上传暂存→确认→解析三段制 |
| POST /api/materials/{id}/confirm · /parse | |
| POST /api/plans/generate `[SSE]` · POST /api/sessions/{id}/retry|clarify `[SSE]` · /cancel | 409 并发/续接、402 预算、503 过载语义保持 |
| POST /api/plans/{id}/regenerate `[SSE]`（scope 局部重生成） | 429 超 version>2 |
| GET /api/plans · /api/plans/today · /api/plans/{id} | |
| POST /api/plans/{id}/materials（内容协商：SSE/JSON/后台） · GET …/materials · /export · /search | 后台完成经全局事件流 quiz_ready/materials_completed 通知（#60） |

### 学习闭环
| 端点 | 说明 |
|---|---|
| POST /api/plans/{id}/quiz · GET /api/plans/{id}/quiz/wrong|stats | |
| POST /api/quizzes/{id}/submit（服务端判分） · GET /api/quizzes/wrong-overview|wrong-queue | |
| POST /api/progress · GET /api/progress/{plan_id} · /review/due · /review/queue · /stats/summary|calendar|by-plan · /logs | 掌握度/复习排期语义同旧 |
| GET /api/search（四域聚合） | |

### 笔记 / 知识库 / Wiki
| 端点 | 说明 |
|---|---|
| GET/POST/PUT/DELETE /api/notes · /api/notes/import|export | ZIP 导入导出 |
| GET /api/knowledge · /notes · /notes/{node_id} · /export | Wiki 聚合 |
| POST /api/kb/documents/upload|text|url · /documents/{id}/confirm|parse · GET/PUT/DELETE /api/kb/documents… · /raw · /parsed · /export | |
| POST /api/kb/chat（限流+引用问答） · GET /api/kb/chats · DELETE /api/kb/chats/{id} · POST /api/kb/generate | |

### 对话（Chat = Controller，#21/#53）
| 端点 | 说明 |
|---|---|
| POST/GET /api/threads · GET/PATCH/DELETE /api/threads/{id} | |
| GET /api/threads/{id}/messages | 从内核会话投影（分页） |
| POST /api/threads/{id}/runs `[SSE]` · GET /api/threads/{id}/runs/active（附流） | 事件见 §五 |
| GET /api/runs/{id} · /steps · DELETE · POST /cancel · GET /files · /files/{name} · POST /approve | promote 审批（#21） |

### 内核能力面（#22）
| 端点 | 说明 |
|---|---|
| POST /api/runs/{id}/approve-tool（审批请求应答：allow/deny） | 工具审批（ask 档）经全局事件流+本端点 |
| GET /api/threads/{id}/rewind · POST …/rewind（prepare→commit 两阶段） · POST …/rewind/undo | Checkpoint/Rewind UI |
| GET /api/tools · /api/tools/modes（审批档位/白名单状态） | 工具发现+当前会话工具面（#35/#53） |
| GET/POST /api/plugins… `[admin]` | 对接 Reasonix 插件包安装态（install/enable/disable/reload，#26/#55） |
| GET /api/skills… · /api/mcp… `[admin]` | 技能/MCP 管理（全局 admin #55） |
| GET /api/jobs · POST /api/jobs/{id}/cancel | 后台作业（quiz/materials 后台模式） |

### 设置 / 供应商 / 用量
| 端点 | 说明 |
|---|---|
| GET /api/settings · PUT /api/settings/prefs | 用户偏好（含 websearch 切换 #24、planner_model #38） |
| GET/POST/PUT/DELETE /api/providers… · /api/providers/{id}/activate|test · /api/providers/kinds | 每用户 DB 档案（#17）；kinds=三种（#18）；test 掩码返回 |
| GET/POST/PUT/DELETE /api/credentials… | Bing 等（#24） |
| GET /api/pricing · PUT /api/pricing/overrides | Reasonix 费率表只读 + 用户覆盖层（#20） |
| GET /api/usage · /usage/daily · /usage/by-model | SQL 台账供数（#19） |

### 导出 / 分享 / 备份 / 事件
| 端点 | 说明 |
|---|---|
| GET /api/export/formats · POST /api/export/plans/{id} | md/pdf(Go 真实现 #28)/ics/apkg/slides |
| POST/DELETE/GET /api/plans/{id}/share · GET /api/shares `[admin]` · GET /api/shared/{token}（免登录只读） | iter 372 起默认 90 天过期（SHARE_DEFAULT_EXPIRE_DAYS，0=永久；过期与不存在同形 404、响应带 expires_at；iter 373 起过期后再创建轮换 token 重发、/shares 逐条带 expired）——shim 须保语义 |
| GET /api/backup/export · POST /api/backup/preview|import | v1 格式 v2（threads/memories/kb_documents 索引互链 + 原件内嵌，`version>2` 拒绝）；import 上限 256MB（413 `too_large`）、并发导入互斥（409 `conflict`，同 plans 生成立即拒绝约定）——shim 须保语义 |
| GET /api/events/stream `[SSE]` | **全局事件流**（#60，§六） |
| GET /api/health | 探活 |

## 三、shim 层设计（v1 兼容）

- **位置**：`internal/web/shim/`，挂在 mux 之前按路径前缀拦截 v1 端点 → 翻译到 v2 handler。
- **翻译面**（验收=API.md 逐端点）：
  1. 路径映射表（v1→v2，绝大多数同路径——v2 清单刻意保持 v1 路径骨架，差异点集中在：消息结构（v1 Anthropic blocks→v2 中性结构）、部分管理端点语义）；
  2. 错误码映射（v1 24 码→v2 码表，含 401/402/409/413/422/429/503 语义不变）；
  3. SSE 事件名与载荷（v1 词表→v2 词表，见 §五映射）；
  4. 内容协商（materials 的 Accept 头三模式）与 Last-Event-ID 续接语义。
- **生命周期**：阶段①②②使用；阶段④随旧 SPA 退役删除（#45/#47）。
- **测试**：`shim_test.go` 用 API.md 端点全集 + SSE 序列录制回放（对拍基准文件 `internal/web/shim/testdata/v1_*.json`）。

## 四、错误码表（v2 重定义，结构不变）

沿用信封 `{error:{code,message,details}}`。核心码：unauthorized(401) · budget_exceeded(402, 三层防线触发) · not_found(404, 含跨用户防枚举 #10) · conflict(409, 并发/续接) · payload_too_large(413) · validation_error(422) · rate_limited/quota_exceeded(429) · no_llm_provider/search_unavailable/server_busy(503) · upstream_timeout(504) · bad_gateway(502)；新增：approval_required（runs/工具审批）· rewind_conflict（快照覆盖缺口）。R-Tutor tutor 的 25 个细分 refusal code（tutor.not_found/busy/regen_quota/no_llm/kb_quota…）映射进对应 HTTP 码 details.refusal 字段。

## 五、SSE 事件协议（v2）

### 生成流（generate/retry/clarify/regenerate/materials）
v2 事件（新前端消费）：`tool_start{tool,step,total,attempt}` · `tool_end{tool,status,meta{tokens,cost,model}}` · `unit_start/unit_done/materials_done` · `clarify_needed{questions}` · `warn{code}` · `plan_chunk` · `review_started/review_done{planner_findings}`（#57 planner 审查者新事件） · `budget_exceeded{spent,limit}` · `error{code,message,stage,retryable}` · `done{plan_id,meta}`。
shim 翻译规则：v1 无 review_* 事件→shim 吞掉（旧 SPA 不感知）；其余名称保持 v1 原名原载荷结构。终态事件不重连、id 游标语义同 v1。

### Chat run 流
v2：`run_start{run_id,model}` · `text_delta` · `thinking_delta` · `tool_call{id,name,step}` · `tool_progress` · `approval_request{id,tool,summary}`（ask 档新事件） · `tool_result{id,name,is_error,step}` · `message_end{step,stop_reason}` · `rewind_available{turns[]}` · `done{run_id,status}` · `error`。
shim 翻译：v1 的 message_start 保留、approval_request/rewind_available 吞掉。

### 全局事件流（#60，替代旧 /api/events/stream）
- 端点：GET /api/events/stream（JWT；EventSource 可用——经 query token 或同源 cookie；文档注明扩展场景用 PAT query 参数）。
- 事件类型：领域事件 `plan_created` `plan_completed` `quiz_ready` `materials_completed` `learning_log_added` `run_completed` `run_awaiting_approval`；内核桥接事件（按用户过滤）：`tool_approval_request` `job_done`；系统事件 `startup/shutdown/maintenance`。
- 语义：用户隔离（不可归属事件不下发，沿用 v1）；`Last-Event-ID` 续接（events 表回放窗口，超出窗口回 complete=false 指引重读权威端点——对齐 Reasonix events/replay 语义）；15s 心跳。

## 六、验收口径（对照 note/06 + API.md）

1. 旧 SPA 19 页全功能回归（M5 出口判据）——逐页操作脚本清单见 05 附录 A。
2. SSE 对拍：三条流的 v1 事件序列（正常/降级/取消/预算超限四路径）录制回放全等。
3. 错误码矩阵：v1 全部 24 码触发路径在 shim 上复现同码同语义。
4. 横切守卫：生成配额（用户 1/全局 8→409/503）、chat（线程 1/全局 8）、限流窗口、413 上传限额——语义同 v1。
