# 53 · SQL 事务与索引（第二十二轮）

> 审计范围：全部非 test .rs 的 prepare/query_row/execute/execute_batch 穷尽
> 核对（JOIN/WHERE、事务边界、索引覆盖、UPDATE/DELETE 守卫、LIMIT 语义、
> json_extract、UPSERT）。前端轮询频率实测在案（通知 30s/提醒 60s/dashboard
> 事件即时）。主审对三条热路径逐行亲验。**零 P2**——SQL 正确性面干净，
> 债务集中在热路径随历史无界增长的全表扫描与 N+1 连接。

## P3

### 53#1 预算护栏对 provider_requests（库中最大表）每次模型请求全表扫描

- 位置：`desktop/src-tauri/src/backend/providers/usage.rs:443-470`（budget_spend，主审亲验）；schema `providers/mod.rs:288-304`——**agent_session_id/created_at 均无索引**（仅 request_id PK）；调用点 `routing.rs:916`（每次路由请求）、`providers/mod.rs:1141`（每次直连）、`agent/mod.rs:881`（agent 每步）。
- **传递链条（机理）**：① 每次模型请求进入预算判定 → ② daily 口径 `date(r.created_at,?)=?` 函数包裹列且 created_at 无索引；session 口径 `agent_session_id=? OR (... GLOB ...)` 的 OR+GLOB 阻断任何索引合并——每次模型请求（含 25 步 agent 循环的每一步）全表扫描含全部 LLM 输出文本的最大表，延迟随历史线性增长。27#3 只消除了「读入内存」，未消除扫描本身。`agent/mod.rs:644` 的 session_output_tokens（同谓词）被每个 agent.* 事件触发的 get() 调用，流式期间高频放大。
- **影响范围**：模型重度用户的每次请求延迟随 provider_requests 行数线性劣化——25 步 agent 循环每步扫一次、流式期间 session_output_tokens（同谓词）再被每个 agent.* 事件放大；叠加 44 号主线程事实（同步命令）表现为请求前 UI 卡顿持续放大。轻度用户（数十条请求）无感。修复：`CREATE INDEX provider_requests_session ON provider_requests(agent_session_id)`（外键列尚无索引本身也值得补）+ created_at 索引，或预算改为累计表。

### 53#2 get_calendar 每次调用 84 次全表扫描——dashboard 事件热触发

- 位置：`desktop/src-tauri/src/backend/reports/mod.rs:965-969`（主审亲验）；索引仅 `(status, started_at)`（:184）。
- **传递链条**：① 复习评分/练习提交/任务变更产生事件 → ② App.tsx:161-163 每个事件触发 `dashboard()` → ③ `get_calendar` 在 `(0..84).rev()` 循环内逐日执行 `WHERE status='completed' AND ended_at >= ? AND < ?` → ④ 现有索引不覆盖 ended_at → 84 次全表扫描+临时聚合，同步命令在主线程执行（叠加 44 号）。
- **影响范围**：focus_sessions 行数随使用线性增长的用户（重度专注者数千行）——每次评分/提交的 dashboard 延迟随之上升，主线程占用造成可感顿挫。冷启动用户无感。修复：`(status, ended_at)` 索引或合并单条 GROUP BY。

### 53#3 learning/knowledge 列表 N+1 每项新开一条 SQLite 连接——39#1 同族漏网

- 位置：`learning/mod.rs:88-90`（`store()`=Store::new：PRAGMA×4+WAL+指纹 SHA256+meta，主审亲验）、`:607-618`；`knowledge/mod.rs:240/284-296` 同型。
- **传递链条**：① 任意 dashboard/搜索/报告/周计划请求 → ② `learning::list` 逐方案调 `get` → ③ 每方案 `Store::new(database_path)` 开一条全新 SQLite 连接（重跑 PRAGMA、WAL 检查、schema 指纹 SHA256、meta 读）→ ④ 100 个存档方案 = 100 次开库 + 300 组查询；knowledge 列表同型（每会话一条，LIMIT 100）。
- **影响范围**：方案/知识会话存档多的用户——列表类页面与 dashboard 的延迟随存档数线性放大（39#1 已证明该模式在百份资料时可感）。39#1 只修了 knowledge::search 的资料配对路径，本族是同型漏网。修复：沿 39#1 的批量取回模式。

### 53#4 planning overview 读快照后 autocommit DELETE——并发 adopt/保存的毫秒窗误删新排期行

- 位置：`desktop/src-tauri/src/backend/planning/mod.rs:157-164`。
- **传递链条**：① 周视图 overview 读 `collection("tasks")` 得 live_ids 快照 → ② 并发的 `replace_collection("tasks")` 或 `adopt`（单事务插入新任务 kv 行 + 排期行）恰在快照之后提交 → ③ 两条孤儿清理 `DELETE … WHERE task_id NOT IN (旧快照)` 以各自独立的 autocommit 执行 → ④ 新任务的排期行被判定为孤儿而误删——且下轮 overview 不会恢复（任务 kv 行还在，需重新 adopt/排期）。
- **影响范围**：恰在周计划页浏览时执行采纳/保存的用户（毫秒窗）；后果为该新任务的排期行静默丢失（任务本身仍在，日期需重设）。无数据损坏到不可恢复。修复：DELETE 与 live_ids 重读收进同一 IMMEDIATE 事务。

### 53#5 复习/练习统计聚合无索引全表扫（dashboard 热路径）

