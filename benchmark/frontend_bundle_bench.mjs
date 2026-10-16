// Frontend first-load benchmark: walks the built SPA (desktop/frontend-next/dist)
// and reports what a browser must download and parse before the first paint,
// against everything the bundle ships. Deterministic by construction — the
// same dist yields the same table — so before/after comparisons carry no
// machine noise, unlike wall-clock timing on an AV-busy host.
//
//   node benchmark/frontend_bundle_bench.mjs [path-to-dist]
//
// Eager set = the scripts, stylesheets and modulepreloads dist/index.html
// names (the rolldown runtime follows the entry synchronously). Everything
// else is a lazy chunk the app fetches only when a view opens. Gzip sizes use
// node:zlib at level 9, the stand-in for what the loopback server sends.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const dist = process.argv[2] ?? "desktop/frontend-next/dist";
const assets = join(dist, "assets");

const files = readdirSync(assets).map((name) => {
  const path = join(assets, name);
  const raw = readFileSync(path);
  return { name, raw, bytes: statSync(path).size };
});

const html = readFileSync(join(dist, "index.html"), "utf8");
const named = new Set([...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((m) => m[1].replace(/^.*\//, "")));

const kb = (n) => (n / 1024).toFixed(1);
const js = files.filter((f) => f.name.endsWith(".js"));
const css = files.filter((f) => f.name.endsWith(".css"));
const eagerJs = js.filter((f) => named.has(f.name));
const eagerCss = css.filter((f) => named.has(f.name));
const sum = (list, pick) => list.reduce((acc, f) => acc + pick(f), 0);

console.log(`chunks: ${js.length} js / ${css.length} css`);
console.log(`total js: ${kb(sum(js, (f) => f.bytes))} KB (gzip ${kb(sum(js, (f) => gzipSync(f.raw).length))} KB)`);
console.log(`eager js: ${kb(sum(eagerJs, (f) => f.bytes))} KB / ${eagerJs.length} chunks (gzip ${kb(sum(eagerJs, (f) => gzipSync(f.raw).length))} KB)`);
console.log(`eager css: ${kb(sum(eagerCss, (f) => f.bytes))} KB`);
console.log("");
console.log("eager chunks:");
for (const f of [...eagerJs, ...eagerCss].sort((a, b) => b.bytes - a.bytes)) {
  console.log(`  ${kb(f.bytes).padStart(8)} KB  ${f.name}`);
}
console.log("");
console.log("top lazy chunks:");
const lazy = js.filter((f) => !named.has(f.name)).sort((a, b) => b.bytes - a.bytes).slice(0, 12);
for (const f of lazy) {
  console.log(`  ${kb(f.bytes).padStart(8)} KB  ${f.name}`);
}
