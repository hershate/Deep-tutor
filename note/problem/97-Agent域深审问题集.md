# 97 号：Agent 域深审问题集（第三十四轮·全面审计）

审计基线：`454fa4a`。审计面：agent\ 全域深审——run_loop 步骤循环的消息窗口、工具结果
持久化/事件面、产物审批状态机、run 终态守卫、会话删除竞态、取消检查点覆盖。90#5（中断
步骤重复计费）与 92 号（run_started 隐性耦合）已报勿重复。共 1 P2 + 2 P3 + 5 信息。

## P2

### 97#1 run_loop 运行中 messages 向量无窗口裁剪——多步工具循环中途命中 validate_messages 上限，陷入「恢复→跑一步→failed」循环且每次白付模型费

- 位置：`desktop/src-tauri/src/backend/agent/mod.rs:862`（`let mut messages = history(&session)?` 仅 run 启动时构建一次；对照 :730-754 的 34#8 裁剪只作用于 run 起始历史）、`:1028`（循环内 push assistant）、`:1061`（push tool）；上限在 `providers/openai.rs:641`（`messages.len() > 64` → invalid_messages）与 `:667`（总字节 > 256KB → context_too_large）。
- 传递链条：①会话重放历史已收敛到 62 条（+2 引导 = 64，贴着上限）或全新会话高密度工具步累积 → ②第 1 步 stream_chat 恰好以 64 条通过，随后每步 push 1 条 assistant + N 条 tool，向量变为 65+ 条（或 25 步 × 16KB 工具结果累计超 256KB） → ③第 2 步 stream_chat 命中 `invalid_messages`/`context_too_large`，run 直接 failed → ④用户 resume 后 history() 重新裁回 62 条，再推进一步又失败——每次恢复恰好执行一次模型请求即死，每次都产生真实计费。tests.rs:1152-1192 的长历史测试只覆盖单步纯文本完成，未覆盖循环内增长。
- 影响范围：长会话（重放 ≥62 条）一旦触发工具调用，第二个模型请求必失败；高密度工具步（单步 8 调用，第 8 步达 75 条）或大工具输出的全新会话中途失败。34#8 的修复在 run 内部失效，属功能性损坏 + 重复无效计费。
- 修复方向：每步循环顶部对 `messages` 复用 history() 的尾部窗口与字节预算裁剪（保持 23#9 的 tool 声明自洽性重裁），或改「丢弃最旧非 system 消息」的滑动窗口；补多步工具循环的回归测试。

## P3

### 97#2 工具结果无大小上限即全量持久化并整包进事件 payload——持久层/事件面/前端刷新路径三级放大

- 位置：`desktop/src-tauri/src/backend/agent/mod.rs:1046-1054`（完整结果原样入库；16KB 截断只作用于发模型的那份 :1064）；`agent/tools.rs:691-697`（`agent.tool_completed` 事件 payload 内嵌完整 result，无截断）；`tools.rs:620-642`（history.load limit clamp(1,100) 全内容读回）；`web/mod.rs:17`（单页正文 256KB 进入工具结果）；前端 `desktop/src/backend/useAgent.ts:169`（每个 agent.* 事件触发 getAgentSession 全量刷新）。
- 传递链条：①web.fetch 抓取 256KB 正文 → ②结果 JSON 全量写入 agent_messages.content，并以同量级经事件序列化发往 WebView → ③模型随后调 history.load(limit=100)，把含此前 256KB 工具消息的最多 100 条再全量读出，其结果本身又达数 MB，再次落库 + 发事件（存储放大） → ④前端每个事件再拉一次全量会话。MODEL_TOOL_CONTENT_LIMIT=16KB 只保护模型上下文，其余三层无任何上限。
- 影响范围：DB 行与 IPC payload 膨胀至 MB 级；事件表与备份体积放大；前端内存压力（80 号已修的渲染面之外新增的消息面放大）。
- 修复方向：工具结果入库与入事件前统一上限（如 64KB，超出截断并注明可用 workspace.read 分段读取）；tool_completed 事件只带摘要与 toolCallId；get() 消息内容按需截断或提供分页详情。

