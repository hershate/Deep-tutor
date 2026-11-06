# 02 · 运行原理

> 基准：main 分支 commit `cf2cb36c`。引用形如 `文件:行号`，相对 `desktop/` 目录。`L/`=learning，`P/`=providers，`M/`=materials，`K/`=knowledge，`SEC/`=security。

## 1. 启动流程

```mermaid
sequenceDiagram
    participant M as main.rs
    participant SU as startup.rs
    participant SEC as security
    participant B as BackendState
    participant FE as 前端
    M->>SU: initialize_background(root)
    SU->>SU: validate_location / inspect_location
    SU->>SU: assemble
    SU->>SEC: SecurityState::new + initialize_session
    SU->>B: manage BackendState(11 个域 State)
    SU->>SU: with_user: materials/providers/learning/agent/practice/projects/knowledge/sync recover
    SU->>SU: start_scheduler(sync 24h)
    FE->>FE: bootstrap 轮询(250ms, 20s 上限)
    FE->>FE: events_since 追赶 + get_app_state
```

1. 目录决策（`startup.rs:18-36`）：读 `app_local_data_dir/storage-location.json`（≤4096B，version==1，`StoredLocation` :20-25）失败则回退 `app_data_dir`。`validate_location`（:86-130）拒绝：相对路径/根目录/`..` 段/UNC 网络目录/Windows 与 Program Files 目录。`prepare_directory`（:146）自动创建缺失目录，无对话框。
2. `assemble`（:332-388）顺序：`SecurityState` → `prepare_directory` → `Store::new` → 11 个域 State 构造（materials/providers/learning/knowledge/agent/practice/review/sharing/community/plugins）→ `BackendState` manage → `security::initialize_session`（无有效会话且存在活跃管理员时自动签发首位会话，`SEC/mod.rs:680-684`）→ `with_user(principal)` 内重建 store 连接并逐模块 `recover`（materials→providers chats/routing→learning→agent→practice→projects→knowledge→sync，`startup.rs:372-381`）→ 独立 `with_user` 启动 sync 调度线程（:383-385）。
3. 失败语义：初始化线程 panic 被 catch（:299-307）置 `startup_failed`；前端 20 秒看门狗（`src/backend/useBackendState.ts:88-97`）后显示 DataDirectoryPanel，提供重试（`retry_storage_initialization` 仅在未运行且无 pending 时重新 `initialize`，`startup.rs:431-451`）、改目录（写 pointer 原子替换 `.tmp`+rename，:212-227）、恢复默认（只删 pointer 不动数据，:231-243）、立即重启。
4. 命令可用性：未就绪时守卫 `ensure_ready`（`SEC/guard.rs:150`）对除存储外的命令返回 `startup_not_ready`，前端据此继续轮询。

## 2. 数据库打开与 schema 指纹迁移

`Store::open`（`store.rs:106-203`）每次打开：
1. 解析工作区路径（无 owner=根库；有 owner=`users/{id}/`）。
2. `STORE_OPEN_LOCK` 全局互斥（:117-119）串行化打开与 user-1 旧库复制。
3. PRAGMA：WAL（非 WAL 才切换）、`foreign_keys=ON`、`busy_timeout=10000`。
4. `user_version > 1` → `unsupported_schema` 拒绝降级（:135-141）。
5. 建四张通用表（:142-161），计算 `schema_fingerprint`（sqlite_master 全量 SHA-256，:82-104）；与 `meta.schema_fingerprint` 不一致时依次执行 15 个模块 `migrate`（:173-187），再回写指纹（:188-193）。指纹未变则 20 次重开零迁移（测试 `store.rs:1162-1185`）；DROP 表造成的漂移会被修复重跑。

各模块 migrate 均为幂等 `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` 检测补列（如 providers/mod.rs:284-295、reports/mod.rs:197-199、practice/exams.rs:26-44）。

## 3. 事件系统与 Last-Event-ID 续接

