# 101 号：Agent 与学习练习域回归问题集（第三十五轮·修复波回归审计）

审计基线：`b57f260a`（1.1.5）。审计面：1.1.5 修复波中 97#1-97#8（agent 域）、98#1-98#7
（学习练习项目域）修复的回归审计——每条修复是否真正闭合、是否引入新缺陷、是否留下同类
入口漏改。97#1 窗口重裁、97#2 三处一致落库、97#3 主路径、97#5-97#8、98#3/98#4/98#5/98#7
主路径均已核实通过（见文末）。101#1/101#2 经临时探针测试实证后回退（工作树干净）。
共 7 P3 + 1 信息。

## P3

### 101#1 97#4 的 promoted 检查发生在文件写入之后——报错时 scratch 文件已被覆盖，产物行哈希永久失配（探针实证）

- 位置：`desktop/src-tauri/src/backend/agent/tools.rs:298`（`atomic_write(&target, &bytes)?` 先执行）、`:312-320`（`status == "promoted"` 才返回 `artifact_promoted`，晚于写盘）。
- 传递链条：①Agent 对已提升（promoted）路径调 `workspace.write` 且 `overwrite=true`（「把已提升的讲义再改进」是合理动机）→ ②`atomic_write` 先把 scratch 文件替换为新草稿内容 → ③随后才命中 promoted 检查返回错误，DB 行仍是 promoted + 旧 sha256 → ④此后任何 `workspace.read artifact:<id>` / `artifact.promote` 走 `read_artifact`（tools.rs:347-355）校验文件哈希 → 永久报 `artifact_modified_externally`，该产物不可读不可提升；新草稿只存在于无行指向的孤儿文件。修复前该路径会一致地 UPDATE 行哈希，不产生失配——此缺陷由 97#4 的新早退引入。
- 影响范围：所有已提升产物的二次写入；正式讲义（lecture 版本表）不受影响，但该产物从此卡死，报错文案（「已被外部修改」）误导排障。
- 探针实证：`rejected: artifact_promoted`，`db_sha=72919d16… file_sha=3e0c19a8… file_changed=true`，随后 `workspace.read → artifact_modified_externally`。
- 修复方向：把 existing 行查询与 promoted 检查移到 `atomic_write` 之前；或报错前从 `content_json` 恢复原文件。

### 101#2 97#3 的 promote 拒绝分支不 resolve 审批——产物已 promoted/cancelled 后的再次提升审批成为永久死 pending（探针实证）

- 位置：`desktop/src-tauri/src/backend/agent/tools.rs:887-895`（`artifact_not_committed` 分支直接 return，无 `resolve_pending`；对照上方 not_found 分支 :873-884 有 97#8 的对称处理）；`tools.rs:377-389`（`approval()` 去重只复用 `status='pending'` 行）。
- 传递链条：①产物 P 提升成功（审批 A1=approved，P=promoted）→ ②模型再次 `artifact.promote` P——A1 已非 pending，去重不命中，新建审批 A2=pending → ③用户在 UI 批准 A2 → `promote()` 命中 `artifact_not_committed` 报错 → ④A2 永远停留 pending：审批卡片反复「批准失败」，只能手动取消。cancel 后模型重试再批准的路径同理（产物 status='cancelled'）。这正是 97#8 要消灭的「死 pending 条目」在另一分支重现。
- 影响范围：Agent 重复请求提升 + 用户批准的正常组合即可触发；审批列表残留不可完成的卡片。
- 探针实证：`second promotion: {approvalId:2, status:approval_required}` → `approve error: artifact_not_committed` → `approval_status after failed approve: pending`。
- 修复方向：该分支同步 `resolve_pending(approval_id, "expired", …)` 并发 `agent.approval_expired` 事件（与 97#8 的 purge 分支对称）；或 `approval()` 在产物非 committed 时直接拒绝建审批。

### 101#3 97#2 截断使模型可见内容从「前 16KB」缩水到「2048 字符预览」——大结果工具调用的回答质量回归

