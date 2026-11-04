# 知舟 Deep Tutor

**版本 1.0.2 · 唯一交付形态：Tauri 2 桌面安装包**

界面来自用户提供的 `REF/frontend/app`，由 React 18、TypeScript、Vite 和 Tauri 2（Rust）组成。前后端在应用进程内通过 commands/events 连接，不需要启动网页、HTTP 服务、Electron 或旧脚本。

## 产品与数据

应用提供学习计划与方案生成、讲义与知识地图、练习与模拟考试、间隔复习、Agent、自由笔记、KB/Wiki、项目成果、学习报告、本机社区、模型供应商、声明式插件、备份分享、导入导出、同步与本地访问安全。

- 业务状态、任务执行、版本和事件 outbox 保存于应用数据目录的 SQLite。
- 材料原件、媒体和导出文件保存在应用数据目录，不随仓库分发。
- 模型与网页请求由 Rust 实际执行；不可用时返回明确错误，不生成模拟结果。
- API Key 只进入系统密钥库。日志、导出、同步载荷和测试夹具不得包含明文密钥。
- 文本模型不处理图像或音频识别；媒体仅作真实原件预览，不伪造 OCR、字幕或内容分析。

功能与验收矩阵见 [`note/14-Tauri原生后端覆盖清单.md`](./note/14-Tauri原生后端覆盖清单.md)。

## 构建安装包

### 环境

- Windows 与 WebView2。
- Node.js 24+ 和 npm。
- Rust/Cargo 与 MSVC 构建环境。

### 命令

```bash
cd desktop
npm ci
npm run tauri build
```

该命令执行 TypeScript/Vite 构建、Rust release 构建和 NSIS 打包。最终安装包统一输出到仓库根目录，并在复制后校验 SHA-256：

```text
dist/
```

例如 `dist/知舟 Deep Tutor_1.0.2_x64-setup.exe`。

安装后从开始菜单启动 **知舟 Deep Tutor**，不需要运行脚本或配置服务器。默认学习数据目录自动创建，主窗口直接显示初始化状态；可在“设置 → 隐私、备份与数据”修改目录，重启后生效且不自动迁移旧数据。目录、数据库或后端连接失败时，主窗口内提供重试、修改目录和恢复默认的入口。

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
├── note/                    # 当前产品决策与功能验收矩阵
├── plan/                    # 当前验证契约
├── .github/workflows/       # Tauri 2 无界面 CI
├── Makefile                 # 构建与检查入口
├── NOTICE                   # 来源与许可披露
└── REF/                     # 用户提供的参考源，非交付目录
```

旧浏览器前端、Electron、Python/Go 后端、网页启动脚本、`stage.md`、旧服务端文档和改造档案已删除。历史只通过 git 追溯，不保留重复文档。

## 验证

```bash
make lint                         # Rust 格式与 TypeScript/Vite 构建
make test                         # 原生后端全量测试，串行执行
make check                        # 上述全部检查
make build                        # Tauri 2 NSIS 安装包
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
