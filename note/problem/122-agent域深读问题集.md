# 122 号：agent 域全量逐行深读问题集（第四十一轮）

审计基线 `8875ee55`。审计面：agent 域（mod.rs/tools.rs/commands.rs/tests.rs，
3834 行）首次整体逐行深读 + AgentCenter/AgentTaskCenter 契约核对（此前 23 轮
只修过单点）。经子代理逐文件探查 + 主审逐条实证。共 2 P3 + 4 信息 + 3 待核。

## P3

### 122#1 工具结果截断把中文说明拼进 JSON 尾部——「运行过程」面板对超长工具结果永久显示「等待模型完成」

- 位置：`desktop/src-tauri/src/backend/agent/mod.rs:30-45`（`truncate_for_model` 在 >16384B 时从中间截断并**末尾追加**「…[工具输出已截断至 16384 字节；…]」）→ `:1098/:1115`（tool 消息 content = 该拼接串落库）；前端 `desktop/src/pages/AgentCenter.tsx:28-31`（`toolResult()` 用 `JSON.parse(message.content)`，失败返回 null）→ `:70`（`result?.status ?? 'waiting'`）。
- 传递链条：①单条工具结果序列化后 >16KB（web.fetch 长文、大 history.load 等）；②落库 content = 截断的 JSON 前缀 + 追加中文说明——不再是合法 JSON；③前端解析恒失败 → 该工具调用在「运行过程」面板 status 恒 waiting、Badge「等待模型完成」、图标转圈；④run 已结束后仍停留在该状态（无自愈路径，历史会话同样显示）。
- 影响范围：任何产生 >16KB 工具结果的 Agent 会话（运行过程面板该条目永久误显等待）；≤16KB 结果与模型侧消费（模型看到的是截断文本，本就如此设计）不受影响；纯展示层误显，数据本身无损（截断前缀仍在库里）。
- 修复方向：截断说明不进 JSON 体——改在 JSON 外层包装（如 `{"truncated":true,"preview":...}` 结构化产物）或放 tool 消息独立备注字段；前端解析失败时降级显示「结果过长已截断」而非 waiting。

### 122#2 resume 入口缺会话预算闸，UI 对必然再失败的终态仍给「恢复运行」——与 send 的入口拦截口径不对称

- 位置：`desktop/src-tauri/src/backend/agent/mod.rs:1194-1239`（`resume` 无 `session_output_tokens >= budget` 检查；对照 `send` :594-600 有入口闸）+ `:811-816`（begin_step 对 step>25 无条件报错不区分恢复来源）；前端 `AgentCenter.tsx:53/132`（`resumable = ['failed','cancelled','interrupted'].includes(status)` 对 step_limit 与 budget_exceeded 的 run 也展示恢复按钮）。
- 传递链条：①预算耗尽/步数到顶的 run 落 failed（budget_exceeded/step_limit）；②UI 展示「恢复运行」，文案称「已完成的步骤不会重复请求，中断的步骤将重新请求并计费」——该场景下误导；③点击 → resume 翻 running + spawn → run_loop 首轮（:927）或 begin_step（:811）立即再失败；④产生一对 run_resumed/run_failed 事件与一条必败 run 行——与 31#41「send 由入口拦截」的口径不对称。
- 影响范围：预算/步数耗尽的 run 上点恢复的用户（必失败 + 误导文案 + 无谓事件）；中断类（interrupted/cancelled/模型侧 failed）恢复不受影响（真可恢复）；无数据损坏，浪费一次请求与事件噪音。
- 修复方向：resume 补预算闸（与 send 同款 `session_output_tokens` 检查，步数到顶同理按 steps>=25 拒绝）；前端 resumable 排除 budget_exceeded/step_limit 错误码。

## 信息

### 122#3 同 id 工具调用重放返回 64KB-cap 预览副本——与首次执行的「原始结果」口径不一致，101#3 注释失实

- 位置：`desktop/src-tauri/src/backend/agent/tools.rs:744-758`（首次执行：`cap_tool_result` 产物只入持久层，`Ok(result)` 原样返回；注释 101#3 自述「返回原始结果给 run_loop」）vs `:532-533`（重放分支：`Ok(serde_json::from_str(&result)?)` 返回持久层副本）。
- 传递链条：①run 中断后 resume，同 call id 命中重放；②重放返回 cap 副本——>64KB 的结果退化为 `{status,truncated,notice,preview(2048字符)}`；③run_loop 以它落 tool 消息——同一 call id 首次执行模型可见 ~16KB 真实内容、重放后只剩 2048 字符预览；④注释宣称的口径只在首次成立。
- 影响范围：包含 >64KB 工具结果且经历中断恢复的 run（恢复后该结果对模型退化为预览）；≤64KB 结果（cap 不生效）与不中断的 run 不受影响；回答质量与注释真实性，无安全面。
- 修复方向：重放分支对 truncated 结果按需重执行（幂等键放开）或修正 101#3 注释声明该取舍。

