# Deep-tutor project memory

This file is loaded into every session's system prompt (the cache-stable prefix),
so keep it concise and durable — it is the project's standing instructions to the
agent. It is the Deep-tutor analog of Claude Code's CLAUDE.md.

## Conventions

- Go kernel under `internal/`; each package owns one concern. A package's long
  explanation belongs in its `doc.go`, not spread across implementation files.
- One transport-agnostic `control.Controller` sits behind every frontend (chat
  TUI, HTTP/SSE serve, Electron desktop). Add behavior to the controller, not a
  frontend, so all three inherit it.
- Layering (enforced): utility packages import nothing under `deep-tutor/` except
  one another — that layer is closed, so a leaf reaching a leaf drags no graph
  along, while refusing it leaves one judgement copied into several of them; only
  the frontends `cli`, `serve`, `acp`, `boot` and the hosts `cmd/`, `desktop/`
  may import `control`; nothing below a frontend may import one. The declared
  sets live in `tools/repolint/layers.go`.
- Subagent delegation keeps five concepts apart: a profile says how a worker
  thinks, `TaskSpec` what this call wants, `CapabilityGrant` what it may touch,
  `ContextRequest` what it starts from, `SchedulerPolicy` when it runs. Put a
  field in whichever member decides its value — profiles carry ceilings, never
  per-call values. `internal/runtime/delegation/profile_boundary_test.go` enforces it.
- Cache-first: the system-prompt prefix (base prompt, tools, declared prefix
  configuration) must stay byte-stable across turns so DeepSeek's automatic
  prefix cache stays warm. Never mutate it mid-session — ride the turn tail
  instead (see `control.Compose`), under the rules in *Context projection*.
- Performance features land with an effect test at their final boundary
  (`internal/assembly/boot/effect_test.go` pattern): assert what actually reaches the
  provider request, frontend sink, or trajectory through the real `boot.Build`
  assembly. Component correctness is not system effectiveness.
- A mutex- or atomic-guarded struct is ratcheted on its **scalar** field count
  (`struct-state`), not its total: independent flags multiply into states no
  type records as legal. Fixing a boundary case by adding one more `bool` is
  the move this blocks — group by lifetime into a named sub-state instead
  (`agent.perTurnState` is the pattern), which costs one field and removes the
  whole product.
- The context window (projection, compaction, estimation) owns `windowState`;
  the loop reaches it via `a.window()`, it reaches the loop via `windowHost`.
  `window-state` fails any other read of `sess.win`.
- Judgements read structure, never wording: shell ASTs (`shellparse`), types,
  contracts, tool schemas. Phrase tables and message sniffing have been retired
  in batches (task-policy prose, the planner's approval phrases, the executor
  handoff tables) because each one misfires on real input, and a misfiring
  judgement inside a gate is worse than no gate. Security allow-lists are the
  exception and stay. Something that can only be built by matching words does
  not get built — say what is missing instead of guessing at it.
- The other half of that rule: an error a caller must tell apart **carries an
  identity** — a sentinel, a typed code — never a sentence. Only the producer
  knows a deadline expired rather than a socket closed, and a message is where
  that knowledge goes to die: the reader is left matching words, or with
  nothing. Across HTTP the identity is a dotted code through `refuse`;
  `refusal-path` fails a new `http.Error` in `internal/frontend/serve`, and the parity
  test fails a code the frontend cannot say. Inside Go it is `errors.Is` on a
  sentinel the wrapping preserves; `error-text` fails a new match against an
  error message, following it one hop into a local because storing the text
  first is what the direct form turns into.
- **Whoever first knows why an operation failed owns its class.** A transport
  status is not a domain identity, and a display string is not a classification;
  past that boundary a layer may project the class, never re-derive it. Both
  directions were live: `catalog.go` answered 400 for a name nobody declared and
  for a disk that would not write, so a failed store told the user their request
  was wrong; `ServerRow` decided a connection was an authorisation problem by
  matching `/401|403|unauthorized/` against prose an external MCP server wrote,
  which the host had sanitised and truncated on the way through. Domain,
  internal and dependency are three answers with three different things to do
  next, and a caller that cannot tell them apart reports the last two as the
  first.
