import { describe, expect, it } from "vitest";

const SHEETS = import.meta.glob(["./studio.css", "./tokens.css"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const SHEET = Object.values(SHEETS).join("\n");

const faces = (family: string) =>
  [...SHEET.matchAll(/@font-face\s*\{([^{}]*)\}/g)].map((m) => m[1]).filter((body) => body.includes(`font-family: "${family}"`));

describe("the CJK font stack", () => {
  it("serves a first screen from the core face and keeps the full font as a later family", () => {
    const [core] = faces("Noto Studio");
    expect(core, "a Noto Studio face").toBeTruthy();
    expect(core).toContain("noto-studio-core.woff2");
    expect(core).toMatch(/unicode-range\s*:/);
    const [full] = faces("Noto Studio Full");
    expect(full, "a Noto Studio Full face").toBeTruthy();
    expect(full).toContain("noto-studio.woff2");
  });

  it("names the full font after the core one in every stack that uses either", () => {
    const stacks = [...SHEET.matchAll(/--(?:ui|mono)\s*:\s*([^;]*);/g)].map((m) => m[1]).filter((s) => s.includes("Noto Studio"));
    expect(stacks.length).toBeGreaterThan(0);
    for (const stack of stacks) {
      const core = stack.indexOf('"Noto Studio"');
      const full = stack.indexOf('"Noto Studio Full"');
      expect(core, stack).toBeGreaterThan(-1);
      expect(full, stack).toBeGreaterThan(core);
    }
  });
});
