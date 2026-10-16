import { expect, it } from "vitest";

const SHEETS = import.meta.glob("./*.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

// A tweened custom property that inherits restyles every node under the element
// it changes on, every frame. The workbench width is tweened, so it must not
// inherit and must change on the pane that reads it, not on .app.
it("keeps the workbench width off the inherited, app-wide tween", () => {
  const tokens = SHEETS["./tokens.css"];
  expect(tokens).toMatch(/@property --dock-w \{[^}]*inherits: false/);
  for (const [name, css] of Object.entries(SHEETS)) {
    for (const block of css.matchAll(/(^|\n)\s*\.app\s*\{([^}]*)\}/g)) {
      expect(block[2], `${name}: .app declares --dock-w`).not.toMatch(/--dock-w\s*:/);
      expect(block[2], `${name}: .app tweens --dock-w`).not.toMatch(/transition:[^;]*--dock-w/);
    }
  }
});
