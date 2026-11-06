# Deep-tutor engineering rules

## Current delivery

- The only deliverable is the Tauri 2 desktop installer built from `desktop/`; its UI
  originates from the user-supplied `REF/frontend/app`.
- Generated installation, Start Menu and installer names use ASCII `DeepTutor`. Keep the Chinese
  interface brand and the `com.deeptutor.workspace` identifier unchanged so existing data stays put.
- Browser SPA, Electron, `frontend-next`, Python/Go backends, web startup scripts
  and `stage.md` are retired and removed from the working tree.
- Backend code lives in `desktop/src-tauri/src/backend/`; frontend clients live in
  `desktop/src/backend/`. Do not reintroduce a standalone HTTP service.
- Create the default application data directory automatically. Keep the main window visible and
  put startup recovery and directory management inside it. A custom absolute local directory is
  persisted independently, validated before saving and used on the next launch; never move or
  delete the old workspace implicitly. `--verify-native` is non-interactive and uses only its
  separate absolute verification directory and configuration.
- Project status is recorded in git commit descriptions, never in a progress log.

## Architecture and invariants

- Tauri commands provide request/response; Tauri events provide real-time output.
- SQLite stores business state, execution records and the event outbox. Large originals
  and generated files live in the app data directory, not in the repository.
- Run the full schema migration only when its fingerprint changes; repair detected drift instead
  of skipping it. Reuse the account connection, but release its guard before another operation
  or command needs the same account store.
- Use one material parse worker. Keep pending jobs in SQLite, support cancellation while queued,
  do not start another job after stopping, and register the next job before retiring the old one.
- Events have monotonically increasing ids and support Last-Event-ID replay. Persist a
  terminal result before emitting it. Reconnecting must not repeat execution or billing.
- Cancellation and failure paths retain all tokens and costs already consumed.
- API keys enter the OS keyring through one-time forms. They never enter WebView storage,
  plaintext database columns, logs, exports, sync payloads or test fixtures.
- External capabilities return their actual result or a typed error. Simulated success is
  not an implementation. Models are text-only; media preview is not content recognition.
- Keep system prompt and tool schema prefixes byte-stable. Dynamic state belongs at the
  turn tail, not in a cache-stable prefix.
- Errors callers distinguish carry typed codes, not sentences to match.
- Security decisions use structure: typed inputs, URL resolution, schemas, paths and hashes.
- New Rust comments are English, explain only non-obvious constraints and are omitted by default.
- Declaration docs are at most 5 lines, package docs at most 8 and other comments at most 3.

## Change discipline

- Assess callers, shared state, replay/reload/restart paths, error cases and wire mirrors
  before editing. Fix the owning layer instead of adding a second implementation.
- Write tests first when the project already covers the area. They must fail without the change.
- Frontend work must exercise the real command and durable result, not only a notification.
  Real-time work must also verify event replay and terminal ordering.
- Use MockRuntime, real SQLite, bounded local loopback services and temporary files for tests.
  Never drive foreground windows or dialogs, synthesize keyboard/mouse input or capture the screen.
- Do not install test software or packages without need. Reuse existing project and F-drive caches.
- Run affected build and tests before committing. Do not claim completion with red tests,
  missing implementation or unverified native behavior.
- Work happens on `main`; the `refactor/reasonix` branch is retired. Commits are
  fine-grained, Chinese, and carry no co-author. Do not push proactively; the user
  decides pushes and releases.
  Describe behavior, verification and remaining limits in the commit description.

## Sensitive host checks

Changes to the following native paths require review and security review. Sensitivity is
explicitly declared, not inferred from a file name.

- sensitive: desktop/src-tauri/src/backend/security/**
- sensitive: desktop/src-tauri/src/backend/providers/**
- sensitive: desktop/src-tauri/src/backend/web/**
- sensitive: desktop/src-tauri/src/backend/materials/**
- sensitive: desktop/src-tauri/src/backend/agent/**
- sensitive: desktop/src-tauri/src/backend/backup/**
- sensitive: desktop/src-tauri/src/backend/sharing/**
- sensitive: desktop/src-tauri/src/backend/sync/**
- sensitive: desktop/src-tauri/src/backend/plugins/**
- sensitive: desktop/src-tauri/src/backend/store.rs
- sensitive: desktop/src-tauri/src/main.rs

Review path containment, archive and decompression limits, URL resolution and redirect
checks, keyring handling, user/workspace isolation, approval boundaries, event ordering,
foreign-key/id mapping, and exclusion of secrets from logs, exports and test fixtures.
A displayed allow-list is not an OS sandbox. Read-only behavior does not prove confidentiality
or authority. Do not claim a security property without an enforcing implementation.

## Repository rules and verification

- Current decisions: `note/产品与技术决策.md`.
- Feature and acceptance matrix: `note/14-Tauri原生后端覆盖清单.md`.
- Validation contract: `plan/README.md`.
- `make lint` checks Rust formatting and the frontend production build.
- `make test` runs `cargo test --release --locked -- --test-threads=1` in `desktop/src-tauri`.
- `make check` runs both. `make build` produces the Tauri 2 NSIS installer.
- Real provider accounts, media-codec playback and interactive installation require separate
  acceptance; local protocol fixtures and compilation do not prove those environments.
- Preserve `NOTICE` and `LICENSE-REASONIX`. Historical sources and deleted product routes
  are traceable through git, not through retired documentation copies.
