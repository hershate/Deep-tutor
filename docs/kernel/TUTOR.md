---
owner: @SivanCola
backup: @esengine
status: active
reviewed: 2026-10-15
---

# Tutor

## Purpose

The tutor turns material you choose and a learning goal into a staged study
plan, then keeps the studying going: scheduled units, quizzes with a
wrong-answer deck, spaced review, notes, and a searchable knowledge base.

It is the pane every window opens on, not a page beside the agent: Studio
and `reasonix web` share one window — rail, session column, pane strip —
with the tutor pinned first. It is an HTTP namespace (`/tutor/*`) on the
same loopback server every frontend uses; there is no separate tutor web
application:

- One SPA is the whole interface. The agent chat — Reasonix's full surface —
  is a pane in the same window: the header's chat button (聊天) opens or
  focuses a conversation, and the rail's or the palette's entry brings the
  study pane back.
- The study pane's pages — goal through usage — and its plans sit in the
  session column beside chat sessions, one column for both halves of the
  window. The pane's header is the window's own chrome row, crumb and icon
  buttons included.
- An address can name either pane once: `#tutor` opens the study pane, `#chat`
  opens the chat pane last active; in the terminal, `/tutor` prints where the
  surface lives.
- The only other HTML the namespace emits are download artifacts (exports)
  and the read-only public share page.

Three standing rules:

1. The model is the chat model configured for new conversations, resolved per
   run, so a settings change applies to the next generation.
2. Plan, quiz, note, and knowledge-base data persist as local JSON files under
   the state root; nothing else leaves the machine.
3. A missing or failing provider never loses data: generation refuses with a
   refusal code, and deterministic paths (plan rendering, fallback slides)
   keep working.

## Where the data lives

| Data | Path under `<state root>/tutor/` |
| --- | --- |
| Generation sessions | `sessions/<id>/` — `session.json`, `materials/`, `originals/`, `parsed/` |
| Plans | `plans/index.json`; `plans/<id>/` — `plan.json`, `versions/vNNN.json`, `quizzes.json`, `attempts.json`, `logs.json`, `progress.json` |
| Notes | `notes/<id>.json` |
| Knowledge base | `kb/` |
| Share links | `shares/<token>.json` |
| Daily usage ledger | `usage/<date>.json`, one file per local day |

## Making a plan

1. `POST /tutor/sessions` creates a session for one goal.
2. Add material: `POST /tutor/sessions/{id}/materials/text|url|upload`.
   Uploads accept the parsers the kernel carries (PDF, DOCX, PPTX, EPUB,
   Markdown, plain text). A PDF without an embedded text layer refuses with
   `tutor.no_raw_text`; printing pages to text first is the supported path.
3. `POST /tutor/sessions/{id}/materials/{mid}/confirm` confirms the
   material, taking an optional `parse_mode` — `full`, `fast`, or `none` to
   keep the original unparsed — that parses it in the same step;
   `.../parse` extracts the text on demand.
4. `POST /tutor/sessions/{id}/generate` streams the pipeline as SSE events:
   `tool_start`/`tool_end` per tool, `unit_start`/`unit_done`/`plan_chunk` as
   the plan is written, `doc` for a web-search hit, `warn`, `clarify_needed`,
   `budget_exceeded`, `error`, and `done`.
5. `clarify_needed` pauses the run for answers. `POST
   /tutor/sessions/{id}/clarify` sends them and the paused run resumes with
   the answers applied; a session with no pending clarification refuses
   `tutor.no_clarification`.
6. The pipeline runs eight tools — parse material, understand intent, build
   knowledge map, design stages, search resources and schedule plan (those
   two concurrently), validate, export. A failed validation check reruns the
   tool that owns it, at most two feedback rounds.
7. A saved plan is versioned. `POST /tutor/plans/{pid}/regenerate` makes the
   next version; the quota is two regenerations per plan, then
   `tutor.regen_quota` refuses.