后端写路径（事务内 `Store::event` store.rs:408-421 → commit → `emit` backend/commands.rs:22-26）保证**终态先落盘再发送**。事件 id 由 events 表 AUTOINCREMENT 单调递增。

前端双通道（`src/backend/useBackendState.ts`）：
1. 实时：`listen('backend://event')` 逐条应用。
2. 追赶：`sync()`（:41-66）以 `events_since(lastId)` 循环翻页（每页 200，`store.rs:681` LIMIT 201 判定 has_more）直到无更多，再 `get_app_state()` 全量校准；`applySnapshot` 丢弃 `lastEventId` 回退的过期事件并在 workspaceId 变化时重置游标（:29-39）。
3. 触发点：bootstrap 后、窗口 focus/visibilitychange（:159-160）、`security.session_started` 全量重载（:131-141）。

断线不重复执行的机制不在事件层，而在任务层：每个模型步骤持久化 `request_id`+`attempt`，恢复时 `providers::completed_chat` 为真则复用原请求结果不再调上游（见 §5）。

## 4. 方案生成八步流水线（learning）

入口 `start_learning_plan` → `learning::start`（L/mod.rs:406）：
1. `validate_input`（L/mod.rs:281）：goal/level ≤1000B、daily_minutes∈10..600、deadline 日期格式、materials 1-8 份去重并经 `materials::detail` 校验、provider 经 `routing::provider_for_task("plan")`（:323）。
2. 事务 INSERT `learning_plans('running')` + 8 行 `learning_plan_steps('pending')` + materials 关联 + `plan.started`（:416-432）。
3. `launch`（:371）用 `security::thread_builder()` 起线程 `learning-plan-{id}`，catch_unwind 兜底 `fail_plan`。

`pipeline::run`（L/pipeline.rs:534）循环 `execute_index`（:428）：

| 步 | 名称 | 实现 | 关键约束 |
|---|---|---|---|
| 0 | 解析资料 | sources :54 | 逐份 materials::detail，status 须 parsed/degraded，文本合计 ≤192KB 否则 `context_too_large` |
| 1 | 澄清意图 | 本地 | 无模型调用，落规范化输入 |
| 2 | 知识图谱 | graph_prompt :139 → model_json :195 | `validate_graph`（validate.rs:18）：节点 1-100、引用 quote 必须为资料原文子串（:68）、Kahn 拓扑判环（:110-125） |
| 3 | 渐进阶段 | stages_prompt :148 | `validate_stages`（:199）覆盖全部 required、依赖不逆序 |
| 4 | 资源检索 | resources :92 | `web::search(goal,3)` 真实抓取验证；rejected>0 → partial；失败 → unavailable，不虚构链接 |
| 5 | 确定性排期 | validate::schedule :206 | 纯本地按 daily_minutes 逐日装箱，算 within_deadline/within_milestone |
| 6 | 五维校验 | validation_prompt :158 | 模型输出无效降级 unavailable_model；`validation_report`（:354）原生复核依赖/难度/认知负载 |
| 7 | 方案落盘 | finish_result :363 | warnings 聚合；status=done（passed 且无警告）否则 degraded（complete_plan L/mod.rs:779-795） |

