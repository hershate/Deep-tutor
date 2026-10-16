import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const BASE = process.env.PERF_URL ?? "http://localhost:4399/perf.html";
const before = process.env.GLOW_BEFORE === "1";
const shots = process.env.GLOW_SCREENSHOTS;
if (shots) await mkdir(shots, { recursive: true });
const browser = await chromium.launch();
let checked = 0;
const errors = [];

async function open(scheme, paper, width, effects, motion, glow) {
  const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, reducedMotion: motion });
  await context.addInitScript(({ scheme, effects }) => {
    localStorage.setItem("rx-theme", scheme);
    localStorage.setItem("rx-effects", effects);
  }, { scheme, effects });
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/themes/bench-photo/background", (route) => route.fulfill({
    contentType: "image/svg+xml",
    body: `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000"><rect width="1600" height="1000" fill="${paper === "bright" ? "#fff9e5" : "#081329"}"/><circle cx="1250" cy="300" r="280" fill="${paper === "bright" ? "#eebc7c" : "#253d69"}"/></svg>`,
  }));
  await page.goto(`${BASE}?pack=photo&glow=${glow}&turns=2&ws=1&sess=2`, { waitUntil: "networkidle" });
  await page.waitForSelector('.flow:not([data-empty])');
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(({ before, effects }) => {
    const value = getComputedStyle(document.querySelector('.flow:not([data-empty])')).textShadow;
    return before && effects === "full" ? value.includes("7px") : value === "none";
  }, { before, effects });
  return { context, page };
}

async function styles(page) {
  return page.locator('.flow:not([data-empty])').first().evaluate((el) => {
    const own = getComputedStyle(el);
    const backing = getComputedStyle(el, "::before");
    return {
      shadow: own.textShadow,
      blur: backing.backdropFilter || backing.webkitBackdropFilter,
      backing: backing.backgroundColor,
      content: backing.content,
      frameShadow: getComputedStyle(document.querySelector(".rail")).textShadow,
    };
  });
}

try {
  for (const scheme of ["light", "dark"]) {
    for (const width of [1440, 390]) {
      for (const paper of before ? [scheme === "light" ? "bright" : "dark"] : ["bright", "dark"]) {
        for (const effects of before ? ["full"] : ["full", "reduced"]) {
          for (const motion of before ? ["no-preference"] : ["no-preference", "reduce"]) {
            const { context, page } = await open(scheme, paper, width, effects, motion, "none");
            const off = await styles(page);
            if (before) assert.notEqual(off.shadow, "none");
            else assert.equal(off.shadow, "none");
            assert.notEqual(off.content, "none");
            if (effects === "full") {
              assert.match(off.blur, /blur\(11px\)/);
              assert.notEqual(off.frameShadow, "none");
            } else {
              assert.equal(off.frameShadow, "none");
            }
            if (shots && paper === (scheme === "light" ? "bright" : "dark") && effects === "full" && motion === "no-preference") {
              await page.screenshot({ path: `${shots}/${scheme}-${width === 390 ? "phone" : "wide"}-${before ? "before" : "after"}.jpg`, type: "jpeg", quality: 88 });
            }
            if (!before) {
              const samples = await page.locator('.flow p').evaluateAll((els) => {
                const canvas = document.createElement("canvas");
                const ctx = canvas.getContext("2d", { willReadFrequently: true });
                return els.flatMap((el) => {
                  const r = el.getBoundingClientRect();
                  if (r.width < 10 || r.height < 10 || r.top < 0 || r.bottom > innerHeight) return [];
                  if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return [];
                  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                  if (!hit || !el.contains(hit)) return [];
                  ctx.clearRect(0, 0, 1, 1);
                  ctx.fillStyle = getComputedStyle(el).color;
                  ctx.fillRect(0, 0, 1, 1);
                  return [{ x: Math.floor(r.left + r.width / 2), y: Math.floor(r.top + r.height / 2), text: el.textContent, fg: [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3) }];
                });
              });
              assert.ok(samples.length, "visible transcript prose must be measured");
              const hiding = await page.addStyleTag({ content: ".flow * { color: transparent !important; text-shadow: none !important }" });
              const blank = (await page.screenshot()).toString("base64");
              const ratios = await page.evaluate(async ({ blank, samples }) => {
                const img = new Image();
                img.src = `data:image/png;base64,${blank}`;
                await img.decode();
                const ctx = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
                ctx.canvas.width = img.width; ctx.canvas.height = img.height;
                ctx.drawImage(img, 0, 0);
                const lum = (c) => c.map((v) => v / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
                return samples.map(({ x, y, fg }) => {
                  const bg = [...ctx.getImageData(x, y, 1, 1).data].slice(0, 3);
                  const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a);
                  return (hi + .05) / (lo + .05);
                });
              }, { blank, samples });
              await hiding.evaluate((el) => el.remove());
              assert.ok(Math.min(...ratios) >= 4.5, `prose contrast ${Math.min(...ratios).toFixed(2)}:1 ${JSON.stringify(samples)}`);
              await page.evaluate(async () => {
                const { apply } = await import("/src/ui/theme.ts");
                const tokens = {
                  light: { bg: "#F2F6F8", bgSoft: "#F9FCFD", panel: "#FFFFFF", border: "#CBD9E0", fg: "#12191C", fgDim: "#4E5D65", accent: "#0E6E82" },
                  dark: { bg: "#080D10", bgSoft: "#0C1316", panel: "#131C21", border: "#23333A", fg: "#E4EEF2", fgDim: "#8298A2", accent: "#4FB6CE" },
                };
                const background = { image: true, focusX: .5, focusY: .5, homeOpacity: .9, taskOpacity: .5, overlayStrength: .4 };
                apply({ id: "bench-photo", name: "Old pack", tokens, background }, document.documentElement.dataset.theme);
              });
              if (effects === "full") await page.waitForFunction(() => getComputedStyle(document.querySelector('.flow:not([data-empty])')).textShadow.includes("7px"));
              const legacy = await styles(page);
              assert.equal(legacy.backing, off.backing);
              assert.equal(legacy.blur, off.blur);
              if (effects === "full") assert.match(legacy.shadow, /7px/);
              else assert.equal(legacy.shadow, "none");
              checked += 2;
              console.log(`ok ${scheme} ${width} ${paper} ${effects} ${motion}; prose min ${Math.min(...ratios).toFixed(2)}:1`);
            }
            await context.close();
          }
        }
      }
    }
  }
  assert.deepEqual(errors, []);
  console.log(before ? "4 baseline views captured" : `${checked} glow/default scenarios passed; no page errors`);
} finally {
  await browser.close();
}
