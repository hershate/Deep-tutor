import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const sheets = await Promise.all(["tokens", "app", "studio"].map(
  (name) => readFile(new URL(`../src/styles/${name}.css`, import.meta.url), "utf8"),
));
const rows = Array.from({ length: 80 }, (_, i) => `<div class="tool-row" style="height:28px">step ${i + 1}</div>`).join("");
const browser = await chromium.launch();
try {
  for (const scheme of ["light", "dark"]) {
    const page = await browser.newPage({ colorScheme: scheme, viewport: { width: 640, height: 480 } });
    await page.setContent(`<div id="scroller" style="height:400px;overflow:auto"><div>
      <details class="activity-group" open><summary><span class="activity-title">Process</span><span class="activity-count">80</span></summary>
      <div class="activity-body">${rows}</div></details>
      <div style="height:600px">after</div></div></div>`);
    for (const content of sheets) await page.addStyleTag({ content });
    await page.locator("#scroller").evaluate((el) => { el.scrollTop = 900; });
    const at = await page.evaluate(() => {
      const summary = document.querySelector(".activity-group > summary").getBoundingClientRect();
      const top = document.querySelector("#scroller").getBoundingClientRect().top;
      const bg = getComputedStyle(document.querySelector(".activity-group > summary")).backgroundColor;
      return { offset: Math.round(summary.top - top), bg };
    });
    assert.ok(at.offset >= 0 && at.offset <= 1, `${scheme}: the header of an open group stays at the top while its steps scroll (offset ${at.offset}px)`);
    assert.notEqual(at.bg, "rgba(0, 0, 0, 0)", `${scheme}: a stuck header paints its own ground`);
    await page.locator("#scroller").evaluate((el) => { el.scrollTop = 0; });
    const rest = await page.evaluate(() => Math.round(document.querySelector(".activity-group > summary").getBoundingClientRect().top - document.querySelector("#scroller").getBoundingClientRect().top));
    assert.ok(rest >= 0, `${scheme}: at rest the header sits where it did`);
    await page.locator(".activity-group > summary").click();
    const quiet = await page.evaluate(() => {
      const group = getComputedStyle(document.querySelector(".activity-group"));
      return { border: group.borderTopWidth, bg: group.backgroundColor };
    });
    assert.equal(quiet.border, "0px", `${scheme}: a folded group is a quiet line, not a bordered card`);
    assert.equal(quiet.bg, "rgba(0, 0, 0, 0)", `${scheme}: a folded group paints no ground of its own`);
    console.log(`${scheme}: header held at the top of the scroller, opaque; folded group is a quiet line`);
    await page.close();
  }
} finally {
  await browser.close();
}
