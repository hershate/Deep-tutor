import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const contract = require("../../../internal/platform/computer/cua-driver.lock.json");
const { install } = require("../packaging/install-cua-driver");
const { startEmbedded, metadata } = require("../src/cua");
const executable = await install();
let owned;
try {
 const env = { ...process.env, REASONIX_LIVE_COMPUTER: executable };
 if (process.platform === "darwin") {
  owned = await startEmbedded(executable, contract, text => process.stderr.write(text));
  const before = await metadata(owned.socket);
  assert.equal(before.result.embedded, true);
  env.REASONIX_LIVE_COMPUTER_SOCKET = owned.socket;
  env.REASONIX_LIVE_COMPUTER_ABORT_SOCKET = owned.abortSocket;
 }
 const child = spawn("go", ["test", "./internal/platform/computer", "-run", "^TestLive", "-count=1", "-v", "-timeout=3m"], { cwd: root, env, stdio: "inherit", windowsHide: true });
 const code = await new Promise((resolve, reject) => { child.once("error", reject); child.once("close", resolve); });
 if (code !== 0) process.exitCode = code ?? 1;
} finally { await owned?.stop(); }
