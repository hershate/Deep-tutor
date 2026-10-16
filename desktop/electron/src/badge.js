"use strict";

const DOT_RADIUS = 4.5;
const DOT_RING = 5.75;

function setUnreadBadge(unread, win, electron = require("electron"), platform = process.platform) {
  if (platform === "darwin") {
    electron.app.dock.setBadge(unread ? "•" : "");
  } else if (platform === "win32") {
    let image = null;
    if (unread) {
      const pixels = Buffer.alloc(16 * 16 * 4);
      for (let y = 0; y < 16; y++) {
        for (let x = 0; x < 16; x++) {
          const d = Math.hypot(x + 0.5 - 8, y + 0.5 - 8);
          // A red dot inside a thin white ring, edges smoothed by coverage, so it
          // reads on any taskbar colour and over any icon.
          const outer = Math.min(Math.max(DOT_RING + 0.5 - d, 0), 1);
          if (outer === 0) continue;
          const inner = Math.min(Math.max(DOT_RADIUS + 0.5 - d, 0), 1);
          // Native bitmaps are premultiplied BGRA, including on Windows ARM.
          const mix = (red) => Math.round((255 + (red - 255) * inner) * outer);
          pixels.set([mix(64), mix(64), mix(224), Math.round(255 * outer)], (y * 16 + x) * 4);
        }
      }
      image = electron.nativeImage.createFromBitmap(pixels, { width: 16, height: 16 });
    }
    win.setOverlayIcon(image, unread ? "Reasonix Studio" : "");
  } else {
    electron.app.setBadgeCount(unread ? 1 : 0);
  }
}

module.exports = { setUnreadBadge };
