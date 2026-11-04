# 知舟 Deep Tutor · Tauri 2 桌面应用

本目录是唯一用户界面和应用后端。界面来自 `REF/frontend/app`，由 React 18、TypeScript、Vite 与 Tauri 2（Rust）组成，不包含 Electron、独立网页服务或旧桌面宿主。

## 目录

| 路径 | 作用 |
|---|---|
| `src/`、`public/` | 应用页面、组件、样式与静态资产 |
| `src/backend/` | Tauri commands/events 客户端与状态恢复 |
| `index.html`、`vite.config.ts`、`tsconfig.json` | Vite/TypeScript 入口与配置 |
| `package.json`、`package-lock.json` | 界面依赖与 Tauri CLI |
| `src-tauri/src/backend/` | Rust 原生业务、SQLite、事件、文件与网络能力 |
| `src-tauri/tauri.conf.json` | 应用与 NSIS 打包配置 |

`dist/`、`node_modules/`、`src-tauri/target/` 和 `src-tauri/gen/` 是可重建产物，不入库。

## 原生契约

- commands 返回真实结果或类型化错误，events 提供实时输出。
- SQLite 保存业务状态、执行记录与事件 outbox；终态先落盘再发送。
- 事件具有单调递增 id，支持 Last-Event-ID 续接，不重复执行或计费。
- 大文件、原件与导出文件位于应用数据目录，不写入仓库。
- API Key 只进入系统密钥库，不进入 WebView 长期存储、日志、导出或同步载荷。
- 文本模型不识别媒体内容；音视频和图片只提供真实原件预览。

详细工程与安全规则见 [`DEEPTUTOR.md`](../DEEPTUTOR.md)，功能验收矩阵见 [`note/14-Tauri原生后端覆盖清单.md`](../note/14-Tauri原生后端覆盖清单.md)。

## 构建与测试

```bash
cd desktop
npm ci
npm run build
npm run tauri build

cd src-tauri
cargo test --release --locked -- --test-threads=1
```

安装包统一输出到仓库根目录的 `dist/`，例如 `dist/知舟 Deep Tutor_1.0.2_x64-setup.exe`；复制后校验 SHA-256。安装后从开始菜单运行，不需要脚本、虚拟环境或服务。默认数据目录自动创建，可在设置中修改，重启后生效且不自动迁移旧数据；初始化失败在主窗口内提供重试与目录管理入口。

开发调试可运行 `npm run tauri dev`；Vite 仅绑定 `127.0.0.1:1420` 供开发壳使用，不是产品网页入口。

真实供应商、媒体编码播放和交互安装需要单独验收，不能以本机协议夹具或编译结果代替。