模型调用统一 `model_json`（:195）：`begin_step` → `routing::stream_chat`（request_id=`plan-{id}-{步}-{attempt}`，L/mod.rs:664-668）→ `parse_model_json`（剥 ``` 围栏，:177）→ 校验 → 失败重试 2 次。

**断点续接**：done 步骤的产物持久化在 `steps.output_json` 与 `artifacts_json $.stepN`（`complete_step` L/mod.rs:707 json_set）；`run` 先经 `step_output`+`persisted_artifact`（pipeline.rs:301,346）还原再执行未完成步骤。`begin_step`（L/mod.rs:655-668）对 status==interrupted 且 `providers::completed_chat` 的请求**复用原 request_id/attempt**，不重调上游、不重复计费。恢复入口：用户 `resume_learning_plan`（failed/cancelled/interrupted → running，L/mod.rs:456-473）与重启 `recover`（:903，running/cancelling → interrupted/cancelled）。

## 5. 提供商调用、流式与取消

直连链：`stream_provider_chat[_with_tools]`（P/commands.rs:81/107）→ `spawn_blocking` → `stream_chat_with_policy`（P/mod.rs:1071）：
1. `begin_chat`（:934）INSERT `provider_requests('running')` + 注册取消标志 + emit `provider.chat_started`。
2. enabled/has_key 校验（:1096-1103）→ `protocol::stream_chat_with_policy`（protocol.rs:325）按 `profile.protocol` 分派：OpenAI Chat（openai.rs:669 自建 SSE 泵 `stream_once` :973）/ OpenAI Responses（responses.rs:76）/ Anthropic（anthropic.rs:98）/ Gemini（gemini.rs:97）。
3. HTTP 客户端（openai.rs:243-254）：禁自动重定向 + DNS 解析后固定 IP（防 DNS rebinding）+ `public_address` 拒绝非公网（:103）；`endpoint`（:197）做 SSRF/路径穿越校验。
4. 流式约束：`validate_messages` 1-64 条/256KB（:532）；响应 1MB（read_json :295）；增量文本累计 1MB（protocol.rs:132）；SSE 每 chunk 超时窗口前后各检查一次取消（protocol.rs:280-292）。
5. 每个 delta 双写：`Store::event("provider.chat_delta")` 落库 + emit（P/mod.rs:1107-1115）——delta 事件同时是恢复重建源（:894-909）。
6. 结束 UPDATE status=done/cancelled/failed + 终态事件（:1149-1171）。

取消：`cancel_provider_chat`（P/commands.rs:135）→ 路由层 `cancel_inner`（routing.rs:1109）置 `Arc<AtomicBool>`（Acquire/Release）→ 级联直连层 flag（:1162）→ SSE 泵检测后以 `cancelled` 错误收尾。

## 6. 路由、聚合、故障转移与熔断

`route_provider_chat`（P/commands.rs:182）→ `routing::stream_chat`（routing.rs:867）：
1. `select`（:402）按 RoutingSettings.mode（direct/routing/aggregate）选 profile+model；任务路由表 `routes: BTreeMap<task, providerId>`。
2. 幂等：已存在同 request_id attempt 直接复用（:888-910）。
3. `usage::ensure_budget`（usage.rs:426）按 session_usd/daily_usd 拒绝超预算。
4. INSERT `provider_route_runs('running')` + `provider.route_started`（:922-932）。
5. 候选循环（failover 队列 :933-951）：`circuit_permit`（:626）——熔断 open 且冷却未到 → `circuit_open` 拒绝；到期 CAS 置 half_open（:653）；`save_attempt`（:724）逐试次落库 + `provider.route_attempt` 事件（携带 upstream_request_id，前端借此切换流匹配，`useAgent.ts:99-106`）；调用直连层（StreamPolicy 取自 circuit timeout/retries，:1020-1024）；成功 `record_circuit`（:670）closed 复位 failures；失败且 `can_failover`（:592）→ 下一候选。
6. 全部不可用 → `all_providers_unavailable`（:1082）。

熔断状态机（`provider_circuits.state`）：closed→open（failures≥3，:699-704）；open→half_open（冷却 60s 到期，:653）；half_open→closed（成功 :695）/→open（失败或重启恢复 :687-689,1267-1269）。

用量：每次请求 usage_json 落 `provider_requests`；`overview`（usage.rs:314）聚合日/模型/会话口径；`export_csv` 防公式注入（:469）。

## 7. 材料导入与解析队列

导入链（`import_material_files` M/commands.rs:14 → `import_files` M/mod.rs:643 → `stage_one` :586）：
`validate_source`（:408：canonicalize→普通文件→非空→≤50MB→文件名≤500B→19 种扩展名白名单 :24→读盘比对→魔数 `validate_content` :453）→ SHA-256 → `stage_validated`（:482：sha256 查重返回 deduplicated=true → `ensure_quota("materials",1)` :506 → 原件写 `originals/{sha256}.{kind}`（临时文件+rename :509-530）→ 事务 INSERT 'staged' + `sync_resources` + `material.staged` 事件）。

解析队列（单工作线程语义，`MAX_PARSE_WORKERS=1` M/mod.rs:30）：
- `enqueue`（:912）：media/metadata_only 拒绝 → 活跃任务查重 → 事务 INSERT `material_jobs('queued')` + materials→queued/progress=10 + `material.queued` → `spawn_job`（:780）：`active.len()>=1` 则不启动（留在 queued）。
- 工作线程 `run_job`（:709）：取消预检 → parsing/35 → 重读原件并哈希复核（:735）→ `parse`（parse.rs:40，PDF/docx/pptx/epub/txt/md/媒体元数据等）→ parsing/85 → 事务 UPSERT `material_text` + materials→parsed(或 degraded)/100 + jobs→done + `material.parsed`。
- 队尾 `schedule_next`（:1040）：取下一个 queued 先 spawn 再移除本 id（接续登记先于旧任务移除，不能提前显示空闲）。
- 取消：排队中直接置 cancelled（:1025）；运行中置 AtomicBool + `cancel_requested=1` + status='cancelling'（:1011-1014）。
- 恢复：`recover`（:1073）对 queued/parsing/cancelling：cancel_requested=1 → cancelled，否则回置 queued 重新 spawn。

ZIP 资料包（archive.rs:125 `import_archive` → `read_archive` :19）：扩展名 zip → 1-50MB → 内存 `ZipArchive` → 条目≤64 → `enclosed_name` 防穿越 → 拒嵌套 zip（:72）→ 单项≤50MB、压缩比≤200× → 总解压≤200MB → 逐条 `stage_bytes` 复用导入链；不在文件系统解包。

媒体（media.rs）：`info`（:132）整读校验 SHA-256；`chunk`（:153）offset 合法 + ≤256KiB 分块读；路径约束 originals 目录内 + 拒绝符号链接（:105-121）。

## 8. Agent 工具循环

`send_agent_message`（agent/mod.rs:547）：
1. 预算闸：会话累计 output_tokens ≥ budget_tokens 拒绝（:562）；同会话 running 查重（:577）。
2. Immediate 事务 INSERT `agent_runs('running')` + 用户消息 + `agent.run_started`；注册取消标志（:588-597）。
3. 线程 `agent-run-{id}`（:487）跑 `run_loop`（:746）：每轮首查取消（:757）→ 会话预算 → `usage::ensure_budget`（:772）→ `begin_step`（:666，25 步上限 :678；可复用已完成请求 :698-703；request_id=`agent-{run}-{attempt}-{step}`）→ `routing::stream_chat("agent")` 带 12 工具目录（:774-785）。
4. 成本从 `provider_requests.usage_json` 聚合，会话口径 GLOB `agent-<id>-*`（:608,786-792）。
5. 无 tool_calls → `finish_run("done")`；有则工具循环（:841-874）：`tools::execute`（tools.rs:400）先幂等登记 `agent_tool_calls`（ON CONFLICT 重放或 `tool_active`，:415-459）再分发。

工具目录（tools.rs:13-91）：workspace.list/read/write/edit、web.search/web.fetch（走 §9 抓取链）、memory.save/recall/delete、history.load/search、artifact.promote。写文件产物 `commit_artifact`（:258）：阶段/单元校验 → `lecture::validate_content` → `artifact_path`（:167，拒绝对对路径/父级/>6 层/非 json-md-txt/符号链接/越界）→ 原子写（tmp+previous 双 rename :218）→ `agent_artifacts`（expires_at=+24h）。
审批：memory.save/delete、artifact.promote 产生 `agent_approvals('pending')`；`approve_agent_request`（:686）CAS pending→approved 执行——promote 校验 sha256 与阶段未漂移（:792-803），讲义须 done，整版本拷贝 `learning_lecture_units` 并推进版本（:823-831）。

## 9. 网页检索与抓取（web/）

- `search`（web/mod.rs:300）：query 1-300B、limit 1-5 → `https://html.duckduckgo.com/html/`（:61）→ `document`（:139）循环：`resolve`（:71）URL ≤900B、query 拒 token/secret 键、DNS `to_socket_addrs` 后拒绝非公网（`ssrf_blocked`，复用 providers::openai::public_address :122）→ 禁代理+禁重定向客户端 → 手工跟随重定向 ≤3 次逐次重校验（:139-181）→ Content-Type 白名单（:195）→ 非 UTF-8 拒（:204）→ 1MB 上限（:213,227）→ `html::parse` 提取结果（html.rs:216-242）→ `candidate_url` 展开 DDG 跳转（:279-296）→ 逐条 `fetch` 真实抓取后才标记 verified（mod.rs:335-337）。
- `fetch`（:250）：`html::parse(body,false)` 剥脚本样式 → text/plain 保留原文 → 空拒 → `truncate` 256KB → sha256 内容哈希。