- Failure attribution is host-owned state. When the host can determine why an
  operation failed or stopped — truncation, timeout, unavailable context,
  dependency skipping, policy refusal, output spill, argument parsing — that
  cause goes to the model. Surfacing only the downstream symptom leaves it to
  invent one, and the invented rule outlives the incident: a response cut at the
  output limit reaches the model as "invalid arguments", and the session learns
  to stop batching calls that were never the problem.
- A host-owned identity has to cross the model-visible boundary. Stable step
  ids, typed error codes, boundary causes — anything the host expects the model
  to cite must appear wherever that state is rendered for it. Telling the model
  in a schema to prefer X while every concrete rendering shows only Y trains it
  to use Y; `complete_step` said `step_id` PREFERRED while all four todo
  renderers printed bare ordinals. Canonical dynamic state still stays out of
  the cache-stable prefix — project the identities needed to act on it at the
  turn tail.
- A metric is its definition, not its name. Before a number becomes a finding,
  state what it counts, from which anchor, over which population, and what it
  excludes. Three readings in one study died in that gap: a command of unknown
  extent read as a proven write, the latest report's adjudication read as every
  report's, and a syntactically accepted call read as a closed one. Each time a
  convenient proxy had been left standing in for the state it was named after,
  and each time the mechanism story built on it was wrong. A number without
  those four lines is an observation, not a result.
- A constructed state is not a reachable one. Before a destructive test or a
  counterfactual is reported as a defect, say who in production produces that
  state, through which transitions, across which persistence boundary, under
  whose authority, and whether a real run has been seen reaching it. A test that
  assigns a private field proves the mechanism *given* the state and must be
  reported as exactly that: `a.projectChecks` is written once at boot and never
  again, so reassigning it stood in for a resume rather than demonstrating one,
  and the bypass built on it was never shown to be production-reachable.
  Severity is reachability times frequency times authority, not "can be
  bypassed".
- A wire type the desktop re-declares by hand is held to the kernel's by
  `wire-parity`, both directions: a field one side sends and the other cannot
  read fails, and so does one it reads that nothing sends. The two are compiled
  by different toolchains and meet only over JSON, so nothing else in the tree
  notices when one of them grows a field. The pairs are declared in
  `tools/repolint/wireparity.go`, never inferred — no spelling tells a mirrored
  contract from a struct that merely carries json tags.
- "Add behavior to the controller so every frontend inherits it" is held by
  `frontend-parity`: a capability one frontend already drives, a frontend whose
  declared port carries it, and no call, is a missing edge, and the ratchet
  counts edges. Which port each frontend drives is declared in
  `tools/repolint/frontendparity.go`; a deliberate exception goes in the scope
  table there with an issue and a reason, and costs what the edge cost, so the
  table cannot become where debt disappears. The matrix is read from
  `go/types` at one pinned build target, never from selector names — `Close`
  and `Status` are capabilities and also stdlib methods, and a name match
  records `os.File.Close` as a frontend driving `Lifecycle.Close`, a false
  "wired" that hides real debt. How a verb is spelled — `/branch`,
  `POST /branches` — is presentation and stays out of it.
- The desktop's presentation layer answers to canonical state and never
  redefines it. Six rules, each held by a gate rather than by memory:
  presentation reorders and reweights what the kernel owns but never restates
  it; **mount is not a motion cause** — a fact enters once, and replay or
  virtualization bringing its element back is not that fact happening again;
  a search alias buys discoverability and never becomes a thing's identity;
  when a setting takes effect is declared per block, from what its endpoint
  does, never inferred from wording or handler names; ordering panels by
  lifecycle moves them and must not remount them; and a control's identity
  survives a change of where it is drawn — the settings sheet and the composer
  render `chrome.preset` because that is one intent, not two.

