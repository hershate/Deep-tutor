import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const script = fileURLToPath(new URL("linux-install-smoke.sh", import.meta.url));

function fixture(t, fail = false, occupied = false, githubActions = "true") {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "linux-install-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const bin = path.join(dir, "bin");
  fs.mkdirSync(bin);
  const tools = {
    sudo: 'exec "$@"',
    dpkg: 'echo dpkg >> "$RX_FIXTURE/probes"\necho arm64',
    "dpkg-deb": 'case "$*" in *Package) echo reasonix-studio ;; *Architecture) echo arm64 ;; *Version) echo "0.0.0~ci" ;; -x*) mkdir -p "$3" ;; *) exit 1 ;; esac',
    "dpkg-query": '[ -e "$RX_FIXTURE/installed" ] || exit 1\ncase "$*" in *Status*) echo "install ok installed" ;; *Version*) echo "0.0.0~ci" ;; *) exit 1 ;; esac',
    "apt-get": 'echo "$1" >> "$RX_FIXTURE/apt-calls"\ncase "$1" in install) touch "$RX_FIXTURE/installed" "$RX_FIXTURE/app" ;; purge) [ -e "$RX_FIXTURE/installed" ] || exit 100; rm "$RX_FIXTURE/installed" "$RX_FIXTURE/app" ;; *) exit 1 ;; esac',
    "xvfb-run": 'shift\nexec "$@"',
    node: 'case "$1" in */linux-tree.js) printf "%s%s/app\\n" "$2" "$RX_FIXTURE" ;; */linux-package-smoke.js) echo "$4" >> "$RX_FIXTURE/smoke-versions"; [ "$RX_FAIL" != 1 ] || exit 42 ;; *) exit 1 ;; esac',
  };
  for (const [name, body] of Object.entries(tools)) fs.writeFileSync(path.join(bin, name), `#!/bin/sh\n${body}\n`, { mode: 0o755 });
  const deb = path.join(dir, "fixture.deb");
  fs.writeFileSync(deb, "fixture");
  if (occupied) fs.writeFileSync(path.join(dir, "installed"), "existing install");
  const env = { ...process.env, PATH: bin + path.delimiter + process.env.PATH, RX_FIXTURE: dir, RX_FAIL: fail ? "1" : "0" };
  if (githubActions === null) delete env.GITHUB_ACTIONS;
  else env.GITHUB_ACTIONS = githubActions;
  const run = spawnSync("bash", [script, deb, "arm64", "0.0.0-ci"], {
    encoding: "utf8",
    env,
  });
  const read = (name) => fs.existsSync(path.join(dir, name)) ? fs.readFileSync(path.join(dir, name), "utf8").trim().split("\n") : [];
  return { run, dir, calls: read("apt-calls"), versions: read("smoke-versions"), probes: read("probes") };
}

for (const value of [null, "", "false", "TRUE", "1"]) {
  test(`GITHUB_ACTIONS=${value ?? "unset"} refuses before any package operation`, (t) => {
    const result = fixture(t, false, true, value);
    assert.notEqual(result.run.status, 0);
    assert.match(result.run.stderr, /only runs in GitHub Actions/);
    assert.deepEqual(result.probes, []);
    assert.deepEqual(result.calls, []);
    assert.deepEqual(result.versions, []);
    assert.equal(fs.existsSync(path.join(result.dir, "installed")), true);
  });
}

test("successful install/reinstall acceptance uninstalls once and preserves the shell's semver", (t) => {
  const result = fixture(t);
  assert.equal(result.run.status, 0, result.run.stderr);
  assert.deepEqual(result.calls, ["install", "install", "purge"]);
  assert.deepEqual(result.versions, ["0.0.0-ci", "0.0.0-ci"]);
  assert.equal(fs.existsSync(path.join(result.dir, "installed")), false);
});

test("a failed packaged-shell acceptance keeps its failure and removes the test install", (t) => {
  const result = fixture(t, true);
  assert.equal(result.run.status, 42, result.run.stderr);
  assert.deepEqual(result.calls, ["install", "purge"]);
  assert.equal(fs.existsSync(path.join(result.dir, "installed")), false);
});

test("an existing Studio install is refused without installation or cleanup", (t) => {
  const result = fixture(t, false, true);
  assert.notEqual(result.run.status, 0);
  assert.deepEqual(result.calls, []);
  assert.equal(fs.existsSync(path.join(result.dir, "installed")), true);
});
