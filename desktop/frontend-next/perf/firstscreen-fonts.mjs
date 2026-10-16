// The first screen is drawn from the core CJK face (about 1.9 MB), not the 7.8 MB full font. The full font is a
// later family in the stack for characters the core lacks: it must not be fetched for ordinary UI text, and it
// must still load when a character only it has is shown.
import { chromium } from "playwright";

const PAGE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const browser = await chromium.launch();
const page = await (await browser.newContext({ locale: "zh-CN", viewport: { width: 1440, height: 900 } })).newPage();
const fetched = [];
page.on("response", (r) => {
  const m = /\/fonts\/(noto-studio[^/?]*)/.exec(r.url());
  if (m) fetched.push(m[1]);
});
await page.goto(PAGE, { waitUntil: "networkidle" });
await page.waitForSelector(".app", { timeout: 15000 });
await page.waitForTimeout(1500);

const fails = [];
if (fetched.includes("noto-studio.woff2")) fails.push("the first screen fetched the full 7.8 MB font");
if (!fetched.includes("noto-studio-core.woff2")) fails.push("the first screen did not use the core font");
console.log(fails.length ? `FAIL  ${fails.join("; ")} (${fetched.join(", ")})` : `  ok  first screen fetched ${fetched.join(", ")} only`);

await page.evaluate(() => {
  const d = document.createElement("div");
  d.style.cssText = "position:fixed;left:0;top:0;font:32px var(--ui)";
  d.textContent = "龘";
  document.body.append(d);
});
await page.waitForTimeout(1500);
const rare = fetched.includes("noto-studio.woff2");
console.log(rare ? "  ok  a character only the full font has still loads it" : "FAIL  a rare character did not load the full font");
await browser.close();
process.exit(fails.length || !rare ? 1 : 0);