- 位置：`desktop/src-tauri/src/backend/agent/tools.rs:433-450`（`cap_tool_result` 把 >64KB 结果整体替换为 2048 字符头部预览）、`tools.rs:728`（落库/事件前替换）、`agent/mod.rs:1122-1129`（`truncate_for_model(&content)` 现在作用于已替换后的小对象）。
- 传递链条：①模型 `web.fetch` 一个 >64KB 正文的页面（web/mod.rs:17 单页允许至 256KB）→ ②`cap_tool_result` 把整个结果替换为 ≈2-6KB 的预览对象 → ③run_loop 把该对象塞进上下文——修复前模型能看到完整结果的前 16KB（truncate_for_model 对原始内容截断）→ ④模型基于极少量正文回答质量下降；notice 建议「缩小读取范围后重试」，但 `web.fetch`/`workspace.read` 均无范围参数（仅 history.load 有 limit），模型重复抓取同一页面反复命中同一截断，白耗步数与预算。`truncate_for_model` 追加的「完整结果已保存在会话记录中」对超限结果也不再真实（全量不再入库）。
- 影响范围：仅 >64KB 的工具结果（大网页抓取、大方案读取）；≤64KB 结果行为不变。写库/事件/消息三处一致性完好（核实通过），此条是模型侧可见内容的回归。
- 修复方向：cap 只作用于持久层/事件副本，模型份额沿用原 16KB 截断；或 preview 提升到 16KB 并让 truncate_for_model 文案对超限结果如实。

### 101#4 98#1 未覆盖知识图谱 evidence 旁路——deferred 考试逐题提交即写真实得分，考试中可从掌握度反推对错

- 位置：`desktop/src-tauri/src/backend/practice/mod.rs:1262-1269`（`if set.feedback_mode != "hints"` 即写 `learning_node_evidence`，score 为真实 0/1；考试 mode 强制 deferred，practice/mod.rs:582）、`practice/subjective.rs:489-498`（主观题同口径）、`learning/knowledge.rs:50`（progress 聚合全部 evidence，无考试进行中过滤）。
- 传递链条：①模拟考试承诺「交卷后统一反馈」→ ②考试中每提交一题，客/主观路径都立即 INSERT/UPDATE evidence（kind='exam'，真实得分）→ ③学生同窗切到知识图谱页 → progress 全历史聚合，「N 项测评记录」与节点掌握度随答题实时变化 → ④对错信号在交卷前泄露——与 31#12 为 hints 模式跳过 evidence 写入的理由完全同源，98#1 只堵了 mistakes 读面。
- 影响范围：所有模拟考试（exam 必为 deferred）；只泄露对错，不泄露答案/解析。
- 修复方向：与 hints 同法——考试未 completed 时延迟/跳过 evidence 写入（finalize 时补写），或 progress 聚合过滤未交卷 exam 集合的证据。

### 101#5 98#1 的「交卷后错题恢复可见」依赖 finalize，而到期惰性收卷只挂在 get_practice_set——到期后不再打开卷面的考试，错题本/导出长期缺该卷错题

- 位置：`desktop/src-tauri/src/backend/practice/mod.rs:1357`（过滤条件 `mode='exam' AND status != 'completed'`）、`mod.rs:1042-1064`（`lazy_finalize_expired_exam`）、`practice/commands.rs:25`（全仓唯一触发点在 `get_practice_set`）。
- 传递链条：①学生答题（错题行已落库）后未点交卷，deadline 过后不再打开该卷详情 → ②惰性收卷只在读取卷详情时触发，`list_practice_mistakes`/`export_practice_mistakes_file` 不触发 → ③该 set 永远停留 `status='ready'`，98#1 过滤持续生效 → ④错题本/导出一直看不到这场考试的错题；学生需自行想到「重新打开那张卷」才能让错题出现。
- 影响范围：到期未交且不再打开详情的考试；数据未丢，仅可见性被无关操作阻塞。
- 修复方向：`mistakes()`/`export_mistakes()` 前对到期考试批量惰性收卷（同 13#24 语义），或过滤条件改判 `exam_deadline` 已过即放行。

### 101#6 98#2 前缀查重与变式生成语义冲突——变式题保留题干主干即整批拒绝，错误提示不指明重复

- 位置：`desktop/src-tauri/src/backend/practice/mod.rs:836-865`（`seen` 预填 historical 后 `!seen.insert(prompt 前 80 字)` 即整批 `invalid_model_output`；变式原题全等比对在 :847-849 保留）、`:722-732`（historical=同 plan 全部集合最新 200 题，含变式的原错题）。
- 传递链条：①错题本「生成变式」→ 负约束要求「禁止相同或近似题」，变式 prompt 同时要求「保持原考点与原题型，改变设问情境/数值/论证角度」→ ②模型按变式惯例保留题干主干（短题干 <80 字时前 80 字=全题干；材料引用类长题干开头相同）→ ③`seen.insert` 失败 → 整批拒绝 `invalid_model_output` → ④错误消息不提「重复」，用户重试无指引，变式功能可用性下降；批内合法近似题（同材料开头两道阅读题）同样被误杀。
- 影响范围：错题变式生成与同材料多题生成；旧逻辑对变式只做原题全等比对。
- 修复方向：变式请求把原错题从 seen 预填排除（保留全等比对）；整批拒绝降级为「丢弃重复、按数量校验提示重生成」，或错误消息显式说明与已有题目重复。