8. `POST /tutor/sessions/{id}/cancel` cancels a running generation;
   `.../retry` reruns a failed or interrupted one from its saved run state.

Web search during generation is optional and rides Reasonix's unified
scheme: the `agent.web_search_model` selection in Model preferences, its
credentials from the shared credential store. The tutor adds SSRF-guarded
verification and a mandatory AI relevance gate; the search spend is
metered into the daily budget.

## Studying

| Surface | Route prefix | What it holds |
| --- | --- | --- |
| Plan detail | `GET /tutor/plans/{pid}` | Outline, schedule, resources, validation report, materials, quizzes |
| Versions | `GET /tutor/plans/{pid}/versions[/{v}]` | Every saved version, readable side by side |
| Quizzes | `POST /tutor/plans/{pid}/quiz`, `POST /tutor/quizzes/{qid}/submit` | Fresh quiz sets; grading records every attempt |
| Wrong-answer deck | `GET /tutor/plans/{pid}/quiz/wrong`, `GET /tutor/quizzes/wrong-queue`, `.../wrong-overview` | Questions answered wrong, plan by plan |
| Progress | `POST /tutor/progress` (read per plan at `GET /tutor/plans/{pid}/progress`), `GET+POST /tutor/progress/logs` | Unit completions and free-form study logs |
| Review | `GET /tutor/progress/review/queue`, `.../due` | Spaced-review queue with per-unit mastery |
| Statistics | `GET /tutor/progress/stats/summary|by-plan|calendar` | Study-time rollups |
| Units | per plan | Self-study units grouped from the plan's knowledge map; `materials-index.md` renders the roadmap |

Spaced review schedules each unit by its mastery: below 0.5 reviews in one
day, below 0.7 in three, below 0.85 in seven, otherwise fourteen. Mastery
rises with completed units and quiz scores.

## Notes

| Action | Route | Limits |
| --- | --- | --- |
| Create, update, read, delete | `POST|PUT|GET|DELETE /tutor/notes[/{nid}]` | At most 500 notes, 20,000 characters each |
| Import | `POST /tutor/notes/import` | Markdown files or a zip of them; existing names are skipped, the quota is respected, the response reports `imported`/`skipped`/`quota_full` |
| Export | `GET /tutor/notes/export.zip` | Every note as Markdown in one zip |

## Knowledge base

| Action | Route | Limits |
| --- | --- | --- |
| Add documents | `POST /tutor/kb/documents/text|url|upload` | At most 200 documents; identical content is deduplicated and never counts twice against the quota; text and URL land parsed, uploads wait for confirmation |
| Confirm, parse, rename, read | `POST /tutor/kb/documents/{id}/confirm`, `POST .../parse`, `PUT+GET /tutor/kb/documents/{id}` | One parse per document at a time (`tutor.kb_parsing` while it runs) |
| Chat | `POST /tutor/kb/chat` | Answers cite `used_doc_ids` drawn only from documents that exist and were injected |
| History | `GET+DELETE /tutor/kb/chats[/{id}]` | Recorded question/answer pairs with their citations |
| Export | `GET /tutor/kb/export` | The library as a zip |
| Generate from docs | `POST /tutor/kb/generate` | Starts a plan session whose material is the selected documents' parsed text |

## Search

`GET /tutor/search?q=` (`&limit=`) searches plans, knowledge-map nodes,
notes, and knowledge-base text, and returns snippets with per-hit kinds. It
is the same index the Studio search view reads.

## Exports

| Format | Route | Behavior |
| --- | --- | --- |
| Markdown | `GET /tutor/plans/{pid}/export.md` | Rendered plan document |
| Calendar | `GET /tutor/plans/{pid}/export.ics` | The plan's schedule as events; a plan without one refuses `tutor.no_schedule` |
| PDF | `GET /tutor/plans/{pid}/export.pdf` | Degraded by design: the Markdown body with an `X-Tutor-Export-Warning` header, because printing is a host capability |
| Anki deck | `GET /tutor/plans/{pid}/export.apkg[?wrong_only=1]` | Schema-11 `.apkg`; stable guids make re-import update in place; `wrong_only` exports only questions answered wrong |
| Slides | `GET /tutor/plans/{pid}/export.html`, `GET /tutor/plans/{pid}/units/{uid}/export.html` | Standalone HTML presentation; the LLM spec step falls back to a deterministic deck when no provider is configured or repairs run out |

