# 156 号：Agent 工具循环与前端语义问题集（第四十八轮）

审计基线 `b68a1459`。审计面块一（后端）：agent/（tools 1065 行 +
mod 1526 行 + commands）、plugins/（861 行）——工具循环/记忆审批/产物
提升/插件事件/Skill 校验；块二（前端）：Library/KnowledgeMap/Community/
Reports/Review/Integrations 六页与 BackupPanel/DataDirectoryPanel/
MaintenancePanel/SecurityPanel/SyncPanel/UsagePanel/WikiPanel/MediaPlayer/
AgentTaskCenter/ReportViews/ProviderRouting/SharePlanDialog 十二组件的
React 语义（竞态/守卫/清理/弹层）。每条对照 51#x/108#x/136#x/144#4 等
已修注释确认非重述。方法：子代理深审 + 主审对 #1/#2 二次源码定案。
共 5 P3 + 4 待核 + 1 信息。

## P3

### 156#1 promote() 三个拒绝分支未收口 pending 审批——101#2 同型遗漏，审批卡片永驻且反复报错

- 位置：`desktop/src-tauri/src/backend/agent/tools.rs:983-987`
  （`artifact_approval_stale`）、`:991-996`（`lecture_not_ready`）、
  `:997-1002`（`invalid_artifact_scope`），以及 `:976-977` 处
  `read_artifact` 抛 `artifact_modified_externally` 的传播路径——均直接
  `return Err` 不调 `resolve_pending`。101#2 只修了
  `artifact.4 != "committed"` 分支（:938-953），97#8 修了 purge 分支。
- 传递链条：①审批请求后模型又 workspace.write 同一 artifact（哈希变化）
  或讲义未完成/单元越界成立；②用户点批准 → 命中拒绝分支报错但审批行
  停留 pending；③AgentCenter 审批卡片只渲染 pending（AgentCenter.tsx:135）
  → 卡片永驻，反复批准反复报错，只能手动取消。memory.save/delete 审批
  无此问题（成功即 resolve）。
- 影响范围：受影响=产物提升审批的三个拒绝分支。损害性质=审批流死循环
  UI。
- 修复方向：三分支（含 read_artifact 传播）拒绝时同步 resolve_pending
  （status=expired/rejected），镜像 101#2。

### 156#2 Library openNote 的 draftRevisionRef 写在序号守卫之前——旧响应污染版本基线可误触发草稿冻结

- 位置：`desktop/src/pages/Library.tsx:298-299`——
  `draftRevisionRef.current = revision` 先于
  `if (ticket !== openingNoteSeq.current) return`；错误兜底
  `:304-308` 打开笔记但不重置该 ref。
- 传递链条：①连点笔记 A→B，A 的 getDraft 后到（慢盘/大草稿）→ B
  编辑器打开时 ref 残留 A 的 revision；②350ms 自动保存以错误期望版本
  提交 → 后端按真实 revision 判冲突 → 假 `revision_conflict`；
  ③75#4 的 draftBlocked 防护被误触——弹「草稿已因并发修改冻结」，
  用户可能误点「放弃本地修改」丢 B 的编辑。触发 B：B 的 getDraft 失败
  走兜底 → ref 残留上一次打开的值，同样错版本。51#4 同型遗漏。
- 影响范围：受影响=快速切换笔记打开编辑器的竞态窗口与 getDraft 失败
  路径。损害性质=编辑数据风险（经确认弹窗缓冲）。
- 修复方向：ref 写入移到守卫之后；兜底路径重置 ref 为 null（保存前
  忽略期望版本）。

### 156#3 MaintenancePanel compact() 绕过自己刚补的序号通道——迟到的旧报告覆盖 VACUUM 后新报告

- 位置：`desktop/src/components/MaintenancePanel.tsx:70`——
  `setReport(await maintenanceBackend.report())` 直连 set，不经
  `pullReport`/`reportSeq`（:27-34 同文件刚按 144#4 加了守卫）。
- 传递链条：①整理进行中一条 maintenance.* 事件触发 pullReport（读快照
  较早、响应较晚）；②compact 完成写入 post-VACUUM 报告；③迟到的
  pullReport 通过自己的 ticket 检查（compact 不递增 seq）→ 旧报告覆盖
  新报告直到下一事件。clean()（:62）用 pullReport 正确。144#4 同型
  遗漏。
- 影响范围：受影响=维护面板整理操作后的报告显示。损害性质=陈旧数据
  覆盖（自愈于下一事件）。
- 修复方向：compact 改经 pullReport 或递增 seq。

### 156#4 catch 侧序号守卫遗漏家族——旧失败晚到重设错误横幅（5 处）

