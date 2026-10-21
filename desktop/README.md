# Reasonix Studio

> **本仓状态（2026-10-20）**：本文档描述上游 Reasonix Studio 桌面端，是阶段④ **Deep-tutor Studio** 的基座。文中 "Reasonix" 产品名与 `reasonix` 命令名属上游语境，本地化（改名 `deep-tutor`）属阶段① M1.2、品牌化属 M1.4，均未完成。修改披露见根 [`NOTICE`](../NOTICE)。

A desktop window around the Reasonix Go kernel. The window hosts a React SPA,
and every request the SPA makes reaches the kernel's own HTTP server
(`internal/frontend/serve`) over loopback, so Studio, `reasonix serve` and the remote UI
all answer the same routes.

```
┌──────────────────────────────────────────────────────────────┐
│  renderer (React + TS, Vite)  —  desktop/frontend-next        │
│    fetch("/…") · EventSource("/events")                       │
└───────────────▲────────────────────────────┬─────────────────┘
                │ HTTP + SSE over 127.0.0.1  │
┌───────────────┴────────────────────────────▼─────────────────┐
│  desktop/electron  main process                               │
│    windows, menus, dialogs, tray, the platform opener         │
│    owns OS capability and no business state                   │
└───────────────▲────────────────────────────┬─────────────────┘
                │ spawns, holds the lease    │
┌───────────────┴────────────────────────────▼─────────────────┐
│  cmd/reasonix-studio-host  (main module, CGO-free)            │
│    internal/assembly/boot.Build → internal/session/control.Controller          │
│    (same assembly the CLI uses: providers, tools, gate, …)    │
└───────────────────────────────────────────────────────────────┘
```

The renderer never learns which shell it is in: anything a page cannot do it
asks `HostPort` (`frontend-next/src/port/host.ts`) for, and the main process
answers over IPC that carries OS capability only. The boundaries this runs on
are written down in [`docs/STUDIO_SHELL_BOUNDARIES.md`](../docs/kernel/STUDIO_SHELL_BOUNDARIES.md).

## Layout

| Path | What it is |
| --- | --- |
| `electron/` | the shell: main process, preload bridge, packaging |
| `frontend-next/` | the SPA it serves |
| `internal/platform/update/` | manifest, download, verify, apply — shared by the shell and the helper |
| `cmd/update-helper/` | the elevated half of an update (dpkg on Linux) |
| `cmd/studio-manifest/`, `cmd/sign/` | release-side tools |
| `internal/winuninstall/` | taking a previous Windows install over |

The host binary itself lives in the main module at `cmd/reasonix-studio-host`,
because the updater it carries has to stay in the kernel's language.

## Why a nested module

`desktop/` is its own Go module (`module reasonix/desktop`, `replace reasonix =>
../`). The parent module's `go build / vet / test ./...` skip this directory
while the import path stays under `reasonix/`, so it can still import the
`deep-tutor/internal/*` kernel. The module is CGO-free, so no platform carries
build dependencies of its own.

## Prerequisites

- Go (matches the parent module).
- Node 24+ and **pnpm 10** (`npm install -g pnpm@10`).

## Running it

```sh
make studio          # build the SPA + kernel and launch the Electron shell
```

One launch path on every platform. The dev build is already an .app, so macOS
treats it as a real GUI app and native panels — "Add a folder…" — open normally.

For frontend iteration, run the kernel and point Vite at it:

```sh
go run ./cmd/reasonix serve                       # from the repository root
cd desktop/frontend-next && pnpm install && pnpm dev   # :5273, proxies API paths
```

## Building and testing

```sh
make studio-test                                  # go test ./... for this module
cd frontend-next && pnpm typecheck && pnpm test && pnpm build
```

Packaging runs through electron-builder; `release-studio.yml` drives it on all
three platforms. Linux ships a `.deb` because that is what makes Studio
self-updating there. See `docs/STUDIO_RELEASE.md`.
