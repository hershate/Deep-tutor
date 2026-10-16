import assert from "node:assert/strict";
import { chromium } from "playwright";

// The small magnifier beside the session filters opens the rail's find box and
// closes it again: a second press must not reopen it.
const base = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const browser = await chromium.launch();
try {
  for (const theme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, colorScheme: theme, reducedMotion: "reduce" });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${base}?pref=zh`, { waitUntil: "networkidle" });
    await page.waitForSelector(".studio-filter-search");
    const shown = () => page.evaluate(() => Number(getComputedStyle(document.querySelector(".rail .wsfind")).opacity) > 0.5);
    const press = () => page.locator(".studio-filter-search").click();
    assert.equal(await shown(), false, `${theme}: the find box starts hidden`);
    await press();
    await page.waitForTimeout(250);
    assert.equal(await shown(), true, `${theme}: the first press shows the find box`);
    await press();
    await page.waitForTimeout(250);
    assert.equal(await shown(), false, `${theme}: a second press on the magnifier must hide the find box, not show it again`);
    await press();
    await page.waitForTimeout(250);
    assert.equal(await shown(), true, `${theme}: the third press shows it again`);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("ok search-toggle");
} finally {
  await browser.close();
}