- 位置（成功分支有守卫、失败分支无，或整函数无 ticket）：
  `KnowledgeMap.tsx:64`（refreshOverview catch——且整函数无 ticket，仅靠
  planRef 成功侧守卫）、`:76`（refreshPlans catch：ticket 在 :69 生成但
  catch 未比对）；`SecurityPanel.tsx:52`（成功守卫在 :49-50）；
  `Community.tsx:63-67`（effect 挂的 .catch → setLoadError）；
  `Review.tsx:57`（mount 挂的 .catch → setLoadError）。
- 传递链条：①两个并发刷新 R1（先发慢回、失败）与 R2（后发快回、成功）；
  ②R2 成功分支过 ticket 检查清错并写入新数据；③R1 的失败分支无 ticket
  复查，晚到仍 setLoadError——界面在显示最新数据的同时挂出陈旧错误
  横幅与重试按钮，直到下一次成功刷新才清除。数据本体不被污染。
- 对照：已修同型 Reports.tsx:47、SyncPanel.tsx:45、UsagePanel.tsx:43、
  Integrations.tsx:29、WikiPanel.tsx:47、AgentTaskCenter.tsx:51 均两侧
  守卫——144#4「成功与失败分支均须守卫」的批量收口漏了这 5 处。
- 影响范围：受影响=五处页面的错误横幅准确性（并发刷新竞态窗口）；
  不受影响=数据读写与页面其余功能。损害性质=陈旧错误闪现。
- 修复方向：catch 内补同 ticket 复查（KnowledgeMap refreshOverview 并补
  整体 ticket）。

### 156#5 Integrations earlier 分页缓存在「卸载后重装同 id」时复活已删投递

- 位置：`desktop/src/pages/Integrations.tsx:40`——B-19 过滤按当前插件集
  比对 pluginId，`earlier` 数组本身在卸载/刷新时从不清空；服务端
  plugin_deliveries 随卸载 CASCADE 清空（plugins/mod.rs:241-246）。
- 传递链条：①加载过更早事件（≥100 条）；②卸载插件 a → 重装同 id 的 a
  → `installed.has('a')` 重新为真 → earlier 中 a 的旧行重新显示——
  event_id 已在库中不存在的幽灵投递记录（pluginName 为旧 JOIN 结果）。
  卸载不重装场景过滤正确。
- 影响范围：受影响=插件卸载重装同 id 的事件浏览。损害性质=幽灵数据
  展示。
- 修复方向：卸载/刷新时清空 earlier（或按 event_id 存在性过滤）。

## 待核

### 156#6 commit_artifact 的文件写与 DB commit 无补偿——commit 点失败使该 artifact 永久不可用

- 位置：`desktop/src-tauri/src/backend/agent/tools.rs:352`
  （atomic_write 在事务内执行）、`:365`（`tx.commit()` 在文件写之后）、
  `:381`（read_artifact 哈希比对）。
- 传递链条：①workspace.write 落新内容：文件已 temp+sync+rename 为新
  哈希版本；②`tx.commit()` 失败（busy 超时/落盘错误）——DB 回滚到
  旧哈希记录；③文件与 DB 哈希永久不一致：此后 read_artifact 比对恒报
  `artifact_modified_externally`，该 artifactId 的 read/edit/promote 全部
  拒绝（workspace.edit 需先 read 成功，无自愈路径），只能换新 path 重写。
  97#4/101#1 处理了写序与冻结但未覆盖 commit 点失败。
- 影响范围：受影响=commit 点失败窗口内的单个产物（会话其余功能正常）；
  不受影响=正常提交路径。概率低（atomic_write 已 sync_all，commit 点
  窗口窄）。损害性质=产物永久不可用（数据仍在文件区但不可达）。
- 待核：commit 失败后回滚文件（备份旧内容）或以新哈希补写 DB。

### 156#7 跨响应重复 tool_call id 使 resume 重放序列非法——122#7 只修了单响应内

- 位置：`agent/mod.rs:1125`（run_loop 保存 assistant(tool_calls) 先于
  tools::execute）、`agent/tools.rs:540`（`tool_call_conflict`）、
  `agent/mod.rs:750-800`（history() 的 declared/answered 修剪）。
- 传递链条：①部分本地/小模型跨响应重用早前步的 tool_call id（122#7
  注释明言单响应内重复存在）；②run_loop 保存 assistant(tool_calls) 后
  execute 撞 `tool_call_conflict` → run 失败——历史中已有两个 assistant
  声明同一 id、仅一条 tool 回复；③resume 重放时 history() 修剪只删
  「未答复的声明」不删「重复声明的第二个」→ 重放序列含悬空声明，
  严格端点 400——resume 每次必败且每次消耗 tokens。