## 10. 判分与复习调度算法

**客观题**（practice/mod.rs:1053 起）：choice——answer 须为合法选项下标且等于期望（:1093-1103）；fill——`normalize`（去空白+lowercase :1045）后全等（:1104-1110）。
**主观题**（subjective.rs:126 起）：INSERT `practice_answers('grading')` + request_id=`practice-grade-{id}-1` → 线程 `grade`（:385）：题面+参考答案+rubric+学生作答 prompt → `stream_chat` 4096 → `evaluate`（:325）逐项 `score∈[0,1]×points` 求和并 clamp，`correct = score≥0.8`（:372）→ 二次 `validate_citations`。rubric 校验 2-6 项、points∈(0,1]、总和=1（:56）。
**共同尾部**：错题 upsert wrong_count+1；逐节点写 `learning_node_evidence`（kind practice/exam、evidence_key=`practice-answer-{answer_id}`，mod.rs:1133）；变式答对 → 原错题 mastered=1（variants.rs:106-133）；全部作答完 → set completed。
**模拟考交卷**（exams.rs:74）：幂等；评分中拒收（:112）；未答题逐题 0 分 + mistakes('未作答') + evidence(0)（:138-165）。
**复习调度**（review/mod.rs:398 grade，SM-2 简化、4 档无 q 因子）：
- 间隔（:419-428）：again=10 分钟；首轮 hard=2/good=5/easy=12 天；后续 hard=prev×1.2、good=prev×ease、easy=prev×ease×1.3。
- ease（:429-435）：again −0.2 / hard −0.15（下限 1.3）、easy +0.15（上限 3.0）；again 重置 repetitions=0、lapses+1。
- 并发防护：条件 UPDATE 带 `due_at<=?8`，仅一行生效（:438-447，测试 :768）。
- 证据映射：again/hard/good/easy = 0/0.6/0.8/1.0（:453-465）。