- 位置：`review/mod.rs:514-518`、`reports/mod.rs:759-760`。
- **传递链条**：① 每次 dashboard 事件 → ② `stats()` 对 review_history 全表 AVG（无时间界，表只增不减）+ reports 按窗口过滤（reviewed_at/created_at 均无索引）→ ③ practice_answers 仅 set_id 索引，status+created_at 过滤全表扫 → ④ 扫描量随历史线性增长，与 53#2 同在 dashboard 热路径叠加。
- **影响范围**：长期使用（数百次评分/作答）后 dashboard 逐次变慢；短历史用户无感。修复：`review_history(reviewed_at)`、`practice_answers(status, created_at)` 两个索引。

### 53#6 focus/排期 N+1 与 task_id 缺索引

- 位置：`reports/mod.rs:740`（点查）、`:574/:723`（聚合无时间下推）。
- **传递链条**：① 报告计算专注-任务关联 → ② 每条已完成 focus 行执行 `SELECT plan_id FROM learning_plan_schedule_tasks WHERE task_id=? LIMIT 1` → ③ 该表 PK 为 (plan_id, task_id)，task_id 无前缀索引 → 每行全表扫（行数×表长）→ ④ 专注历史长的用户报告页显著变慢。
- **影响范围**：专注记录多且有排期任务的用户（报告/周报路径）；修复一个索引即消。

### 53#7 usage 台账全表读 + 每行费率点查 N+1

- 位置：`providers/usage.rs:297-307`（全表）、`:264`（点查）、`:330-353`（内存过滤）。
- **传递链条**：① 打开用量页或导出 CSV → ② `entries()` 无 WHERE 读取全部 provider_requests（含全部 LLM 输出文本的反序列化）→ ③ 每行点查 usage_rates 费率 → ④ overview 再以 31 天×全部条目做内存日期过滤。
- **影响范围**：模型请求数多的用户打开用量页/导出 CSV 的耗时线性劣化（provider_requests 是最大表）；主线程执行（44 号叠加）期间 UI 冻结。修复：JOIN 单查 + 分页。

### 53#8 community_replies 无 post_id 索引——帖子列表二次方

- 位置：`community/mod.rs:339`（每帖点查）；建表 :191-197 仅 PK+FK。
- **传递链条**：① 打开社区页/搜索 → ② posts() 外层取至多 500 帖 → ③ 每帖执行 `WHERE post_id=? ORDER BY id` 无索引全表扫 replies → ④ 帖子删除时 FK CASCADE 同样全表找子行。
- **影响范围**：社区重度使用（帖子/回复多）的用户——社区页打开与搜索随数据量二次方变慢；帖子删除偶发卡顿。修复：`community_replies_post(post_id)` 一个索引。

## 信息性

- **53#9** pause/resume_focus 竞态窗多余事件。链条：① pause 请求先 SELECT id 再 UPDATE …AND status='running'（reports/mod.rs:439/465）→ ② 窗口内并发 finish 已把行置 completed → ③ UPDATE 静默 0 行但未检查 changed → ④ 照发 focus.paused 事件并返回旧会话。影响：一条与会话终态矛盾的事件（前端靠下一事件自愈）；低概率毫秒窗。对照 finish_focus（:372-375）有 changed 检查。
- **53#10** materials 进度回调写放大。链条：① 大 PDF 逐页解析 → ② 每页进度回调 transition（materials/mod.rs:807）→ ③ 每次都 sync_resources：DELETE 全 resources namespace + 全量重插（:359）并提交。影响：解析期间 resources 集合按页数整写 N 次（磁盘与 WAL 放大），功能正确；节流即可。
- **53#11** 冷路径全表扫三处。链条与影响：plugins deliveries 列表 `(?1 IS NULL OR plugin_id=?1)` OR 通配杀索引且跨插件按 event_id 排序无索引（插件多、事件多时慢，低频页面）；agent_messages `WHERE run_id=?` 无索引（agent 每步一次全表扫，25 步×消息量）；notifications 无索引且无清理路径（30s 轮询全表扫+排序，表按日增长，长期膨胀后轮询变慢——兼有容量问题）。
- **53#12** display_name 重名检测语义。链条：`COLLATE NOCASE` 仅折叠 ASCII 大小写（security/mod.rs:1028）→ 中文「张三」与「张三 」等仅空白差异也被视为不同/英文大小写视为同。影响：本地重名提示对中文用户实为精确匹配（提示漏报），无功能破坏。

## 正向结论（核对过、无问题）

- **JOIN/WHERE**：全部多表 JOIN 按 PK/UNIQUE 关联无行放大；learning steps 六个 COALESCE 子查询均走前缀索引+UNIQUE；两处 NOT IN 均 NULL 安全（CAST 永不 NULL、json_each value 恒 INTEGER）。
- **事务**：IMMEDIATE/DEFERRED 无嵌套混用；「先提交再写」断链处均有注释防护且由约束兜底（adopt 撞 PK 回滚）；多语句写全部单事务且 changed==0 有校验（review/practice/agent/projects/materials）。
- **UPDATE/DELETE**：全部按 PK + 状态守卫；**无任何按 title/text 匹配的破坏性语句**；除 53#9 外 changed 均检查。
- **LIMIT/分页**：所有 LIMIT 伴随确定性 ORDER BY；events_since 多取 1 判 has_more 的截断正确；全部游标式无 OFFSET。
- **json_extract 热路径**：事件回放（150ms 轮询）与 store.rs:210 表达式索引文本完全一致可命中——27#2 的索引设计有效。
- **UPSERT**：全部 ON CONFLICT 目标与实际约束一致；INSERT OR IGNORE 均为幂等去重/种子且 OR REPLACE 三处目标表无 FK 子表无级联副作用。
