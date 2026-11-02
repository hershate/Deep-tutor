# 知舟 Deep Tutor · Tauri 2 桌面应用

本目录是唯一用户界面的来源。界面来自用户提供的 `REF/frontend/app`，
由 React 18、TypeScript、Vite 与 Tauri 2（Rust 壳）组成。
不再交付旧浏览器 SPA、Electron 或 `frontend-next`。

## 目录

| 路径 | 作用 |
|---|---|
| `src/`、`public/` | 用户提供的应用界面与静态资产 |
| `index.html`、`vite.config.ts`、`tsconfig.json` | Vite/TypeScript 入口与配置 |
| `package.json`、`package-lock.json` | 界面依赖与 Tauri CLI |
| `src-tauri/` | Tauri 2 配置、Rust 入口和构建脚本 |
| `cmd/`、`internal/`、`packaging/`、`go.mod` | 既有 Go 桌面发布工具，不是用户界面 |

`dist/`、`node_modules/` 与 `src-tauri/target/` 都是可重建产物，不入库。

## 环境

- Node.js 24+ 与 npm。
- Rust/Cargo（Tauri 2 原生构建必需）。
- Windows：WebView2 与 MSVC；其他平台使用 Tauri 对应的系统 WebView。

## 运行与验证

```bash
cd desktop
npm install
npm run build          # TypeScript + Vite 生产构建
npm run tauri dev      # 启动 Tauri 2 开发应用
npm run tauri build    # 构建原生应用
```

Vite 开发服务只绑定 `127.0.0.1:1420`，不作为浏览器产品入口。
`tauri.conf.json` 的 `frontendDist` 指向 `../dist`，与 Vite 输出一致。

当前 `bundle.active=false`：本轮目标是唯一应用界面及其原生壳，
不在此阶段生成安装包。后续发布时再启用 Tauri bundle 并补齐图标与签名配置。

## 当前边界

`PRODUCT.md` 明确说明该界面目前是本地交互演示：AI 回复、SSE、
供应商测试、云同步、安全策略与后台任务均为界面模拟。
数据保存在应用 WebView 的本地存储中；Tauri 化不代表已经接通真实后端、
模型服务、云同步或多用户安全边界。

`desktop/go.mod` 仍属于既有 Go 桌面发布工具模块，可独立执行
`cd desktop && go test ./...`；它不参与新界面的渲染。