### 122#4 web.search 工具层不钳制 limit——目录 schema 的 1-5 约束由错误路径而非 clamp 实现，与 history.load 风格不统一

- 位置：`desktop/src-tauri/src/backend/agent/tools.rs:641-643`（`arguments.get("limit").as_u64().unwrap_or(3)` 直接透传）vs 工具目录 `"minimum":1,"maximum":5`（:53）vs `web/mod.rs:415`（`!(1..=5).contains(limit)` 整步报 invalid_search_query）；对照 history.load 的 `clamp(1,100)`（:691-695）。
- 传递链条：①模型传 limit=50（超目录声明）；②工具层不收敛直接调 web::search；③web 层校验失败 → 整步以 invalid_search_query 失败（浪费一步）；④候选抓取另有 MAX_CANDIDATE_FETCHES=10 上限（web/mod.rs:442），无安全/配额风险——仅目录声明与执行口径不一致。
- 影响范围：模型传超界 limit 的工具调用步（该步整步失败而非收敛执行）；合法 limit 与其他工具不受影响；无资源风险。
- 修复方向：工具层对 limit 做 clamp(1,5)（对齐 history.load），或在目录描述中明确超界即失败。

### 122#5 docs/architecture/02 §8 agent 段行号全面漂移——行为描述本身仍与代码一致

- 位置：`docs/architecture/02-operation-principles.md:311/313` 引用 `run_loop（:855）`、`begin_step（:775）`、`tools::execute（tools.rs:413）`；实际 `run_loop` mod.rs:913、`begin_step` :799、`execute` tools.rs:483、`commit_artifact` tools.rs:272（文档 :258）、`send_agent_message` :580（文档 :575）。
- 传递链条：①多轮修复在 agent 域插入代码未同步文档行号；②文档引用行号定位到错误函数；③所有行为断言（25 步/8 工具/62 条窗口/审批 CAS/双通道计量）经逐条核实**仍然成立**——纯定位漂移。
- 影响范围：按行号跳转文档读者的定位效率；行为描述与测试不受影响。
- 修复方向：随下一波修复同步校正行号（或改引函数名不引行号）。

### 122#6 send 自动标题绕过自身 160 字节约束 + 重复取消文案语义失真 + list() N+1——三处口径小项

- 位置：`agent/mod.rs:624`（`content.chars().take(60)` 纯 CJK 约 180B 超 `new_session` 的 160 **字节**校验 :310，UPDATE 路径无校验——仅口径不一致无功能危害）；`mod.rs:1166-1175`（cancelling 态再 cancel 报 `no_active_run`「该 Agent 任务**已经结束**」——实际仍在取消流程；前端已禁用且码在 isCancelRace 白名单，仅文案失真）；`mod.rs:463-474`（`list()` 每会话一次完整 `get()` 含 6 prepare + GLOB 聚合，50 会话 × AgentTaskCenter 每终态事件触发一次——有索引与事件合并兜底，实测可承受）。
- 传递链条：三条均为单点口径/文案/性能小项，无用户可感知故障链。
- 影响范围：CJK 长首句会话标题（60 字符≈180B 超自限）、并发取消的极端调用方、大会话量下的列表开销；主路径不受影响。
- 修复方向：标题 take 改按字节截断；cancelling 态返回独立文案；list() 按需做单条聚合 SQL（低优先）。

## 待核

### 122#7 单响应内重复 tool_call id 不去重——严格端点下可能不可自愈 400

- 位置：`agent/mod.rs:1085-1138`（工具循环无 id 去重）+ `tools.rs:502-543`（幂等登记把同 id 同参的第二次调用判为重放）+ `mod.rs:684-706`（`answered` 为集合，两条同 id tool 消息都保留重放）。
- 传递链条：①部分本地/小模型在同一响应返回两个相同 id 的 tool_call；②第一次正常执行、第二次命中重放返回同结果，两条相同 tool_call_id 的 tool 消息都落库；③23#9 修剪只保证「有声明必有回复」不去重重复回复；④严格 OpenAI 系端点若拒绝重复 id → run 失败且 resume 每次重放同样序列——会话卡死（与 23#9 同症状不同成因）。端点是否实际拒绝未验证。
- 影响范围：仅产生重复 id 的非主流端点/模型；标准 OpenAI 兼容端点不受影响；待构造复现。
- 修复方向：落库前对同 id tool 消息去重（或执行层第二次同 id 直接跳过），重放序列过滤重复 tool_call_id。

