"use strict";
const fs = require("node:fs");
const path = require("node:path");

// Where the window was when it last closed, so the next launch opens the same
// rectangle instead of the default. Only a rectangle some display can still
// show is reused: a monitor that has gone leaves the saved position pointing at
// nothing, and a frameless window has no title bar to drag back from there.
const SAVE_DELAY_MS = 400;
const GRAB = { width: 120, height: 40 };

const stateFile = (userData) => path.join(userData, "window-state.json");

const whole = (v) => Number.isFinite(v) && Number.isInteger(v);

function loadState(file) {
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!raw || typeof raw !== "object" || !whole(raw.width) || !whole(raw.height) || raw.width <= 0 || raw.height <= 0) return null;
    const placed = whole(raw.x) && whole(raw.y);
    return { ...(placed ? { x: raw.x, y: raw.y } : {}), width: raw.width, height: raw.height, maximized: raw.maximized === true };
  } catch {
    return null;
  }
}

// Written beside and renamed over, so a crash mid-write leaves the last good copy.
function saveState(file, state) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state));
  fs.renameSync(tmp, file);
}

// The strip a person grabs the window by must lie inside some display's work area.
function grabbable(saved, areas) {
  if (saved.x === undefined) return null;
  return (
    areas.find((a) => {
      const across = Math.min(saved.x + saved.width, a.x + a.width) - Math.max(saved.x, a.x);
      return across >= Math.min(GRAB.width, saved.width) && saved.y >= a.y && saved.y + GRAB.height <= a.y + a.height;
    }) ?? null
  );
}

/** placement answers the rectangle to open at. The size is cut to the display
 *  that shows the saved position, and the position is moved in until the whole
 *  window fits; a rectangle nothing shows keeps only its size, cut to the
 *  primary display, and the platform picks the place. The layout's minimum
 *  comes last. */
function placement({ saved, areas, primary, fallback, min }) {
  if (!saved) return { ...fallback, maximized: false };
  const home = grabbable(saved, areas);
  const area = home ?? primary;
  const width = Math.max(min.width, Math.min(saved.width, area.width));
  const height = Math.max(min.height, Math.min(saved.height, area.height));
  if (!home) return { width, height, maximized: saved.maximized };
  const within = (v, lo, span, size) => Math.max(lo, Math.min(v, lo + span - size));
  return { x: within(saved.x, area.x, area.width, width), y: within(saved.y, area.y, area.height, height), width, height, maximized: saved.maximized };
}

// A minimised or full-screen window reports the screen's rectangle as its normal
// bounds on some platforms, so what it says then is not what to come back to.
const settled = (win) => !win.isMinimized() && !win.isFullScreen();

const capture = (win) => ({ ...win.getNormalBounds(), maximized: win.isMaximized() });

/** track writes the window's rectangle shortly after it settles and once more as
 *  it closes. A minimised or full-screen window keeps its normal bounds. */
function track(win, file, { delay = SAVE_DELAY_MS, timer = setTimeout, untimer = clearTimeout } = {}) {
  let pending = null;
  const write = () => {
    pending = null;
    if (win.isDestroyed() || !settled(win)) return;
    try {
      saveState(file, capture(win));
    } catch {
      // A rectangle that did not save is one launch at the default size, not a reason to fail the window.
    }
  };
  const later = () => {
    if (pending) untimer(pending);
    pending = timer(write, delay);
  };
  for (const event of ["resize", "move", "maximize", "unmaximize"]) win.on(event, later);
  win.on("close", () => {
    if (pending) untimer(pending);
    write();
  });
}

/** openWindow builds the window at its remembered rectangle and keeps that
 *  record current. It is hidden until the caller shows it, so a maximise has
 *  nothing to flicker against. */
function openWindow({ BrowserWindow, screen, file, fallback, min, options }) {
  const { maximized, ...rect } = placement({
    saved: loadState(file),
    areas: screen.getAllDisplays().map((d) => d.workArea),
    primary: screen.getPrimaryDisplay().workArea,
    fallback,
    min,
  });
  const win = new BrowserWindow({ ...rect, ...options });
  if (maximized) win.maximize();
  track(win, file);
  return win;
}

module.exports = { stateFile, loadState, saveState, placement, track, openWindow, GRAB };
