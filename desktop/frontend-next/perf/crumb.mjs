// The session title sits right beside the project name in the title bar, at every width, and the
// project name still gives way (ellipsis) before the title is pushed off. Layout is a real-browser question.
import { chromium } from "playwright";

const BASE = process.env.PERF_URL ?? "http://localhost:4399/perf.html?pref=zh&ws=2&sess=6&turns=2";
const fails = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  if (!ok) fails.push(name);
};

const browser = await chromium.launch();
try {
  for (const width of [1400, 1000, 700]) {
    const page = await browser.newPage({ viewport: { width, height: 640 } });
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.waitForSelector(".chrome .crumb-proj");
    const at = await page.evaluate(() => {
      const box = (sel) => document.querySelector(sel).getBoundingClientRect();
      const sep = box(".chrome .crumbsep"), title = box(".chrome .crumb > b");
      const projEl = document.querySelector(".chrome .crumb-proj");
      const range = document.createRange();
      range.selectNodeContents(projEl);
      const text = range.getBoundingClientRect();
      return { gap: Math.round(sep.left - text.right), titleRight: Math.round(title.right) };
    });
    check(`${width}px: the title follows the project name`, at.gap >= 0 && at.gap <= 12, `gap ${at.gap}px`);
    check(`${width}px: the title stays on screen`, at.titleRight <= width, `right edge ${at.titleRight}px`);
    const long = await page.evaluate(() => {
      document.querySelector(".chrome .crumb-proj").textContent = "a-very-long-project-folder-name-".repeat(8);
      document.querySelector(".chrome .crumb > b").textContent = "a very long session title ".repeat(8);
      const crumb = document.querySelector(".chrome .crumb").getBoundingClientRect();
      const title = document.querySelector(".chrome .crumb > b").getBoundingClientRect();
      return { overflow: Math.round(title.right - crumb.right), titleWidth: Math.round(title.width) };
    });
    check(`${width}px: long names stay inside the title bar`, long.overflow <= 0 && long.titleWidth > 0, `title overflows by ${long.overflow}px`);
    await page.close();
  }
} finally {
  await browser.close();
}
if (fails.length) {
  console.error(`\n${fails.length} failed: ${fails.join("; ")}`);
  process.exit(1);
}
