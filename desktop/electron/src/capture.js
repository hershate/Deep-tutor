"use strict";

// Pixels wider than this are scaled down before they leave the shell: a 4K
// window as a PNG overruns the feedback upload limit.
const MAX_WIDTH = 1920;

// The verb takes no argument and has no target of its own: the window is
// whichever one the asking page lives in, and fromWindow refuses any other
// sender. Nothing outside that window's own contents can be captured.
function captureHandler(fromWindow) {
  return async (event) => {
    const target = fromWindow(event);
    if (!target) return null;
    let image = await target.webContents.capturePage();
    if (image.isEmpty()) return null;
    const { width } = image.getSize();
    if (width > MAX_WIDTH) image = image.resize({ width: MAX_WIDTH, quality: "good" });
    return new Uint8Array(image.toPNG());
  };
}

module.exports = { captureHandler, MAX_WIDTH };
