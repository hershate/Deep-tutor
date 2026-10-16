import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const sheets = await Promise.all(["tokens", "app", "studio", "sessionquick", "sessionpin"].map(
  (name) => readFile(new URL(`../src/styles/${name}.css`, import.meta.url), "utf8"),
));
const title = "这是什么？ @.reasonix/attachments/clipboard-" + "long-title".repeat(20);
const browser = await chromium.launch();
try {
  for (const scheme of ["light", "dark"]) {
    for (const width of [220, 320]) {
      const page = await browser.newPage({ colorScheme: scheme });
      await page.setContent(`<div class="rail" style="width:${width}px">
        <div class="kids">
          <div class="sessrow sessrow-context" tabindex="0" aria-selected="true">
            <span class="sesstitle" title="${title}"><i class="sesspin"></i><span>${title}</span><em class="sessarch">已归档</em></span>
            <div class="sessquick"><button>归档</button><button>删除</button></div>
          </div>
          <div class="sessrow sessrow-context" data-unread tabindex="0">
            <span class="sesstitle" title="短标题"><span>短标题</span></span><span class="unread-dot"></span>
          </div>
          <div class="sessrow sessrow-remote">
            <span class="sesstitle" title="${title}"><span>${title}</span></span><span class="sessmeta">3 轮</span>
          </div>
          <div class="sessrow sesscopy">
            <span class="sesstitle" title="早先版本 · 4 轮"><span>早先版本 · 4 轮</span></span>
          </div>
        </div>
      </div>`);
      for (const content of sheets) await page.addStyleTag({ content });
      const measure = () => page.evaluate(() => [...document.querySelectorAll(".sessrow")].map((row) => {
        const text = row.querySelector(".sesstitle > span");
        const box = row.getBoundingClientRect();
        const style = getComputedStyle(text);
        return {
          height: box.height, top: box.top, bottom: box.bottom,
          textHeight: text.getBoundingClientRect().height, lineHeight: parseFloat(style.lineHeight),
          nowrap: style.whiteSpace, ellipsis: style.textOverflow,
          truncated: text.scrollWidth > text.clientWidth,
          fullTitle: row.querySelector(".sesstitle").title === text.textContent,
        };
      }));
      for (const state of ["idle", "hover", "focus"]) {
        if (state === "hover") await page.locator(".sessrow").first().hover();
        if (state === "focus") await page.locator(".sessrow").first().focus();
        const rows = await measure();
        for (const row of rows) {
          assert.equal(row.height, 34, `${scheme}/${width}/${state}: row height`);
          assert.equal(row.nowrap, "nowrap");
          assert.equal(row.ellipsis, "ellipsis");
          assert.ok(row.textHeight <= row.lineHeight + 1);
          assert.ok(row.fullTitle);
        }
        assert.equal(rows[1].top, rows[0].bottom, "no gap between sessions");
        assert.equal(rows[2].top, rows[1].bottom, "no gap before remote session");
        assert.equal(rows[3].top, rows[2].bottom, "no gap before earlier version");
        assert.ok(rows[0].truncated && rows[2].truncated, "long local and remote titles truncate");
        if (state !== "idle") {
          const contained = await page.locator(".sessquick > button").evaluateAll((buttons) => buttons.every((button) => {
            const rect = button.getBoundingClientRect();
            const row = button.closest(".sessrow").getBoundingClientRect();
            return rect.top >= row.top && rect.bottom <= row.bottom && rect.left >= row.left && rect.right <= row.right;
          }));
          assert.ok(contained, "quick actions fit inside the compact row");
        }
      }
      console.log(`${scheme}/${width}: 34px rows, no gaps, single-line ellipsis, hover/focus stable`);
      await page.close();
    }
  }
} finally {
  await browser.close();
}
