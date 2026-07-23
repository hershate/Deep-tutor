// 前端插件装载验证脚本（Node ESM）—— 与实际插件物理分离（插件是 .js，本脚本是 .mjs）
// 目的：mock window.DeepTutor 宿主 API，加载本目录真实前端插件 *.js，断言：
//   ① 装载无异常 ② 复合 API 调用正确（register/events） ③ 注册的组件函数可调用不抛
// 运行：node plugin-examples/frontend/verify.mjs
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const calls = { widgets: [], overlay: [], skins: [], pages: [], events: [], ui: [], router: [] };
const errors = [];
const assert = (cond, msg) => {
  if (cond) console.log("  ✓ " + msg);
  else {
    errors.push(msg);
    console.error("  ✗ " + msg);
  }
};

// mock 宿主：window.DeepTutor 各命名空间记录调用；React hooks 返回安全占位
const ctx = {
  window: {
    location: { search: "", pathname: "/p/reader", href: "/p/reader" },
    DeepTutor: {
      version: "1.16.0",
      React: {
        createElement: (type, props, ...children) => ({ _react: true, type, props, children }),
        useState: (v) => [typeof v === "function" ? v(undefined) : v, () => {}],
        useEffect: () => () => {},
        useRef: () => ({ current: null }),
        useSyncExternalStore: () => "",
      },
      widgets: {
        register: (d) => calls.widgets.push(d),
        slots: () => ["sidebar.footer", "home.dashboard", "plan.detail"],
        list: () => [],
        subscribe: () => () => {},
        unregister: () => true,
      },
      overlay: {
        register: (d) => calls.overlay.push(d),
        layers: () => ["background", "content", "top"],
        list: () => [],
        subscribe: () => () => {},
        unregister: () => true,
      },
      skins: {
        register: (d) => calls.skins.push(d),
        apply: () => true,
        active: () => "default",
        list: () => [],
        subscribe: () => () => {},
      },
      pages: {
        register: (d) => calls.pages.push(d),
        navItems: () => [],
        list: () => [],
        get: () => undefined,
        subscribe: () => () => {},
        unregister: () => true,
      },
      events: { on: (e) => { calls.events.push(e); return () => {}; }, emit: () => {} },
      api: { get: async () => ({}), request: async () => ({}) },
      ui: {
        makeDraggable: () => { calls.ui.push("makeDraggable"); return { destroy: () => {} }; },
        makeResizable: () => { calls.ui.push("makeResizable"); return { destroy: () => {} }; },
        toast: { success: (m) => calls.ui.push("toast:" + m), info: () => {}, error: () => {} },
      },
      router: {
        navigate: (to, opts) => { calls.router.push({ to, opts }); },
        path: () => "/p/reader",
        href: () => "/p/reader",
        subscribe: () => () => {},
      },
      plugins: { load: async () => {}, unload: () => {}, list: () => [] },
    },
  },
  URLSearchParams: URLSearchParams,
  document: {
    createElement: () => ({ setAttribute() {}, appendChild() {}, style: {} }),
    addEventListener() {},
    body: { appendChild() {} },
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  console,
  setTimeout,
  setInterval,
  clearInterval,
  clearTimeout,
};

const files = fs.readdirSync(__dirname).filter((f) => f.endsWith(".js"));
console.log("加载 " + files.length + " 个前端插件：");
for (const f of files) {
  try {
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, f), "utf-8"), ctx, { filename: f });
    console.log("  ✓ " + f + " 装载无异常");
  } catch (e) {
    errors.push(f + ": " + e.message);
    console.error("  ✗ " + f + " " + e.message);
  }
}

console.log("\n断言复合 API 调用：");
assert(
  calls.widgets.some((d) => d.id === "pomodoro.timer" && d.slot === "sidebar.footer"),
  "pomodoro 注册 sidebar.footer 小组件（widgets）"
);
assert(
  calls.overlay.some((d) => d.id === "floating_notes.panel" && d.layer === "content"),
  "floating_notes 注册 content 覆盖层（overlay）"
);
assert(calls.skins.some((d) => d.name === "brand.ocean"), "brand_skin 注册 brand.ocean 皮肤（skins）");
assert(calls.pages.some((d) => d.path === "reader"), "reader_app 注册 reader 插件页面（pages）");
assert(calls.events.indexOf("skinchange") !== -1, "pomodoro 订阅 skinchange 事件（events 桥）");

console.log("\n验证注册的组件函数可调用（mock hooks，不抛）：");
for (const d of [...calls.widgets, ...calls.overlay, ...calls.pages]) {
  try {
    const out = d.component();
    assert(out && out._react === true, d.id || d.path + " 组件返回 React 元素");
  } catch (e) {
    errors.push((d.id || d.path) + " 组件异常: " + e.message);
    console.error("  ✗ " + (d.id || d.path) + " 组件异常: " + e.message);
  }
}

if (errors.length) {
  console.error("\n❌ 验证失败 " + errors.length + " 项");
  process.exit(1);
}
console.log("\n✅ 全部前端插件装载验证通过");
