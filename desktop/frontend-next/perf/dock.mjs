// A docked browser column starts at the top of the pane, beside the navigation row, and gives the rows back when it
// is undocked. Where things start is a layout question, so it is asked of a real browser.
import { chromium } from "playwright";

const BASE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const SHOTS = process.env.DOCK_SHOTS;
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!ok) fails.push(name);
};

const geometry = (page) =>
  page.evaluate(() => {
    const box = (sel) => {
      const el = document.querySelector(sel);
      const r = el?.getBoundingClientRect();
      return r && r.width > 0 ? { left: r.left, right: r.right, top: r.top, bottom: r.bottom } : null;
    };
    const card = document.querySelector('.pane > .scroll[data-pane="browser"] > .workbench');
    const divider = card ? parseFloat(getComputedStyle(card).borderLeftWidth) : 0;
    return { pane: box(".pane"), tabs: box(".pane > .tabs"), side: box('.pane > .scroll[data-pane="browser"]'), body: box(".pane > .pbody"), strip: box(".workbench-tabs"), divider };
  });

const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  for (const [label, width] of [["wide", 1440], ["narrow", 560]]) {
    const page = await browser.newPage({ viewport: { width, height: 800 }, colorScheme: scheme, reducedMotion: "reduce" });
    page.setDefaultTimeout(8000);
    page.on("pageerror", (e) => fails.push("page error: " + e.message));
    const tag = `${scheme}/${label}`;
    let docked = null;
    for (const round of ["first open", "after a reload"]) {
      await page.goto(`${BASE}?pref=en&turns=3`, { waitUntil: "networkidle" });
      await page.waitForSelector(".app");
      await page.locator('[data-action="browser.open"]').first().click();
      await page.waitForTimeout(700);
      const open = await geometry(page);
      docked = open;
      check(`${tag} ${round}: the browser column is drawn`, !!open.side);
      if (open.side) {
        if (label === "wide") {
          check(`${tag} ${round}: it starts at the top of the pane, beside the navigation row`, Math.abs(open.side.top - open.pane.top) <= 1 && Math.abs(open.tabs.top - open.pane.top) <= 1, `pane ${open.pane.top} tabs ${open.tabs.top} column ${open.side.top}`);
          check(`${tag} ${round}: it runs to the bottom of the pane`, Math.abs(open.side.bottom - open.pane.bottom) <= 1, `${open.side.bottom} against ${open.pane.bottom}`);
          check(`${tag} ${round}: the navigation row stays left of it`, open.tabs.right <= open.side.left + 1, `${open.tabs.right} / ${open.side.left}`);
          check(`${tag} ${round}: its strip ends on the navigation row's line, so the two rows read as one`, !!open.strip && Math.abs(open.strip.bottom - open.tabs.bottom) <= 1, JSON.stringify([open.tabs, open.strip]));
          check(`${tag} ${round}: the workbench card's own edge separates it from the conversation`, open.divider >= 1, String(open.divider));
        } else {
          check(`${tag} ${round}: the navigation row stays above the page`, !!open.tabs && open.side.top >= open.tabs.bottom - 1, JSON.stringify([open.tabs, open.side]));
        }
      }
      if (SHOTS && round === "first open") await page.screenshot({ path: `${SHOTS}/${process.env.DOCK_TAG ?? "shot"}-${tag.replace("/", "-")}.png` });
    }
    await page.locator('[data-action="browser.open"]').first().click();
    await page.waitForTimeout(700);
    const shut = await geometry(page);
    check(`${tag} undocked: the column is gone and the navigation row is as wide as the pane's body again`, (!shut.side || shut.side.left >= shut.pane.right - 1) && !!shut.tabs && shut.tabs.right - shut.tabs.left >= shut.pane.right - shut.pane.left - 24, JSON.stringify([shut.side, shut.tabs, shut.pane]));
    await page.close();
  }
}
// Every room between phone and wide, not two samples: the left column shrinks with the window, and the tabs it holds
// must still fit and stay clear of the browser column.
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 800 } });
  const bad = [];
  for (let width = 560; width <= 1440; width += 20) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(`${BASE}?pref=en&turns=3`, { waitUntil: "networkidle" });
    await page.waitForSelector(".app");
    await page.locator('[data-action="browser.open"]').first().click();
    await page.waitForTimeout(400);
    const g = await page.evaluate(() => {
      const tabs = document.querySelector(".pane > .tabs");
      const side = document.querySelector('.pane > .scroll[data-pane="browser"]');
      const t = tabs.getBoundingClientRect();
      const last = [...tabs.querySelectorAll(".tab")].at(-1)?.getBoundingClientRect();
      const s = side?.getBoundingClientRect();
      const stacked = !!s && s.top >= t.bottom - 1;
      const first = tabs.querySelector(".tab")?.getBoundingClientRect();
      const pad = parseFloat(getComputedStyle(tabs).paddingLeft) * 2;
      const need = last && first ? last.right - first.left + pad : 0;
      const pane = document.querySelector(".pane").getBoundingClientRect().width;
      return { fits: tabs.scrollWidth <= tabs.clientWidth + 1, clear: stacked || !s || !last || last.right <= s.left + 1, stacked, need, pane };
    });
    const should = g.pane < g.need + 320 || width <= 640;
    if (!g.fits || !g.clear || g.stacked !== should) bad.push(`${width}:${g.fits ? "" : "overflow "}${g.clear ? "" : "overlap "}${g.stacked !== should ? `stacked=${g.stacked} pane=${Math.round(g.pane)} need=${Math.round(g.need)}` : ""}`);
  }
  check("from 560 to 1440 the tabs fit their row, stay clear of the browser column, and the column stacks exactly when tabs plus its 320px floor no longer fit", bad.length === 0, bad.join(" "));
  await page.close();
}
await browser.close();
if (fails.length) {
  console.log(`\n${fails.length} failed`);
  process.exit(1);
}
console.log("\nall ok");
