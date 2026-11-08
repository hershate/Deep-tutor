# 53 · SQL 事务与索引（第二十二轮）

> 审计范围：全部非 test .rs 的 prepare/query_row/execute/execute_batch 穷尽
> 核对（JOIN/WHERE、事务边界、索引覆盖、UPDATE/DELETE 守卫、LIMIT 语义、
> json_extract、UPSERT）。前端轮询频率实测在案（通知 30s/提醒 60s/dashboard
> 事件即时）。主审对三条热路径逐行亲验。**零 P2**——SQL 正确性面干净，
> 债务集中在热路径随历史无界增长的全表扫描与 N+1 连接。

## P3

### 53#1 预算护栏对 provider_requests（库中最大表）每次模型请求全表扫描

- 位置：`desktop/src-tauri/src/backend/providers/usage.rs:443-470`（budget_spend，主审亲验）；schema `providers/mod.rs:288-304`——**agent_session_id/created_at 均无索引**（仅 request_id PK）；调用点 `routing.rs:916`（每次路由请求）、`providers/mod.rs:1141`（每次直连）、`agent/mod.rs:881`（agent 每步）。
- **机理**：daily 口径 `date(r.created_at,?)=?` 函数包裹列且 created_at 无索引；session 口径 `agent_session_id=? OR (... GLOB ...)` 的 OR+GLOB 阻断任何索引合并——每次模型请求（含 25 步 agent 循环的每一步）全表扫描含全部 LLM 输出文本的最大表，延迟随历史线性增长。27#3 只消除了「读入内存」，未消除扫描本身。`agent/mod.rs:644` 的 session_output_tokens（同谓词）被每个 agent.* 事件触发的 get() 调用，流式期间高频放大。
- **影响**：模型重度用户的每次请求延迟持续劣化；叠加 44 号主线程事实（同步命令）表现为请求前 UI 卡顿放大。修复：`CREATE INDEX provider_requests_session ON provider_requests(agent_session_id)`（外键列尚无索引本身也值得补）+ created_at 索引，或预算改为累计表。

### 53#2 get_calendar 每次调用 84 次全表扫描——dashboard 事件热触发

- 位置：`desktop/src-tauri/src/backend/reports/mod.rs:965-969`（`(0..84).rev()` 循环内 `WHERE status='completed' AND ended_at >= ? AND < ?`，主审亲验）；索引仅 `(status, started_at)`（:184），**不覆盖 ended_at**。App.tsx:161-163 使复习评分/练习提交/任务变更每个事件都整跑 dashboard。修复：`CREATE INDEX focus_sessions_ended ON focus_sessions(status, ended_at)` 或 84 天合并单条 GROUP BY。

### 53#3 learning/knowledge 列表 N+1 每项新开一条 SQLite 连接——39#1 同族漏网

- 位置：`learning/mod.rs:88-90`（`store()` = `Store::new(path)`：PRAGMA×4 + WAL 检查 + schema 指纹 SHA256 + meta 读，主审亲验）、`:607-618`（list → 逐方案 get → 每方案开一条连接 + 3 类查询）；`knowledge/mod.rs:240/284-296` 同型（每会话一条，LIMIT 100）。list 被 dashboard/engagement::search/reports/planning 复用——一次 dashboard 最多 100 次开库。39#1 只修了 knowledge::search 的资料配对路径。修复：沿 39#1 模式做批量取回。

### 53#4 planning overview 读快照后 autocommit DELETE——并发 adopt/保存的毫秒窗误删新排期行

- 位置：`desktop/src-tauri/src/backend/planning/mod.rs:157-164`。`live_ids` 来自 :148 的 collection 快照；两条孤儿清理 DELETE 各自独立 autocommit。若 `replace_collection("tasks")` 或 `adopt`（单事务插任务+排期行）恰在快照与 DELETE 之间提交，新任务的排期行按「NOT IN 旧快照」被误删且不自愈。窗口毫秒级。修复：DELETE 与 live_ids 重读收进同一 IMMEDIATE 事务。

