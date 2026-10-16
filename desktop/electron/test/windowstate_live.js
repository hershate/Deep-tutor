"use strict";
// Opens, moves, maximises and closes real windows through the shell's own openWindow, then reopens them: the
// rectangle a window had when it closed is the one the next launch opens at. Needs no kernel and no network.
// Run with `pnpm windowstate-live`.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow, screen } = require("electron");
const { stateFile, openWindow } = require("../src/windowstate");

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const fails = [];
const check = (name, ok, got) => {
  console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : " — " + JSON.stringify(got)));
  if (!ok) fails.push(name);
};

const file = stateFile(fs.mkdtempSync(path.join(os.tmpdir(), "windowstate-live-")));
const launch = () =>
  openWindow({
    BrowserWindow,
    screen,
    file,
    fallback: { width: 1000, height: 640 },
    min: { width: 400, height: 300 },
    options: { show: true, minWidth: 400, minHeight: 300 },
  });
const closeOne = async (win) => {
  win.close();
  await wait(100);
};

// Closing the last window would otherwise end the run between launches.
app.on("window-all-closed", () => {});
app.whenReady().then(async () => {
  const first = launch();
  await wait(300);
  check("a first launch opens at the default size", first.getBounds().width === 1000 && first.getBounds().height === 640, first.getBounds());
  const area = screen.getPrimaryDisplay().workArea;
  const rect = { x: area.x + 60, y: area.y + 50, width: 820, height: 560 };
  first.setBounds(rect);
  await wait(700);
  await closeOne(first);
  const kept = JSON.parse(fs.readFileSync(file, "utf8"));
  check("closing records the rectangle", kept.width === rect.width && kept.height === rect.height && kept.x === rect.x && kept.y === rect.y && kept.maximized === false, kept);

  const second = launch();
  await wait(300);
  const got = second.getBounds();
  check("the next launch reopens at that rectangle", got.x === rect.x && got.y === rect.y && got.width === rect.width && got.height === rect.height, got);

  second.maximize();
  await wait(700);
  await closeOne(second);
  const maxed = JSON.parse(fs.readFileSync(file, "utf8"));
  check("a maximised close keeps the rectangle it will restore to", maxed.maximized === true && maxed.width === rect.width && maxed.height === rect.height, maxed);

  const third = launch();
  await wait(500);
  check("the next launch is maximised again", third.isMaximized() === true, third.getBounds());
  third.unmaximize();
  await wait(600);
  const restored = third.getBounds();
  check("and restores to the rectangle it had before", restored.width === rect.width && restored.height === rect.height, restored);
  await closeOne(third);

  const fourth = launch();
  await wait(400);
  fourth.maximize();
  await wait(900);
  fourth.minimize();
  await wait(900);
  await closeOne(fourth);
  const minimised = JSON.parse(fs.readFileSync(file, "utf8"));
  check("closing while minimised keeps the maximised record and its restore size", minimised.maximized === true && minimised.width === rect.width && minimised.height === rect.height, minimised);

  fs.writeFileSync(file, JSON.stringify({ x: area.x + 40, y: area.y + 40, width: area.width + 800, height: area.height + 600, maximized: false }));
  const big = launch();
  await wait(400);
  const o = big.getBounds();
  check("a saved size larger than the display is cut to it and kept on screen", o.width <= area.width && o.height <= area.height && o.x >= area.x && o.y >= area.y && o.x + o.width <= area.x + area.width && o.y + o.height <= area.y + area.height, o);
  await closeOne(big);

  fs.writeFileSync(file, JSON.stringify({ x: area.x + area.width + 5000, y: 10, width: 700, height: 500, maximized: false }));
  const lost = launch();
  await wait(300);
  const b = lost.getBounds();
  const shown = screen.getAllDisplays().some((d) => b.x >= d.workArea.x && b.x < d.workArea.x + d.workArea.width && b.y >= d.workArea.y);
  check("a rectangle on a display that is gone opens on one that is there", shown && b.width === 700, b);
  await closeOne(lost);

  console.log(fails.length ? `${fails.length} failed` : "all checks passed");
  app.exit(fails.length ? 1 : 0);
});
