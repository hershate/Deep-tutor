# 72 · Tauri 安全配置与外链专项（第二十七轮·此前 26 轮未专项覆盖）

> 审计范围：tauri.conf.json/capabilities/Cargo feature/main.rs builder/前端
> plugin 调用面 + 用户点名场景（弹选择器/闪命令行）复查。tauri 2.12.5 /
> wry 0.57.1 / opener 2.7.2 / open 5.4.5 源码级核验。主审已抽验 C-1 属实。

## P3

### 72#1（=C-1）Markdown 渲染链接可顶层导航至任意外域——无地址栏钓鱼窗口（唯一 opener 绕过路径）

- 位置：`desktop/src/pages/Library.tsx:299`（笔记预览）、`desktop/src/pages/StudyRoom.tsx:263-266`（AI 讲义四处 ReactMarkdown）；根因为框架默认放行（tauri on_navigation 未注册，插件默认 `|_,_| true`）。
- 传递链条：① 讲义（模型输出）/笔记（用户输入）中的 `[text](https://attacker.example)` → ② ReactMarkdown 默认渲染裸 `<a href>`（无拦截）→ ③ 点击触发 WebView **顶层导航**，Tauri 默认放行 → ④ 整个应用窗口被替换为攻击者页面——`decorations:true` 原生标题栏无地址栏，构成钓鱼窗口。CSP `default-src 'self'` 不拦顶层导航；远程 origin 拿不到 capability（local:true）故无 IPC 提权，定级中。
- 影响范围：提示注入（资料/网页导入→讲义）或恶意分享包可在无地址栏窗口渲染钓鱼页；全站仅 ProviderHub.tsx:391 文档链接做了 preventDefault+openExternalUrl。
- 修复方向：两处 ReactMarkdown 传 `components.a`——非锚点 href 一律 preventDefault 后走 openExternalUrl（系统浏览器打开）；或注册 `on_navigation` 白名单（tauri:// + dev 的 127.0.0.1:1420）。

### 72#2（=C-2）opener:allow-reveal-item-in-dir 未设路径 scope

- 位置：`desktop/src-tauri/capabilities/default.json:11`。
- 传递链条：openUrl 有 `https://**` scope，reveal 无对应约束——被注入脚本调用时可 reveal 本机任意路径（资源管理器定位任意敏感文件）。当前 3 个调用点（DataDirectoryPanel/ExportCenter/BackupPanel）传后端受控路径，实际可利用性低（需先攻破脚本层）。
- 影响范围：信息泄露面扩大。
- 修复方向：reveal 权限加 scope 限定到数据目录与导出目录前缀。

## 信息性

### 72#3（=C-3）NSIS 安装包未配置代码签名

- `tauri.conf.json` bundle.windows.nsis 无 certificateThumbprint、CI 无 sign 步骤——SmartScreen 拦截/未知发布者警告训练用户无视警告，亦留中间人替换空间。有发布渠道后补签名+timestamp。

### 72#4（=C-4）WebView2 默认右键菜单未禁用；style-src 'unsafe-inline'

- 右键菜单保留「后退/刷新/另存为」（release 已禁 devtools，「检查」不可用）；`style-src 'unsafe-inline'` 为 React 内联样式常规妥协，`script-src 'self'` 无 unsafe-eval。可选收紧，非必须。

### 72#5（=C-5）「零子进程」结论的框架级注记

- `restart_application`（startup.rs:510-512）→ tauri `app.restart()` 在 Windows 以 `Command::new(自身exe).spawn()` 重启——用户显式点「重启应用」才触发的一次性自重启。结论表述宜为「业务代码零 Command::new；唯一 spawn 为框架自重启（自 exe）」。

## 已核实（配置正确面）

- CSP 生产严格：`script-src 'self'`（无 unsafe-inline/eval）、`connect-src` 仅 `ipc:`（全部网络出口走 Rust reqwest）、img/media 限 blob:；devCsp 单独放宽 dev 端口。
- withGlobalTauri 默认 false 且前端无 `__TAURI__`；updater/assetProtocol 未启用（无插件、无配置）；媒体读取走自定义 IPC（46#2）。
- bundle 仅 nsis、currentUser 免 UAC；窗口与 capability `windows:["main"]` 一致；release devtools 关闭（无 feature/无配置）。
- capabilities 最小权限 6 条（core:event listen/unlisten、dialog open/save、opener 两权限）；未授予 core:default 大礼包、无 fs ACL（fs 仅为 dialog 传递依赖被编译，IPC 面未授权）；gen/schemas 快照一致。
- 单实例（命名互斥体+FindWindowW 前置）；guard_handler 会话门控+写锁+principal 传播。
- 前端零 emit、零 window.open/target=_blank/外域 location.href/eval；wry NewWindowRequested 未注册 handler 时 SetHandled(true) 拒绝——中键/新窗口无绕过。
- **用户点名场景复查维持 58 号结论**：业务代码零 `std::process::Command`（全部 .spawn 为线程）；opener 2.7.2 的 open crate `shellexecute-on-windows` 为**非 optional 强制 feature**——openUrl/reveal 全走 ShellExecuteExW/SHOpenFolderAndSelectItems 纯 API，零子进程零控制台闪窗；open crate 的 powershell/explorer spawn fallback 编译不可达；无 ffmpeg/终端/外部二进制。