## Comments

Default is none — the code is the truth. Write one only when the **why** is
non-obvious: a hidden constraint, a workaround anchored to something verifiable,
an invariant the type system cannot express, or an external-protocol quirk.

A comment states the constraint that holds now. It does not narrate how the code
got here: a reader who has never seen an earlier version must lose nothing, and
past tense about this code's own behaviour is the tell — an external protocol's
history is not. "Ids alone do not answer it" is the rule; "ids alone were the
wrong question, so the list went on reading as the model wrote it" is the
incident behind the rule, and the incident belongs in the commit message. This
outranks the surrounding style: much of the tree reads as prose and none of it
is the standard. No gate reads register — telling narrative from a statement of
constraint needs a phrase table, which is the one thing this repo does not
build — so this rule is held by review.

- **Stylesheets carry none.** A rule's selector and its declarations are the
  whole statement. What a `.css` comment would have said goes to whichever of
  two places can act on it: a constraint a component has to honour belongs in
  that component, and a decision about the medium itself — a measured colour, a
  layout mechanic, why the obvious rule is the wrong one — belongs in the
  stylesheet's companion document (`src/styles/TOKENS.md`, `LAYOUT.md`), which
  is read end to end rather than met 600 times as an aside.
- **English, always** — every comment, in every other language the tree is
  written in. A codebase whose commentary is half one language reads as two
  codebases, and the half a given reader cannot read is the half that rots.
- Declaration doc: ≤5 lines. Package comment: ≤8 lines, or ≤40 in a `doc.go`.
- Every other comment: ≤3 lines. Struct-field and trailing `//`: 1 line.
- Never: restatements of the code, phase/stage narrative, incident or
  conversation history, section banners, commented-out code, `@param` lists.
- `TODO(#nnn):` and `HACK(#nnn):` need the issue anchor. `FIXME` is banned.
- One responsibility per file; 800 lines is the ceiling. Test files are exempt:
  their length tracks how many cases they cover, not how many concerns they
  carry, so splitting one only scatters a subject's table across files.

`go run ./tools/repolint` enforces the budgets above against a ratchet baseline:
recorded debt is tolerated, anything new fails CI. Never widen the baseline to
land a change — fix the code. `-update` lowers budgets freely and refuses to
raise one without `-allow-widen`, so carrying debt through a rename or an
extraction is asked for in the command and justified in the PR. A clean run also
reports budget the tree stopped using: it is only reclaimed by an `-update`, and
until then it is room a file can grow back into.

## Context projection

Canonical dynamic state — the skill registry, the memory store — lives in its own
owner, and what the model sees is a projection of it onto each request. The
prefix was never where these belonged wrongly; freshness ownership was missing.

- **Prefix ownership.** Only declared prefix configuration may move the
  cache-stable prefix; ordinary memory writes, skill writes and runtime facts
  ride the turn. Pinned memory bodies are the one declared exception, because
  pinning is a request to pay one cold start.
- **Projection freshness.** When canonical state changes, the next eligible turn
  carries the new projection or can say why it is not owed. "Right after a fold,
  or in the next session" is not an answer.
- **Projection ownership.** Whoever owns a dynamic fact owns the invalidation
  that makes its projection owed again. A projection kept fresh because every
  writer remembered to announce itself is one forgotten call from stale —
  `skillSet.owedCatalog` asks the registry rather than waiting to be told.
- **Determinism.** The same canonical state and the same projection state
  produce byte-identical model-visible bytes.
- **Detection is semantic.** Freshness may not rest on writer notification, on
  mtime, or on which call path made the change. The registry answers from
  canonical state, so a switch flipped and a file edited past every API are the
  same question, and a change no projection renders is not a change.