## 11. 备份、分享、同步、运维

- **导出**（backup/archive.rs:347）：停 sync → `.dtbackup` 扩展名校验 → RAII 临时目录 → `VACUUM INTO state.db` → 回填 local_users/security_quotas → `secure_delete=ON` 清三张敏感表再 VACUUM（:392-398）→ 收集三类文件（原件/插件清单/agent scratch 仅 json-md-txt）→ 写入前逐项哈希重验防 TOCTOU（:444-452）→ zip ≤2GB → `materials::atomic_export` + `backup.exported`。
- **预演/导入**（transfer.rs:1096）：预演只读统计；导入前停全部子系统（backup/commands.rs:47-52）；schema 双端比对（唯一容忍 focus_sessions.paused_at）→ `prepare_ids`（:199）：materials 按 sha256 复用已有 ID、shared_plans 按 token_hash 复用、其余 INTEGER 主键 MAX+1、文本主键 `imported-{n}`、kv 重编号、preferences 已有则跳过 → 外键拓扑排序（:515）→ `transform` 重写列与 `*_json` 内嵌 ID（:680）→ 单事务 import_rows + install_files（canonicalize+hard_link）→ 失败回滚删已装文件 → 收尾各模块 recover + `backup.imported(appendOnly:true)`。
- **分享**（sharing/mod.rs:263 create）：仅 done/degraded 方案、快照≤2MB；令牌 `dt_` 46 字符只存 SHA-256（token_hash）+ 根库 share_index；读取无登录时先查索引定位 owner，`with_user` 切库读取，仍未命中遍历 users/ 目录（≤10000）回填索引（:375-426）；revoked/expired 一律 `share_unavailable`。
- **同步**（sync/）：上传 = `providers::export_config`（脱敏，无 apiKey，测试 sync/tests.rs:223-225）→ PUT 历史对象+当前对象 → GET 回读双双比对（`sync_verify_failed`）→ 版本保留：list（S3 list-type=2 / WebDAV PROPFIND）按时间倒序 skip(10) 删除，复查 remaining>10 → `sync_retention_failed`（versions.rs:206-218）。调度线程 1s tick/60s 检查，auto_sync 且距上次上传 ≥24h 触发（scheduler.rs:16-43）。凭据 WebDAV 密码/S3 密钥存 keyring，库中 sync_config 脱敏（mod.rs:249-296）。
- **运维**（maintenance/）：`health` 读 PRAGMA integrity_check/foreign_key_check/journal_mode/page 统计/WAL 字节数（mod.rs:100-151）；孤儿扫描白名单=DB 记录的原件 + agent scratch + 有效插件清单 + 可识别临时目录（:210-346），缺失原件只报告；清理/VACUUM 前置 `OPERATION` 互斥 + 停全部任务 + 锁 store，结束必重启调度（commands.rs:39-73）。

