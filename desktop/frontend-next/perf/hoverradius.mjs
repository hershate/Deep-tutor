// The controls in the composer's bottom row (mode, branch, permission, model, effort) read as one family:
// hovered, each takes the same corner radius and the same ground. A control with its own radius or its own
// hover colour is the kind of slip a reader sees at once. Hover state is a question for a real browser.
import { chromium } from "playwright";

const BASE = process.env.PERF_URL ?? "http://localhost:4399/perf.html?pref=zh&ws=2&sess=6&turns=2";
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!ok) fails.push(name);
};

const CONTROLS = [".studio-mode-picker", ".studio-branch", ".model-picker", ".compose .mode.plain:not(.studio-branch):not(.attach):not(.studio-attach)"];

const browser = await chromium.launch();
try {
  for (const scheme of ["light", "dark"]) {
    const page = await browser.newPage({ colorScheme: scheme, viewport: { width: 1100, height: 760 } });
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.waitForSelector(".compose .studio-branch");
    const seen = [];
    for (const sel of CONTROLS) {
      const el = page.locator(`.compose ${sel}`.replace(".compose .compose", ".compose")).first();
      if (!(await el.count())) continue;
      await el.hover();
      await page.waitForTimeout(300);
      seen.push([sel, await el.evaluate((e) => ({ radius: getComputedStyle(e).borderRadius, bg: getComputedStyle(e).backgroundColor }))]);
    }
    check(`${scheme}: the hovered controls were all found`, seen.length === CONTROLS.length, `${seen.length} of ${CONTROLS.length}`);
    const radii = new Set(seen.map(([, v]) => v.radius));
    const grounds = new Set(seen.map(([, v]) => v.bg));
    check(`${scheme}: one corner radius across the row`, radii.size === 1, [...radii].join(" / "));
    check(`${scheme}: one hover ground across the row`, grounds.size === 1, [...grounds].join(" / "));
    await page.close();
  }
} finally {
  await browser.close();
}
if (fails.length) {
  console.error(`\n${fails.length} failed: ${fails.join("; ")}`);
  process.exit(1);
}