`internal/assembly/boot/context_projection_test.go` holds all five at the provider
boundary, with the skills listing and the standing instructions as the two
worked examples. A block is settled only once the turn carrying it reaches the
runner: a turn blocked by a hook or refused by an extension after composing put
those bytes nowhere, and clearing the debt there would leave the model holding
state nobody sent it. A projection that has become empty is still owed once —
otherwise the block the model already has goes on reading as current.

## Memory

- Standing instructions are hierarchical: committed/shared `REASONIX.md`,
  `AGENTS.md`, and `CLAUDE.md`; personal `*.local.md` variants; matching files in
  ancestor directories; and user-global files under the memory state root
  (`DEEPTUTOR_STATE_HOME`, otherwise `DEEPTUTOR_HOME`, otherwise `~/.reasonix` on
  macOS/Linux or `%APPDATA%\reasonix` on Windows). All distinct supported files
  in a directory load; `AGENTS.md` is not merely a fallback.
- `@path` on its own line imports another file's contents.
- `#<note>` in chat quick-adds an always-on instruction. The `remember` tool
  instead saves a fallible background fact (frontmatter file + `MEMORY.md`
  index). Fact `type` classifies content; independent `scope` controls whether it
  is project-only (the default) or explicitly global.
- The saved-fact index stays out of the stable prefix and is reached through the
  `memory` tool; pinned bodies and global user/feedback bodies load into it, the
  latter as lower-priority compatibility guidance. The current turn receives a
  tail note.

## Notes

One tool call costs one model round trip, so calls that do not depend on each
other belong in the same round: several reads, edits to different files, a check
after the edit that enables it. Several edits to the *same* file are one
`multi_edit` — it applies them against each other in memory and rewrites the
file only if all of them land, so a failure midway leaves nothing half-edited.

## Vendor rates

Official list prices live in one place: `officialRates` in
`internal/contract/pricing/rates.go`, oldest generation first, the last being what a
model charges today. A price change is an **append, never an edit** — the
superseded rate is what lets an installed config be recognised as holding one
of ours and brought forward, instead of quoting a retired rate for the rest of
its life. `config` reads these through `pricing.CurrentRate` and
`SupersededRates` and restates none of them; a table written twice is one that
drifts, which is how a vendor's USD rate ended up published on one side only.

`make pricecheck` reads each vendor's own page and compares it against that
table, and reports how long ago a person last confirmed each one. It reaches
the network, so it stays out of `make lint` and out of CI: run it before
touching a rate and when a vendor announces a change. A page it cannot read
exits non-zero — unread is its own verdict and never counts as agreement.

## Curated presets

A preset changing shape reaches the installs that already have the old one
through `shippedPresets` in `internal/contract/config/preset_upgrade.go`: one declared
shape per generation, and one upgrade that brings a matching entry forward. The
guard is the whole shape — catalog, window, default — because an entry holding
what we shipped is ours to move and anything else its user curated.

A preset is also what settles which of its models read images. Probing an
address a preset covers answers from that declaration, because reading a name
misses every Kimi, Qwen, MiniMax and Claude model they declare and `deepseek-
flash` spells nothing at all; the spelling is the last resort for an address
nothing has declared, and stays a correctable suggestion rather than a claim.

Three distinctions are load-bearing, and each was a separate migration function
before they were written down. A window the shape did not name is the user's,
so only a shape identified by its window moves one. `vision_models` **narrows**,
so an upgrade marks only the models it adds and only where the preset says they
read images. And an explicitly empty list is a choice — nothing here reads
images — which is not the same as the nil that means nobody has said.

## Pre-push CI simulation

Run these **before every commit** to catch the fastest CI failures locally:

```bash
gofmt -w .                          # catches gofmt (saves ~13s CI)
go vet ./...                        # catches vet warnings (saves ~52s CI/lint)
make lint                           # golangci-lint at CI's pin + repolint
go test ./internal/tools/builtin/ ./internal/assembly/boot/  # catches tool/boot test breaks
```