## 12. 状态机汇总

| 实体（表.列） | 状态集 | 终态 | 恢复函数 |
|---|---|---|---|
| learning_plans.status | running/cancelling → done/degraded/failed/cancelled；interrupted | done/degraded/failed/cancelled | recover L/mod.rs:903 |
| learning_plan_steps.status | pending/running/done/failed/cancelled/interrupted | done/failed/cancelled | 同上 + begin_step 复用 |
| learning_lectures.status | empty/running/cancelling → done/failed/cancelled；interrupted | done/failed/cancelled | lecture::recover :766 |
| knowledge_sessions.status | running/cancelling → done/failed/cancelled；interrupted | done/failed/cancelled | K::recover :797 |
| practice_sets.status | generating/cancelling → ready/failed/cancelled；interrupted；ready→completed | ready/completed/failed/cancelled | practice::recover :1377 |
| practice_answers.status | grading/cancelling → done/failed/cancelled；interrupted | done/failed/cancelled | subjective::recover :652 |
| project_versions.status | running/cancelling → done/failed/cancelled；interrupted | done/failed/cancelled | projects::recover :571 |
| agent_runs.status | running/cancelling → done/cancelled/failed；interrupted | done/cancelled/failed | agent::recover :1011 |
| agent_approvals.status | pending → approved/cancelled/expired | approved/cancelled/expired | 过期由 promote 惰性置 :770-791 |
| provider_requests.status | running → done/cancelled/failed；interrupted | done/cancelled/failed | recover_chats P/mod.rs:1205 |
| provider_route_runs.status | running/cancelling → 终态；interrupted | done/failed/cancelled | routing::recover :1265 |
| provider_circuits.state | closed/open/half_open | — | recover :1267 |
| material_jobs.status | queued/parsing/cancelling → done/failed/cancelled | done/failed/cancelled | materials::recover :1073 |
| sync_jobs.status | running(stage:request/version/verify/retention)/cancelling → done/failed/cancelled；interrupted | done/failed/cancelled | sync::recover :799 |
| focus_sessions.status | running ⇄ paused → completed/cancelled | completed/cancelled | — |
| shared_plans（派生） | active → revoked/expired | revoked/expired | — |
| local_users.active | 1 ⇄ 0；软删 deleted_at | — | — |

