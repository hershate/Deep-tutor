# 37 · 前端交互人性化 III（第十六轮·应用户型专项）

> 审计范围：用户要求的「UI 操作是否人性化」专项——16 页面 + 17 组件全量通读，
> 从真实使用者的操作预期出发。用户例举的「点击打开工作区弹出任务管理器选择」
> 经查**不存在**（全应用无任何打开外部位置的入口，见 37#4）。
> 每条含自包含的传递链条（逐跳 file:line）与影响范围。

## P3

### 37#1 AgentCenter 删除会话无确认——一次点击硬删除整个会话与全部历史

- 位置：`desktop/src/pages/AgentCenter.tsx:104`、`desktop/src/backend/useAgent.ts:216-224`、后端 `desktop/src-tauri/src/backend/agent/mod.rs:1257-1269`
- 证据：
  ```tsx
  <IconButton label="删除当前会话" disabled={!activeSession || running || busy === 'delete'} onClick={() => void removeSession()}>
  ```
  ```rust
  tx.execute("DELETE FROM agent_sessions WHERE id = ?1", params![id])?;  // CASCADE
  ```
- **传递链条**：① Agent 页顶栏垃圾桶（`AgentCenter.tsx:104`，紧邻「后端连接状态」徽标）→ ② `removeSession()`（`:83-86`，无确认层）→ ③ `useAgent.remove`（`useAgent.ts:216-224`，直接 invoke）→ ④ `backend.deleteAgentSession` → `invoke('delete_agent_session')` → ⑤ 后端 `agent::delete_session`（`mod.rs:1257`：仅拦 running，随后 `DELETE FROM agent_sessions`）→ ⑥ 外键 CASCADE 连带清空 `agent_messages / agent_runs / agent_tool_calls / agent_approvals / agent_artifacts` → ⑦ 发 `agent.session_deleted` 事件并刷新列表。
- **影响范围**：误点一次即不可恢复地丢失该会话全部消息、工具结果、运行与审批历史；唯一护栏是 busy/running 时的 disabled。对照同型破坏性操作全部有确认（20#5 家族）：删笔记 `Library.tsx:296`、删资料 `Library.tsx:295`、删记忆卡 `Review.tsx:149`、卸载插件 `Integrations.tsx:99`、删供应商档案 `ProviderHub.tsx:394`、取消学习安排 `Community.tsx:158`——独漏此处。Agent 重度用户的积累数据风险最高。

### 37#2 PlanGenerator 暂存区删除资料无确认——同一操作在资料库页有确认

- 位置：`desktop/src/pages/PlanGenerator.tsx:288`（入口）、`:219-225`（`removeMaterial`）；后端 `desktop/src-tauri/src/backend/materials/mod.rs:1216-1259`；对照 `desktop/src/pages/Library.tsx:295`
- 证据：PlanGenerator 暂存列表每行垃圾桶 `onClick={() => void removeMaterial(material.id)}` 直调 `delete_material`；Library 页对同一命令包 ConfirmDialog（「将同时删除资料记录与可用原件文件……不可恢复」）。
- **传递链条**：① 方案生成第一步暂存列表行内垃圾桶（`:288`）→ ② `removeMaterial`（`:219-225`，无确认）→ ③ `backend.deleteMaterial` → `invoke('delete_material')` → ④ `materials::delete`（`mod.rs:1225-1234`：若被方案/Wiki 引用则 `material_in_use` 拦截——部分护栏）→ ⑤ 未被引用则 `DELETE FROM materials`（CASCADE `material_text/material_jobs`）→ ⑥ `fs::remove_file` 删除磁盘原件（`mod.rs:1245-1257`）。
- **影响范围**：未引用资料一键即删且原件文件一并消失（不可恢复）；已被引用的资料有 `material_in_use` 拦截。主要危害是同一应用内同一操作两套护栏的心智模型不一致——用户在资料库页学到「删除有确认」，在生成器页遭遇「点了就没」。影响所有在生成器管理资料的用户。

