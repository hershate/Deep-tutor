# AGENTS.md — Deep-tutor 仓库工作指引

> 唯一交付物：Tauri 2 + `REF/frontend/app` 的桌面安装包。

## 开工前必读

1. [`note/产品与技术决策.md`](./note/产品与技术决策.md) — 当前有效决策。
2. [`note/14-Tauri原生后端覆盖清单.md`](./note/14-Tauri原生后端覆盖清单.md) — 功能与验收矩阵。
3. [`DEEPTUTOR.md`](./DEEPTUTOR.md) — 工程规约、敏感路径与安全边界。
4. [`desktop/README.md`](./desktop/README.md) 与 [`desktop/PRODUCT.md`](./desktop/PRODUCT.md) — 应用结构与产品边界。
5. [`NOTICE`](./NOTICE) — 来源、许可与修改披露。

## 当前边界

- 唯一 UI 是 `desktop/` 的 Tauri 2 应用。不得恢复浏览器 SPA、Electron、
  `frontend-next`、旧前端插件宿主、剪藏扩展或独立 HTTP 服务。
- 后端位于 `desktop/src-tauri/src/backend/`，前端客户端位于 `desktop/src/backend/`。
  commands 提供请求/响应，events 提供实时输出。
- SQLite、事件 outbox 和异步任务必须真实持久化；终态先落盘再发送，续接不重复执行或计费。
- API Key 只能进入系统密钥库，不得进入 WebView 长期存储、日志、导出或同步载荷。
- 模型只处理文字。媒体预览不冒充 OCR、字幕或内容识别；不可用能力返回类型化错误。
- `stage.md` 已删除。项目现状只通过 **git commit description** 记录，不新建进度日志。

## 工作方式

- 分支：`main`。改造分支 `refactor/reasonix` 已停止使用。细粒度中文 commit，
  禁止 co-author，不主动 push；推送与发布由用户决定。
- 小步实现并逐片验证。commit 说明实际行为、验证结果和未完成边界，不把部分实现写成全部完成。
- 只保留当前有效文档；历史通过 git 追溯。改变本文记录的规则时同步更新本文件。
- 默认不新增依赖或安装环境，先检查项目与 F 盘现有工具和缓存。
- 工作区外删除只使用 `C:\Users\Z\.minimax\bin\mavis-trash.cmd -- <绝对路径>`，小批量执行。
- 不读取图片等多模态内容；不读取或传输 `.env`、密钥、私钥等敏感文件。

## 构建与验证

- 前端：`cd desktop && npm run build`。
- Rust 可复用 `F:\DevCache\toolchains\1.95-x86_64-pc-windows-msvc\bin`，
  同步设置 `CARGO_HOME=F:\DevCache\cargo`、`RUSTUP_HOME=F:\DevCache\rustup`。
- 后端：`cd desktop/src-tauri && cargo test --release --locked -- --test-threads=1`。
- 门禁：`make check`；安装包：`make build`。不要并行启动多个重型测试套件。
- 临时文件使用 `F:\TEMP\deeptutor`，完成后按仓库规约分批清理。
- 禁止前台 UI 自动化、打开原生窗口或对话框、模拟键鼠及读取屏幕。
  使用 MockRuntime、真实 SQLite、无界面 IPC、临时文件和本机回环服务。
- 前端功能须实际调用后端命令，实时功能须验证事件持久化、续接与终态顺序。
- 真实供应商、媒体编码播放和交互安装需要单独验收；协议夹具与构建成功不能替代这些验收。

## 提交前检查

- 相关构建、单测和真实调用必须通过，不能只验证 import/compile。
- 密钥、导出、日志和测试夹具不得包含真实凭据。
- 检查 `git status`，删除测试产生的非必要文件；可复用缓存保留在 F 盘。
- commit description 写清完成内容、验证证据及仍未完成的部分。