通用规律：取消 = AtomicBool 置位 + status='cancelling' + `providers::cancel_chat` 上游中断三件套；重启 = `recover` 把 running→interrupted、cancelling→cancelled，永不自动重跑；用户 `resume` 显式续接，且 `providers::completed_chat(request_id)` 为真时复用已完成请求不重计费。

## 13. 并发模型总览

- **线程**：全部后台线程经 `security::thread_builder()`（SEC/mod.rs:87）创建以继承 principal（thread-local PRINCIPAL，:31-66；不继承则 Store 落到 owner 1，store.rs:68）。命名线程：`backend-startup`、`learning-plan-{id}`、`lecture-{id}`、`wiki-answer-{id}`、`practice-generate-{id}`、`practice-grade-{id}`、`project-feedback-{id}`、`agent-run-{id}`、`material-parse-{id}`、`sync-{id}`、调度线程。全部 catch_unwind，panic 映射 `task_panicked` 落失败态。
- **锁**：`BackendState.store: Mutex<Store>`（全局单连接）；`STORE_OPEN_LOCK`（打开互斥）；`WORKSPACE_SWITCH: RwLock`（切换写锁/命令读锁）；各域 `active: Mutex<HashMap<id, Arc<AtomicBool>>>` 取消注册表；maintenance `OPERATION`；sync 全局 `ACTIVE/NEXT_JOB`；startup `LOCATION_LOCK`（pointer 原子写）。
- **SQLite 并发**：WAL + busy_timeout 10s；写路径 `unchecked_transaction`；关键防重用 Immediate 事务（agent send :576、resume :937）或条件 UPDATE（review grade :438）。
- **停止协议**：`stop`/`stop_all` 置全部取消标志 + 取消全部上游请求 + 15s 轮询等待 active map 清空（learning L/mod.rs:844、providers P/mod.rs:1246、materials M/mod.rs:106、agent :969、sync :818）；重置（reset_application，backend/commands.rs:82-110）先停五类任务，DB reset 成功后才删原件备份与凭据。

## 14. 前端运行原理要点

- 启动：bootstrap 对 `startup_not_ready` 每 250ms 重试至 20s（useBackendState.ts:105-115）；认证错误码集合切 loginRequired（:6,78-83）。
- 实时渲染：页面按事件 kind 前缀 + 业务 id 过滤后调用对应 get 命令刷新（如 PlanGenerator.tsx:95-110 `plan.*` → getLearningPlan；`provider.chat_delta` 按 requestId 命中后 getProviderChat 拉增量 :111-118）。
- 媒体预览：MediaPlayer.tsx:19-36 `get_material_media_info` → 循环 `read_material_media_chunk(256KB)` → 拼 Blob → `crypto.subtle` SHA-256 与 info 比对 → objectURL 渲染，卸载 revoke。
- 主题/无障碍：preferences 经 `replace_collection('preferences')` 持久化，App.tsx:135-138 同步 `data-theme/large-text/high-contrast/reduce-motion` 到 `<html>`。

## 15. 观察到的边界情况

- reports 模块 `finish_focus`（reports/mod.rs:343-361）对 paused 行执行 complete/cancel 时 UPDATE 条件 `WHERE status='running'` 不命中但未检查受影响行数，仍发终态事件——暂停中完成/取消的会话行状态可能停留 paused 而事件已发出。
- `stream_provider_chat_with_tools`、`authenticate_access_token` 注册但前端不直接调用（前者是 agent 工具循环的内部通道，后者为 PAT 认证预留）。
