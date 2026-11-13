# 知舟 Deep Tutor

**版本 1.1.14 · 唯一交付形态：Tauri 2 桌面安装包**

界面来自用户提供的 `REF/frontend/app`，由 React 18、TypeScript、Vite 和 Tauri 2（Rust）组成。前后端在应用进程内通过 commands/events 连接，不需要启动网页、HTTP 服务、Electron 或旧脚本。

## 产品与数据

应用提供学习计划与方案生成、讲义与知识地图、练习与模拟考试、间隔复习、Agent、自由笔记、KB/Wiki、项目成果、学习报告、本机社区、模型供应商、声明式插件、备份分享、导入导出、同步与本地访问安全。

- 业务状态、任务执行、版本和事件 outbox 保存于应用数据目录的 SQLite。
- 材料原件、媒体和导出文件保存在应用数据目录，不随仓库分发。
- 模型与网页请求由 Rust 实际执行；不可用时返回明确错误，不生成模拟结果。
- API Key 只进入系统密钥库。日志、导出、同步载荷和测试夹具不得包含明文密钥。
- 文本模型不处理图像或音频识别；媒体仅作真实原件预览，不伪造 OCR、字幕或内容分析。

首次使用时可直接添加任务、笔记、项目或记忆卡。制定学习方案只需填写目标、当前基础、时间与日期，辅助资料为 0–8 份可选项；无资料方案继续使用通用知识框架，不伪造原文引用。全局导师与 Agent 支持不绑定方案的通用问答。首个启用的模型档案自动成为默认项，后续新增档案不覆盖已有选择。

功能与验收矩阵见 [`note/14-Tauri原生后端覆盖清单.md`](./note/14-Tauri原生后端覆盖清单.md)。

## 构建安装包

### 环境

- Windows 与 WebView2。
- Node.js 24（`>=24 <25`）和 npm。
- Rust/Cargo 与 GNU 工具链（rust-toolchain.toml 钉住 1.99.1，目标 x86_64-pc-windows-gnu；链接依赖 dlltool，Windows 上由 winlibs MinGW-w64 提供）。

### 命令

推荐在仓库根目录执行：

```bash
make build
```

也可直连 Tauri 构建（第三方许可收集挂在 `beforeBuildCommand` 的 `npm run build:bundle`，两条路径同一口径）：

```bash
cd desktop
npm ci
npm run tauri build -- -- --locked
```

`make build` 执行 TypeScript/Vite 构建、Rust release 构建和 NSIS 打包。最终安装包统一输出到仓库根目录，并在复制后校验 SHA-256：

```text
dist/
```

例如 `dist/DeepTutor_<版本号>_x64-setup.exe`。

安装后从开始菜单的 **DeepTutor** 目录启动 **DeepTutor**（快捷方式与目录同名，窗口标题为「知舟 Deep Tutor · 个人学习操作系统」），不需要运行脚本或配置服务器。默认学习数据目录自动创建，主窗口直接显示初始化状态；可在“设置 → 隐私、备份与数据”修改目录，保存后重试或下次启动使用新目录，不自动迁移旧数据。目录、数据库或后端连接失败时，主窗口内提供重试、修改目录和恢复默认的入口。

开发调试可运行 `npm run tauri dev`。Vite 只绑定 `127.0.0.1:1420` 供 Tauri 开发壳使用，不是产品网页入口。

## 当前目录

```text
Deep-tutor/
├── desktop/                 # 唯一应用界面与 Tauri 2 安装包
│   ├── src/                 # React 页面、样式与原生后端客户端
│   ├── public/              # 静态资产
│   ├── src-tauri/           # Rust 壳、原生后端与 NSIS 配置
│   └── package.json         # 界面依赖与 Tauri CLI
├── dist/                    # 最终安装包，可重建且不入库
├── docs/architecture/       # 架构与运维原理文档（状态机、模块清单、工作流）
├── note/                    # 当前产品决策与功能验收矩阵
├── plan/                    # 当前验证契约
├── .github/workflows/       # Tauri 2 无界面 CI
├── .githooks/pre-push       # 推送前执行 make check
├── scripts/                 # 版本校验、dev 冒烟与第三方许可收集
├── Makefile                 # 构建与检查入口
├── rust-toolchain.toml      # 钉住 Rust 1.99.1（CI 与本地一致）
├── LICENSE / LICENSE-REASONIX # 本仓库 Apache-2.0 与历史上游 MIT 许可全文
├── NOTICE                   # 来源与许可披露
└── REF/                     # 用户提供的参考源，非交付目录
```

旧浏览器前端、Electron、Python/Go 后端、网页启动脚本、`stage.md`、旧服务端文档和改造档案已删除。历史只通过 git 追溯，不保留重复文档。

## 验证

```bash
make lint                         # Rust 格式、六处版本校验、TypeScript/Vite 构建与 npm audit（runtime+dev 双口径）
make test                         # 原生后端全量测试，串行执行
make dev-smoke                    # dev server 冒烟：端口预检（被占即败）→ 起 vite（43198）→ 断言 index 含应用标记 → 树杀进程树 + 按端口兜底孤儿
make check                        # 上述全部检查
make build                        # Tauri 2 NSIS 安装包
make hooks                        # 启用 .githooks/pre-push（推送前 make check）
```

也可直接运行：

```bash
cd desktop && npm run build
cd desktop/src-tauri && cargo test --release --locked -- --test-threads=1
```

测试使用 MockRuntime、真实 SQLite、临时文件和本机回环服务，不打开窗口、模拟键鼠或截屏。真实供应商、真实媒体编码播放及交互安装仍需单独验收，不能由协议夹具或构建成功替代。

## 文档与许可

- 当前决策：[`note/产品与技术决策.md`](./note/产品与技术决策.md)
- 验收契约：[`plan/README.md`](./plan/README.md)
- 工程规约：[`DEEPTUTOR.md`](./DEEPTUTOR.md)
- 桌面说明：[`desktop/README.md`](./desktop/README.md)
- 来源与许可：[`NOTICE`](./NOTICE)、[`LICENSE-REASONIX`](./LICENSE-REASONIX)

历史上游参考为 [DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)（MIT）。当前界面来自用户提供的 `REF/frontend/app`，Tauri 与依赖适用各自许可。
