# AGENTS.md — Deep-tutor 仓库工作指引

> 当前唯一交付物：Tauri 2 + `REF/frontend/app` 的桌面安装包。
> 当前实施任务：REF 全部功能接入 Tauri 原生后端，使用真实实时输出。

## 开工前必读

1. [`note/产品与技术决策.md`](./note/产品与技术决策.md) — 当前产品与技术决策。
2. [`note/14-Tauri原生后端覆盖清单.md`](./note/14-Tauri原生后端覆盖清单.md) — 全功能覆盖与验收矩阵。
3. [`DEEPTUTOR.md`](./DEEPTUTOR.md) — 工程规约、敏感路径与安全边界。
4. [`desktop/README.md`](./desktop/README.md) 与 [`desktop/PRODUCT.md`](./desktop/PRODUCT.md) — 应用结构及当前能力边界。
5. [`NOTICE`](./NOTICE) — 上游来源、许可与修改披露。

## 当前边界

- 唯一 UI 是 `desktop/` 的 Tauri 2 应用。不得重新引入浏览器 SPA、Electron、
  `frontend-next`、旧前端插件宿主或剪藏扩展。
- 前后端在 Tauri 进程内通过 commands/events 连接，不恢复独立 HTTP 服务。
- SQLite、事件 outbox 和后端异步任务必须真实持久化并输出结果；
  不得以 `setTimeout`、假数据或模拟成功代替实现。
- API Key 只能进入系统密钥库，不得进入 WebView 长期存储、日志、导出或同步载荷。
- 既有 Python/Reasonix 源码只在新实现覆盖后按冗余清理，不先删后缺。
- `stage.md` 已删除。项目现状只通过 **git commit description** 记录，
  不再新建进度日志或等效替代文件。

## 工作方式

- 分支：`refactor/reasonix`。细粒度中文 commit，禁止 co-author，**不 push**。
- 小步实现并逐片验证。每次 commit 说明实际行为、验证结果与未完成边界，
  不把计划或部分实现写成全部完成。
- 修改文档时只保留当前有效内容；历史通过 git 历史追溯，不保留重复档案。
- 修改 `AGENTS.md` 记录的规则时，同步更新本文件。
- 需要产品级新方向时先看当前产品决策；不要沿用旧四前端、
  全登录服务、shim 或 Reasonix Web 服务方案。
- 默认不新增依赖或安装环境。确需安装先说明必要性并征得同意；
  先检查项目及 F 盘现有工具与缓存。
- 文字模型约束：不读取图片等多模态内容。

## 构建与验证

- Node 24+：`cd desktop && npm run build`。
- Rust 可复用：`F:\DevCache\toolchains\1.95-x86_64-pc-windows-msvc\bin`，
  不必安装 rustup。同步设置 `CARGO_HOME=F:\DevCache\cargo`、
  `RUSTUP_HOME=F:\DevCache\rustup`。
- 后端测试：`cd desktop/src-tauri && cargo test --release`。
- 保留的 Go 代码：`go vet ./...`、`go test ./tools/repolint`。
- 变更门禁：`make check`；Application Control 拦截新 Go 二进制时，
  构建到 `F:\TEMP\deeptutor\tb` 后运行。Rust 构建脚本被拦截时优先复用 release 缓存。
- 全量测试不要并行启动多个重套件。`internal/frontend/serve` 需单独长超时运行。

## 提交前检查

- 当前改动相关的构建、单测和真实调用必须通过，不能只验证 import/compile。
- 前端功能须实际调用后端命令；实时功能须验证事件落盘与续接。
- 密钥、导出、日志和测试中不得出现真实凭据。
- 检查 `git status`，删除测试产生的非必要文件；可复用缓存保留在 F 盘。
- commit description 写清完成内容、验证证据及仍未完成的部分。