`make lint` runs both gates CI runs, at the version in `.golangci-version`;
`make lint-install` installs it. Do not skip it: a `modernize` finding never
shows up in `go vet`, and the CI round trip that catches it instead costs ten
minutes.

A full repolint run reports every file over budget, including debt your change
never touched. To see only what your own edits owe:

```bash
make check
```

Repo-wide ceilings still report under a narrowed run, because a file can push
the tree past one without exceeding its own budget.

`make check` owns the evidence-bearing changed-file universe — everything the
worktree altered *and* everything it added that git has not been told about.
Never substitute a direct `go run ./tools/repolint` invocation for it when
recording verification evidence: the host recognizes a `make` target as a check
whose result it can read, while an arbitrary `go run ./tools/...` is a program
it has to assume writes, and the flags behind the target are its implementation
rather than the contract. Building that universe from `git diff` alone once let
a new file reach the gate unopened and be reported clean.

## Import cycle rule

Before importing a new internal package from a non-test file, verify the target package's **test files** aren't already importing back to you:

```
# BAD: agent(_test.go) → tool/builtin(sessions.go) → agent  → setup failed
```

Use `go test ./path/to/target/` to detect cycles **before** pushing. A `[setup failed]` message means a cycle exists.

## Change discipline

**Assess before editing.** For every change, humans and agents list callers,
consumers of the same state, sibling features, reload/replay/restart paths,
error and edge states, TUI/CLI and desktop equivalents, and wire mirrors.

A fix that breaks a neighbouring behaviour is a failed fix.

**Test the neighbourhood.** UI changes add or extend interaction tests for
adjacent functions of the same component or flow (composer: submit, clear,
queue, withdraw, restore, steer, failure, replay after reload) and run the whole
affected suite.

Write tests first; they must fail without the change.

**Fix at the owning layer.** Use the existing unified pattern: no special-case
flags, duplicated paths, or workarounds at the symptom site.

If the design is duplicated or wrong, make the smallest unifying change:
one path instead of two, deleting what becomes dead. Use current language and
framework idioms; do not add a new pattern where one exists.

**PR evidence.** Every PR description carries: Cause, Blast radius,
Neighbouring behaviours tested, and Why this layer.

## Deep-tutor host checks

