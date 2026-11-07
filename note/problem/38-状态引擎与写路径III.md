# 38 · 状态引擎与写路径 III（第十六轮）

> 审计范围：useBackendState/useAgent 状态骨架、client.ts 错误通道的写口径
> 全量清点（35#6 修复族的漏网点）、十五轮修复 diff 的回归复查残留。
> 每条含自包含的传递链条（逐跳 file:line）与影响范围。

## P3

### 38#1 写路径错误口径漏改族——17 处写操作 catch 仍用默认 read 口径，database_busy 时零提示

- 判据（35#6 确立、878b345b 修了九处）：写操作失败没有后续事件自愈，必须
  `reportBackendError(error, 'write')`；read 口径遇 `database_busy` 直接 return。
- **公共传递链条**（17 处共用）：① busy 窗口真实存在——流式 delta 每条写 events 表（`providers/mod.rs:1169`）、备份导入持大事务、vacuum/清理期间写锁竞争 → ② 用户恰在窗口内点击下表按钮 → ③ 前端函数 catch 后调 `reportBackendError(error)`（默认 kind='read'）→ ④ `client.ts:462`：`kind === 'read' && code === 'database_busy'` → **直接 return，不派发 backend-error 事件** → ⑤ 无 toast、无横幅——用户以为已保存 → ⑥ 后续事件刷新把界面拉回旧值，表现为「点了没反应」。对照已修路径（'write' 口径）：`client.ts:463` 弹错误 toast，用户可重试。
- 错误码来源佐证：`store.rs` WAL + busy_timeout 10000ms 下并发写命中 `SQLITE_BUSY` → `error.rs:32-39` 折叠为 `database_busy`。
- 17 处点位与各自链条入口（本轮逐一回源确认）：

  | # | UI 入口 | 前端函数 | invoke 命令 | 后端写行为 | catch 位置 |
  |---|---|---|---|---|---|
  | 1 | 设置→学习档案「重新诊断」 | `Settings.tsx:79-84` restartDiagnostic | `restart_learning_diagnostic` | 记录新诊断开始时间 | `Settings.tsx:84` |
  | 2 | 设置→「清除本地学习数据」确认键 | `Settings.tsx:107` onReset | `reset_application` | 停全部任务+清库（`commands.rs:82-110`） | `Settings.tsx:107` `.catch(reportBackendError)` |
  | 3 | 报告→「采纳所选建议」 | `Reports.tsx:67-75` adopt | `adopt_report_recommendations` | 写入真实任务 | `Reports.tsx:74` |
  | 4 | 练习→错题「标记掌握/恢复待巩固」 | `Practice.tsx:168-176` setMastered | `set_practice_mistake_mastered` | 翻转掌握标记 | `Practice.tsx:175` |
  | 5 | 练习→错题「生成变式」 | `Practice.tsx:177-193` generateVariant | `generate_practice` | 创建变式生成任务 | `Practice.tsx:192` |
  | 6 | 模拟考试逐题「提交答案」 | `ExamWorkspace.tsx:126-137` submit | `submit_practice_answer` | 写答案 | `ExamWorkspace.tsx:135` |
  | 7 | 模拟考试「交卷」（不可逆） | `ExamWorkspace.tsx:138-149` finalize | `submit_exam` | 未答按 0 分计并写证据（28#11 注释在案） | `ExamWorkspace.tsx:146` |
  | 8 | 主观题「恢复评分」 | `SubjectivePractice.tsx:104-110` resume | `resume_practice_answer` | 重启评分 | `SubjectivePractice.tsx:109` |
  | 9 | 主观题「生成题组」 | `SubjectivePractice.tsx:111-119` generate | `generate_practice` | 创建生成任务 | `SubjectivePractice.tsx:118` |
  | 10 | 资料库「导入资料」（批量+ZIP） | `Library.tsx:126-150` importMaterials | `import_material_files/archive` | 暂存+配额计数 | `Library.tsx:149` |
  | 11 | 资料详情「开始解析/重新解析」 | `Library.tsx:160-167` startParse | `enqueue_material_parse` | 登记 job+置 queued | `Library.tsx:166` |
  | 12 | 笔记页「批量导入」 | `Library.tsx:273-284` importNotes | `import_notes` | 追加导入笔记 | `Library.tsx:282` |
  | 13 | 数据目录面板「保存目录/恢复默认/重试初始化」 | `DataDirectoryPanel.tsx:26-34` run | `set_storage_location` / `reset_storage_location` / `retry_storage_initialization` | 写 pointer/重初始化 | `DataDirectoryPanel.tsx:32` |
  | 14 | 社区「预约提醒/取消预约」 | `Community.tsx:127-137` reserve | `reserve_community_room` / `cancel_community_reservation` | 写预约/提醒 | `Community.tsx:135` |
  | 15 | 设置「导出全量备份」 | `BackupPanel.tsx:18-30` exportBackup | `export_full_backup` | 写包+事件落库 | `BackupPanel.tsx:28` |
  | 16 | Wiki 问答「删除此条」 | `WikiPanel.tsx:98-108` remove | `delete_knowledge_session` | 硬删除 | `WikiPanel.tsx:107` |
  | 17 | Wiki「清空问答历史」确认键 | `WikiPanel.tsx:111-118` clear | `clear_knowledge_history` | 硬删除全部 | `WikiPanel.tsx:116` |