### 37#3 dragDropEnabled 开启但全仓零拖放处理器——拖文件入窗无任何反应，且界面视觉暗示可拖

- 位置：`desktop/src-tauri/tauri.conf.json`（`"dragDropEnabled": true`）；全仓 `grep -i drag` 于 `desktop/src`、`desktop/src-tauri/src` 零命中；`desktop/src/pages/PlanGenerator.tsx:283`（`source-dropzone` 样式块带云上传图标）
- 证据：Tauri 2 在 WebView2 上启用 dragDrop 后会拦截 OS 级拖放并转为 JS 事件派发——没有任何 `onDragDropEvent`/`tauri://drag-drop` 监听或 React onDrop 处理器时，拖入被静默吞掉（连默认行为都没有）。
- **传递链条**：① 用户把 PDF/ZIP 拖向「资料输入」的 dropzone 样式区域（`:283`，视觉上是标准拖放目标）→ ② WebView2 拦截 OS 拖放（tauri.conf.json 配置生效）→ ③ 事件派发给 JS → ④ 全仓无监听器接收 → ⑤ 无导入、无提示、无错误——看起来像功能坏了。
- **影响范围**：所有新用户的第一直觉操作（把文件拖进窗口）；不影响键盘/按钮路径。修复方向二选一：接入 `getCurrentWebview().onDragDropEvent` 做拖入即导入，或移除 `dragDropEnabled` 并把 dropzone 样式改为纯按钮语义。

### 37#4 全应用无「在资源管理器中显示/打开位置」能力——绝对路径只能看不能用

- 位置：`desktop/src-tauri/capabilities/default.json`（无 opener 授权）、`desktop/src-tauri/Cargo.toml`（未引入 tauri-plugin-opener）、后端无 ShellExecute/explorer 调用；路径展示点：`DataDirectoryPanel.tsx:81`（`<code>{overview.dataRoot}</code>`）、BackupPanel 导出结果、ExportCenter 回执（`:120` SHA-256 与字节数）、插件资产下载提示等
- 证据：用户例举的「点击打开工作区弹出『你要如何打开这个文件』选择器」在本应用**不可能发生**——因为不存在打开外部位置的入口；代价是路径只读展示。
- **传递链条**：① 导出备份/报告/资料成功，回执给出落盘路径 → ② 界面无任何「打开所在文件夹」按钮（无 opener 插件、无后端打开命令）→ ③ 用户只能选中复制路径 → ④ 手动开资源管理器粘贴导航。
- **影响范围**：所有导出与目录管理场景的可用性落差；属能力缺口而非缺陷（不影响正确性与安全）。修复方向：引入 opener 插件并加 `reveal-item-in-dir` 按钮（capabilities 同步授权、注意与 CSP/外链策略一并收口）。

### 37#5 四处导出走 WebView Blob 静默下载——与其余原生保存对话框两套体验

- 位置（Blob + anchor.download 模式）：
  - `desktop/src/pages/Practice.tsx:157-163`（错题导出 `DeepTutor-错题.json`）
  - `desktop/src/pages/ProviderHub.tsx:36-45`（`downloadJson` 脱敏配置）、`:82-91`（插件资产下载）
  - `desktop/src/components/UsagePanel.tsx:80-89`（用量 CSV）
  对照组（原生 `plugin:dialog|save` 可选位置）：`ExportCenter.tsx:106`、`reports.ts:72`、`backup.ts:22`、`sharing.ts:31`、`client.ts:344`。
- **传递链条**：① 用户点「导出错题/下载资产/导出 CSV」→ ② 前端 invoke 拿到内容 → ③ `URL.createObjectURL(Blob)` + `anchor.download` + `click()`（`:159-162` 等四处）→ ④ WebView2 下载管线接管：落到系统默认下载目录，Tauri 壳内是否有可见下载反馈未经真实安装包验证 → ⑤ 对照组路径：`plugin:dialog|save` 弹原生保存对话框由用户选位置（capabilities `dialog:allow-save` 已授权）。
- **影响范围**：四个导出入口的用户拿不到位置选择权、可能找不到文件（与导出中心/备份/报告的行为不一致）；WebView2 下载条在 Tauri 壳内的表现属「未验证行为」。修法：统一改走 `saveMaterialFile` 同款原生对话框。

