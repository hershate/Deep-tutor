# 37 · 前端交互人性化 III（第十六轮·应用户型专项）

> 审计范围：用户要求的「UI 操作是否人性化」专项——16 页面 + 17 组件全量通读，
> 从真实使用者的操作预期出发（点按钮会发生什么、失败能不能看见、破坏性操作
> 有没有护栏、给出的路径/链接能不能用）。用户例举的「点击打开工作区弹出
> 任务管理器选择」经查**不存在**（全应用无任何打开外部位置的入口，见 37#4）。
> 每条附传递链条与影响范围（文末）。

## P3

### 37#1 AgentCenter 删除会话无确认——一次点击硬删除整个会话与全部历史

- 位置：`desktop/src/pages/AgentCenter.tsx:104`（顶栏垃圾桶 IconButton → `removeSession`）、
  `desktop/src/backend/useAgent.ts:216-224`（`remove` 无确认直调）、后端
  `desktop/src-tauri/src/backend/agent/mod.rs:1257-1269`（`DELETE FROM agent_sessions` + CASCADE，硬删除）
- 证据：
  ```tsx
  <IconButton label="删除当前会话" disabled={!activeSession || running || busy === 'delete'} onClick={() => void removeSession()}>
  ```
  ```rust
  tx.execute("DELETE FROM agent_sessions WHERE id = ?1", params![id])?;
  ```
  对照同型破坏性操作全部有确认：删除笔记（Library.tsx:296）、删除资料（Library.tsx:295）、
  删除记忆卡（Review.tsx:149）、卸载插件（Integrations.tsx:99）、删除供应商档案
  （ProviderHub.tsx:394）、取消学习安排（Community.tsx:158）——20#5 家族独漏此处。
- 传递链条：会话图标紧邻「后端连接状态」徽标，误点即不可恢复地删除该会话的
  全部消息、工具结果与运行历史（agent_messages/agent_runs/agent_approvals 随 CASCADE 清空）。
  影响范围：Agent 重度用户的积累数据；无任何恢复手段。

### 37#2 PlanGenerator 暂存区删除资料无确认——同一操作在资料库页有确认

- 位置：`desktop/src/pages/PlanGenerator.tsx:288`（暂存列表每行垃圾桶
  `onClick={() => void removeMaterial(material.id)}`）、`:219-225`（`removeMaterial`
  直调 `delete_material`）；对照 `desktop/src/pages/Library.tsx:295`（同一
  `delete_material` 操作包 ConfirmDialog：「将同时删除资料记录与可用原件文件……不可恢复」）
- 证据：PlanGenerator 侧无任何确认层；后端 delete_material 为硬删除（原件文件一并删除）。
- 传递链条：方案生成第一步的暂存列表行内图标，误点即删除资料原件；用户在同一应用
  的资料库页面对同一操作见到的却是「有确认护栏」的心智模型，行为不一致放大误删风险。
  影响范围：所有在生成器页管理资料的用户。

### 37#3 dragDropEnabled 开启但全仓零拖放处理器——拖文件入窗无任何反应，且界面视觉暗示可拖

- 位置：`desktop/src-tauri/tauri.conf.json`（window 配置 `"dragDropEnabled": true`）；
  全仓 grep `-i drag` 于 `desktop/src`、`desktop/src-tauri/src` 零命中（无
  `onDragDropEvent`/`tauri://drag-drop` 监听、无任何 React onDrop 处理器）；
  `desktop/src/pages/PlanGenerator.tsx:283`（`source-dropzone` 样式块带云上传图标，
  视觉上是标准拖放目标）
- 证据：Tauri 2 在 WebView2 上启用 dragDrop 后会拦截 OS 级拖放并转发给 JS 事件——
  没有监听器时拖入被静默吞掉（连默认浏览器行为都没有）。
- 传递链条：用户把 PDF/ZIP 拖到「资料输入」的 dropzone 样式区域 → 无反馈、无导入、
  无错误提示，看起来像功能坏了。影响范围：所有新用户的第一直觉操作。
  修复方向二选一：接入 `getCurrentWebview().onDragDropEvent` 做拖入即导入，或关掉
  `dragDropEnabled` 并把 dropzone 样式改为纯按钮语义。

### 37#4 全应用无「在资源管理器中显示/打开位置」能力——绝对路径只能看不能用

- 位置：`desktop/src-tauri/capabilities/default.json`（无 opener 插件授权）、
  `desktop/src-tauri/Cargo.toml`（未引入 tauri-plugin-opener）、后端无
  ShellExecute/explorer 调用；而界面多处只读展示绝对路径：
  `DataDirectoryPanel.tsx:81`（`<code>{overview.dataRoot}</code>` 学习数据目录）、
  `BackupPanel`（导出结果路径）、`ExportCenter`（导出回执路径）、插件资产下载提示等
- 证据：用户例举的「点击打开工作区弹出『你要如何打开这个文件』选择器」在本应用
  **不可能发生**——因为根本不存在打开外部位置的入口；代价是用户拿到一长串
  `C:\Users\...\DeepTutor\learning-data` 路径后只能手动抄写/复制到资源管理器地址栏。
