// A column's drag handle must not sit on top of the scrollbar beside it: a press on the thumb has to scroll, and a
// press on the handle has to resize. Hit-testing is a layout question, so it is asked of a real browser.
import { chromium } from "playwright";

const BASE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const SHOTS = process.env.GUTTER_SHOTS;
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!ok) fails.push(name);
};

// The scrollbar is 10px wide against the scroller's right edge, its thumb inset 3px on each side.
const THUMB = 5;
const hit = (page, x, y) =>
  page.evaluate(([x, y]) => {
    const el = document.elementFromPoint(x, y);
    const g = el?.closest(".gutter");
    return { gutter: g ? g.className : null, tag: el ? el.className || el.tagName : null };
  }, [x, y]);

// Every row of the scroller's right edge, not one sample: a handle that only covers the top or the middle is still a
// handle on the thumb whenever the thumb is there.
const sweep = (page, right, top, height) =>
  page.evaluate(([right, top, height, inset]) => {
    const hits = [];
    for (let y = top + 2; y < top + height - 2; y += 12) {
      for (const dx of inset) {
        const g = document.elementFromPoint(right - dx, y)?.closest(".gutter");
        if (g) hits.push(`${g.className.split(" ").at(-1)}@${Math.round(y)}`);
      }
    }
    return hits;
  }, [right, top, height, [4, 5, 6]]);

const flowBox = (page) =>
  page.evaluate(() => {
    const r = document.querySelector('[data-pane="flow"]').getBoundingClientRect();
    return { right: r.right, top: r.top, height: r.height };
  });

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  for (const width of [1280, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 800 }, colorScheme: scheme, reducedMotion: "reduce" });
    page.setDefaultTimeout(8000);
    page.on("pageerror", (e) => fails.push("page error: " + e.message));
    await page.goto(`${BASE}?pref=en&turns=30`, { waitUntil: "networkidle" });
    await page.waitForSelector(".app");
    await page.evaluate(() => document.fonts.ready);
    const tag = `${scheme} ${width}px`;
    const flow = await page.evaluate(() => {
      const r = document.querySelector('[data-pane="flow"]').getBoundingClientRect();
      return { right: r.right, top: r.top, height: r.height };
    });
    const dockShut = await page.evaluate(() => document.querySelector(".gutter-r")?.hasAttribute("data-shut"));
    check(`${tag}: the workbench starts shut`, dockShut === true);
    const covered = await sweep(page, flow.right, flow.top, flow.height);
    check(`${tag}: no row of the transcript's scrollbar is under the workbench handle`, covered.length === 0, covered.slice(0, 4).join(" "));
    await page.mouse.move(flow.right - 5, flow.top + 8);
    await page.mouse.down();
    await page.mouse.move(flow.right - 5, flow.top + 160, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(250);
    check(`${tag}: pressing the scrollbar's top and dragging does not open the workbench`, (await page.evaluate(() => document.querySelector(".gutter-r")?.hasAttribute("data-shut"))) === true);

    const grip = await page.locator(".gutter-r .grip").boundingBox();
    const pane = await page.evaluate(() => {
      const r = document.querySelector(".pane").getBoundingClientRect();
      return { top: r.top, height: r.height };
    });
    const mid = pane.top + pane.height / 2;
    check(`${tag}: the shut handle sits mid-height`, Math.abs(grip.y + grip.height / 2 - mid) <= pane.height * 0.05, `${Math.round(grip.y + grip.height / 2)} against ${Math.round(mid)}`);
    const onGrip = await hit(page, grip.x + grip.width / 2, grip.y + grip.height / 2);
    check(`${tag}: the shut workbench still has a handle to press`, onGrip.gutter?.includes("gutter-r") === true, String(onGrip.gutter));

    const rail = await page.evaluate(() => {
      const el = document.querySelector(".rail .scroll");
      const r = el?.getBoundingClientRect();
      return r && r.width > 0 ? { right: r.right, top: r.top, height: r.height } : null;
    });
    if (rail) {
      const edge = await hit(page, rail.right - THUMB, rail.top + rail.height * 0.25);
      check(`${tag}: the workspace column's scrollbar is not under its handle`, edge.gutter === null, String(edge.gutter));
      const seam = await hit(page, rail.right + 3, rail.top + rail.height * 0.75);
      check(`${tag}: the workspace column's seam still resizes`, seam.gutter?.includes("gutter-l") === true, String(seam.gutter));
    }

    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.GUTTER_TAG ?? "shot"}-shut-${scheme}-${width}.png` });
    await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
    await page.mouse.down();
    await page.mouse.move(grip.x - 200, grip.y + grip.height / 2, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(300);
    const shut = await page.evaluate(() => document.querySelector(".gutter-r")?.hasAttribute("data-shut"));
    check(`${tag}: dragging the handle opens the workbench`, shut === false);
    const now = await flowBox(page);
    const openCovered = await sweep(page, now.right, now.top, now.height);
    check(`${tag}: nor is it once the workbench is open`, openCovered.length === 0, openCovered.slice(0, 4).join(" "));
    const seamHit = await hit(page, now.right + 2, now.top + now.height * 0.5);
    check(`${tag}: the open seam still resizes`, seamHit.gutter?.includes("gutter-r") === true, String(seamHit.gutter));
    const wide = () => page.evaluate(() => document.querySelector('.scroll[data-pane="browser"]').getBoundingClientRect().width);
    const before = await wide();
    await page.mouse.move(now.right - 5, now.top + 8);
    await page.mouse.down();
    await page.mouse.move(now.right - 5, now.top + 160, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(250);
    check(`${tag}: pressing the scrollbar and dragging leaves the open workbench's width alone`, Math.abs((await wide()) - before) < 1);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.GUTTER_TAG ?? "shot"}-${scheme}-${width}.png` });
    await page.close();
  }
}
await browser.close();
if (fails.length) {
  console.log(`\n${fails.length} failed`);
  process.exit(1);
}
console.log("\nall ok");
