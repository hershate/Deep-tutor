"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync, spawn } = require("node:child_process");
const { _electron } = require("playwright");

const [executablePath, arch, version] = process.argv.slice(2);
assert.ok(executablePath && ["amd64", "arm64"].includes(arch) && version, "usage: linux-package-smoke.js EXECUTABLE ARCH VERSION");
const home = fs.mkdtempSync(path.join(os.tmpdir(), "studio-installed-"));
const env = { ...process.env, REASONIX_HOME: home, XDG_CONFIG_HOME: path.join(home, "config"), XDG_CACHE_HOME: path.join(home, "cache") };
for (const key of ["REASONIX_STUDIO_HOST", "REASONIX_STUDIO_PAGE", "REASONIX_COMPUTER_HELPER", "REASONIX_DEV", "REASONIX_STATE_HOME", "ELECTRON_RUN_AS_NODE"]) delete env[key];
fs.writeFileSync(path.join(home, "config.toml"), 'default_model = "smoke/model"\n[desktop]\nwelcomed = true\n[[providers]]\nname = "smoke"\nkind = "openai"\nbase_url = "http://127.0.0.1:9/v1"\nmodels = ["model"]\ndefault = "model"\napi_key_env = "SMOKE_API_KEY"\n');
fs.writeFileSync(path.join(home, ".env"), "SMOKE_API_KEY=fixture\n");

async function until(check, message, timeout = 60000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

function alive(pid) {
  try { process.kill(pid, 0); return true; } catch (error) {
    if (error.code === "ESRCH") return false;
    throw error;
  }
}

async function run() {
  let application;
  let diagnostics = "";
  try {
    application = await _electron.launch({ executablePath, env, chromiumSandbox: true, timeout: 60000 });
    application.process().stderr?.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-16384); });
    const identity = await application.evaluate(({ app }) => ({ packaged: app.isPackaged, arch: process.arch, version: app.getVersion() }));
    assert.deepEqual(identity, { packaged: true, arch: arch === "amd64" ? "x64" : arch, version });
    let page;
    await until(async () => {
      page = application.windows().find((win) => win.url().startsWith("http://127.0.0.1:"));
      return Boolean(page);
    }, "the installed shell never loaded its bundled kernel's page");
    await page.waitForFunction(() => document.getElementById("root")?.children.length > 0 && document.documentElement.dataset.shell === "electron");
    const response = await page.evaluate(async () => {
      const result = await fetch("/status", { credentials: "same-origin" });
      return { status: result.status, body: await result.json() };
    });
    assert.equal(response.status, 200);
    assert.equal(typeof response.body, "object");
    const shell = application.process();
    const children = execFileSync("ps", ["-o", "pid=", "--ppid", String(shell.pid)], { encoding: "utf8" }).trim().split(/\s+/).map(Number);
    const hostPath = path.join(path.dirname(executablePath), "resources/bin/reasonix-studio-host");
    const hosts = children.filter((pid) => {
      try { return fs.readlinkSync(`/proc/${pid}/exe`) === hostPath; } catch { return false; }
    });
    assert.equal(hosts.length, 1, "the installed shell must own exactly one bundled host");
    const second = spawn(executablePath, [], { env, stdio: "ignore" });
    const result = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { second.kill(); reject(new Error("second launch never yielded to the installed instance")); }, 30000);
      second.once("error", (error) => { clearTimeout(timer); reject(error); });
      second.once("exit", (code, signal) => { clearTimeout(timer); resolve({ code, signal }); });
    });
    assert.deepEqual(result, { code: 0, signal: null });
    assert.equal(alive(shell.pid), true);
    await application.close();
    application = null;
    assert.equal(shell.exitCode, 0, "the installed shell must exit normally");
    await until(() => hosts.every((pid) => !alive(pid)), "the bundled kernel outlived the installed shell", 10000);
    console.log(`installed ${arch} ${version}: packaged shell, renderer RPC, second launch and clean shutdown passed`);
  } catch (error) {
    console.error(diagnostics);
    throw error;
  } finally {
    if (application) await application.close();
    fs.rmSync(home, { recursive: true, force: true });
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