- 传递链条：每次导出备份/报告/导出资料后，用户想找文件必须手动复制路径另开资源
  管理器粘贴。影响范围：所有导出与目录管理场景的可用性；属能力缺口而非缺陷
  （不影响正确性，但与桌面应用的使用预期有落差）。修复方向：引入 opener 插件并
  增加 `reveal-item-in-dir` 按钮（capabilities 同步授权）。

### 37#5 四处导出走 WebView Blob 静默下载——与其余原生保存对话框两套体验

- 位置（Blob + anchor.download 模式）：
  - `desktop/src/pages/Practice.tsx:157-163`（错题导出 `DeepTutor-错题.json`）
  - `desktop/src/pages/ProviderHub.tsx:36-45`（`downloadJson` 脱敏配置）、`:82-91`（插件资产下载）
  - `desktop/src/components/UsagePanel.tsx:80-89`（用量 CSV）
  对照组（原生 `plugin:dialog|save` 可选位置）：`ExportCenter.tsx:106`、`reports.ts:72`、
  `backup.ts:22`、`sharing.ts:31`、`client.ts:344`（资料原件导出）。
- 证据：同一应用内「导出」存在两种行为：一半弹原生保存对话框让用户选位置；
  另一半直接走 WebView2 下载管线落到系统下载目录（且 WebView2 下载条在 Tauri
  壳内的表现未经验证，可能出现零可见反馈）。
- 传递链条：用户在练习页点「导出错题」后找不到文件保存到了哪里（与刚才在导出中心
  选中位置的行为不一致）。影响范围：错题/用量/配置/插件资产四个导出入口。

## 信息性

### 37#6 同一分钟到期的多条预约只 toast 最后一条标题

- 位置：`desktop/src/App.tsx:171-172`（`notify(`预约「${due[due.length - 1].title}」即将开始`)`）；
  后端 `community/mod.rs:969-1015`（claim 一次领取全部到期提醒并全部置 delivered_at）
- 证据：领取是批量的、提示是单条的——其余 N-1 条只进 toast 之外的通道。
  缓解：通知中心有独立的「学习预约即将开始」通道（`engagement/mod.rs:615-641`，
  按房间逐条生成），信息不丢，仅弹层提示以一概全。

### 37#7 SharePlanDialog「复制本机只读地址」复制出的 URL 无任何消费方

- 位置：`desktop/src/backend/sharing.ts:34-36`（`localShareUrl` 基于
  `window.location.origin`，Tauri 生产环境为 `http://tauri.localhost`）、
  `SharePlanDialog.tsx:92/109`（复制按钮）
- 证据：对话框文案诚实（「它不是公共互联网链接」），但该 URL 在应用内无地址栏可粘、
  无 deep-link 注册、浏览器打开必失败——复制动作本身没有可用目的地。
  预览走 `onPreview`（replaceState + 路由），不经过这个 URL。

### 37#8 ProviderHub 预设文档外链 target="_blank" 在 Tauri WebView2 内的行为未验证【待复核】

- 位置：`desktop/src/pages/ProviderHub.tsx:398`（`<a href={...docs} target="_blank" rel="noreferrer">查看官方接入文档</a>`）
- 证据：本仓未启用 opener 插件，也未配置 WebView2 新窗口策略；`target="_blank"`
  触发的 NewWindowRequested 在当前壳内是被拦截（点了没反应）还是转交系统浏览器，
  没有测试或文档结论。这是全应用唯一的对外超链接。需在真实安装包里点一次确认，
  若被拦截应改为显式 opener 调用或去掉链接改为可复制文本。

### 37#9 Library「检查知识库」按钮使用下载图标——动作语义与图标不符

- 位置：`desktop/src/pages/Library.tsx:291`（`<button onClick={() => setCheckOpen(true)}>检查知识库<ArrowDownToLine size={16} /></button>`）
- 证据：`ArrowDownToLine` 在本应用其余位置全部表示「下载/导出」（Plan 导出、
  ProviderHub 导出配置、Integrations 资产下载）；此处动作是打开检查弹窗。
  对照同排其他入口均无图标或用语义匹配图标。

## 传递链条与影响范围汇总

| # | 链条 | 用户可感知影响 |
|---|---|---|
| 37#1 | 顶栏图标 → useAgent.remove → DELETE CASCADE | 误点即永久丢失整个会话历史 |
| 37#2 | 暂存区图标 → delete_material（硬删原件） | 误点删原件；与资料库页同一操作的护栏不一致 |
| 37#3 | OS 拖放 → WebView2 拦截 → 无监听器 → 静默丢弃 | 拖入资料毫无反应，像功能损坏 |
| 37#4 | 无 opener 插件 → 绝对路径仅只读展示 | 导出后需手动抄路径找文件 |
| 37#5 | Blob anchor 下载 → WebView2 下载管线 | 四个导出入口无位置选择、反馈不确定 |
| 37#6 | 批量 claim → 单条 toast | 多条同时到期只见到最后一条标题 |
| 37#7 | clipboard ← tauri.localhost URL | 复制出的链接无处可用 |
| 37#8 | target=_blank → NewWindowRequested（未配置策略） | 行为未知，可能点了没反应 |
| 37#9 | 下载图标 → 打开弹窗 | 图标误导为导出动作 |