### 53#5 复习/练习统计聚合无索引全表扫（dashboard 热路径）

- `review/mod.rs:514-518`（review_history 无时间界全表 AVG，只增不减）；`reports/mod.rs:760`（reviewed_at 无索引）；`reports/mod.rs:759`（practice_answers 仅 set_id 索引，status+created_at 全表扫）。修复：`review_history(reviewed_at)`、`practice_answers(status, created_at)`。

### 53#6 focus/排期 N+1 与 task_id 缺索引

- `reports/mod.rs:740`：每条已完成 focus 行点查 `learning_plan_schedule_tasks WHERE task_id=?`——该表 PK 为 (plan_id, task_id)，**task_id 无前缀索引** → 每行全表扫（N+1）。`:574/:723` focus 聚合无时间下推全部历史进内存。修复：`CREATE INDEX lpst_task ON learning_plan_schedule_tasks(task_id)`。

### 53#7 usage 台账全表读 + 每行费率点查 N+1

- `providers/usage.rs:297-307`（entries 无 WHERE 读全表 provider_requests）、`:264`（每行点查 usage_rates）、`:330-353`（overview 再以 31 天×全部条目内存过滤）。usage 页与 export_csv 触发。可改 JOIN 单查。

### 53#8 community_replies 无 post_id 索引——帖子列表二次方

- `community/mod.rs:339`（每帖 `WHERE post_id=? ORDER BY id`，建表 :191-197 仅 PK+FK）；外层配额 500 帖 × 每回复点查；帖子删除的 CASCADE 同样全表找子行。修复：`CREATE INDEX community_replies_post ON community_replies(post_id)`。

## 信息性

- **53#9** pause/resume_focus（reports/mod.rs:439/465）UPDATE 带状态守卫但未检查 changed——竞态窗内静默 0 行仍发 focus.paused/resumed 事件（对照 finish_focus :372-375 有检查）。
- **53#10** materials 转换回调每页进度全量重写 resources 集合（DELETE 全 namespace + 重插，materials/mod.rs:807→:359）——大 PDF 解析期写放大。
- **53#11** 冷路径全表扫：plugins deliveries OR 通配+跨插件 event_id 排序无索引；agent_messages run_id 无索引（每步一次）；notifications 无 created_at/read_at 索引且无清理路径（30s 轮询但表小）。
- **53#12** security display_name COLLATE NOCASE 仅 ASCII 折叠——中文重名检测实为精确匹配（功能可用，语义与「不区分大小写」预期不符）。

## 正向结论（核对过、无问题）

- **JOIN/WHERE**：全部多表 JOIN 按 PK/UNIQUE 关联无行放大；learning steps 六个 COALESCE 子查询均走前缀索引+UNIQUE；两处 NOT IN 均 NULL 安全（CAST 永不 NULL、json_each value 恒 INTEGER）。
- **事务**：IMMEDIATE/DEFERRED 无嵌套混用；「先提交再写」断链处均有注释防护且由约束兜底（adopt 撞 PK 回滚）；多语句写全部单事务且 changed==0 有校验（review/practice/agent/projects/materials）。
- **UPDATE/DELETE**：全部按 PK + 状态守卫；**无任何按 title/text 匹配的破坏性语句**；除 53#9 外 changed 均检查。
- **LIMIT/分页**：所有 LIMIT 伴随确定性 ORDER BY；events_since 多取 1 判 has_more 的截断正确；全部游标式无 OFFSET。
- **json_extract 热路径**：事件回放（150ms 轮询）与 store.rs:210 表达式索引文本完全一致可命中——27#2 的索引设计有效。
- **UPSERT**：全部 ON CONFLICT 目标与实际约束一致；INSERT OR IGNORE 均为幂等去重/种子且 OR REPLACE 三处目标表无 FK 子表无级联副作用。