Slide and export content is sanitized server-side: script and iframe blocks
are dropped, `javascript:`/`vbscript:` URLs are removed after entity decoding,
and class or theme values collapse to single safe tokens.

## Sharing a plan

| Action | Route | Behavior |
| --- | --- | --- |
| Share | `POST /tutor/plans/{id}/share` | Idempotent; returns the same token for the same plan |
| Public page | `GET /tutor/shared/{token}` | Server-rendered read-only plan; answers and explanations are never included, links are sanitized |
| Status, list, revoke | `GET /tutor/plans/{id}/share`, `GET /tutor/shares`, `DELETE /tutor/plans/{id}/share` | Revoking invalidates the token; the page then answers 404 |

## Backup

| Action | Route | Behavior |
| --- | --- | --- |
| Export | `GET /tutor/backup/export` | One JSON file holding the whole tutor store, answers included |
| Preview | `POST /tutor/backup/preview` | Counts what an import would add; writes nothing |
| Import | `POST /tutor/backup/import` | Appends with fresh IDs; knowledge-base documents deduplicate by content; the note quota applies |

Preview and import take a base64 JSON envelope (`content_base64`), capped at
256 MB decoded.

## Budget and usage

1. Every LLM step records its metered usage in the day's ledger
   (`usage/<date>.json`); `GET /tutor/usage` returns the daily rollup.
2. The day's recorded cost is capped by a budget in rate-card currency:
   `TUTOR_DAILY_BUDGET` (default 10, at or below 0 disables the cap). Past the
   cap, LLM steps refuse `tutor.budget_exceeded` (HTTP 402) until the next
   local day.
3. Slides rendered from the deterministic fallback without a provider cost
   nothing and are not counted as degraded.

## Refusals

| Code | HTTP | Meaning |
| --- | --- | --- |
| `tutor.not_found` | 404 | No such plan, session, note, document, or unit |
| `tutor.busy` | 409 | A generation is already running for the session |
| `tutor.no_clarification` | 409 | No pending clarification to answer |
| `tutor.not_retryable` | 409 | The session is not in a retryable state |
| `tutor.no_active_run` | 409 | No running generation to cancel |
| `tutor.bad_scope` | 400 | A scope parameter did not parse |
| `tutor.regen_quota` | 429 | Regeneration quota exhausted |
| `tutor.no_raw_text` | 409 | The material carries no extractable text |
| `tutor.unsupported_format` | 400 | Upload format outside the parsers |
| `tutor.no_schedule` | 400 | Export asked for a schedule the plan does not have |
| `tutor.no_quizzes` | 400 | Export asked for quizzes the plan does not have |
| `tutor.no_wrong_quizzes` | 400 | Nothing answered wrong yet |
| `tutor.budget_exceeded` | 402 | Daily budget exhausted |
| `tutor.no_llm` | 503 | No LLM provider is configured |
| `tutor.upstream_timeout` | 504 | The model call timed out |
| `tutor.bad_gateway` | 502 | The model call failed |
| `tutor.kb_quota` | 429 | Knowledge-base document quota exceeded |
| `tutor.kb_parsing` | 409 | The document is being parsed |
| `tutor.kb_no_original` | 400 | The document's original bytes are gone |
| `tutor.kb_bad_input` | 400 | Empty, oversized, or wrong-kind knowledge-base input |
| `tutor.kb_no_docs` | 400 | The knowledge base has no usable documents yet |
| `tutor.kb_export_empty` | 400 | A library export selected no documents (empty library or filter) |
| `tutor.disabled` | 503 | The tutor store is unavailable |
