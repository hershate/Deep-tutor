// The pane's view tabs share one highlight that moves to the chosen tab, so a
// switch reads as one motion. Where it lands is geometry, asked of a real browser.
import { chromium } from "playwright";

const BASE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!ok) fails.push(name);
};

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: scheme, reducedMotion: "reduce" });
  page.on("pageerror", (e) => fails.push("page error: " + e.message));
  await page.goto(`${BASE}?pref=en&turns=5`, { waitUntil: "networkidle" });
  await page.waitForSelector(".pane > .tabs [role=tab]");
  const tabs = page.locator(".pane > .tabs [role=tab]");
  const count = await tabs.count();
  for (let i = 0; i < count; i++) {
    await tabs.nth(i).click();
    await page.waitForTimeout(400);
    const g = await page.evaluate(() => {
      const thumbs = document.querySelectorAll(".pane:not([data-off]) > .tabs .vthumb");
      const on = document.querySelector('.pane:not([data-off]) > .tabs [role=tab][aria-selected="true"]');
      const a = thumbs[0]?.getBoundingClientRect();
      const c = on?.getBoundingClientRect();
      return { thumbs: thumbs.length, thumb: a && [Math.round(a.left), Math.round(a.width), Math.round(a.top), Math.round(a.height)], tab: c && [Math.round(c.left), Math.round(c.width), Math.round(c.top), Math.round(c.height)], tabBg: on && getComputedStyle(on).backgroundColor };
    });
    check(`${scheme} tab ${i}: one highlight, lying exactly under the chosen tab`, g.thumbs === 1 && JSON.stringify(g.thumb) === JSON.stringify(g.tab), JSON.stringify(g));
    check(`${scheme} tab ${i}: the chosen tab draws no box of its own`, g.tabBg === "rgba(0, 0, 0, 0)", g.tabBg);
  }
  await page.close();
}
await browser.close();
if (fails.length) {
  console.log(`\n${fails.length} failed`);
  process.exit(1);
}
console.log("\nall ok");
