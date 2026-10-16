import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const { elfArchitecture, elfInterpreter } = require("../packaging/elf.js");
const { verifyLinuxTree } = require("../packaging/linux-tree.js");
const { name } = require("../package.json");

function elf(arch, dynamic = false) {
  const bytes = Buffer.alloc(160);
  bytes.writeUInt32BE(0x7f454c46);
  bytes[4] = 2;
  bytes[5] = 1;
  bytes[6] = 1;
  bytes.writeUInt16LE(arch === "arm64" ? 183 : 62, 18);
  if (dynamic) {
    bytes.writeBigUInt64LE(64n, 32);
    bytes.writeUInt16LE(56, 54);
    bytes.writeUInt16LE(1, 56);
    bytes.writeUInt32LE(3, 64);
    bytes.writeBigUInt64LE(120n, 72);
    bytes.writeBigUInt64LE(12n, 96);
    bytes.write("/lib/ld.so\0", 120);
  }
  return bytes;
}

function fixture(t, arch) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "studio-linux-tree-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const app = path.join(root, "opt", "Reasonix Studio");
  const files = {
    shell: path.join(app, name),
    host: path.join(app, "resources/bin/reasonix-studio-host"),
    helper: path.join(root, "usr/lib/reasonix-studio/reasonix-studio-update-helper"),
    library: path.join(app, "libEGL.so"),
  };
  for (const file of Object.values(files)) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, elf(arch));
  }
  return { root, ...files };
}

for (const arch of ["amd64", "arm64"]) {
  test(`the ${arch} shell, host, helper and libraries agree on the package architecture`, (t) => {
    const tree = fixture(t, arch);
    assert.equal(verifyLinuxTree(tree.root, arch), tree.shell);
    assert.equal(elfArchitecture(elf(arch)), arch);
    assert.equal(elfInterpreter(elf(arch)), null);
  });

  for (const member of ["shell", "host", "helper", "library"]) {
    test(`a ${arch} package refuses a ${member} from the other architecture`, (t) => {
      const tree = fixture(t, arch);
      fs.writeFileSync(tree[member], elf(arch === "arm64" ? "amd64" : "arm64"));
      assert.throws(() => verifyLinuxTree(tree.root, arch), /expected/);
    });
  }

  for (const member of ["host", "helper"]) {
    test(`a ${arch} package refuses a dynamically linked ${member}`, (t) => {
      const tree = fixture(t, arch);
      fs.writeFileSync(tree[member], elf(arch, true));
      assert.throws(() => verifyLinuxTree(tree.root, arch), /must be static/);
    });
  }
}

test("missing or non-ELF required executables cannot pass the package check", (t) => {
  const tree = fixture(t, "arm64");
  fs.writeFileSync(tree.helper, "#!/bin/sh\n");
  assert.throws(() => verifyLinuxTree(tree.root, "arm64"), /not an ELF/);
  fs.unlinkSync(tree.host);
  assert.throws(() => verifyLinuxTree(tree.root, "arm64"), /expected one packaged host/);
});

test("unsupported ELF encodings and release architectures fail closed", (t) => {
  const bytes = elf("arm64");
  bytes[5] = 2;
  assert.throws(() => elfArchitecture(bytes), /little-endian/);
  const tree = fixture(t, "arm64");
  assert.throws(() => verifyLinuxTree(tree.root, "aarch64"), /unsupported release architecture/);
});

test("ARM64 RPM naming is normalized before the manifest consumes it", async (t) => {
  const { record, built } = require("../packaging/arch.js");
  const hook = require("../packaging/canonical-artifact-names.js");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "studio-arm64-name-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const deb = path.join(dir, "ReasonixStudio-linux-arm64.deb");
  const rpm = path.join(dir, "ReasonixStudio-linux-aarch64.rpm");
  for (const file of [deb, rpm]) {
    fs.writeFileSync(file, "fixture");
    record(file, "arm64");
    t.after(() => built.delete(path.resolve(file)));
  }
  const files = await hook({ artifactPaths: [deb, rpm] });
  assert.deepEqual(files.map((file) => path.basename(file)), ["ReasonixStudio-linux-arm64.deb", "ReasonixStudio-linux-arm64.rpm"]);
  assert.ok(fs.existsSync(files[1]));
});
