# 前端插件示例（`window.DeepTutor`）

真实有用的前端插件示例，复合多个前端插件 API（契约见仓库根 [API.md](../../API.md)「前端插件 API」）。
每个插件是普通 `.js`（IIFE），经 `DeepTutor.plugins.load(url)` 加载或控制台临时加载。

## 插件清单

| 插件 | 用途 | 复合的 API |
|---|---|---|
| [pomodoro.js](pomodoro.js) | 番茄钟侧栏小组件（完成自动提示 + 打卡） | `widgets` + `ui.toast` + `events` + `api` |
| [floating_notes.js](floating_notes.js) | 可拖放浮窗笔记（位置记忆） | `overlay` + `ui.makeDraggable` + `ui.toast` |
| [brand_skin.js](brand_skin.js) | 品牌皮肤（海洋蓝 + 大圆角） | `skins` |
| [reader_app.js](reader_app.js) | 速读卡插件页面（整页 + SPA 导航） | `pages` + `router` |

## 装载

把这些 `.js` 经任意静态服务器（或 `frontend/public/plugins/` 下由 Vite 服务）暴露为 URL，然后：

```js
// 浏览器控制台或 /plugins 管理页
await DeepTutor.plugins.load("https://你的域/pomodoro.js")
```

刷新后自动加载（持久化于 `localStorage('dt_plugins')`）。

## 装载验证（与插件分离）

`verify.mjs` 是 Node 验证脚本——mock `window.DeepTutor` 宿主 API，加载本目录真实插件 `*.js`，
断言：① 装载无异常 ② 复合 API 调用正确（`register`/`events`）③ 注册的组件函数可调用不抛。
**不依赖浏览器 / jsdom / React 运行时**（最小 mock），可在 CI 跑：

```bash
node plugin-examples/frontend/verify.mjs
```

> 该脚本验证「插件能正确装载并调用宿主 API」这一前置稳定性；组件在真实浏览器的渲染行为
> 由宿主 `frontend/src/lib/plugins.ts` 的 tsc 类型检查与 React 错误边界保证（前端无独立单测框架）。
