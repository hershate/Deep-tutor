// The signal rail of the run analysis says what it is and why its marks cannot be moved.
import { chromium } from "playwright";

const BASE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const SHOTS = process.env.SIGNALS_SHOTS;
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!ok) fails.push(name);
};

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  for (const [label, width] of [["wide", 1280], ["phone", 390]]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme: scheme, reducedMotion: "reduce" });
    page.on("pageerror", (e) => fails.push("page error: " + e.message));
    await page.goto(`${BASE}?pref=en`, { waitUntil: "networkidle" });
    await page.waitForSelector(".app");
    await page.evaluate(() => {
      window.__feed({ kind: "turn_started" });
      window.__feed({ kind: "retrying", retryAttempt: 1, retryMax: 2, retryScope: "stream" });
      window.__feed({ kind: "tool_dispatch", tool: { id: "t1", name: "bash", args: "{}" } });
      window.__feed({ kind: "tool_result", tool: { id: "t1", name: "bash", args: "{}", output: "ok", durationMs: 40 } });
      window.__feed({ kind: "turn_done" });
    });
    await page.locator('[data-action="pane.view"][data-value="analysis"]').click();
    await page.waitForSelector(".run-signals");
    const tag = `${scheme}/${label}`;
    const g = await page.evaluate(() => {
      const note = document.querySelector(".run-signals .run-section-note");
      const r = note?.getBoundingClientRect();
      const box = document.querySelector(".run-signals").getBoundingClientRect();
      return { text: note?.textContent ?? "", inside: !!r && r.left >= box.left - 1 && r.right <= box.right + 1 && r.width > 0 };
    });
    check(`${tag}: the rail says what its marks are and why they stay put`, /位置就是时间|position is their time/.test(g.text), g.text);
    check(`${tag}: the explanation sits inside the section`, g.inside);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.SIGNALS_TAG ?? "shot"}-${tag.replace("/", "-")}.png` });
    await page.close();
  }
}
await browser.close();
if (fails.length) {
  console.log(`\n${fails.length} failed`);
  process.exit(1);
}
console.log("\nall ok");
