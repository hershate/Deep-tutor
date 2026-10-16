// A finished turn's work, folded under one line when the kernel's setting asks for it: the answer stays outside, the
// line opens on click and keeps its state, a failure shows on the line, and the running turn is never folded.
import { chromium } from "playwright";

const BASE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const SHOTS = process.env.FOLD_SHOTS;
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!ok) fails.push(name);
};

const feedTurn = (page, { fail = false, done = true } = {}) =>
  page.evaluate(async ({ fail, done }) => {
    const frame = () => new Promise((r) => requestAnimationFrame(r));
    window.__feed({ kind: "turn_started", authoredTurn: 1, msgIndex: 0, text: "Please fix the failing test" });
    window.__feed({ kind: "text", text: "I will read the test first." });
    window.__feed({ kind: "message", text: "I will read the test first." });
    for (const [i, name] of ["read_file", "bash", "edit_file"].entries()) {
      const tool = { id: `t${i}`, name, args: JSON.stringify({ path: `pkg/f${i}.go` }) };
      window.__feed({ kind: "tool_dispatch", tool });
      window.__feed({ kind: "tool_result", tool: { ...tool, output: fail && i === 1 ? "exit 1" : "ok", durationMs: 120, ...(fail && i === 1 ? { err: "exit status 1" } : {}) } });
      await frame();
    }
    window.__feed({ kind: "text", text: "The cause was an off-by-one in the loop bound.\n\nI changed it and the test passes." });
    window.__feed({ kind: "message", text: "The cause was an off-by-one in the loop bound.\n\nI changed it and the test passes." });
    if (done) window.__feed({ kind: "turn_done" });
    await frame();
  }, { fail, done });

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  for (const [label, width] of [["wide", 1280], ["narrow", 420]]) {
    const tag = `${scheme}/${label}`;
    const page = await browser.newPage({ viewport: { width, height: 800 }, colorScheme: scheme, reducedMotion: "reduce" });
    page.setDefaultTimeout(8000);
    page.on("pageerror", (e) => fails.push("page error: " + e.message));
    await page.goto(`${BASE}?pref=en&fold`, { waitUntil: "networkidle" });
    await page.waitForSelector(".app");
    await feedTurn(page, { fail: true });
    await page.waitForTimeout(700);
    const g = await page.evaluate(() => {
      const fold = document.querySelector("details.work-fold");
      const final = [...document.querySelectorAll('.call[data-k="say"]')].at(-1);
      const r = fold?.getBoundingClientRect();
      return {
        folds: document.querySelectorAll("details.work-fold").length,
        open: fold?.open,
        failed: fold?.hasAttribute("data-failed"),
        line: fold?.querySelector("summary")?.textContent ?? "",
        bodyHidden: fold ? fold.querySelector(".work-fold-body").getBoundingClientRect().height === 0 || getComputedStyle(fold.querySelector(".work-fold-body")).display === "none" || !fold.open : null,
        answerOutside: !!final && !fold?.contains(final) && final.textContent.includes("off-by-one"),
        inside: !!r && r.left >= 0 && r.right <= innerWidth,
      };
    });
    check(`${tag}: the finished turn has one folded line`, g.folds === 1 && g.open === false, JSON.stringify(g));
    check(`${tag}: the answer stays outside it`, g.answerOutside);
    check(`${tag}: the line counts the calls and marks the failure`, /3/.test(g.line) && g.failed === true, g.line);
    check(`${tag}: the line fits the window`, g.inside);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.FOLD_TAG ?? "shot"}-folded-${tag.replace("/", "-")}.png` });
    await page.locator("details.work-fold > summary").click();
    await page.waitForTimeout(500);
    check(`${tag}: a click opens it and the steps show`, await page.evaluate(() => document.querySelector("details.work-fold").open && document.querySelectorAll("details.work-fold .call").length >= 3));
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.FOLD_TAG ?? "shot"}-open-${tag.replace("/", "-")}.png` });
    await page.close();
  }
}
{
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(`${BASE}?pref=en&fold`, { waitUntil: "networkidle" });
  await page.waitForSelector(".app");
  await feedTurn(page, { done: false });
  await page.waitForTimeout(500);
  check("a turn still running is never folded", (await page.locator("details.work-fold").count()) === 0);
  await page.evaluate(() => window.__feed({ kind: "turn_done" }));
  await page.waitForTimeout(500);
  check("it folds when the turn settles", (await page.locator("details.work-fold").count()) === 1);
  await page.close();
}
{
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(`${BASE}?pref=en`, { waitUntil: "networkidle" });
  await page.waitForSelector(".app");
  await feedTurn(page);
  await page.waitForTimeout(500);
  check("with the setting off nothing is folded", (await page.locator("details.work-fold").count()) === 0);
  await page.close();
}
{
  const page = await browser.newPage({ viewport: { width: 1280, height: 700 } });
  await page.goto(`${BASE}?pref=en&fold&turns=30`, { waitUntil: "networkidle" });
  await page.waitForSelector(".app");
  await feedTurn(page, { done: false });
  await page.waitForTimeout(600);
  await page.evaluate(() => { const sc = document.querySelector('[data-pane="flow"]'); sc.scrollTop = sc.scrollHeight; });
  await page.waitForTimeout(300);
  const before = await page.evaluate(() => {
    const sc = document.querySelector('[data-pane="flow"]');
    const answer = [...document.querySelectorAll('.call[data-k="say"]')].at(-1).getBoundingClientRect();
    return { top: sc.scrollTop, h: sc.scrollHeight, answerTop: Math.round(answer.top), view: innerHeight };
  });
  const frames = await page.evaluate(async () => {
    const sc = document.querySelector('[data-pane="flow"]');
    const seen = [];
    const sample = () => {
      const a = [...document.querySelectorAll('.call[data-k="say"]')].at(-1);
      const fold = document.querySelector("details.work-fold");
      seen.push({ folds: fold ? 1 : 0, open: !!fold?.open, answerTop: Math.round(a.getBoundingClientRect().top), top: sc.scrollTop, h: sc.scrollHeight });
    };
    window.__feed({ kind: "turn_done" });
    for (let i = 0; i < 12; i++) {
      await new Promise((r) => requestAnimationFrame(r));
      sample();
    }
    return seen;
  });
  const first = frames[0];
  const last = frames.at(-1);
  const answerMoves = new Set(frames.map((f) => f.answerTop)).size;
  console.log(`settle: before answerTop=${before.answerTop} scrollHeight=${before.h}; first frame ${JSON.stringify(first)}; last ${JSON.stringify(last)}; distinct answer positions over 12 frames: ${answerMoves}`);
  check("the work is folded in the first frame after the turn ends, never drawn open and then closed", first.folds === 1 && frames.every((f) => !f.open));
  check("the answer settles in one move rather than wandering", answerMoves <= 2, String(answerMoves));
  check("the answer is still on screen after the fold", last.answerTop >= 0 && last.answerTop < before.view, String(last.answerTop));
  check("a reader pinned to the bottom stays there: the page and the scroll position shrink together", Math.abs(last.top + before.view - last.h) < before.view && last.answerTop > 0, JSON.stringify({ top: last.top, h: last.h }));
  await page.close();
}
await browser.close();
if (fails.length) {
  console.log(`\n${fails.length} failed`);
  process.exit(1);
}
console.log("\nall ok");