- **同文件不一致铁证**：`Community.tsx:135`（reserve，read 口径）与 `:140`（cancelRoom，'write' 口径）相距 5 行——同源修复波（29#1）只给后者补了口径。
- **影响范围**：17 个入口的全部使用者；危害排序：#7 交卷不可逆、失败静默后用户离开考场而成绩未落（最高）；#2 清除数据失败静默会误导「已清空」；#10-12 导入类失败静默表现为「选了文件没反应」；其余为单次写操作静默。busy 窗口在流式输出期间高频存在（delta 每条写库）。修复面窄：17 处 catch 补第二参 `'write'` 即可。

### 38#2 useBackendState.sync() 先逐页翻完 eventsSince 再取全量快照——事件载荷全被丢弃，纯冗余 IPC

- 位置：`desktop/src/backend/useBackendState.ts:56-62`；后端 `desktop/src-tauri/src/backend/store.rs:779-814`
- **传递链条**：① 事件密集场景（备份导入/长 Agent 运行/讲义逐单元）产生大量 events 行 → ② `onBackendEvent` 触发 `requestSyncCoalesced`（`useBackendState.ts:188-194`，120ms 合并）→ ③ `sync()`（`:56-61`）循环 `eventsSince(lastEventId)`：每次取 201 行、逐行 JSON 反序列化（`store.rs:800-805`），前端**只消费 `page.events.at(-1).id` 与 `hasMore`**，全部载荷丢弃 → ④ `hasMore` 时以尾 id 为游标再取下一页，数千事件即数十次串行往返 → ⑤ 最后 `getAppState()`（`:62`）取全量快照——其返回的 `lastEventId`（`store.rs:771-777` 全局 MAX(id)）本身就是最新游标，`applySnapshot`（`:39-40`）直接采用 → ⑥ 翻页所得游标完全冗余；去掉循环行为等价（全仓 `eventsSince` 消费方仅此一处，grep 佐证）。
- **影响范围**：每次合并刷新至少多 1 次 IPC + 201 行无效解析；事件爆发后首次 sync 串行数十次往返（每次含载荷序列化/解析），表现为导入完成后界面「卡几秒才刷新」。无正确性影响（游标语义等价）。修法：删除翻页循环直接 `getAppState()`。

## 信息性

### 38#3 App.renderPage 的 case 'shared' 为不可达死代码

- 位置：`desktop/src/App.tsx:310`（switch 分支）与 `:314`（早退）
- **传递链条**：① `renderPage` 定义于 `:293-312`（含 `case 'shared': return <SharedPlan …/>`）→ ② 唯一调用点 `:343` 位于主返回树（`:331` 起）内 → ③ 但 `:314` `if (page === 'shared') return <div className="shared-screen">…` 在主返回树之前拦截了 shared 的全部路径 → ④ switch 分支永不执行；两处 `<SharedPlan>` 实例化仅 `:317` 生效。
- **影响范围**：无功能影响；死代码造成阅读误导（看似两条渲染路径）。删除 `:310` 一行即可。

### 38#4 StudyRoom 35#9 修复引入缩进丢失——注释与 useLayoutEffect 落在列 0

- 位置：`desktop/src/pages/StudyRoom.tsx:39-41`（878b345b 引入；`cat -A` 确认零前导空格）
- **传递链条**：① 878b345b 以手工 diff 替换 `useEffect` 为 `useLayoutEffect` → ② 替换块第二行注释与语句行未保留组件体缩进 → ③ 编译/运行无影响（JS 语义与缩进无关），tsc/rustfmt 门禁不覆盖 TSX 格式。
- **影响范围**：无；纯格式残留。修复即补两格缩进。

## 十五轮修复回归复查备注（无新缺陷项的记录）

- c3895ffe（后端 23 项）：34#2 锁序（drop→exclusive→store）双向一致；34#4 step 表
  双写与 complete_step 同口径（df63ee0f 回归测试覆盖）；34#5 tool_calls 声明仅追加
  不重复（messages 单次构建于循环外，逐轮 append）；34#6 回填置于 stream_chat 之后
  且 Ok/Err 双路径执行（INSERT 前失败则 UPDATE 匹配 0 行，无害）；34#8 二次孤儿
  裁剪方向正确（尾部预算 + 头部 retain）。
- 878b345b（前端 10 项）：35#1 终态清单与 providers 实际发射的 8 个终态 kind 逐一对上
  （`route_recovered` 为非终态正确排除）；35#3 practiceTarget 消费即清（App.tsx:301
  `onTargetConsumed`）；35#10 serde 抽查一致。
- 本轮在上述复查中未发现新引入的功能性回归；38#4 是唯一的修复残留（格式）。
