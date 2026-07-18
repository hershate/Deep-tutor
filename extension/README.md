# Deep-tutor 剪藏（浏览器扩展，P4 iter 65）

把选中文本 / 网页链接一键剪藏到 Deep-tutor（`POST /api/materials/clip`：建会话 + 解析资料一步到位），
随后在 Web 端打开生成页即可基于剪藏资料生成学习方案。

## 安装（开发者模式加载）

1. Chrome / Edge 打开 `chrome://extensions`，开启「开发者模式」。
2. 「加载已解压的扩展程序」-> 选择本目录（`extension/`）。
3. 打开扩展「选项」页：填写服务器地址（默认 `http://localhost:41173`）与 Token
   （Deep-tutor 登录后的 JWT，或服务端 `.env` 的 `AUTH_TOKEN`），点「测试连接」确认。

## 使用

- **右键菜单**：选中文本 ->「剪藏选中内容到 Deep-tutor」；页面空白处 ->「剪藏本页链接到 Deep-tutor」。
- **工具栏弹窗**：「剪藏选中内容」/「剪藏本页链接」按钮；成功后出现「打开并生成学习方案 →」链接
  （跳 `/generate/{session_id}`，进入即自动开始生成，raw_text 为空仅用剪藏资料）。
- 图标角标 ✓/✗ 指示剪藏成败。

## 说明

- Manifest V3，纯 JS 无构建步骤；权限：`contextMenus`/`storage`/`activeTab`/`scripting` + `<all_urls>`
  （后台 fetch 需目标服务器源的 host 权限；服务器地址由用户在选项页配置）。
- Token 仅存 `chrome.storage.local`（本机）；剪藏内容经 HTTPS/HTTP 发往用户自配服务器。
- 剪藏会话归属 Token 对应的用户（P4 数据隔离，iter 58）。
