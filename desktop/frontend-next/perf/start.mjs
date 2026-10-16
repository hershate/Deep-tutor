// The workbench tab opens a launcher instead of a browser; the title-bar Browser button still goes straight to one.
import { chromium } from "playwright";

const BASE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const SHOTS = process.env.START_SHOTS;
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!ok) fails.push(name);
};

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  for (const [label, width] of [["wide", 1440], ["narrow", 900]]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme: scheme, reducedMotion: "reduce" });
    page.setDefaultTimeout(8000);
    page.on("pageerror", (e) => fails.push("page error: " + e.message));
    await page.goto(`${BASE}?pref=en&turns=2`, { waitUntil: "networkidle" });
    await page.waitForSelector(".app");
    const tag = `${scheme}/${label}`;
    const tabs = () => page.evaluate(() => document.querySelectorAll(".workbench-tab").length);
    await page.locator('[data-action="browser.open"]').first().click();
    await page.waitForTimeout(700);
    const direct = await page.evaluate(() => ({ launcher: !!document.querySelector(".workbench-launcher"), tabs: document.querySelectorAll(".workbench-tab").length }));
    check(`${tag}: the title-bar Browser button goes straight to a browser`, direct.tabs === 1 && !direct.launcher, JSON.stringify(direct));
    await page.goto(`${BASE}?pref=en&turns=2`, { waitUntil: "networkidle" });
    await page.waitForSelector(".app");
    await page.locator('[data-action="pane.view"][data-value="browser"]').click();
    await page.waitForTimeout(700);
    const start = await page.evaluate(() => {
      const box = document.querySelector(".workbench-launcher");
      const r = box?.getBoundingClientRect();
      return {
        shown: !!r && r.width > 0,
        inside: !!r && r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight,
        choices: [...(box?.querySelectorAll("button") ?? [])].map((b) => b.textContent.trim()),
        tabs: document.querySelectorAll(".workbench-tab").length,
      };
    });
    check(`${tag}: the Workbench tab opens the launcher`, start.shown, JSON.stringify(start));
    check(`${tag}: it offers files, changes and the browser`, start.choices.length === 3 && ["Files", "Changes", "Browser"].every((c, i) => start.choices[i].startsWith(c)), start.choices.join(" | "));
    check(`${tag}: no browser has been opened for the person`, start.tabs === 0, String(start.tabs));
    check(`${tag}: the launcher is inside the window`, start.inside);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.START_TAG ?? "shot"}-${tag.replace("/", "-")}.png` });
    await page.locator(".workbench-launcher button", { hasText: "Browser" }).click().catch(() => {});
    await page.waitForTimeout(500);
    const after = await page.evaluate(() => ({ tabs: document.querySelectorAll(".workbench-tab").length, launcher: !!document.querySelector(".workbench-launcher") }));
    check(`${tag}: choosing the browser opens one and the launcher steps aside`, after.tabs === 1 && !after.launcher, JSON.stringify(after));
    await page.close();
  }
}
await browser.close();
if (fails.length) {
  console.log(`\n${fails.length} failed`);
  process.exit(1);
}
console.log("\nall ok");