### 122#8 agent 终态事件无 sessionId——依赖前端内存映射 runOwners 反查，监听间隙事件被丢

- 位置：`agent/mod.rs:888-897`（run_completed/cancelled/failed/interrupted 载荷仅 runId/status/error）→ `desktop/src/backend/useAgent.ts:171-185`（靠 runOwners 由 run_started 与列表加载填充）。
- 传递链条：①startup recover 在前端首拉前发 `agent.run_interrupted`；②前端监听器挂载前该 run 不在任何已加载会话（runOwners 空）；③事件被丢弃；④初始 refresh() 兜底自愈——窄窗口，仅记录。
- 影响范围：启动恢复窗口内到达的终态事件（首拉兜底）；常规运行期（run_started 先到）不受影响。
- 修复方向：终态事件载荷补 sessionId（与 plan.*/lecture.* 事件族对齐）。

### 122#9 memory.save 审批的 key_exists 读取在事务外——同键并发删除的极窄配额绕过缝

- 位置：`agent/tools.rs:817-828`（`key_exists` 在 `write_transaction()`（IMMEDIATE）之前读取；配额检查 25#18/31#42 本身在事务内）。
- 传递链条：①同键 memory.delete 审批并发执行：读取时键存在（跳过配额检查）；②delete 事务提交删除该键；③save 事务以「覆盖已有键」路径 INSERT 新行——单槽位配额被绕过。需两个审批并发且键相同，窗口极窄。
- 影响范围：同键并发审批交叠的毫秒窗（单槽位配额差一）；常规审批串行不受影响；无越权（仍是审批后写入）。
- 修复方向：key_exists 挪进写事务内复查（IMMEDIATE 下天然串行）。

## 核实通过项（免后续复查）

- **25 步上限/单步 8 工具/预算三闸**：begin_step step>25 → step_limit（测试断言 25 请求 51 消息）；>8 → too_many_tool_calls 且判定在 assistant 落库前；send 入口 :594-600、循环顶 :927-933、工具循环后 :1139-1144 三闸齐全；带 calls 响应预算耗尽不落库（31#41）、纯文本已计费回复落库置 done（测试覆盖 backfill）。
- **输出 token 双通道计量**：`agent_session_id` FK 主 + `IS NULL AND GLOB agent-{run}-*` 兜底（34#16 防串扰）；usage 缺失按 LENGTH(output)/2 保守估算（08#7/25#17）；34#6 回填时序正确；run 级聚合 GLOB 天然含 failover -fN。
- **终态先落盘再发送**：全部发事件点（save_message/begin_step/finish_run/cancel/commit_artifact/approval 族/recover/send/resume）均为 Store::event 进 tx → commit → emit。
- **取消/恢复竞态守卫**：finish_run 遇 cancelling 翻 cancelled、已终态不覆写不重发（97#5）；cancel 只翻 running 行；run_loop 工具执行后复查标志（97#7）；launch catch_unwind → task_panicked、spawn 失败回收 active；resume IMMEDIATE 事务内重查+同会话查重+active 检查、commit 失败回收；recover 活跃态守卫 changed==0 跳过（112#1）+ running 态 tool_calls 收口（56#5）。
- **审批门不可绕过**：memory.save/delete、artifact.promote 工具内只产生 pending；实际写删全在 approve 路径；promote 五重校验（存在/committed 单调/24h 过期/sha256+stage+unit 漂移/lecture done+版本 CAS）；去重复用同 kind+payload；cancel_approval 仅 committed 翻 cancelled；resolve_pending CAS 拦重复。
- **workspace 路径安全**：artifact_path 拒空/超长/非 Normal/超 6 层/符号链接，canonicalize 父目录限根内（测试覆盖 escape/C:/symlink/篡改哈希）；atomic_write 双 rename 失败回收；read 复验 sha256；已提升路径冻结。
- **web 工具 SSRF**：直接委托 web::search/fetch 复用统一校验链（file:// 拒绝测试在案）。
- **历史重放自洽**：23#9 悬挂/孤儿修剪、34#5 声明先于 tool、34#8 62 条/200KB 窗口、97#1 每请求前重裁（各有测试）；validate_messages 64 条/256KB 与窗口匹配。
- **前后端契约**：10 命令注册齐全；serde camelCase 与 TS 类型逐字段一致（含 toolCallId/outputTokensUsed）；事件 kind 全集与前端过滤匹配；审批 payload 键与 ApprovalCard 一致。
- **UX busy 守卫**：删除需静止+确认+后端 TOCTOU 关闭；发送/创建/取消/恢复/审批逐项禁用；取消竞态码静默；会话切换清空流式状态。