### 97#3 同一产物可产生多条 pending 审批——重复批准造成双重版本提升，promote 后取消另一条把状态翻成 cancelled

- 位置：`desktop/src-tauri/src/backend/agent/tools.rs:363`（approval() 每次 INSERT 新 pending 行，无同 kind+payload 去重）、`:838-849`（promote 陈旧判定只比 sha256/stage/unit，不含产物 status）、`:906-914`（cancel_approval 无条件 `UPDATE agent_artifacts SET status='cancelled'`）。
- 传递链条：①模型在两个步骤各调一次 artifact.promote（或重试）→ 产生两条 pending 审批 → ②用户批准第一条：promote 成功、讲义 version+1、产物置 'promoted' → ③批准第二条：sha/stage/unit 均未变，检查全部通过 → 再次 INSERT 新版本行、version+2（相同内容重复提升）；若改为取消第二条 → 产物行由 'promoted' 翻 'cancelled'，而讲义版本已实际提升——状态与事实相反。
- 影响范围：讲义版本链被无意义重复推进；产物状态机出现 promoted→cancelled 非法转移；UI 展示与真实提升历史脱节。单条审批自身的幂等由 resolve_pending 的 pending 守卫保证（核实通过），破口在「同产物的不同审批行」。
- 修复方向：请求审批前按 kind+payload 查重复用 pending 行；promote 前校验 `artifact.status != 'promoted'`；cancel_approval 仅在产物仍为 committed 时置 cancelled。

## 信息

### 97#4 commit_artifact 覆盖同名时无条件把行状态重置回 committed（promoted→committed 非单调转移）

- 位置：`desktop/src-tauri/src/backend/agent/tools.rs:304-317`（UPDATE … SET status='committed' WHERE id 无条件）。
- 传递链条：①产物已提升（status='promoted'）→ ②workspace.write(overwrite=true) 或 workspace.edit 命中同 path 的 existing 行 → ③状态覆盖回 'committed'，产物视图回退为草稿，提升痕迹只存于 learning_lecture_versions。
- 影响范围：与 97#3 叠加造成状态机往返混乱；单独看是「重新起草」的合理语义但未区分「提升过的路径被复用」。
- 修复方向：提升后冻结该 path（复用要求新 path），或覆盖 promoted 行时新建行保留历史。

### 97#5 finish_run 无状态守卫，可把 cancelling 覆写为 done——run_cancel_requested 后紧跟 run_completed 的事件矛盾

- 位置：`desktop/src-tauri/src/backend/agent/mod.rs:830-853`（UPDATE 无 status 条件；对照 cancel() 在 :1097-1100 有守卫）；纯文本终答路径 `:1008-1009`（tool_calls 为空直接终态，此前仅 tool_calls 非空分支查取消标志 :957-980）。
- 传递链条：①cancel() 在流式响应尾段落库 running→cancelling 并返回 cancelling → ②工作线程此刻已拿到完整响应且 tool_calls 为空，不再检查标志 → ③finish_run('done') 覆写 cancelling → ④前端先收 run_cancel_requested 再收 run_completed，run_cancelled 永不出现；cancel 命令返回值与终态矛盾。
- 影响范围：毫秒级窗口的取消语义/事件序列不一致；因输出已实际计费，'done' 结果本身可辩护，主要危害是状态与事件矛盾。
- 修复方向：finish_run 加 `WHERE status IN ('running','cancelling')`，done 分支遇 cancelling 时落 cancelled；或终答前补一次标志检查。

### 97#6 delete_session 的运行检查在事务外，与 send() 构成 TOCTOU——删除后仍产生模型费用与悬空事件

