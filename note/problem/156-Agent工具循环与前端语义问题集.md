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

- 以下刷新函数成功分支有守卫、失败分支无（或整函数无 ticket），旧
  失败晚到会在新成功清错后重设错误横幅+重试按钮（数据不被污染）：
  `KnowledgeMap.tsx:64`（refreshOverview catch，且整函数无 ticket）、
  `:76`（refreshPlans catch）；`SecurityPanel.tsx:52`；`Community.tsx:63-67`
  （effect 挂的 .catch）；`Review.tsx:57`（mount 挂的 .catch）。
  对照已修同型：Reports.tsx:47、SyncPanel.tsx:45、UsagePanel.tsx:43、
  Integrations.tsx:29、WikiPanel.tsx:47、AgentTaskCenter.tsx:51 均两侧
  守卫。144#4「成功与失败分支均须守卫」的批量收口漏了这 5 处。
- 影响范围：受影响=五处的错误横幅准确性。损害性质=陈旧错误闪现。
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

- `tools.rs:352` atomic_write 在事务内、`tx.commit()`（:365）之前；
  commit 失败（busy 超时/落盘错误）→ 文件已为新内容而 DB 回滚到旧哈希。
  此后 read_artifact（:381）哈希比对永久 `artifact_modified_externally`，
  read/edit/promote 全废（workspace.edit 需先 read 成功，无法自愈），只能
  换新 path 重写。97#4/101#1 处理了顺序与冻结但未覆盖 commit 点失败；
  概率低（atomic_write 已 sync_all，commit 点窗口窄）。建议核实是否需
  commit 失败后回滚文件或重写旧哈希。

### 156#7 跨响应重复 tool_call id 使 resume 重放序列非法——122#7 只修了单响应内

- 122#7 注释明言「部分本地/小模型会重复 id」。此类模型在后续步重用
  早前步的 id：run_loop 保存 assistant(tool_calls)（mod.rs:1125）后
  tools::execute 撞 `tool_call_conflict`（tools.rs:540）→ run 失败；
  历史出现两个 assistant 声明同一 id、仅一条 tool 回复。`history()`
  （mod.rs:750-800）的 declared/answered 修剪只删「未答复的声明」不删
  「重复声明的第二个」→ 重放序列非法，严格端点 400，resume 每次必败且
  计费。需真实本地模型复现确认。

### 156#8 Library saveNote 未容忍已拒绝的草稿链——deleteNote 已修、saveNote 漏

- `Library.tsx:336` `await draftWrite.current`（链尾可能 rejected）；
  同文件 :361 deleteNote 用了 `.catch(() => {})`。冲突冻结态（draftBlocked
  后链尾恒 rejected）点「保存笔记」：每次都抛错报「保存失败」，笔记本体
  （setNotes 路径与草稿无关）无法保存；瞬时失败与下一次自动保存之间的
  窗口同理。修法与 deleteNote 对齐即可。

### 156#9 DataDirectoryPanel restart() 无 finally——busy 永久停留

- `DataDirectoryPanel.tsx:76-82`：成功分支不 `setBusy('')`。若后端
  restart 命令在进程退出前 resolve（或重启被拦截），面板 busy 永久停留
  'restart'，按钮全禁用直到重挂载。需核实 storageBackend.restart() 的
  resolve 语义（通常进程即将退出，实际暴露面可能为零）。

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