## 信息性

### 37#6 同一分钟到期的多条预约只 toast 最后一条标题

- 位置：`desktop/src/App.tsx:171-172`；后端 `desktop/src-tauri/src/backend/community/mod.rs:969-1015`
- **传递链条**：① 应用打开期间每 60s `claimDueReminders`（`App.tsx:169-176`）→ ② 后端一次领取全部到期提醒并**全部**置 `delivered_at`（`mod.rs:991-995`），逐条发 `community.reminder_due` 事件 → ③ 前端 `notify(`预约「${due[due.length - 1].title}」即将开始`)`（`:172`）只取最后一条弹 toast → ④ 其余 N-1 条的弹层提示缺失（delivered 已置位，不会下轮再弹）。
- **影响范围**：多条预约同时到期（或应用关闭一段时间后首次领取）时，用户只见最后一条标题；信息不丢——通知中心另有独立通道按房间逐条生成「学习预约即将开始」（`engagement/mod.rs:615-641`）。仅弹层以一概全。

### 37#7 SharePlanDialog「复制本机只读地址」复制出的 URL 无任何消费方

- 位置：`desktop/src/backend/sharing.ts:34-36`（`localShareUrl` 基于 `window.location.origin`）；`SharePlanDialog.tsx:92/109`
- **传递链条**：① 点「复制」→ ② `navigator.clipboard.writeText(localShareUrl(token))` → ③ `localShareUrl` = `${window.location.origin}${pathname}?share=…#shared`——Tauri 生产环境 origin 为 `http://tauri.localhost` → ④ 该 URL 无处可用：应用内无地址栏可粘贴、无 deep-link 插件注册（main.rs 无 deep-link）、粘贴到系统浏览器必失败 → ⑤ 应用内预览实际走 `onPreview`（replaceState + 路由），不经此 URL。
- **影响范围**：复制动作本身是死重量；对话框文案诚实（「它不是公共互联网链接；请将分享包发给接收者」），真正给接收者的是 `.dtshare` 文件下载。可考虑移除复制按钮或改为复制纯令牌。

### 37#8 ProviderHub 预设文档外链 target="_blank" 在 Tauri WebView2 内的行为未验证【待复核】

- 位置：`desktop/src/pages/ProviderHub.tsx:398`（`<a href={…docs} target="_blank" rel="noreferrer">查看官方接入文档</a>`）
- **传递链条**：① 用户点预设（DeepSeek/Anthropic/OpenAI 等）编辑器中的文档链接 → ② `target="_blank"` 触发 WebView2 `NewWindowRequested` → ③ 本仓未启用 opener 插件、未配置任何新窗口/导航策略（capabilities 仅四项权限）→ ④ 结果未经验证：可能被 wry 拦截（点了没反应）也可能转交系统浏览器——无测试、无文档结论。
- **影响范围**：全应用唯一对外超链接；若被拦截则用户点了无反应（类似 37#3 的静默失败）。判定需在真实安装包点一次确认；若拦截应改显式 opener 调用或退化为可复制文本。

### 37#9 Library「检查知识库」按钮使用下载图标——动作语义与图标不符

- 位置：`desktop/src/pages/Library.tsx:291`
- **传递链条**：① 页面底部「检查知识库」按钮携带 `ArrowDownToLine` 图标 → ② 点击动作实为 `setCheckOpen(true)` 打开检查结果弹窗（读取类操作）→ ③ 该图标在本应用其余位置一律表示「下载/导出」（Plan 导出、ProviderHub 导出配置、Integrations 资产下载、ExportCenter）→ ④ 用户误以为会下载什么东西。
- **影响范围**：纯图标语义误导，无功能损害；同排其他入口无图标或用语义匹配图标。改用 `ShieldCheck`/`Search` 类图标即可。