### 101#7 98#2 去重窗口只覆盖最近 200 题，且同 plan 并发生成互不查重

- 位置：`desktop/src-tauri/src/backend/practice/mod.rs:724`（`ORDER BY q.id DESC LIMIT 200`）。
- 传递链条：①plan 历史题超过 200 道（多次生成+考试+变式，重度学习可达）→ ②最老的题既不在负约束提示也不在 seen → ③模型照抄老题 → 通过校验写入，跨集合重复题复现（98#2 修复目标落空于长尾）→ ④另一同 plan 集合并发生成时，两个 worker 都在对方写入前读 historical，互相盲区。
- 影响范围：>200 题的长寿 plan 与并发生成场景；主路径（≤200 题、串行）有效。
- 修复方向：写入事务内按 `set_id != 当前集` 再查一次前缀命中；或 historical 窗口扩容（前 80 字截断已控提示词膨胀，200 条上限可放宽）。

## 信息

### 101#8 98#3 跳过行对 UI 不可见——坏行永久占据 200 条上限前排且无清理入口

- 位置：`desktop/src-tauri/src/backend/projects/mod.rs:181-202`（skipped 仅 `eprintln`）、`desktop/src/backend/projects.ts:29`（`ProjectWorkspace` 无 skipped 字段）、`projects/mod.rs:174-176`（`ORDER BY id DESC LIMIT 200`）。
- 传递链条：①坏行（多为断电窗口的最新 done 行）被跳过 → 该项目最新版本从列表消失，用户以为提交丢失 → ②坏行仍占 200 窗口名额，更老版本不被补取 → ③iteration_note/resume 的 version_id 只能来自 workspace 列表，坏行版本不可操作也不可清除（除非删除项目触发 98#4 GC）。recover() 不受影响（feedback_json 仅 done 行写入、recover 只遍历 running/cancelling，已核实 projects/mod.rs:402/599-601）。
- 影响范围：projects workspace 坏行场景；数据未丢（行仍在库），可见性与名额问题。
- 修复方向：ProjectWorkspace 增 `skippedCount`；提供坏行重置或清理入口。

## 核实通过项（免后续复查）

- **97#1**：`trim_run_messages`（agent/mod.rs:785-792）保护头部 system+user 两条（history 恒以这两条开头）；复用与 history() 完全相同的 `trim_window`（34#8 同一算法）；后缀窗不会分离 assistant 与其 tool 结果（tool 恒在其 assistant 之后）；字节预算每次请求对全量 tail 重算；62+2=64 恰在 validate_messages 拒绝线内；重复裁剪幂等；回归测试在 HEAD 通过。
- **97#2 一致性**：cap 后 `agent_tool_calls.result_json`（tools.rs:734-737）、`agent.tool_completed` payload（:744-748）、`agent_messages.content`（mod.rs:1090/1107/1115）三处一致且均为合法 JSON；重放路径读回的就是已 cap 值；前端不解析事件 result 字段。
- **97#3 主路径**：同 kind+payload 的 pending 复用同一行并带 `reused` 标记；`cancel_approval` 加 `AND status='committed'` 守卫不再把已提升产物翻 cancelled。
- **97#5-97#8**：finish_run 双保险终态守卫（done+cancelling→cancelled、终态不覆写不重发事件）；delete_session 运行检查并入同一 IMMEDIATE 事务（FK CASCADE 兜底）；工具执行后取消复查点对称；purge 后 approve 的 not_found 分支同步 resolve 为 expired 并发事件。
- **98#1 主路径**：`mistakes()` 过滤只在 `mode='exam' AND status!='completed'` 时排除；export/variants/detail 三面同口径；考试测试断言进行中 0 条、交卷后 1 条，22/22 通过。
- **98#3/98#4/98#5/98#7**：workspace 坏行降级不瘫痪恢复路径；GC 与 kv 替换同事务、以新集合为准；begin_step/complete_plan 双守卫（cancelling→cancelled）；错题导出补 options/answer/citations 且 TS 类型同步。
