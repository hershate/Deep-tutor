"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

// The kernel the shell spawns, built into the directory electron-builder.yml
// lists as an extra resource.
//
// On macOS it has to be one universal binary rather than the host's own
// architecture. --universal packages an x64 app and an arm64 app and merges
// them, and @electron/universal reads each Mach-O it finds in both: already
// universal on both sides is accepted and left alone, differing slices are
// lipo'd together, and byte-identical thin slices are refused outright. A
// single-architecture kernel copied into both legs is that third case.
const OUT = path.join(__dirname, "..", "bin");
// The host is a main-module package, so it is built from the main module: run
// from here and Go resolves it against desktop/go.mod, whose go.sum carries
// only what the desktop module's own code imports.
const ROOT = path.join(__dirname, "..", "..", "..");
const PKG = "./cmd/reasonix-studio-host";
const CWD = ROOT;

// The symbol table and DWARF are 28% of this binary and nothing in a shipped
// build reads them: a Go panic's traceback comes from the pclntab, which -w
// leaves alone.
//
// The version has to be injected here as well as into the Electron metadata.
// This binary is what opens a workspace on another machine, and the routes that
// install a kernel over there name a release by it; left at "dev" they refuse
// before reaching the network, and npm is all that is left. A build without it
// still works — it just cannot install a remote kernel from a release.
const VERSION = (process.env.REASONIX_VERSION || "").trim();
const LDFLAGS = VERSION ? `-s -w -X main.version=${VERSION}` : "-s -w";

function go(args, env) {
  const run = spawnSync("go", args, { cwd: CWD, stdio: "inherit", env: { ...process.env, ...env } });
  if (run.status !== 0) {
    console.error(`go ${args.join(" ")} failed`);
    process.exit(run.status ?? 1);
  }
}

const driver = spawnSync(process.execPath, [path.join(__dirname, "install-cua-driver.js")], { stdio: "inherit", windowsHide: true });
if (driver.status !== 0) process.exit(driver.status ?? 1);

if (process.platform !== "darwin") {
  // go build names the binary after the package and adds .exe where it belongs.
  // Without cgo, because with it the Linux kernel inherits the runner's glibc.
  // REASONIX_GOARCH names the architecture to build for; unset, Go's own is
  // used, which is the runner's.
  const goarch = (process.env.REASONIX_GOARCH || "").trim();
  go(["build", "-ldflags", LDFLAGS, "-o", OUT + path.sep, PKG], goarch ? { CGO_ENABLED: "0", GOARCH: goarch } : { CGO_ENABLED: "0" });
  console.log("built reasonix-studio-host");
  process.exit(0);
}

// CGO_ENABLED=0 because one of these two is always a cross-compile, and the
// host is pure Go — studio.yml holds it to that.
const slices = ["amd64", "arm64"].map((arch) => {
  const out = path.join(OUT, `reasonix-studio-host-${arch}`);
  go(["build", "-ldflags", LDFLAGS, "-o", out, PKG], { CGO_ENABLED: "0", GOOS: "darwin", GOARCH: arch });
  return out;
});

const universal = path.join(OUT, "reasonix-studio-host");
const merged = spawnSync("lipo", ["-create", "-output", universal, ...slices], { stdio: "inherit" });
if (merged.status !== 0) {
  console.error("lipo failed");
  process.exit(merged.status ?? 1);
}
for (const slice of slices) {
  fs.rmSync(slice, { force: true });
}
console.log("built reasonix-studio-host (universal)");
