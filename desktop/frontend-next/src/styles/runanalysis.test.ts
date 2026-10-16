import { expect, it } from "vitest";

const CSS = import.meta.glob("./studio.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

it("keeps the activity visibility floor in fixed pixels rather than timeline percentages", () => {
  const rule = Object.values(CSS)[0].match(/\.run-round-track\s*>\s*i\s*\{([^}]+)\}/)?.[1];
  expect(rule).toBeDefined();
  expect(rule).toMatch(/min-width:\s*3px\s*;/);
});
