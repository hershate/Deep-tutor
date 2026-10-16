// A transcript card's height is never an estimate. Rows near the viewport render, and Block unmounts the far ones
// while holding the height it measured; a card that skipped layout with `content-visibility: auto` held a guess until
// it first scrolled into view and the page then shrank under the wheel. The stylesheet must not bring that back.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = process.env.PERF_SRC ?? join(HERE, "..", "src");
const css = ["app.css", "studio.css"].map((f) => readFileSync(join(SRC, "styles", f), "utf8")).join("\n");

const fails = [];
const bad = [...css.matchAll(/([^{}]*\.call[^{}]*)\{([^{}]*)\}/g)].filter((m) => /\.chunk|\.enterbox/.test(m[1]) && /content-visibility\s*:\s*auto/.test(m[2]));
if (bad.length) fails.push("estimated height");
console.log(bad.length ? `FAIL  ${bad.map((m) => m[1].trim()).join(" | ")} uses content-visibility: auto` : "  ok  no .call row in the transcript flow estimates its height");
process.exit(fails.length ? 1 : 0);
