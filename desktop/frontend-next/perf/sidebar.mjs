// One sidebar toggle, drawn at one place whatever the sidebar and the icon rail are doing, never under another control;
// and a run of replies from one model is headed once. Position and hit-testing are questions for a real browser.
import { chromium } from "playwright";

const BASE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const SHOTS = process.env.SIDEBAR_SHOTS;
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!ok) fails.push(name);
};

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  for (const mode of ["on", "collapsed", "off"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: scheme, reducedMotion: "reduce" });
    page.setDefaultTimeout(8000);
    page.on("pageerror", (e) => fails.push("page error: " + e.message));
    await page.addInitScript((m) => localStorage.setItem("rx-nav-rail", m), mode);
    await page.goto(`${BASE}?pref=en&turns=3`, { waitUntil: "networkidle" });
    await page.waitForSelector(".app");
    await page.evaluate(() => document.fonts.ready);
    const tag = `${scheme}/${mode}`;
    const spot = async () => {
      await page.waitForTimeout(700);
      return page.evaluate(() => {
        const toggles = [...document.querySelectorAll('[data-action="chrome.rail"]')].filter((b) => b.tabIndex !== -1 && b.getBoundingClientRect().width > 0 && b.getBoundingClientRect().right > 0);
        const b = toggles[0]?.getBoundingClientRect();
        if (!b) return { count: toggles.length };
        const x = b.left + b.width / 2;
        const y = b.top + b.height / 2;
        const top = document.elementFromPoint(x, y);
        return { count: toggles.length, x: Math.round(x), y: Math.round(y), reached: toggles[0].contains(top) };
      });
    };
    const open = await spot();
    check(`${tag}: exactly one sidebar toggle is drawn while the sidebar is open`, open.count === 1, String(open.count));
    check(`${tag}: nothing lies over it`, open.reached === true);
    const inset = await page.evaluate(() => {
      const edge = document.querySelector(".rail").getBoundingClientRect().left;
      const left = (sel) => Math.round(document.querySelector(sel).getBoundingClientRect().left - edge);
      return { newTask: left(".studio-new-task"), search: left(".studio-search") };
    });
    check(`${tag}: the head rows sit 22px in from the sidebar edge, with no blank strip under the toggle`, inset.newTask === 22 && inset.search === 22, JSON.stringify(inset));
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.SIDEBAR_TAG ?? "shot"}-open-${tag.replace("/", "-")}.png` });
    await page.locator('[data-action="chrome.rail"]').filter({ visible: true }).first().click({ position: { x: 15, y: 15 } });
    const shut = await spot();
    check(`${tag}: exactly one toggle after collapsing`, shut.count === 1, String(shut.count));
    check(`${tag}: it has not moved`, shut.x === open.x && shut.y === open.y, `${open.x},${open.y} -> ${shut.x},${shut.y}`);
    check(`${tag}: nothing lies over it once collapsed`, shut.reached === true);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.SIDEBAR_TAG ?? "shot"}-shut-${tag.replace("/", "-")}.png` });
    await page.close();
  }
}
for (const scheme of ["light", "dark"]) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, colorScheme: scheme, reducedMotion: "reduce" });
  await page.goto(`${BASE}?pref=en`, { waitUntil: "networkidle" });
  await page.waitForSelector(".app");
  await page.evaluate(async () => {
    const say = (text) => {
      window.__feed({ kind: "text", text });
      window.__feed({ kind: "message" });
    };
    window.__feed({ kind: "turn_started" });
    say("First reply.");
    window.__feed({ kind: "tool_dispatch", tool: { id: "t1", name: "bash", args: "{}" } });
    window.__feed({ kind: "tool_result", tool: { id: "t1", name: "bash", args: "{}", output: "ok", durationMs: 5 } });
    say("Second reply, same model.");
    say("Third reply, same model.");
    window.__feed({ kind: "turn_done" });
  });
  await page.waitForTimeout(600);
  const heads = await page.evaluate(() =>
    [...document.querySelectorAll('.flow .call[data-k="say"]')].map((c) => {
      const hl = c.querySelector(":scope > .c > .hl");
      const sym = c.querySelector(":scope > .g");
      return { quiet: c.hasAttribute("data-quiet"), head: !!hl && getComputedStyle(hl).display !== "none", mark: !!sym && getComputedStyle(sym).display !== "none" };
    }),
  );
  check(`${scheme}: three replies in a turn, one header`, heads.length === 3 && heads.filter((h) => h.head && h.mark).length === 1 && heads[0].head && heads[0].mark, JSON.stringify(heads));
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.SIDEBAR_TAG ?? "shot"}-replies-${scheme}.png` });
  await page.close();
}
for (const scheme of ["light", "dark"]) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: scheme, reducedMotion: "reduce" });
  await page.goto(`${BASE}?pref=zh&turns=3`, { waitUntil: "networkidle" });
  await page.waitForSelector(".wsrow");
  await page.mouse.move(900, 500);
  const look = () => page.evaluate(() => {
    const row = document.querySelector(".wsrow");
    const op = (sel) => Number(getComputedStyle(row.querySelector(sel)).opacity);
    return { height: Math.round(row.getBoundingClientRect().height), count: op(".rowcount"), actions: op(".rowbtns") };
  });
  const rest = await look();
  check(`${scheme}: a project row is one line`, rest.height <= 36, String(rest.height));
  check(`${scheme}: at rest the row shows its count and not its actions`, rest.count === 1 && rest.actions === 0, JSON.stringify(rest));
  await page.locator(".wsrow").first().hover();
  await page.waitForTimeout(250);
  const hover = await look();
  check(`${scheme}: on hover the actions take the count's place`, hover.count === 0 && hover.actions === 1, JSON.stringify(hover));
  const edge = (sel) => page.evaluate((q) => { const r = document.querySelector(q).getBoundingClientRect(); return Math.round(r.right * 10) / 10; }, sel);
  const projectRight = await edge(".wsrow .wsmore");
  await page.locator(".machrow[data-here]").hover();
  await page.waitForTimeout(250);
  const machineRight = await edge(".machrow[data-here] .machpick");
  check(`${scheme}: the machine row's + ends where a project row's … ends`, Math.abs(machineRight - projectRight) <= 2, `${machineRight} vs ${projectRight}`);
  await page.close();
}
await browser.close();
if (fails.length) {
  console.log(`\n${fails.length} failed`);
  process.exit(1);
}
console.log("\nall ok");
