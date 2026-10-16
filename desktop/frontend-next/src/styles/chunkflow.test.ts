import { describe, expect, it } from "vitest";

const SHEETS = import.meta.glob(["./app.css", "./studio.css"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

describe("a transcript row's height", () => {
  it("is never an estimate swapped for a measurement as the row scrolls into view", () => {
    for (const [name, css] of Object.entries(SHEETS)) {
      for (const m of css.matchAll(/([^{}]*\.chunk[^{}]*)\{([^{}]*)\}/g)) {
        expect(m[2], `${name}: ${m[1].trim()}`).not.toMatch(/content-visibility\s*:\s*auto/);
      }
    }
  });
});
