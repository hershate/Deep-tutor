# 76 · React hooks 与状态机正确性（第二十八轮·34 源文件全量）

> 审计范围：App+16 页+17 组件+backend hooks 逐文件（StrictMode 双挂载环境复核）。
> 60/70 号审修复波回归，本轮为全组件 hooks 专项。主审已抽验 G-5 属实。

## P3

### 76#1（=G-1）考试历史切换无序号守卫——慢响应后到覆盖已选卷，可致提交写到「非所见卷」

- 位置：`components/ExamWorkspace.tsx:161-163`（selectSet）、`:128-139`（submit 的 onUpdated 回写）、`:203`（历史按钮提交期间未禁用）。
- 传递链条：① 点历史卷 A（响应慢）→ 立即点卷 B（快）→ 界面显示 B ② A 的 detail 后到 → onGenerated(A) → 父页 chooseSet(A) → 界面静默跳回 A ③ 提交期间（busy）点 B 后 A 回写覆盖——后续提交写到与所见不同的卷。父页 Practice.tsx:210-215 的 selectHistory 已有 busy 守卫（51#1），本组件漏配同款。
- 影响范围：考试/练习历史快速切换的用户；错卷提交为最重后果。
- 修复方向：selectSet 补 busy+序号守卫；busy 期间禁用历史按钮。

### 76#2（=G-2）主观题历史切换同型竞态

- 位置：`components/SubjectivePractice.tsx:120-122/:85-94/:151`。与 76#1 完全同型。

### 76#3（=G-3）考试/主观题长文草稿只存组件内 useRef——切 tab 即卸载丢稿

- 位置：`pages/Practice.tsx:255/:257`（条件挂载 `{tab === 'exam' && …}`）、`ExamWorkspace.tsx:27-28` 与 `SubjectivePractice.tsx:23`（draftByQuestion 仅 ref）。
- 传递链条：① 论述题输入大段作答（未保存）② 切「错题本」tab 一眼 ③ 切回——组件重挂载 ref 归零，草稿丢失（计时仍走）。28#2 修了「翻题丢稿」，卸载路径未覆盖；对照笔记有后端草稿通道（43#1 家族）。
- 影响范围：考试中切 tab 的用户；主观题同病。StudyRoom 批注弹窗同属组件级无草稿（程度轻）。
- 修复方向：草稿上移父组件/后端 draft 通道/sessionStorage。

### 76#4（=G-4）FocusLayer 倒计时 interval 依赖每次渲染重建的 onFinish——事件密集期相位抖动

- 位置：`App.tsx:254`（finishFocus 渲染期普通函数）、`:406`、`:455-468`（deps 含 onFinish）。
- 传递链条：App 每次重渲染（事件/toast/通知）→ finishFocus 新身份 → effect 重跑 clearInterval+立即 tick+setInterval——倒计时相位反复重置，密集期可感停顿/跳秒；数值由墙钟推导不出错。
- 修复方向：finishFocus 包 useCallback 或 effect 内经 ref 取用。

### 76#5（=G-5）数字输入 onChange 即钳制 min——阻断自然键序（62#4 修复引入的交互缺陷）

- 位置：`pages/PlanGenerator.tsx:305`（每日分钟 min 10）、`pages/Community.tsx:163`（房间时长 min 10）。
- 传递链条：① 全选删除后键入 `4` → 立即被钳为 `10` ② 再键 `5` 意图 45 → 变 `105`——首位数字永远无法小于 10，两位数目标需绕路。仓库正确范式：Plan.tsx:205（onChange 不钳、提交时钳）、ReportViews.tsx:37（仅 round、按钮校验区间）。
- 影响范围：生成器与社区预约的所有用户（每次输入都遇）。
- 修复方向：min 钳制移到提交/失焦，onChange 仅 round。

## 信息性

### 76#6（=G-6）Reports/StudyRoom 事件监听随 period/tab 重挂，间隙丢事件——51#9 家族漏网

- `Reports.tsx:35-60`（deps [period]→[refresh]）、`StudyRoom.tsx:43-58`（deps [tab]）。旧监听注销与新 listen() resolve 之间的 IPC 窗口事件丢失，自愈于下一事件；与其余 9 页已 ref 化的口径不一致。

### 76#7（=G-7）deltaThrottle 定时器无取消通道——卸载后仍触发一次 IPC+setState

- `client.ts:438-444`（无 cancel API），8 个使用点。切页瞬间一次多余 getProviderChat；同族 ProviderHub testAll 循环卸载后继续整轮。

### 76#8（=G-8）51#2 序号守卫覆盖盘点——10 个次级面板未配

- 未配：ProviderHub/Projects/AgentTaskCenter/SecurityPanel/SyncPanel/UsagePanel/Settings.profile/KnowledgeMap.refreshPlans/StudyRoom.refreshPlans/Library.openMaterial（单飞近似串行）。最接近可感：ProviderHub 保存档案后旧 refresh 后到拉回旧列表。均无自伤路径、多为全量重拉。

### 76#9（=G-9）Community 预约/取消缺 busy 守卫（同文件 32#10 口径不一）

- `Community.tsx:131-145`——双击靠 not_reserved 静默兜底，连点期间无视觉反馈。

### 76#10（=G-10）KnowledgeMap 后台方案采用事件重置用户选区

- `KnowledgeMap.tsx:92-96`——plan 对象 identity 变化即重置 selected/path；他处采用方案时地图页选区弹回首节点。低频。

### 76#11（=G-11）Library 笔记被并发删除后保存提示假成功

- `Library.tsx:228-232`——map 不命中零写入仍 toast「已保存」。无害但误导。

## 已核实良好（摘要）

- 26 处事件/窗口监听全部 disposed+晚解析双保险，StrictMode 无泄漏；全部 interval/timeout cleanup 配对；Exam 倒计时用后端权威基准（61#2）无漂移。
- useBackendState（generation/lastEventId/workspaceId 三重防乱序、inflight 串行、queued 循环）与 useAgent（deltaEpoch 游标、failover 映射、activeIdRef 复查）核心竞态防线核实有效。
- 列表 key 全用稳定业务 id；无条件 hooks；受控输入 IME/保存期锁定正确；pendingToggles 家族无泄漏；MediaPlayer blob 对称 revoke；快照合并四处守卫有效。
