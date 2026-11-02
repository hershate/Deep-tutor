# 知舟 Deep Tutor

> **版本**：1.0.0 · **唯一交付形态**：Tauri 2 桌面安装包
>
> 界面来自用户提供的 `REF/frontend/app`，由 React 18、TypeScript、Vite 和
> Tauri 2（Rust）组成。项目不再制作或交付浏览器 SPA、Electron、
> `frontend-next`、网页启动脚本或服务器运行环境。

## 1. 应用能力

- 今日学习航线、AI 方案生成向导、学习计划与阅读工作区。
- 知识地图、Agent 工作台、练习测评、间隔复习与项目作品。
- 自由笔记、KB 文档、Wiki 引用问答、学习报告与社区界面。
- 模型供应商档案、任务路由、故障转移与插件事件界面。
- 数据备份、只读分享、访问安全、主题与无障碍设置。

**真实性边界**：当前 REF 界面是本地交互演示。AI 回复、SSE、供应商测试、
云同步、安全策略和后台任务均为界面模拟；学习数据与设置保存在应用
WebView 的本地存储中。不得据此宣称真实后端、模型服务或云同步已经接通。

## 2. 安装包构建

### 环境

- Windows（当前安装目标）。
- Node.js 24+ 与 npm。
- Rust/Cargo（Tauri 2 原生构建必需）。
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
当前用户安装与开始菜单目录。安装后从开始菜单启动
**知舟 Deep Tutor**，不需要再运行任何脚本或启动服务。

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
│   ├── src/                 # REF 提供的 React 页面与组件
│   ├── public/              # 静态资产
│   ├── src-tauri/           # Rust 壳与 NSIS 配置
│   ├── package.json         # 界面依赖与 Tauri CLI
│   └── PRODUCT.md           # 产品能力与演示边界
├── backend/                 # 既有 Python 源码，保留但不是应用运行前提
├── internal/                # Reasonix Go 内核源码，保留但不是应用运行前提
├── cmd/                     # 保留的 Go 命令（旧 Studio host 已删除）
├── sdk/                     # 扩展 SDK
├── tools/                   # 工程门禁
├── docs/                    # 历史与内核文档
├── note/                    # 领域笔记与最新决策修订
├── plan/                    # 改造方案与最新执行路径
└── REF/                     # 用户提供的参考源，非交付目录
```

已删除：`frontend/`、`desktop/frontend-next/`、`desktop/electron/`、
`plugin-examples/frontend/`、`extension/`、旧桌面发布工具、
`cmd/reasonix-studio-host` 和两个网页启动脚本。后端源码的保留不表示
安装应用后还要单独启动服务端。

## 5. 验证

```bash
cd desktop && npm run build       # TypeScript + Vite
make check                        # 变更范围 repolint
go vet ./...                      # 保留的 Go 源码静态检查
make test                         # UI 构建 + Go 测试
make studio                       # Tauri 2 + NSIS 安装包
```

Rust/Cargo 未安装时只能验证 TypeScript/Vite 与配置，不能声称原生安装包
已经构建成功。构建报告以 `stage.md` 的最新记录为准。

## 6. 文档与许可

- 最新决策：[`note/12-Reasonix改造决策.md`](./note/12-Reasonix改造决策.md)
- 当前执行路径：[`plan/README.md`](./plan/README.md)
- 进度：[`stage.md`](./stage.md)
- 桌面运行与边界：[`desktop/README.md`](./desktop/README.md)
- 产品说明：[`desktop/PRODUCT.md`](./desktop/PRODUCT.md)
- 引用与修改披露：[`NOTICE`](./NOTICE)

内核与桌面端历史代码吸收自
[DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)（MIT）。
原许可保留在 [`LICENSE-REASONIX`](./LICENSE-REASONIX)。
当前界面来自用户提供的 `REF/frontend/app`；旧前端已按最新决策删除，
其历史内容仍可从 git 历史追溯。
