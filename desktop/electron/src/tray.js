"use strict";
const { trayImage } = require("./trayimage.js");

// How often the fold is re-read. It is a projection the kernel recomputes on
// demand, so a missed read costs nothing the next one does not restore — which
// is why it is pulled on a timer rather than carried on the stream.
const FOLD_INTERVAL_MS = 5000;

// installTray brings the icon up and reports whether there is one. A shell with
// no icon must never background its window: hiding it would leave a running
// session with nothing left to bring it back.
function installTray(host, { onOpen, onQuit }, electron = require("electron")) {
  const { Menu, Tray, nativeImage, screen } = electron;
  const image = trayImage(nativeImage, screen.getPrimaryDisplay().scaleFactor);
  if (image.isEmpty()) return null;
  const icon = new Tray(image);
  const state = { fold: null, prefs: null };

  // The menu is rebuilt only when what it says changed. Linux hands the menu to
  // the desktop's indicator over D-Bus, and replacing it every poll closes one
  // the person has open or leaves the icon without one.
  let painted = "";
  const paint = (force = false) => {
    // The sentence and the menu words come from the kernel with the numbers:
    // the language is the desktop's setting, and one surface should not need a
    // second catalogue to say what the first already said.
    const line = state.fold?.line ?? "";
    const words = state.fold?.labels ?? {};
    const shown = JSON.stringify([line, words, !!state.prefs?.closeToTray]);
    if (!force && shown === painted) return;
    icon.setToolTip(line ? `Reasonix Studio — ${line}` : "Reasonix Studio");
    icon.setContextMenu(
      Menu.buildFromTemplate([
        { label: words.open ?? "Open", click: onOpen },
        { type: "separator" },
        { label: line || "—", enabled: false },
        {
          label: words.closeToTray ?? "Close to tray",
          type: "checkbox",
          checked: !!state.prefs?.closeToTray,
          click: async () => {
            // Through the same service the settings panel writes with, so one
            // switch has one implementation whichever control asked.
            const next = await host.setTrayPrefs(true, !state.prefs?.closeToTray);
            if (next) state.prefs = next;
            // The native checkbox has already flipped itself; redraw from state
            // even when nothing changed, so a refused save puts it back.
            paint(true);
          },
        },
        { type: "separator" },
        { label: words.quit ?? "Quit", click: onQuit },
      ]),
    );
    painted = shown;
  };

  const refit = () => icon.setImage(trayImage(nativeImage, screen.getPrimaryDisplay().scaleFactor));
  if (process.platform === "win32") screen.on("display-metrics-changed", refit);

  icon.on("click", onOpen);
  const timer = setInterval(() => void refresh(), FOLD_INTERVAL_MS);

  async function refresh() {
    const [prefs, fold] = await Promise.all([host.trayPrefs(), host.trayState()]);
    if (prefs) state.prefs = prefs;
    if (fold) state.fold = fold;
    paint();
  }

  return {
    icon,
    // Cached, so the close button never waits on a round trip and still answers
    // correctly once the kernel has already gone.
    prefs: () => state.prefs,
    // What the icon is showing, so a test can hold it against what the kernel
    // says rather than against what this file thinks it said.
    fold: () => state.fold,
    refresh,
    close() {
      clearInterval(timer);
      screen.removeListener("display-metrics-changed", refit);
      icon.destroy();
    },
  };
}

module.exports = { installTray };
