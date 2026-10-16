"use strict";
const path = require("node:path");

// Where the two things this shell launches live. Packaged, both sit in
// resources/ beside app.asar rather than inside it: a child process cannot be
// spawned from an archive, and the kernel serves the SPA off the filesystem.
// Dev reads them from the working tree, which is the layout run-studio.sh fills.
const HOST = "reasonix-studio-host";

function hostBinary({ packaged, resourcesPath, dirname, platform, env = {} }) {
  if (env.REASONIX_STUDIO_HOST) return env.REASONIX_STUDIO_HOST;
  // go build names the binary after the package and adds .exe on Windows, where
  // spawn will not run a file without it.
  const name = platform === "win32" ? `${HOST}.exe` : HOST;
  return path.join(packaged ? resourcesPath : path.join(dirname, ".."), "bin", name);
}

// The host owns the packaged CUA Driver; macOS permissions belong to its shell.
function computerDriver({ packaged, resourcesPath, dirname, platform, env = {} }) {
  if (env.REASONIX_CUA_DRIVER) return env.REASONIX_CUA_DRIVER;
  if (!["darwin", "win32", "linux"].includes(platform)) return "";
  const name = platform === "win32" ? "cua-driver.exe" : "cua-driver";
  return path.join(packaged ? resourcesPath : path.join(dirname, ".."), "bin", name);
}

function pageDir({ packaged, resourcesPath, dirname, env = {} }) {
  if (env.REASONIX_STUDIO_PAGE) return env.REASONIX_STUDIO_PAGE;
  const root = packaged ? resourcesPath : path.join(dirname, "..", "..");
  return path.join(root, "frontend-next", "dist");
}

function computerDriverContract({ packaged, resourcesPath, dirname }) {
  return packaged ? path.join(resourcesPath, "bin", "cua-driver.lock.json") : path.resolve(dirname, "../../../internal/platform/computer/cua-driver.lock.json");
}

module.exports = { computerDriverContract, hostBinary, computerDriver, pageDir };
