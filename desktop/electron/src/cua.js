"use strict";
const fs = require("node:fs/promises");
const path = require("node:path");
const net = require("node:net");
const { spawn } = require("node:child_process");

function metadata(socketPath) {
  return new Promise((resolve, reject) => {
    const connection = net.createConnection(socketPath);
    let text = "";
    const finish = (error, value) => { connection.destroy(); error ? reject(error) : resolve(value); };
    connection.setTimeout(500, () => finish(new Error("CUA readiness timeout")));
    connection.on("error", error => finish(error));
    connection.on("connect", () => connection.write('{"method":"metadata"}\n'));
    connection.on("data", bytes => {
      text += bytes.toString("utf8");
      if (Buffer.byteLength(text) > 16384) return finish(new Error("CUA readiness response exceeds limit"));
      const end = text.indexOf("\n");
      if (end < 0) return;
      try { finish(null, JSON.parse(text.slice(0, end))); } catch (error) { finish(error); }
    });
    connection.on("end", () => finish(new Error("CUA readiness connection ended")));
  });
}

function accepts(reply, pid, contract) {
  const value = reply?.result;
  return reply?.ok === true && value?.pid === pid && value.embedded === true && value.host_bundle_id === contract.host_bundle_id && value.driver_version === contract.version && value.contract_version === contract.contract_version && value.tools_list_schema_version === contract.tools_list_schema_version && value.capability_version === contract.capability_version && value.mcp_protocol_version === contract.protocol_version;
}

async function startEmbedded(executable, contract, onStderr = () => {}, spawnDriver = spawn) {
  if (!path.isAbsolute(executable)) throw new Error("CUA executable must have an absolute path");
  const directory = await fs.mkdtemp("/tmp/reasonix-cua-");
  await fs.chmod(directory, 0o700);
  const socket = path.join(directory, "driver.sock");
  const abortSocket = path.join(directory, "abort.sock");
  const peers = new Set();
  let child = null;
  let identity = null;
  let stopping = null;
  let aborting = null;

  async function unlinkOwned() {
    try {
      const current = await fs.lstat(socket);
      if (identity && current.dev === identity.dev && current.ino === identity.ino) await fs.unlink(socket);
    } catch (error) { if (error.code !== "ENOENT") throw error; }
    identity = null;
  }

  async function launch() {
    child = spawnDriver(executable, ["serve", "--embedded", "--parent-liveness-stdio", "--no-permissions-gate", "--socket", socket, "--host-bundle-id", contract.host_bundle_id, "--permission-mode", "standard"], {
      stdio: ["pipe", "ignore", "pipe"], windowsHide: true,
      env: { ...process.env, CUA_DRIVER_EMBEDDED_HOST_PID: String(process.pid), CUA_DRIVER_RS_TELEMETRY_ENABLED: "false", DO_NOT_TRACK: "1" },
    });
    let failure = null;
    child.on("error", error => { failure = error; });
    child.stderr.on("data", bytes => onStderr(bytes.toString("utf8")));
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      if (failure) throw failure;
      if (child.exitCode !== null || child.signalCode !== null) throw new Error("Embedded CUA exited during startup");
      try {
        const current = await fs.lstat(socket);
        if (!current.isSocket() || (current.mode & 0o777) !== 0o600) throw new Error("Invalid private CUA socket");
        const reply = await metadata(socket);
        if (!accepts(reply, child.pid, contract)) throw new Error("Embedded CUA identity/version mismatch");
        identity = current;
        return;
      } catch (error) { if (!["ENOENT", "ECONNREFUSED"].includes(error.code)) throw error; }
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error("Embedded CUA failed readiness");
  }

  async function abort() {
    if (stopping) throw new Error("CUA is shutting down");
    await stopProcess(child, true);
    await unlinkOwned();
    if (stopping) throw new Error("CUA is shutting down");
    await launch();
  }

  const control = net.createServer(peer => {
    peers.add(peer);
    peer.on("close", () => peers.delete(peer));
    peer.on("error", () => peer.destroy());
    peer.setTimeout(15000, () => peer.destroy());
    let text = "";
    peer.on("data", bytes => {
      text += bytes.toString("utf8");
      if (Buffer.byteLength(text) > 1024) return peer.destroy();
      const end = text.indexOf("\n");
      if (end < 0) return;
      peer.removeAllListeners("data");
      try {
        const request = JSON.parse(text.slice(0, end));
        if (request.op !== "abort" || Object.keys(request).length !== 1) throw new Error("Invalid CUA control operation");
        aborting ??= abort().finally(() => { aborting = null; });
        aborting.then(() => peer.end('{"ok":true}\n'), () => peer.end('{"ok":false}\n'));
      } catch { peer.end('{"ok":false}\n'); }
    });
  });

  async function shutdown() {
    for (const peer of peers) peer.destroy();
    if (control.listening) await new Promise(resolve => control.close(resolve));
    if (aborting) await aborting.catch(() => {});
    if (child) await stopProcess(child);
    await unlinkOwned();
    await fs.rmdir(directory).catch(error => { if (!["ENOENT", "ENOTEMPTY"].includes(error.code)) throw error; });
  }
  const stop = () => { stopping ??= shutdown(); return stopping; };
  try {
    await launch();
    await new Promise((resolve, reject) => { control.once("error", reject); control.listen(abortSocket, resolve); });
    await fs.chmod(abortSocket, 0o600);
    return { socket, abortSocket, stop };
  } catch (error) { await stop(); throw error; }
}

async function stopProcess(child, force = false) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(resolve => child.once("close", resolve));
  if (force) child.kill("SIGKILL");
  else {
    child.stdin.end();
    let timer;
    await Promise.race([exited, new Promise(resolve => { timer = setTimeout(resolve, 1500); })]);
    clearTimeout(timer);
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
  }
  await exited;
}

function createQuitGate(current, quit, report) {
  let state = "idle";
  return event => {
    if (state === "finished") return;
    if (state === "draining") { event.preventDefault(); return; }
    const owned = current();
    if (!owned) return;
    event.preventDefault();
    state = "draining";
    Promise.resolve().then(() => owned.stop()).catch(report).finally(() => {
      state = "finished";
      quit();
    });
  };
}

module.exports = { accepts, metadata, startEmbedded, stopProcess, createQuitGate };
