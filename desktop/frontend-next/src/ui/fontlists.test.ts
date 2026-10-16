import { describe, expect, it } from "vitest";
import { MONO_FAMILIES, UI_FAMILIES } from "./look";

describe("the families the font fields suggest", () => {
  it("offers each family once, in a list long enough to choose from", () => {
    for (const list of [UI_FAMILIES, MONO_FAMILIES]) {
      expect(new Set(list).size).toBe(list.length);
      expect(list.length).toBeGreaterThan(20);
    }
  });

  it("keeps a CJK face in the interface list and a Han-capable one in the code list", () => {
    expect(UI_FAMILIES).toContain("Microsoft YaHei");
    expect(UI_FAMILIES).toContain("PingFang SC");
    expect(MONO_FAMILIES).toContain("Sarasa Mono SC");
  });
});
