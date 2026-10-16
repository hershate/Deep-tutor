import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const dir = path.dirname(fileURLToPath(import.meta.url));
const indexScript = path.join(dir, "update-versions-index.sh");
const withdrawScript = path.join(dir, "withdraw-version.sh");

function run(script, ...args) {
  const out = spawnSync("bash", [script, ...args], { encoding: "utf8" });
  return { status: out.status, stderr: out.stderr, json: out.status === 0 ? JSON.parse(out.stdout) : null };
}

function catalog(...versions) {
  let file = "-";
  let json = null;
  for (const v of versions) {
    json = run(indexScript, file, `v${v}`, `studio-v${v}`, "studio", `2026-10-1${versions.indexOf(v)}T00:00:00Z`).json;
    file = path.join(mkdtempSync(path.join(tmpdir(), "wd-")), "c.json");
    writeFileSync(file, JSON.stringify(json));
  }
  return file;
}

const names = (rows) => rows.map((v) => v.version.replace(/^v/, ""));
const AT = "2026-10-12T02:00:00Z";

test("withdrawing moves the row out of versions and keeps it, with the reason", () => {
  const file = catalog("2.36.0", "2.37.0");
  const out = run(withdrawScript, file, "2.36.0", "blank window on first launch", AT);
  assert.equal(out.status, 0, out.stderr);
  assert.deepEqual(names(out.json.versions), ["2.37.0"]);
  assert.equal(out.json.withdrawn.length, 1);
  assert.equal(out.json.withdrawn[0].version, "v2.36.0");
  assert.equal(out.json.withdrawn[0].reason, "blank window on first launch");
  assert.equal(out.json.withdrawn[0].withdrawnAt, AT);
  assert.match(out.json.withdrawn[0].manifest, /studio-v2\.36\.0\/latest\.json$/);
});

test("withdrawing an unknown or already withdrawn version is refused", () => {
  const file = catalog("2.36.0", "2.37.0");
  assert.notEqual(run(withdrawScript, file, "9.9.9", "no such release", AT).status, 0);
  const once = run(withdrawScript, file, "2.36.0", "bad", AT);
  const next = path.join(mkdtempSync(path.join(tmpdir(), "wd-")), "c.json");
  writeFileSync(next, JSON.stringify(once.json));
  assert.notEqual(run(withdrawScript, next, "2.36.0", "again", AT).status, 0);
});

test("the last remaining release cannot be withdrawn", () => {
  const file = catalog("2.37.0");
  assert.notEqual(run(withdrawScript, file, "2.37.0", "bad", AT).status, 0);
});

test("a reason is required", () => {
  const file = catalog("2.36.0", "2.37.0");
  assert.notEqual(run(withdrawScript, file, "2.36.0", "", AT).status, 0);
});

test("restoring puts the original row back and clears the withdrawal", () => {
  const file = catalog("2.36.0", "2.37.0");
  const gone = run(withdrawScript, file, "2.36.0", "bad", AT);
  const mid = path.join(mkdtempSync(path.join(tmpdir(), "wd-")), "c.json");
  writeFileSync(mid, JSON.stringify(gone.json));
  const back = run(withdrawScript, mid, "--restore", "2.36.0", AT);
  assert.equal(back.status, 0, back.stderr);
  assert.deepEqual(names(back.json.versions), ["2.37.0", "2.36.0"]);
  assert.equal((back.json.withdrawn ?? []).length, 0);
  assert.equal("reason" in back.json.versions[1], false);
});

test("publishing a later release keeps the withdrawn list and never revives a withdrawn version", () => {
  const file = catalog("2.36.0", "2.37.0");
  const gone = run(withdrawScript, file, "2.36.0", "bad", AT);
  const mid = path.join(mkdtempSync(path.join(tmpdir(), "wd-")), "c.json");
  writeFileSync(mid, JSON.stringify(gone.json));
  const next = run(indexScript, mid, "v2.38.0", "studio-v2.38.0", "studio", "2026-10-13T00:00:00Z");
  assert.deepEqual(names(next.json.versions), ["2.38.0", "2.37.0"]);
  assert.equal(next.json.withdrawn[0].version, "v2.36.0");
  const rerun = run(indexScript, mid, "v2.36.0", "studio-v2.36.0", "studio", "2026-10-13T00:00:00Z");
  assert.deepEqual(names(rerun.json.versions), ["2.37.0"]);
});
