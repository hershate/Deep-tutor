# 38 · 状态引擎与写路径 III（第十六轮）

> 审计范围：useBackendState/useAgent 状态骨架、client.ts 错误通道的写口径
> 全量清点（35#6 修复族的漏网点）、十五轮修复 diff 的回归复查残留。
> 每条附传递链条与影响范围（文末）。

## P3

### 38#1 写路径错误口径漏改族——17 处写操作 catch 仍用默认 read 口径，database_busy 时零提示

- 判据（35#6 确立的约定）：写操作失败没有后续事件自愈，必须
  `reportBackendError(error, 'write')`；read 口径遇 `database_busy` 直接 return
  （`client.ts:455-464`），写失败静默等于丢操作。十五轮 878b345b 修了九处
  （费率/预算/备份导入确认段/安全 run/建 PAT/签到/插件四操作/清理/压缩），
  本轮全量清点发现以下写操作仍在 read 口径：
- 位置与操作（全部为本轮逐一回源确认的写命令）：
  | # | 位置 | 操作 | 后端命令 |
  |---|---|---|---|
  | 1 | `Settings.tsx:84` | 重新诊断 | `restart_learning_diagnostic` |
  | 2 | `Settings.tsx:107` | 清除本地学习数据确认键 | `reset_application` |
  | 3 | `Reports.tsx:74` | 采纳学习建议 | `adopt_report_recommendations` |
  | 4 | `Practice.tsx:175` | 错题标记/恢复掌握 | `set_practice_mistake_mastered` |
  | 5 | `Practice.tsx:192` | 生成错题变式 | `generate_practice` |
  | 6 | `ExamWorkspace.tsx:135` | 模拟考试逐题作答 | `submit_practice_answer` |
  | 7 | `ExamWorkspace.tsx:146` | 交卷（不可逆） | `submit_exam` |
  | 8 | `SubjectivePractice.tsx:109` | 恢复主观题评分 | `resume_practice_answer` |
  | 9 | `SubjectivePractice.tsx:118` | 生成主观题组 | `generate_practice` |
  | 10 | `Library.tsx:149` | 导入资料（批量+ZIP） | `import_material_files/archive` |
  | 11 | `Library.tsx:166` | 登记解析任务 | `enqueue_material_parse` |
  | 12 | `Library.tsx:282` | 导入笔记归档 | `import_notes` |
  | 13 | `DataDirectoryPanel.tsx:32` | run()（保存目录/恢复默认/重试初始化共用） | `set_storage_location` 等 |
  | 14 | `Community.tsx:135` | 预约/取消预约 | `reserve/cancel_reservation` |
  | 15 | `BackupPanel.tsx:28` | 导出全量备份 | `export_full_backup` |
  | 16 | `WikiPanel.tsx:107` | 删除单条问答 | `delete_knowledge_session` |
  | 17 | `WikiPanel.tsx:116` | 清空问答历史 | `clear_knowledge_history` |
- 铁证（同文件同型不一致）：`Community.tsx:135`（reserve，read 口径）与
  `Community.tsx:140`（cancelRoom，'write' 口径）相距 5 行——同一次修复波
  （29#1 竞态码注释同源）只给后者补了口径。
- 传递链条：流式 delta 每条写 events 表、备份导入/清理持大事务——busy 窗口真实
  存在；恰在窗口内点击上述任一按钮 → 后端已拒绝 → 界面无任何提示 → 用户以为
  已保存（如「标记掌握」「交卷」「预约」）→ 后续列表刷新回旧值，表现为「点了没反应」。
  影响范围：表中 17 个入口的全部使用者；#7 交卷不可逆，失败静默危害最大。

### 38#2 useBackendState.sync() 先逐页翻完 eventsSince 再取全量快照——事件载荷全被丢弃，纯冗余 IPC

- 位置：`desktop/src/backend/useBackendState.ts:56-62`：
  ```ts
  let page = await backend.eventsSince(lastEventId.current)
  while (page.hasMore) { const last = page.events.at(-1); if (!last) throw ...; page = await backend.eventsSince(last.id) }
  applySnapshot(await backend.getAppState(), requestedGeneration)
  ```
  后端 `store.rs:779-800`：events_since 是纯读（SELECT … LIMIT 201，无副作用）；
  前端对每页只用了 `page.events.at(-1).id` 与 `hasMore`——200KB 级 JSON 载荷逐条
  跨 IPC 序列化后整体丢弃；而 `getAppState()` 返回的 `lastEventId` 本身就是全局
  最新游标（`store.rs:774-777`），applySnapshot（:39-40）直接采用。
- 证据：全仓 `eventsSince` 消费方仅此一处（grep 佐证）；去掉翻页循环后行为等价。
- 传递链条：每次 120ms 合并刷新至少多 1 次 IPC；备份导入/长 Agent 运行产生数千事件后，
  下一次 sync 逐 201 条翻页——首次恢复可达数十次串行往返（每次含 201 行载荷解析），
  表现为导入完成后界面「卡几秒才刷新」。影响范围：所有事件密集场景的刷新延迟。

## 信息性

### 38#3 App.renderPage 的 case 'shared' 为不可达死代码

- 位置：`desktop/src/App.tsx:310`（switch 内 `case 'shared': return <SharedPlan …/>`）
  与 `:314`（`if (page === 'shared') return <div className="shared-screen">…`）
- 证据：renderPage 只在 `:343` 主返回树内被调用，而 314 行的早退在其之前拦截了
  `page === 'shared'` 的全部路径——switch 分支永不执行。两处还各自实例化一次
  `<SharedPlan token=…>`（314 行为唯一生效实例）。

### 38#4 StudyRoom 35#9 修复引入缩进丢失——注释与 useLayoutEffect 落在列 0

- 位置：`desktop/src/pages/StudyRoom.tsx:39-41`（878b345b 引入）：
  ```
    // 35#9：useLayoutEffect 在提交后同步刷新——passive effect 有微任务窗口，
  // 在途响应的 promise 微任务可先于 effect 冲刷漏过守卫
  useLayoutEffect(() => { stageRef.current = …
  ```
- 证据：`cat -A` 确认第二行注释与 useLayoutEffect 行无前导空格（组件体应有两格缩进）。
  纯格式残留，功能与 rustfmt/tsc 门禁无关（前端无格式门禁）。

## 传递链条与影响范围汇总

| # | 链条 | 用户可感知影响 |
|---|---|---|
| 38#1 | 写命令 → busy 拒绝 → read 口径静默 | 17 个入口失败零提示，交卷/掌握标记等表现为「点了没反应」 |
| 38#2 | 事件翻页（载荷丢弃）→ getAppState | 事件密集后 sync 多数十次串行 IPC，恢复刷新延迟 |
| 38#3 | 早退拦截 → switch 分支永不达 | 无（死代码，阅读误导） |
| 38#4 | 修复 diff 手工缩进 | 无（格式残留） |

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
