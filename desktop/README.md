# 知舟 Deep Tutor · Tauri 2 桌面应用

本目录是唯一用户界面的来源。界面来自用户提供的 `REF/frontend/app`，
由 React 18、TypeScript、Vite 与 Tauri 2（Rust 壳）组成。
不再交付旧浏览器 SPA、Electron 或 `frontend-next`。

## 目录

| 路径 | 作用 |
|---|---|
| `src/`、`public/` | 用户提供的应用界面与静态资产 |
| `src/backend/` | 前端调用 Tauri 原生后端的客户端 |
| `index.html`、`vite.config.ts`、`tsconfig.json` | Vite/TypeScript 入口与配置 |
| `package.json`、`package-lock.json` | 界面依赖与 Tauri CLI |
| `src-tauri/` | Tauri 2 配置、Rust 原生后端和构建脚本 |

`dist/`、`node_modules/`、`src-tauri/target/` 与 `src-tauri/gen/` 都是可重建产物，不入库。

## 原生后端

后端位于 `src-tauri/src/backend/`，通过 Tauri commands 提供请求/响应，
通过 Tauri events 提供实时输出，不以独立 HTTP 服务作为连接方式。
SQLite 保存业务状态与事件 outbox；实时事件使用单调递增 id，
支持 Last-Event-ID 续接，终态先落盘再发送。

API Key 只能进入系统密钥库，不得进入 WebView 长期存储、日志、导出或同步载荷。
模型、检索、同步等外部结果必须由后端真实执行，不可用时明确报错。

## 安装包构建

```bash
cd desktop
npm ci
npm run tauri build
```

构建产物位于 `desktop/src-tauri/target/release/bundle/nsis/`。
`bundle.active=true` 且目标仅为 `nsis`：交付的是安装后运行的桌面应用，
不是需要启动脚本或服务器环境的网页服务。

开发调试可运行 `npm run tauri dev`；Vite 仅绑定 `127.0.0.1:1420`，
不作为产品入口，也不提供浏览器启动脚本。

## 当前边界

全功能原生后端正在分片实现。应用状态、草稿和事件 outbox 使用原生 SQLite。
材料支持原件暂存、SHA-256 去重，以及 PDF、DOCX、PPTX、EPUB、TXT、Markdown
的文本层解析；解析任务、取消、恢复、删除和导出均有真实实现。

模型档案保存在 SQLite，API Key 保存在系统密钥库。OpenAI Chat 的 `/models`、
真实请求检测、可取消 SSE 流、用量、幂等重放和中断恢复已接入；请求与事件不含密钥。
其他协议、任务路由、聚合、故障转移和云同步仍明确显示待接入。

- 方案生成已接入文件与纯文本、八步事件、图谱/阶段/校验、引用白名单和确定性排期。
- 硬里程碑、取消恢复、局部重生成与采用均有真实实现。
- 知识地图提供真实有向最短路径、阅读状态、测评证据和幂等补学任务。
- 学习空间支持逐单元讲义、实时生成、取消恢复、历史版本、阅读/收藏/批注及原件导出。
- URL 抓取、外部资源检索、视频播放、导出与分享仍待实现。

扫描件 OCR、音视频识别、生成、Agent、同步、社区和安全能力，
按 [`note/14-Tauri原生后端覆盖清单.md`](../note/14-Tauri原生后端覆盖清单.md) 推进。
在对应切片完成前，不宣称该功能已由真实后端提供。
