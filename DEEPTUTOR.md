# Deep-tutor engineering rules

## Current delivery

- The only deliverable is the Tauri 2 desktop installer built from `REF/frontend/app`.
- Browser SPA, Electron, `frontend-next`, web startup scripts and server delivery
  are retired. `scripts/start.ps1`, `scripts/start.sh` and `stage.md` are deleted.
- The current task is full native backend coverage. Backend code lives in
  `desktop/src-tauri/src/backend/`; frontend clients live in `desktop/src/backend/`.
- Existing Python/Reasonix source is removed only after equivalent native coverage exists.
- Project status is recorded in git commit descriptions, never in a progress log.

## Architecture

- Tauri commands provide request/response. Tauri events provide real-time output.
  Do not reintroduce a standalone HTTP service as the frontend's connection.
- SQLite stores business state and the event outbox. Large originals and generated files
  live in the app data directory, not inside the repository.
- Events have monotonically increasing ids and support Last-Event-ID replay.
  Persist a terminal result before emitting it. Reconnecting must not repeat execution or billing.
- Cancellation and failure paths keep every token and cost already consumed.
- API keys enter the OS keyring through one-time forms. They never enter WebView storage,
  plaintext database columns, logs, exports, sync payloads or test fixtures.
- External capabilities return their actual result or a typed error. A simulated success
  is not an implementation.

## Engineering invariants

- Keep the system prompt and tool schema prefix byte-stable when model calls are involved.
  Dynamic state belongs at the turn tail, not in the cache-stable prefix.
- Effect tests assert what reaches the real provider request, frontend sink, database or trajectory.
  Component correctness alone is not proof that the assembled feature works.
- An error a caller must distinguish carries a sentinel or typed code, never a sentence to match.
  The layer that first knows the failure owns its class; downstream layers project, not re-derive it.
- Security decisions use structure, not wording: typed inputs, ASTs, URL resolution and schemas.
- A constructed state is not a reachable one. Before claiming a defect, establish the production
  transition, persistence boundary and authority that can actually produce it.
- New Go and Rust comments are English; other code follows the surrounding file's language.
  Comments explain only non-obvious constraints. Default to no comments.
  Declaration docs are at most 5 lines, package docs at most 8, other comments at most 3.
  Do not narrate incidents or old behavior in code comments.

## Change discipline

- Assess callers, consumers of the same state, replay/reload/restart paths, error cases and wire mirrors
  before editing. A fix that breaks a neighbouring behavior is a failed fix.
- Write tests first when the project already covers the area. They must fail without the change.
- Fix at the owning layer. Reuse the current path rather than adding a second implementation or workaround.
- Frontend work must exercise the real command and durable result, not just a rendered notification.
  Real-time work must also verify event replay and terminal ordering.
- Run the affected build and tests before committing. Do not claim completion with red tests,
  missing implementation or unverified native behavior.
- Commits are fine-grained, Chinese, and carry no co-author. Do not push.
  Describe behavior, verification and remaining limits in the commit description.

## Sensitive host checks

The following paths decide what the agent may execute. Changes there require review and
security review, and are also the coverage gate's subject. Sensitivity is declared, not inferred.

- sensitive: internal/safety/permission/**
- sensitive: internal/safety/sandbox/**
- sensitive: internal/safety/shellsafe/**
- sensitive: internal/base/shellparse/**
- sensitive: internal/session/control/approval.go
- sensitive: internal/session/control/approval_bridge.go
- sensitive: internal/session/control/approval_orchestration.go
- sensitive: internal/model/providerbroker/**
- sensitive: internal/ext/installsource/**
- sensitive: internal/ext/plugin/**
- sensitive: internal/ext/pluginpkg/**
- sensitive: internal/base/netclient/**
- sensitive: internal/safety/redirectguard/**
- sensitive: internal/safety/endpointclient/**
- sensitive: internal/safety/egress/**
- sensitive: internal/platform/browser/**
- sensitive: internal/platform/computer/**
Native file access, credentials, external processes and network egress in
`desktop/src-tauri/src/backend/` also require review and security review.
The `sensitive:` list remains scoped to the retained Go coverage gate; the
unimplemented `internal/web` server paths are not part of the current product.

`make coverage-gate` holds each declared path to its existing floor. Raising a floor is free;
lowering one needs `-allow-drop` and a reason in the commit description.

## Trust and sandbox boundaries

- VerificationRelevant, Observable and HostProtected are different sets.
  `Writable ∩ VerificationRelevant ⊆ Observable ∪ HostProtected`.
- Observation proves what happened; verification proves what the resulting state satisfies.
  An incomplete observation never establishes scope.
- Integrity, confidentiality and authority are independent. ReadOnly addresses integrity only.
  A sandbox claim is valid only where an enforcement primitive exists for that dimension.
- Windows has no general OS-level bash sandbox. Do not turn a white-list or a displayed policy
  into a claim of actual OS isolation.

## Repository rules

- The current decisions live in `note/产品与技术决策.md`; the coverage matrix lives in
  `note/14-Tauri原生后端覆盖清单.md`. The active execution path is `plan/README.md`.
- v1 plugin/API contracts are retired with the old frontend and `API.md`; do not reintroduce them.
- Upstream Reasonix code and documents retain their license and attribution in `NOTICE` and
  `LICENSE-REASONIX`. Display names may change; protocol identifiers and historical facts may not.
- Upstream documents under `docs/kernel/` are for provenance, not the current product architecture.
- Go tooling remains covered by `make lint`, `make check`, `make test`, `make coverage-gate`,
  and `make pricecheck` where relevant. Desktop work uses `npm run build` and `cargo test --release`.
