"use strict";
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");
const lock = require("../../../internal/platform/computer/cua-driver.lock.json");

function artifact(platform, arch) {
  const key = platform === "darwin" ? "darwin-universal" : `${platform}-${arch}`;
  const selected = lock.artifacts[key];
  if (!selected) throw new Error(`No pinned CUA artifact for ${key}`);
  return selected;
}

async function locate(dir, name) {
  for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
    const candidate = path.join(dir, entry.name);
    if (entry.isFile() && entry.name === name) return candidate;
    if (entry.isDirectory()) { const found = await locate(candidate, name); if (found) return found; }
  }
  return null;
}

function targetArchitecture(goarch, nativeArch) {
  if (!goarch) return nativeArch;
  if (goarch === "amd64") return "x64";
  if (goarch === "arm64") return "arm64";
  throw new Error(`Unsupported CUA target architecture ${goarch}`);
}

async function install(platform = process.platform, arch = targetArchitecture(process.env.REASONIX_GOARCH, process.arch)) {
  const selected = artifact(platform, arch);
  const tempRoot = path.resolve(os.tmpdir());
  const stage = await fs.mkdtemp(path.join(tempRoot, "reasonix-cua-"));
  try {
    const url = `https://github.com/${lock.repository}/releases/download/${lock.tag}/${selected.name}`;
    const response = await fetch(url, { signal: AbortSignal.timeout(120000) });
    if (!response.ok) throw new Error(`CUA download failed: HTTP ${response.status}`);
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length > 128 * 1024 * 1024) throw new Error("CUA artifact exceeds size limit");
    if (crypto.createHash("sha256").update(bytes).digest("hex") !== selected.sha256) throw new Error("CUA artifact SHA-256 mismatch");
    const archive = path.join(stage, selected.name);
    await fs.writeFile(archive, bytes);
    const extracted = path.join(stage, "extracted");
    await fs.mkdir(extracted);
    extract(archive, extracted);
    const name = platform === "win32" ? "cua-driver.exe" : "cua-driver";
    const source = await locate(extracted, name);
    if (!source) throw new Error("CUA artifact contains no driver executable");
    const bin = path.resolve(__dirname, "../bin");
    await fs.mkdir(bin, { recursive: true });
    const destination = path.join(bin, name);
    await fs.copyFile(source, destination);
    if (platform !== "win32") await fs.chmod(destination, 0o755);
    return destination;
  } finally {
    if (!path.resolve(stage).startsWith(tempRoot + path.sep)) throw new Error("Invalid CUA staging path");
    await fs.rm(stage, { recursive: true, force: true });
  }
}

function extract(archive, extracted) {
  const cwd = path.dirname(archive);
  const tar = process.platform === "win32" ? path.join(process.env.SystemRoot || "C:/Windows", "System32", "tar.exe") : "tar";
  const result = spawnSync(tar, ["-xf", path.basename(archive), "-C", path.relative(cwd, extracted)], { cwd, stdio: "inherit", windowsHide: true });
  if (result.status !== 0) throw new Error("CUA extraction failed");
}

module.exports = { artifact, install, targetArchitecture, extract };
if (require.main === module) install().then(file => process.stdout.write(`Installed pinned CUA Driver ${lock.version}: ${file}\n`)).catch(error => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
