// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { apply } from "./theme";
import type { ThemePack } from "../port/port";

const pack: ThemePack = {
  id: "glow-probe", name: "Glow probe",
  tokens: { light: { bg: "#ffffff", fg: "#111111" }, dark: { bg: "#111111", fg: "#eeeeee" } },
  background: { image: true, focusX: .5, focusY: .5, homeOpacity: 1, taskOpacity: .2, overlayStrength: .65 },
};
const withGlow = (textGlow: unknown) => ({ ...pack, background: { ...pack.background, textGlow } }) as ThemePack;
const read = () => {
  const sheet = (document.getElementById("pack-theme") as HTMLStyleElement | null)?.sheet;
  return (sheet?.cssRules[0] as CSSStyleRule | undefined)?.style.getPropertyValue("--flow-text-shadow") ?? "";
};
afterEach(() => apply(null, "light"));

describe("wallpaper transcript glow", () => {
  it.each(["light", "dark"] as const)("turns off only the transcript glow in %s", (scheme) => {
    apply(withGlow("none"), scheme);
    expect(read()).toBe("none");
    expect(document.documentElement.dataset.bg).toBe("on");
  });

  it.each([undefined, "default", "bright", "none; color: red", false, null])("keeps the stylesheet default for %s", (value) => {
    apply(withGlow("none"), "light");
    apply(withGlow(value), "light");
    expect(read()).toBe("");
  });

  it("cleans up when switching to an old pack, a solid pack, or default", () => {
    for (const next of [{ ...pack, id: "legacy" }, { ...pack, id: "solid", background: undefined }, null]) {
      apply(withGlow("none"), "dark");
      expect(read()).toBe("none");
      apply(next, "light");
      expect(read()).toBe("");
    }
  });

  it("retains the choice across scheme and running-state changes", () => {
    apply(withGlow("none"), "light", false);
    apply(withGlow("none"), "dark", true);
    expect(read()).toBe("none");
  });
});