- 影响范围：受影响=跨响应重复 id 模型的失败 run 恢复路径；不受影响=
  标准 OpenAI/Anthropic 系模型（id 全局唯一）。损害性质=恢复路径死锁
  + 重复计费。需真实本地模型复现确认。
- 待核：history() 补「同 id 保留首个声明」的去重修剪。

### 156#8 Library saveNote 未容忍已拒绝的草稿链——deleteNote 已修、saveNote 漏

- 位置：`desktop/src/pages/Library.tsx:336`（`await draftWrite.current`
  链尾可能 rejected）；对照同文件 `:361`（deleteNote 用
  `.catch(() => {})`）。
- 传递链条：①草稿链尾 rejected 的两种来源：冲突冻结态（draftBlocked
  后链恒 rejected，不再有新 enqueue 治愈）或瞬时失败（database_busy）
  与下一次 350ms 自动保存之间的窗口；②用户点「保存笔记」→
  `await draftWrite.current` 直接抛出；③错误提示「保存失败」——但
  笔记本体保存（setNotes/replaceCollection）与草稿链无关，被这次前置
  await 连带阻断，用户编辑无法落库。
- 影响范围：受影响=冻结态/瞬时失败窗口内的笔记保存操作；不受影响=
  正常态保存与删除（deleteNote 已容错）。损害性质=保存路径被无关的
  草稿失败阻断。
- 修复方向：与 deleteNote 对齐（`.catch(() => {})` 后继续保存）。

### 156#9 DataDirectoryPanel restart() 无 finally——busy 永久停留

- 位置：`desktop/src/components/DataDirectoryPanel.tsx:76-82`（成功分支
  不 `setBusy('')`，异常分支也未见 finally）。
- 传递链条：①用户点「立即重启」→ busy='restart'，按钮禁用；②后端
  restart 命令 resolve（进程即将退出的正常路径下 UI 生命周期结束，
  无影响）或命令失败/重启被拦截；③面板 busy 永久停留 'restart'——
  所有按钮禁用直到组件重挂载（切页/重开面板）。
- 影响范围：受影响=restart 命令非退出路径的 resolve/失败（需核实
  storageBackend.restart() 的实际语义——通常进程随后退出，实际暴露面
  可能为零）；不受影响=正常退出路径。损害性质=潜在的面板卡死。
- 待核：确认 resolve 语义后补 finally 复位或按需保留禁用。

## 信息

### 156#10 后端核验通过项（无动作）

- 插件：启停持久化（enabled 列、reload 保 enabled、uninstall 先停用
  回写）完整；before 游标分页 SQL（plugins/mod.rs:804）与前端 loadEarlier
  取合并列表最末元素正确、去重键正确；EVENT_NAMESPACES 后端 22 项与
  Integrations.tsx:10 前端逐项一致；Skill/清单校验面（deny_unknown_fields、
  serde_json 128 层递归限深、id/version/订阅数长度全检）无洞，仅
  description 允许空串（name 有 trim 检查、description 无——纯口径不对称）。
- Review 键盘守卫经 input/button closest 兜底覆盖 SearchLayer；
  review.list 后端 LIMIT 200 与 Review.tsx:180 提示一致；本批六页十二
  组件无轮询定时器（唯一 interval 在 App.tsx:216 通知，带 visibilitychange
  清理）——「hidden 页轮询」无残留；Modal/Esc 栈（24#2）、焦点圈禁令牌栈
  （68#1）、hasOpenModal（133#7）实现正确。
- memory.save/delete 审批无 TTL（仅 artifact.promote 有 24h 过期
  resolve 路径）；promote 过期分支（tools.rs:954-975）正确双写
  approval=expired + artifact=expired。
- 31#41 家族小残：execute 参数校验 Err（tools.rs:559-575）路径
  assistant(tool_calls) 已落库（mod.rs:1125 先于 execute）→ run 失败但
  UI 留悬空工具卡片；重放侧已被 23#9 修剪兜底，仅展示问题。
- 文档漂移一处：`agent/mod.rs:26` 注释「完整结果仍持久化在
  agent_messages」对 >16KB 结果不成立（101#3 有意为之，注释未同步）。
- `Library.tsx:122` filteredResources 用未去抖 query（notes 侧已去抖
  80#5）——资源侧仅标题小写比对成本可忽略，口径不一致而已。
- ProviderRouting 连续快速改两个下拉存在旧 settings 闭包覆盖前一改动的
  理论窗口，受 saving 禁用与键盘操作节奏保护，未升级为问题。
