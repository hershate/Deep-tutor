// A long runtime notice has to be readable whole and dismissible without scrolling.
// Reads geometry: the bar's sentence is not clipped by .composenotes and its dismiss
// button lies inside the visible part of it.
import { chromium } from "playwright";

const PAGE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!ok) fails.push(name);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => fails.push("page error: " + e.message));
await page.goto(`${PAGE}?turns=2`, { waitUntil: "networkidle" });
await page.waitForSelector(".app", { timeout: 20000 });
await page.evaluate(() => {
  window.__feed({
    kind: "notice",
    level: "warn",
    audience: "operator",
    code: "permission_rules_dormant",
    text: "1 permission rule matches no tool",
    detail: JSON.stringify({ rules: [{ list: "ask", rule: "Foo(bar)" }] }),
  });
});
await page.waitForSelector(".rtbar", { timeout: 5000 });
await page.waitForTimeout(1500);

for (const [w, h] of [[1440, 900], [400, 800], [1100, 640]]) {
  await page.setViewportSize({ width: w, height: h });
  await page.waitForTimeout(900);
  const g = await page.evaluate(() => {
    const notes = document.querySelector(".composenotes");
    const nr = notes.getBoundingClientRect();
    const btn = document.querySelector(".rtbar button").getBoundingClientRect();
    return {
      clipped: document.querySelector(".rtbar").getBoundingClientRect().bottom > nr.bottom + 1,
      btnIn: btn.top >= nr.top - 1 && btn.bottom <= nr.bottom + 1 && btn.right <= innerWidth + 1,
      sh: notes.scrollHeight, ch: notes.clientHeight,
      text: document.querySelector(".rtbar .t").textContent,
    };
  });
  // A short window has no room for a long sentence; it scrolls, and the button stays on the first row.
  if (h >= 720) check(`${w}x${h}: sentence is not clipped`, !g.clipped, `scroll ${g.sh} / client ${g.ch}`);
  check(`${w}x${h}: dismiss button is visible without scrolling`, g.btnIn);
  check(`${w}x${h}: no raw placeholder`, !/\{\w+\}/.test(g.text), g.text.slice(0, 30));
}
await browser.close();
if (fails.length) {
  console.log(`\n${fails.length} failed`);
  process.exit(1);
}
