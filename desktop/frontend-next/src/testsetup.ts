import { configure } from "@testing-library/react";
import { beforeEach } from "vitest";
import { preload } from "./i18n";

configure({ asyncUtilTimeout: 10_000 });

// An English window fetches its catalogue before the first render — main.tsx
// awaits load beside the kernel probe — and jsdom's navigator reports English,
// so every suite that boots English, explicitly or by default, gets the same
// catalogue here: once per file, the way a real window gets it once per load.
// It only fills the memo; what draws is still decided by each boot().
await preload();
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = query => ({ matches: false, media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false });
}
// The app opens on the study tutor; the agent chat is the #chat surface. Most
// suites here exercise the chat surface, so every test starts on it, and the
// suites that pin the tutor surface (or the default) set their own hash — the
// address is consumed on mount, so a once-per-file write would cover only the
// first render.
if (typeof window !== "undefined") {
  beforeEach(() => {
    window.location.hash = "#chat";
    // Each test is a fresh window: the pane the last test was looking at must
    // not decide which pane this one opens on. Real windows keep theirs —
    // tests share one sessionStorage.
    try { sessionStorage.removeItem("rx-active-pane"); } catch { /* refused: next load falls back */ }
  });
}
