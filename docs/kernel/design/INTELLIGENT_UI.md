---
owner: @esengine
backup: @SivanCola
status: active
reviewed: 2026-10-12
---

# Intelligent UI implementation

[RFC #12584](https://github.com/esengine/DeepSeek-Reasonix/issues/12584) defines the staged design. The desktop implementation covers recovery and measurement (P0), followed by the hidden `render_surface` tool, typed fields and choice cards (P1). TUI changes remain deferred by the owner.

## Recovery boundary

The extension UI hub retains at most 64 current publications, keyed by plugin and surface identity. A republish replaces that identity. Snapshots are isolated copies of the already validated and credential-redacted payload. Notifications are transient and are never replayed.

`GET /extensions/surfaces` reads the controller snapshot and the session's existing wire log. A window refresh in the same running generation restores the current payload.

A retired generation, crashed plugin, or restarted host returns identity only with `surface.expired`; the desktop removes old controls and displays the reason. It does not resubmit a form or open a question.

The wire log records only plugin, surface, session, generation and kind. It never records the publication body, field defaults or user answers. No new session schema or store is introduced.

A history predating this implementation has no publication identities to recover. A truncated or unreadable recorded history returns `surface.replay_incomplete`, rather than claiming that no surfaces existed.

A recovery snapshot does not owe entrance motion. Live publications received during a snapshot read take precedence for their identity; results from an obsolete read or unmounted pane are discarded.

This endpoint does not expand execution or approval authority.

## Desktop presentation

Recovered publications use the existing Studio `.flow` presentation. Expiry is a plain `restore-notice` status row. It has no tool gutter, vertical line, card frame or visible internal error code; `data-code` retains the typed identity for inspection and tests.

## Measurement definitions

All counters are local to one mounted desktop pane, exposed on its `PaneReport.interactions`. They are reset when that pane is remounted, are not uploaded, and contain no question, option or answer strings. Local deduplication retains prompt identities only for that pane's lifetime.

| Counter | Counts | Anchor and population | Excludes |
| --- | --- | --- | --- |
| `requests` | First observed structured ask request per prompt id | First request event in this mounted pane; model and external structured questions visible to this pane | Duplicate delivery and questions rebuilt from stored history; this is not the number of provider tool calls |
| `questions` | Question entries in those first requests | The same request population | Duplicate delivery and stored history |
| `options` | Declared option entries in those questions | The same request population | The frontend's added free-text affordance and answer content |
| `answers` | First successful local answer submission per prompt id | Acknowledged submission in this pane | Failed submissions, retries after the same acknowledgment, other devices and inferred outcomes |
| `customAnswers` | Those submissions with a selection absent from the corresponding declared option labels | The same acknowledged-answer population; exact option membership | Answer text, empty skips, and any claim that the model asked the same question again |

These counters measure desktop interaction exposure and local answers. They do not establish task success, user friction improvement, or an account-wide rate. A later fixed-task trajectory experiment must compare user turns to the same completed outcome before reporting a benefit from P1.

Surface requests and acknowledged submissions contribute to `requests` and `answers`. The other counters retain their ask-question and option-label definitions; they do not count individual surface fields or inspect typed input values.

## P1 fields and choices

The follow-up branch introduces `surfacespec` version 1. Its node is the existing `ExtensionViewNode` contract, with field metadata added. The desktop mirrors that same tree and the field types under wire parity.

The legacy plugin publisher keeps its existing catalogue and action path. Model-authored inputs use the same node contract, with submissions handled by the controller's ask path.

The model catalogue accepts text, plain-text markdown, semantic containers, key/value rows, meters, pips, dividers, fields and choice cards. It rejects executable components, host approval operations and unknown properties. P2 also accepts opaque input buttons.

Fields carry string, number, boolean, choice or multichoice values; choices use stable ids with optional description and preview text.

The parser caps a spec at 64 KiB, 200 nodes, eight tree levels, 16 fields and 16 options per field. Duplicate JSON keys and field ids are refused. Defaults and submitted JSON values are checked against declared types, bounds and option membership, with typed `surface.*` refusals.

Hosts explicitly enable `Options.NativeSurfaces` for the desktop and HTTP native renderer. Other entry points do not register the tool. It is hidden from provider schemas and discovered through `use_capability`; its schema is delivered in the turn tail.

Forms without buttons publish a single ask carrying the validated spec and the host-owned tool call id. A desktop submits typed JSON in `AskAnswer.Value`. Validation and removal from the pending queue happen under the same lock; a refusal leaves the prompt pending.

Empty answer lists retain the existing explicit skip semantics.

Reloaded pending requests replace the matching expired history marker and retain local drafts. Submitted results restore settled inputs from the existing call/result record. Missing or invalid recorded answers show `surface.expired`; no historical card can submit.

Replaying or mounting a restored row owes no entrance motion.

The native renderer uses a restricted A2UI catalogue and styled Mantine controls. Markdown and previews are plain text. Live and historical specs pass credential redaction. Surface input cannot grant permissions or settle approval prompts.

## P2 drafts and previews

`editable_text` uses the existing string field contract with a multiline native textarea. Submitted drafts preserve whitespace and newlines through the controller's typed answer, provider tool result and canonical session record. Failed submissions retain the local draft; settled replay is read-only.

`diff_preview` carries bounded `before` and `after` strings. The native renderer preserves literal text, including apparent diff headers and markup, and shows shared leading/trailing lines around the replaced middle.

Previews do not interpret paths or expose file operations. Both sides pass the same credential redaction as other display text.

The draft and preview vocabulary is mirrored under wire parity. Existing form controls and Studio colours render both components without a gutter or vertical rail. Display-only previews return immediately; draft forms retain the existing blocking ask semantics.

## P2 nonblocking actions

Trees containing `button` nodes publish without opening an ask. Buttons carry only an opaque action id and label. The controller receives the action id and typed field values as a `surface_action` JSON tail note. Approval, permission, shell and invocation operations are absent from this schema.

The desktop chooses queue or steer delivery using host controls. The same durable inbox owns admission, pause, retry, cancellation and withdrawal for both composer input and surface actions. Values remain literal data: references and command syntax inside fields are not interpreted.

`GET /surfaces/{id}/action` reads current replay and control availability. `POST /surfaces/action` validates the session, call, canonical surface hash, action id, request id and field values before queueing. Nonblocking actions require an interactive native publisher; headless runs get the declared unresolved reply.

A controller keeps at most 64 live publication identities, without retaining a second copy of the spec. Eligibility belongs to the current executor session. Old unanswered publications expire after a rebuild or resume; a replay never publishes them again.

Local-only action receipts use the existing session message representation. They stay out of provider requests and conversation rows. Pending envelopes also reconstruct accepted values if a receipt snapshot was unavailable. Inbox tombstones distinguish withdrawn input from an already accepted action.

A durable enqueue is the input acknowledgment. Receipt snapshots follow the same best-effort save rule as admitted user messages. Snapshot failures do not falsely report that the durable input was refused. No new store or persistent schema field is introduced.

Accepted cards restore values readonly. A live withdrawn card can be edited again; a cold withdrawn record remains readonly. Withdrawal is offered only while the inbox says it can succeed. Generic queued-text editing refuses typed action envelopes; withdraw and edit through the original card instead.

Request identities survive uncertain delivery and unchanged retries. Reusing an identity with different values is refused. Native action cards remain outside execution-work disclosures, and remounting or fetching replay state owes no entrance motion.

Steer payloads retain structured content for the model and separate display text for the user. Pending replay, queue cancellation and session rotation preserve their existing ownership rules. Existing asks, plugin actions and approval pipelines remain separate typed channels.

The ask-based measurement counters exclude nonblocking publications and clicks. No improvement in user turns has been measured.

The [fixed-task evaluation protocol](../../benchmarks/intelligent-ui/README.md) defines paired tasks, outcome checks and content-free observer records. Collection remains outstanding; TUI work remains deferred.

## A2UI renderer integration

The desktop uses pinned `@a2ui/react` and `@a2ui/web_core` 0.13.0 through their v0.9 entry points. A local adapter converts existing surface nodes to A2UI messages. The model-facing spec, hidden discovery and controller answer channel keep their existing contracts.

The renderer receives only locally generated messages and the Studio component catalogue. It has no registered functions, media, URLs or executable components. Plugin buttons retain the existing extension callback; model buttons carry opaque action ids through the controller's native input path.

A2UI owns the transient tree and draft data bindings. Field keys map to positional binding ids, so previously accepted prototype-like names never become library paths. Answers map back to original keys and preserve string bytes; numeric inputs convert according to the declared field type.

Settled results replace the draft with recorded answers. A dismissed prompt clears unsent input. Equivalent pending replay retains the same model; unmount disposes it. Native forms and plugin views share the catalogue and tree renderer.

The renderer loads on demand. Failed chunk loading stays within the surface and offers a local retry; it does not submit an answer or take down the conversation.

Mantine 9.7.2 provides styled Card, Radio.Card, Checkbox.Card, form inputs, buttons and feedback. The desktop uses the official card patterns and component defaults; A2UI handles tree composition and binding.

A scoped provider follows the existing desktop appearance without creating a second persisted theme or modifying the global document. Font and surface inks use Studio tokens; library spacing, radii and control styling remain intact. The layered Mantine stylesheet loads with the lazy renderer.

## Review and security review

The input boundary review covers the pending-ask map, legacy answer wrappers, HTTP attribution, cancel/timeout races and replay identities. Every answer entry reaches the same atomic schema check. Ordinary questions still use selections; typed values on those questions are refused.

The security review covers closed component kinds, bounded decoding, duplicate keys, field ids, required values and choice membership. Display strings and string defaults are redacted before native rendering. Approval policy, permission grants, device authentication and session storage ownership are unchanged.

Real boot effect tests prove hidden discovery, typed refusal/retry, pending replay and persisted resume. Adjacent tests cover ordinary asks, extension actions, recovery, frontend acknowledgment, local drafts and the proxy decision barrier. Full-suite baseline failures are reported separately from these targeted checks.


## Rich desktop workspaces

The product scope includes all three task workspaces below, following the owner's
request to deliver rich task interfaces. This extends the initial form-oriented
scope without waiting for the input-count study. It does not establish a measured
benefit, implement arbitrary generated pages, or add a TUI renderer.

- `map`: linked place markers and an ordered itinerary, group filtering, focus,
  add/remove and reorder. Answers contain option ids in visit order. Groups are
  authored labels; dashed connections show visit order, not road navigation.
- `comparison`: searchable and grouped candidate cards, a property comparison
  table with differing-property emphasis, optional prices, sorting and a retained
  shortlist. Answers use the
  existing choice/multichoice contract. Optional images reference existing workspace-relative raster files.
- `dashboard`: bounded category/series data, metric and range selection, summaries,
  area/bar charts and a detail table. Exploration is local and sends no answer.
  Successful display-only dashboards remain visible in the conversation.

Mantine supplies controls and charts; MapLibre with react-map-gl supplies the map.
These renderers load lazily. MapLibre's worker is bundled through Vite's worker
URL import.

The host fixes the OpenStreetMap tile URL and attribution; the model
cannot supply resource URLs or executable markup. A tile failure leaves the list
usable and offers a retry. Network availability still governs the basemap.

The component container determines card columns. Workspaces use one heading and
content hierarchy, borderless map rows and a shared metric strip rather than
repeated framed panels. Expansion preserves the component and selection state;
settled replay disables answer changes while retaining local exploration.

Run `pnpm exec vite --host 127.0.0.1` from `desktop/frontend-next`, then open
`/perf/rich-ui.html?scenario=map&theme=light`. The `comparison` and `dashboard`
scenarios and `theme=dark` use the production renderer with clearly labelled
sample data and a local acknowledgment.

They are development acceptance fixtures,
not provider evaluations.

Validate both conversation-sized and expanded layouts.


### Workspace media

Map and comparison options accept `image: {path, alt, source?}`. Paths are bounded,
workspace-relative raster references; URLs, traversal, absolute paths, SVG and
query strings are refused by both contract validators. The hidden schema names
this vocabulary without changing the provider prefix.

The owning transcript passes its image port to both blocking and nonblocking
surfaces. Images use the existing workspace image endpoint, including its rooted
file access, size cap and content restrictions. No new frontend file or network endpoint
is introduced.

Descriptions and source links are redacted with other display text.

Product images use a fixed aspect ratio with contain fit; the focused place uses
cover fit. Failed images retain the card and its selection. Changing workspace
endpoints resets a failed image. Missing images keep the text layout usable.


### Acquiring real photos

After researching a product or place through the configured search capability,
`web_fetch` exposes up to 32 image candidates from static page metadata (`og:image`,
`twitter:image`) and image tags (`src` and lazy `data-src`, independently).

Candidate URLs
resolve against the first declared HTTP(S) base URL, or the final page URL.

This is page extraction, not a separate
image-search service; it does not execute scripts or bypass login or anti-hotlinking.

The hidden `import_web_image` tool accepts the researched page `url`, selected
`image_url`, a new relative `path`, and `alt`.

It fetches the page again and requires
the chosen URL to occur among its candidates. Both requests reuse the existing
proxy-aware SSRF-guarded transport.

Page bodies are limited to 1 MiB; image bodies
to 8 MiB and decoded dimensions to 40 million pixels. PNG, JPEG, GIF and WebP
are decoded and must match the destination extension.

AVIF can still be displayed
from an existing workspace file but is not imported by this decoder.

Import is a declared write with named-path confinement, session-store protection,
rooted file access, exclusive creation and a recovery receipt. Existing files are
not overwritten.

It retains the normal approval and post-write verification gates.

The result supplies the renderable image object, final source and image URLs,
dimensions and SHA-256; the tool trajectory preserves this retrieval evidence.

Cards display the optional HTTP(S) source-page link without loading remote media
in the renderer. Source links are attribution supplied with the spec, not a
host-certified identity or licensing badge.

Page membership alone cannot prove
that a photo depicts the requested entity: the agent must inspect the candidates
and respect the source's reuse terms.

Missing, unsupported or failed acquisition
leaves text cards usable; generated fixture illustrations must never substitute
for photos of real entities.
