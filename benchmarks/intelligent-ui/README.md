# Desktop surface evaluation

This protocol supplies the fixed-task trajectory comparison required by
[RFC #12584](https://github.com/esengine/DeepSeek-Reasonix/issues/12584).
There are no measured results yet. Component tests and scripted providers prove
delivery, validation and replay; they do not measure a model's choice of interface
or a person's effort.

## Paired runs

Run each task twice in fresh desktop sessions: ordinary text/ask interaction and
native surfaces. Use the same model, sampling settings, initial workspace,
permission mode and task prompt. Record commit, model, configuration and the
native-surface capability setting. Alternate condition order across repetitions.
Keep the hidden tool out of the provider prefix in both conditions; only its
availability through capability discovery differs.

The baseline may batch questions or accept a single text reply. Never force it to
ask one question per field. Do not tell either model how many turns to use or
which component to render. If the surface-enabled model chooses text, retain
that run in the surface condition. Interface choice is part of the experiment.

The operator knows the private values below but does not volunteer them before
the model requests the corresponding information. Supply all requested values
together, regardless of presentation. Allow inspection and local editing before
submission. Save the trajectory and an observer ledger locally; raw transcripts
and draft content must not enter the aggregate report.

## Fixed tasks

| Task | Initial prompt, identical in both conditions | Private operator input | Completion criterion |
| --- | --- | --- | --- |
| `release-brief` | Prepare a release brief. Ask me for the audience, release date and whether this is a staged rollout before writing it. | audience: existing desktop users; date: 2026-11-03; staged rollout: yes | Final brief contains all three values and adds no release commitment. |
| `storage-choice` | Help me choose storage for a small desktop app. Present reasonable choices, ask me to choose and ask whether offline operation is required. Then summarize the decision. | storage: SQLite; offline: yes | Final decision states SQLite and offline operation, with no installation or file changes. |
| `draft-revision` | Draft a two-line release announcement and let me revise it before returning the final version verbatim. | Replace with the exact string `  Desktop release ready.\nKeep existing settings.\n` (decode the escapes). | Final returned string has exactly the specified leading spaces and newlines. Formatting wrappers are excluded from the comparison. |
| `preview-decision` | Show a plain-text before/after preview changing retry count from 2 to 3. Ask whether to keep that change and whether to include a release note. Return a decision summary; do not change files. | keep change: yes; release note: no | Preview shows 2 to 3; final summary preserves both decisions; workspace is unchanged. |

Freeze this task set before collecting results. Record a new protocol version if
prompts, private inputs, completion criteria or metric definitions change.
Neither the model's final claim nor a successful input acknowledgment establishes
completion. The observer checks the resulting output against the criterion.

## Metric definitions (version 1)

The anchor is acknowledgment of the initial task prompt. The observation ends
when the criterion is verified, the operator abandons the task, or the run fails.
The initial prompt is excluded from subsequent-input counts because both
conditions start from the same request.

| Metric | Count and population | Exclusions |
| --- | --- | --- |
| `acknowledged_inputs` | Distinct operator submissions acknowledged after the anchor: chat replies, ask answers, surface answers and surface actions. Includes acknowledged input later withdrawn or superseded. | Initial task prompt, local draft edits, retries with the same acknowledged request identity, automatic dispatch, replay and observer grading. This counts input exchanges, not provider rounds. |
| `submission_attempts` | Explicit operator send attempts, including rejected submissions and manual retries. | Automatic transport retries, replay, local edits and model tool calls. |
| `withdrawals` | Successful operator withdrawals of admitted input. | Failed withdrawals, automatic cancellation and replay. |
| `completed` | Runs whose output meets the fixed criterion. Denominator: every started run in each condition. | None; failures and abandoned runs remain in the denominator. |

Record these in a content-free ledger: task id, repetition, condition, observer
event ordinal, event type, opaque request identity, acknowledgment status and
completion verdict. Record the source trajectory reference and observed start
and end anchors alongside it. Distinguish manual observation from host-recorded
events. Do not copy labels, prompts, private values, drafts or answer strings into
the ledger. Identities are local correlation handles, not account identifiers.

The current pane counters and rendered trajectory export cannot reconstruct all
acknowledgments, refusals and withdrawals. Do not infer them from `ask` calls,
tool-result counts, final prose, message wording or card mounts. Until structural
acknowledgment records cover every channel, an observer ledger is required.

## Report and decision

Report the number started, completed, failed and abandoned for each condition,
then paired input-count differences for repetitions where **both** conditions
meet the same criterion. List excluded pairs and their reason beside that
comparison. Also report submission attempts and withdrawals; fewer admitted
inputs with more failures is not automatically less effort.

Keep per-task results visible. Do not combine scripted fixtures with observed
model/operator runs or describe this small task set as an account-wide result.
Report model/configuration differences and missing evidence as limitations.

The RFC's benefit remains unproven until observed trajectories show fewer input
exchanges to the same verified outcome. These results alone do not justify P3;
adaptive layout or MCP Apps needs a measured problem in addition to this result.

The owner-requested map, comparison and dashboard workspaces extend the delivery
scope independently of this study. Their scripted acceptance fixtures verify
rendering and interaction only; they do not count as observed benefit evidence.