- 位置：`desktop/src-tauri/src/backend/agent/mod.rs:1266-1276`（检查用事务外普通读，之后才开 unchecked_transaction DELETE）；对照 send() :602-632（IMMEDIATE 事务内查+插）。
- 传递链条：①delete 读 running=false → ②send() 并发以 IMMEDIATE 事务插入 run 并 launch 工作线程 → ③delete 提交后 FK CASCADE 删掉 run 行；工作线程继续：下一轮 get_run → not_found → finish_run 更新 0 行却仍发 run_failed（悬空 runId 事件），在途模型请求照常完成并计费（provider_requests.agent_session_id 无 FK，账单行残留悬空引用），随后 agent-workspaces/<id> 目录被删导致工具写失败。
- 影响范围：低概率竞态；删除会话后仍产生模型费用与悬空事件，前端报「Agent 会话不存在」横幅。
- 修复方向：把运行检查并入 DELETE 同一 IMMEDIATE 事务（按 DELETE 行数/条件判定），或删除时持有 state.active 互斥。

### 97#7 工具执行期间无取消检查点——最坏约 2 分钟 run 停留 cancelling 且不可 resume/send

- 位置：`desktop/src-tauri/src/backend/agent/tools.rs:493-669`（execute 内不查取消标志；取消检查仅在调用前 mod.rs:1036-1044）；最坏路径 web.search 10 次候选验证 × 12s 超时（web/mod.rs:160/370-380）。
- 传递链条：①用户取消 → run 翻 cancelling → ②当前工具继续执行至自然结束（最坏 ≈2 分钟） → ③cancelling 状态下 resume/send 均被拦截，只能等待或重启走 recover()。
- 影响范围：有界滞后（非泄漏）；取消响应性差。
- 修复方向：execute 调用后立即复查标志（与调用前检查对称）。

### 97#8 维护 purge 后 approve 报 not_found，pending 审批行残留

- 位置：`maintenance/mod.rs:564-581`（物理删除过期 artifact 行）之后，approve→promote 在 `agent/tools.rs:815` 报 not_found 且不 resolve 审批行（promote 自身的 24h 过期路径会 resolve，但产物行已被删的路径不会）。
- 传递链条：①过期产物被清理 → ②用户批准对应审批 → promote 报 not_found，审批行仍 pending → ③需用户手动取消才消失。
- 影响范围：轻微；审批列表残留死条目。
- 修复方向：approve 的 not_found 分支同步 resolve 审批为 'expired'。

## 核实通过项（免后续复查）

- 工具输入校验（string/number/index/memory_key/value、call.id≤128、arguments≤64KB、非对象 JSON 拒绝）；web.search limit 越界由 web 层 (1..=5) 兜底。
- 路径构造：Component::Normal 白名单 + ≤6 层 + json/md/txt 扩展名 + 父目录 canonicalize 边界 + symlink 拒绝 + 128 字节上限；atomic_write 临时/备份/失败回滚。
- web 工具的 SSRF/凭据/重定向/编码/大小五道闸（public_address、token 参数黑名单、重定向≤3 且逐跳重校验、UTF-8、1MB/256KB）。
- 审批过期判定（24h → resolve 'expired' + artifact 'expired' + approval_expired 事件）；审批不阻塞 run；审批通过不触发模型计费；同一审批行幂等（resolve_pending pending 守卫）。
- 会话数量上限 200（配额，IMMEDIATE 事务内计数）；token 预算按 agent_session_id 聚合 + GLOB 兜底，删会话不影响全局日预算。
- 删会话五表 FK CASCADE + scratch 目录删除（失败报 cleanup_failed）；reset 清整个 agent-workspaces（65#2）；maintenance purge + 孤儿扫描（65#1）。
- 25 步计数（completed 判定重试不耗步数、attempt 递增隔离请求 id）；单步 8 调用上限；已知中断检查点（866-874/957-980/1036-1044）覆盖属实。
- 事件面：message_saved/artifact_committed/approval_requested/run_started 均含 sessionId；无 sessionId 的事件经 runOwners+rememberRuns 兜底；单 run 事件由单工作线程顺序发射，未见重复发射路径（execute 结果提交带 status='running' 守卫；重放命中已存结果直接返回不发事件）。
