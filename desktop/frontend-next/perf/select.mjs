// 跨屏选区：把选区从靠前的一块拉到很远的一块，量画面稳住要多久、挂了多少块，
// 以及复制出来的文字是不是真文本而不是空占位。
import { chromium } from "playwright";

const PAGE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const TURNS = Number(process.env.TURNS ?? 300);
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const fails = [];
page.on("pageerror", (e) => fails.push("页面异常: " + e.message));
await page.goto(`${PAGE}?turns=${TURNS}`, { waitUntil: "networkidle" });
await page.waitForSelector(".app", { timeout: 15000 });
await page.waitForTimeout(1500);
await page.evaluate(() => {
  const flow = document.querySelector('[data-pane="flow"]');
  flow.scrollTop = 0;
});
await page.waitForTimeout(500);

const run = (from, to) =>
  page.evaluate(
    ({ from, to }) =>
      new Promise((resolve) => {
        const chunks = [...document.querySelectorAll(".chunk")];
        const mounted = () => chunks.filter((c) => c.childElementCount > 0).length;
        const before = mounted();
        const first = chunks[from].querySelector("p, div") ?? chunks[from];
        const range = document.createRange();
        range.setStart(first, 0);
        range.setEnd(chunks[to], 0);
        const t0 = performance.now();
        const sel = document.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        requestAnimationFrame(() =>
          requestAnimationFrame(() =>
            setTimeout(() => {
              const text = sel.toString();
              resolve({ ms: Math.round(performance.now() - t0), before, after: mounted(), total: chunks.length, chars: text.length, lines: text.split("\n").filter(Boolean).length });
            }, 0),
          ),
        );
      }),
    { from, to },
  );

const total = await page.evaluate(() => document.querySelectorAll(".chunk").length);
for (const [from, to] of [[2, Math.floor(total / 3)], [2, Math.floor(total / 2)], [2, total - 3]]) {
  const r = await run(from, to);
  console.log(`选区 块${from}→块${to}: ${r.ms}ms  挂载 ${r.before}→${r.after}/${r.total}  复制 ${r.chars} 字 ${r.lines} 行`);
}
await browser.close();
if (fails.length) { console.log(fails.join("\n")); process.exit(1); }