The paths below decide what the agent is allowed to do — a wrong change here
is not a bug in a feature, it is a hole in a boundary. Declaring them makes
every change under them demand `review` plus `security_review` before a turn
can finish. Sensitivity is declared, never inferred: the host does not read it
out of how a path is spelled, which cannot tell `internal/auth` from
`session_write_authority.go`, or a trace file from a data race.

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
- sensitive: internal/web/auth/**
- sensitive: internal/web/session/**
- sensitive: internal/web/shim/**

One declaration, two effects: the same list is also the coverage gate's subject.
`make coverage-gate` holds each path to the coverage it already has, because
when this was written these paths sat *below* the tree's median rather than
above it — the code deciding what the agent may execute was its least-tested
part. Floors ratchet like repolint's budgets: raising one is free, lowering one
needs `-allow-drop` and a reason in the PR. Adding a `sensitive:` path gets both
effects; there is no second list to keep in sync.

## Trust domain

Three sets, kept apart because conflating them turns a performance number into a
security claim: **VerificationRelevant** (state that can change whether a
delivery claim holds), **Observable** (state the host can reconcile after an
action, so it can say what that action did), **HostProtected** (state a
capability/sandbox boundary makes unwritable). The invariant is
`Writable ∩ VerificationRelevant ⊆ Observable ∪ HostProtected`:

> Any state that can both be modified by an action and influence the delivery
> claim must either be observable by the host or protected from modification by
> host-enforced boundaries. An incomplete observation never establishes scope.

Effects and state answer different questions and never stand in for each other —
observation proves what an action did, verification proves what the resulting
state satisfies — which is why `unproven_mutation` is settled only by
observation, never by a check that ran afterwards.

What follows bounds Observable today. It is not a restatement of the invariant:
moving a number here must not move the promise.

- `scanWorkspace` walks the workspace and every granted extra root recursively,
  skips VCS store dirs, and is `complete` only below 50k files in total. Over that, or past a walk error, it
  establishes nothing and the mutation stays unproven.
- Symlinks are recorded by `Lstat`, so a write through one lands outside what
  the walk compares. Confining the resolved target is the sandbox's job.
- `internal/safety/sandbox` confines writes to the workspace, configured extras, temp
  and toolchain caches — on macOS and Linux. Windows has no OS-level bash
  sandbox, so there nothing the host enforces bounds `Writable`.
- The governed host authorities are a *named* set — ssh-agent, docker, podman.
  DBus, Wayland, X11, gpg-agent, keyrings and arbitrary sockets stay reachable,
  so the claim is "these endpoints are governed", never "host IPC is isolated".

`stateEpoch` tracks host-observed mutations, not an exact filesystem snapshot;
unobserved external writers are a gap shared by the whole verification model. Go
baseline test criteria use the toolchain's build overlay and therefore share
ordinary verification's epoch semantics; the overlay is not a general filesystem
view.

A captured criterion may be superseded only by authority outside
execution-mutable state; the current workspace, its tests, and the agent itself
cannot authorize supersession.

Two gaps follow and are open, not decided: a build that reads `.git` makes VCS
state VerificationRelevant while the scan skips it, and on Windows a symlink out
of the workspace is neither observed nor protected. Closing either means
widening observation or narrowing writability — never widening what a check is
taken to prove.

## Sandbox dimensions

`HostProtected` above is the delivery-level abstraction. Sandbox policy refines
it into three independent protection dimensions. Their separation is empirical:
measured bypasses showed that protection on one dimension cannot be assumed to
imply protection on another.

- **Integrity** — what host state may be mutated. Expressed by write roots.
- **Confidentiality** — what host information may be observed. Expressed by
  `ForbidReadRoots`.
- **Authority** — what host capabilities may be exercised *without* possessing
  their secrets. Expressed by `HostAuthorities`.

`ReadOnly` is therefore an Integrity property only; it makes no Confidentiality
or Authority claim.

Network transport and host authority are independent even when a backend
primitive conflates them: on macOS, `(deny network*)` also cut ssh-agent, so one
flag could not express "no internet, still sign".

Filesystem visibility is not authority: denying `file-read*` on a socket did not
stop a connect. Possession is not authority: the key can be unreadable while an
agent still signs with it.

A protection claim exists only where a backend has a demonstrated enforcement
primitive for that dimension. Unsupported dimensions stay explicitly unsupported
— Windows currently enforces none of the three — because a claim that outruns
its enforcement is worse than an absent one: it is believed.

**Known unmeasured surface — macOS host-mediated delegation:** Apple Events and
Mach-service lookup are not denied by the current allow-default profile. Prior
`osascript` and `launchctl submit` probes were inconclusive; no delegation
effect has been demonstrated. This surface is outside the currently governed
host-authority endpoint set.

## Deep-tutor repository rules

This repository is not the upstream project: the kernel and the desktop shell
were absorbed from [DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)
(studio branch, 2026-10 snapshot) under the MIT License. The engineering
chapters above are the upstream's, kept as they were. What follows is this
repository's, and it outranks the upstream's process chapters where the two
disagree.

### Where the decisions live

- **改造决策（宪法）**：[`note/12-Reasonix改造决策.md`](./note/12-Reasonix改造决策.md)
  holds the 61 decisions the user confirmed one by one. Any implementation
  that conflicts with it stops and asks; the answer is written back into that
  file's revision record. It is the first place to look before changing
  behaviour that a decision already settled.
- **实施进度**：[`stage.md`](./stage.md) is the live progress log. Every
  completed step, every commit, every key decision and every blocker updates
  it. A new session reads it first to find out where the work stands.
- **改造方案**：[`plan/reasonix/`](./plan/reasonix/README.md) holds the six
  design documents; the implementation follows them.
- **领域语义基准**：[`note/00-INDEX.md`](./note/00-INDEX.md) indexes the
  analysis notes (01–11). [`API.md`](./API.md) is the frozen v1 contract and
  doubles as the shim's acceptance baseline.

### Working rules

- **Branch and commits**: the refactor work lives on `refactor/reasonix`.
  Commits are fine-grained, one logical change each, with a Chinese message.
  Never add a co-author or collaborator line. Do not push: the user merges.
- **The v1 contract is frozen**: a change to an HTTP status code or a field
  meaning needs the user's decision first. When the contract and the
  implementation disagree, fix the implementation. Adding a settings field or
  a key to an operations return value is not a contract change.
- **Layering**: `internal/web` is the only Deep-tutor service layer, and it
  does not exist yet — it arrives with stages ① M2/M3. Nothing below it may
  know about a user: the user difference belongs in the assembly parameters,
  not in the kernel packages.
- **Comments**: the upstream's English-only comment convention stands. A
  comment explains why, not what.
- **Disposal and caches**: regenerable output does not go to the system
  volume. `internal/base/scratchroot` (Go) and `backend/app/core/scratchroot.py`
  (Python v1) resolve where it goes: a redirected temporary directory first,
  then `$DEEPTUTOR_SCRATCH_DIR`, then the `TEMP` directory on the volume that
  holds the project, then the system temporary directory. The lock roots stay
  where the operating system keeps them: they must converge across every
  instance of the same OS user, and a root that moves with the environment
  breaks the exclusion they exist to provide.
- **Host checks**: the `sensitive:` list above is also the coverage gate's
  subject. A change under a listed path needs `review` plus
  `security_review` before the round can end, and the modification has to be
  disclosed in the root [`NOTICE`](./NOTICE).

### Multi-user kernel changes

One process serves more than one user. The kernel still has no notion of a
user: what it takes is a **storage binding** — `config.Roots`, or the `Home`
that names one — and every path a session touches resolves against it.

- **Binding, not identity.** A kernel package receives `Roots`/`Home`, never a
  user id. The user difference belongs to the assembly layer.
- **The binding travels with the session.** `control.Options.Roots` carries it,
  and a rebuild hands the replacement the binding of the controller it replaces.
- **A lock belongs to the file it guards.** Writing one home's configuration
  locks that home's file; locking another home's file lets two writers race.
- **A file's shape follows the binding.** A user file is rewritten whole, a
  project file is written incrementally. Deciding that by the process rather
  than the binding writes a user file in the project shape and drops the
  entries it never declared.
- **A record belongs to the home it was taken from.** The last-known-good
  configuration snapshot is counted and written per home, so one user's
  assembly does not consume another's recovery point.
- **The credential protections are the process's, not a user's.** The layers
  are read from one place, so one build decides them for every build: a build
  serving one user of many takes them from the global configuration, and a
  host may state the floor outright.
- **Global assets stay global.** MCP servers, skills and plugin packages are
  installed by an administrator and used by everyone: their declarations and
  their enabled state are process-wide, while a session's own settings follow
  the session's binding. The two bindings are not interchangeable.
- **A decision that arrives over the network is bounded; one that arrives at a
  terminal is not.** The wait for an approval is the host's to set: a browser
  that closes takes the answer with it, and a session waiting for good holds
  its controller for good. A person at a prompt is waited for as long as they
  are willing.
- **A session reads the configuration of the project it is in.** The project
  layer resolves against the session's workspace, never the process working
  directory: the assembly already reads that file, so a runtime read from
  elsewhere would answer from another project's configuration.

### What stays upstream's

The absorbed code keeps its copyright headers, its licence and its product
name where the name is part of a protocol, a path or an environment
variable: the workspace directory `.reasonix/`, the ACP vendor method
`_reasonix.io/`, the runtime configuration file `reasonix/config.toml`, and
the git branch prefix `reasonix/delivery-*`. Renaming them mechanically would
change what other programs can find, so each one is renamed on purpose, with
the migration that goes with it.

The environment variables are the case that has been migrated. Each one has
two spellings — `DEEPTUTOR_HOME` and the `REASONIX_HOME` it carried before the
rename — and the product reads the current one first and falls back to the
old one. `internal/base/envvar` is where that rule lives; a new variable goes
through it rather than through `os.Getenv`. The variables the product
**injects** into a plugin or a hook are set under both spellings, because the
program on the other end was written against one of them.

### A test states its own location

The two-spelling rule has a consequence for the test suite: the current
spelling wins, so a machine that sets `DEEPTUTOR_HOME` decides what a test
that set only `REASONIX_HOME` actually reads. The test believes it moved the
home; the code follows the machine.

That is how a test suite ends up writing into a real user's `config.toml` — a
`[storage]` pointer written by a storage test left the user's state root aimed
at a deleted temporary directory.

- A package whose tests touch a home root installs the isolation guard.
  `internal/base/testenv` clears both spellings of the home variables and
  redirects `HOME`, `USERPROFILE` and the OS application-data directories into
  a disposable directory under the scratch area.
- A package that names one of those variables without the guard fails
  `TestPackagesNamingAGuardedVariableInstallTheGuard`.
- A test that hands a location to a **child process** cannot pass it by
  inheriting an environment variable: the child's own guard clears it.
- The location travels through a variable of the test's own —
  `REASONIX_PERMISSION_HOME` in `internal/assembly/boot`,
  `REASONIX_TEST_FATAL_CHILD_HOME` in `cmd/deep-tutor` — and the child states
  it again for itself.

### Where the name is a contract, not a label

A display name is a label: it is written for a person, and it changes when the
product changes. A contract name is what another program matches on, and it
changes only with the migration that goes with it. The two are told apart by
one question — *is this string read by a machine that was written against a
particular value?* — and the answer decides.

The contracts that are already spoken for:

- **Install identity.** `electron-builder`'s `productName` and `packageName`,
  the `reasonix-studio-host` binary, the `io.reasonix.studio.*` profile
  directories, the macOS bundle `Reasonix Studio.app` and the executable
  inside it, and the install layout's own names (`reasonix-cli`,
  `reasonix-desktop`, `reasonix-update-helper`, `reasonix-launcher`).
- **The desktop profile.** `app.setName("Reasonix Studio")` decides Electron's
  `userData` directory and the single-instance lock. A different name is a
  different profile: the settings, credentials and session history stay behind
  in a directory nothing opens any more.
- **The release channel.** The CLI's self-upgrade reads the upstream release
  gateway, whose assets are published as `reasonix-<os>-<arch>` and whose
  archives ship a `reasonix` binary; the npm packages are `reasonix` and
  `@reasonix/cli-*`; the Homebrew cask is `reasonix`. The channel decides the
  name.
- **Persisted derivations.** The machine session ID and the legacy fact ID are
  HMAC/SHA-256 digests over a domain separator. Changing the separator changes
  every ID the product has already handed out, and the old ones stop resolving.
- **Written markers.** The release scripts write markers into the upstream
  repository's README and into GitHub issues, and find them again by the same
  string.

Where a name is matched rather than merely displayed, the code accepts **both
spellings** — the old one and the new one. The managed-install detection, the
pasted-command parser, the plan route marker, the remote kernel probe and its
process match, the update transaction whitelist and the handoff staging
directory all work that way. Accepting only the new name takes capability away
from every existing install; accepting only the old one breaks the build that
is being made.
