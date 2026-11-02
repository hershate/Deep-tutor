# 知舟 Deep Tutor

> **版本**：1.0.0 · **唯一交付形态**：Tauri 2 桌面安装包
>
> 界面来自用户提供的 `REF/frontend/app`，由 React 18、TypeScript、Vite 和
> Tauri 2（Rust）组成。项目不再交付浏览器 SPA、Electron、网页启动脚本或服务器环境。
> 当前正在把 REF 的全部功能接入 Tauri 原生后端，使用真实实时输出。

## 1. 产品边界

应用包含今日、AI 方案生成、学习计划、学习空间、知识地图、Agent 工作台、
练习测评、间隔复习、自由笔记、KB 文档与 Wiki 问答、项目作品、学习报告、
学习社区、模型接入、插件事件、备份、只读分享和访问安全设置。

**当前真实性边界**：REF 界面已能构建和运行，但全功能原生后端仍在分片实现。
在对应切片完成前，部分页面仍含本地状态或模拟逻辑，不据此宣称全部模型、
同步、生成和安全能力已经接通。覆盖矩阵见
[`note/14-Tauri原生后端覆盖清单.md`](./note/14-Tauri原生后端覆盖清单.md)。

## 2. 安装包构建

### 环境

- Windows（当前安装目标）。
- Node.js 24+ 与 npm。
- Rust/Cargo（Tauri 2 原生构建必需）。本机可复用
  `F:\DevCache\toolchains\1.95-x86_64-pc-windows-msvc`。
- WebView2 与 MSVC。

### 构建

```bash
cd desktop
npm ci
npm run tauri build
```

该命令依次执行 TypeScript/Vite 生产构建、Rust 构建和 NSIS 打包。
安装包输出到：

```text
desktop/src-tauri/target/release/bundle/nsis/
```

`tauri.conf.json` 已启用 `bundle.active=true`、`targets=["nsis"]`、
当前用户安装与开始菜单目录。安装后从开始菜单启动 **知舟 Deep Tutor**，
不需要再运行任何脚本或启动服务。

CI 的 `Tauri 2 application` job 在 Windows 上执行同一条构建命令，
并上传 `deep-tutor-windows-nsis` 构建附件。

## 3. 开发调试

```bash
cd desktop
npm run tauri dev
```

Vite 仅绑定 `127.0.0.1:1420`，供 Tauri 开发壳使用，不是产品网页入口。
仓库不再提供 `scripts/start.ps1`、`scripts/start.sh` 或其他 venv、
pip、uvicorn、浏览器/API 服务启动方式。

## 4. 当前目录

```text
Deep-tutor/
├── desktop/                 # 唯一应用界面与 Tauri 2 安装包
│   ├── src/                 # REF 提供的 React 页面与原生后端客户端
│   ├── public/              # 静态资产
│   ├── src-tauri/           # Rust 壳、原生后端与 NSIS 配置
│   ├── package.json         # 界面依赖与 Tauri CLI
│   └── PRODUCT.md           # 产品能力与真实性边界
├── backend/                 # 既有 Python 源码，保留待等价替换，不是运行前提
├── internal/                # 既有 Reasonix Go 源码，保留待等价替换
├── cmd/                     # 保留的 Go 命令（旧 Studio host 已删除）
├── sdk/                     # 扩展 SDK
├── tools/                   # 工程门禁
├── docs/                    # 上游溯源文档
├── note/                    # 当前决策与功能覆盖清单
├── plan/                    # 当前执行路径
└── REF/                     # 用户提供的参考源，非交付目录
```

已删除：旧前端、Electron、旧桌面发布工具、网页启动脚本、`stage.md`、
旧 `API.md`、旧 Reasonix 六份方案、旧 Python 分析笔记和 `docs/archive`。
这些历史内容只通过 git 历史追溯，不另建重复档案。

## 5. 验证

```bash
cd desktop && npm run build       # TypeScript + Vite
cd desktop/src-tauri && cargo test --release
make check                        # 变更范围 repolint
go vet ./...                      # 保留的 Go 源码静态检查
make test                         # UI 构建 + Go 测试
make studio                       # Tauri 2 + NSIS 安装包
```

仅编译或界面展示不能证明后端功能完成。功能验收须覆盖真实命令、
持久化、实时事件续接以及安装包内运行。

## 6. 文档与许可

- 当前决策：[`note/产品与技术决策.md`](./note/产品与技术决策.md)
- 覆盖清单：[`note/14-Tauri原生后端覆盖清单.md`](./note/14-Tauri原生后端覆盖清单.md)
- 当前执行路径：[`plan/README.md`](./plan/README.md)
- 工程规约：[`DEEPTUTOR.md`](./DEEPTUTOR.md)
- 桌面运行与边界：[`desktop/README.md`](./desktop/README.md)
- 引用与修改披露：[`NOTICE`](./NOTICE)

内核与桌面端历史代码吸收自
[DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)（MIT）。
原许可保留在 [`LICENSE-REASONIX`](./LICENSE-REASONIX)。
当前界面来自用户提供的 `REF/frontend/app`。
