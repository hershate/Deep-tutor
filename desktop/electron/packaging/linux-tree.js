"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { assertArchitecture, dynamicallyLinked } = require("./elf");
const { name } = require("../package.json");

function filesIn(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? filesIn(file) : entry.isFile() ? [file] : [];
  });
}

function verifyLinuxTree(root, arch) {
  const files = filesIn(root);
  const hosts = files.filter((file) => file.endsWith(path.join("resources", "bin", "reasonix-studio-host")));
  if (hosts.length !== 1) throw new Error(`expected one packaged host, found ${hosts.length}`);
  const shell = path.resolve(path.dirname(hosts[0]), "../..", name);
  const helper = path.join(root, "usr/lib/reasonix-studio/reasonix-studio-update-helper");
  for (const file of [shell, hosts[0], helper]) {
    if (!fs.statSync(file).isFile()) throw new Error(`missing executable ${file}`);
  }
  const images = files.filter((file) => {
    const fd = fs.openSync(file, "r");
    try {
      const magic = Buffer.alloc(4);
      return fs.readSync(fd, magic, 0, 4, 0) === 4 && magic.readUInt32BE(0) === 0x7f454c46;
    } finally {
      fs.closeSync(fd);
    }
  });
  for (const file of [shell, hosts[0], helper]) {
    if (!images.includes(file)) throw new Error(`${file} is not an ELF executable`);
  }
  assertArchitecture(images, arch);
  const dynamic = dynamicallyLinked([hosts[0], helper]);
  if (dynamic.length) throw new Error(`Go binaries must be static: ${dynamic.map((item) => item.file).join(", ")}`);
  return shell;
}

module.exports = { verifyLinuxTree };

if (require.main === module) {
  if (process.argv.length !== 4) throw new Error("usage: node packaging/linux-tree.js <extracted-package> <amd64|arm64>");
  console.log(verifyLinuxTree(path.resolve(process.argv[2]), process.argv[3]));
}
