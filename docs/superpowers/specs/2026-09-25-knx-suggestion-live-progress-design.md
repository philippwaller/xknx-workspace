# Live process steps for KNX suggestions

- **Status:** Design for review, 2026-09-25
- **Repositories:** `home-assistant-core` and `knx-frontend`
- **Extends:** [KNX project suggestions design](2026-09-24-knx-project-suggestions-design.md)

## Purpose

While a KNX suggestion run is active, the administrator should understand which ETS part is being processed and what the system is doing now. The view should resemble a compact, animated activity list. Every displayed step must correspond to work the system actually performs. It must not present invented AI thoughts, partial suggestions, or a fabricated percentage for an AI request.

The progress display remains inside the existing Suggestions page. It works after closing and reopening the page and does not depend on leaving the start dialog open. It also remains usable for later suggestion sources that do not report detailed phases.

## Existing flow and chosen approach

Core already stores a suggestion run and publishes complete saved snapshots through `knx/suggestions/subscribe`. The frontend already subscribes, shows completed/total parts, the current ETS scope, and progress bars. Core currently puts free-form status suffixes into `current_scope`; the AI Task call itself returns a completed result and exposes no generic stream of model output or reasoning.

Extend the existing run snapshot with optional structured progress fields. Core publishes a new snapshot only when the actual phase changes or a part is checkpointed. The frontend animates the active phase locally with Home Assistant components. There is no new streaming endpoint, polling loop, heartbeat, provider-specific response stream, or event log that grows with project size.

## Progress contract and Core transitions

Add optional `current_phase` to the persisted run and WebSocket type. Its values are `preparing`, `generating`, `validating`, `saving`, `capacity_wait`, and `retry_wait`. Keep `current_scope` as the ETS room or part path only; remove English status suffixes from that field. Add optional `retry_attempt` and `retry_limit` only while in `retry_wait`. Old saved runs without the new fields remain readable and use the existing basic progress display.

For each analysis part, Core reports these transitions:

1. `preparing` while it checks the ETS project and selected context.
2. `generating` immediately before awaiting `ai_task.async_generate_data`.
3. `validating` after AI Task has returned, before interpreting and checking its proposed devices and entities. This requires an internal progress boundary in KNX generation; it does not change the generic AI Task interface.
4. `saving` after validation, while the accepted result is reconciled and durably checkpointed.
5. `capacity_wait` between parts when Core intentionally pauses before the next request.

On a retriable rate limit, Core reports `retry_wait` with the next retry number and the configured maximum, then returns to `preparing` and `generating` for the next attempt. A final provider error stops the run and remains in its existing error list. Completion, stop, Labs disablement, and restart interruption end the active animation; a stale saved phase must never make a non-running run look active. An interrupted run retains its completed parts and proposals. Review edits and generation phase changes do not alter each other's revision semantics.

Each transition is persisted before it is published through the existing subscription. Updates are limited to phase changes; the server sends no per-second ticks. This bounds full-snapshot storage and WebSocket traffic for large ETS projects. The UI can show an indeterminate activity indicator while `generating`, but must not infer a time percentage or token count from its duration.

## Frontend presentation

The existing expandable run panel gains a compact **Aktivität** list above the saved-proposal review. It shows the full ETS scope path, completed/total parts, and saved-proposal count. For the current part, completed phases have a checkmark, the active phase has `ha-spinner`, and pending phases remain plain. The labels describe observable work, for example **ETS-Kontext prüfen**, **AI Task erstellt Vorschläge**, **Antwort prüfen**, and **Vorschläge speichern**. Waiting states replace the active generating step with a clear reason; retry wait includes the attempt count.

Use the existing `ha-expansion-panel` and `ha-progress-bar`, plus `ha-spinner` for the active step. The overall bar remains based on completed parts. Phase changes are announced through one polite live region; the spinner itself is decorative. Localized text is used throughout. Under reduced-motion preferences, the active label and step marker still communicate state without motion. The list shows the current part, not an ever-growing transcript of all parts.

Errors are summarized by count in the run panel, with details expandable on request. The full error strings remain available. A run with no detailed phase, including a future provider that does not report one, keeps the existing completed/total parts display. Reconnecting to the WebSocket subscription restores the latest saved step. A stopped, completed, or interrupted run displays its terminal status and no active spinner.

## Failure behavior and verification

If the browser disconnects, Core continues the run and the resubscribed snapshot restores its latest checkpoint. If the AI Task fails or exhausts retries, the run stops, its error remains available, and the activity list does not imply that the request is still running. Invalid AI proposals still follow the existing validation and rejection path; progress reporting never makes unvalidated output reviewable.

Focused Core tests cover ordered phase transitions, retry and wait labels, persisted/reloaded progress, and terminal interruption. Frontend tests cover the active phase, fallback for old runs, retry/error summary, and reconnection to a saved snapshot. Verify localization, mobile layout, keyboard expansion, and reduced-motion rendering. No separate model-streaming test is needed because this design does not stream model output.

## Acceptance examples

- During a long AI request, the user sees the ETS path and **AI Task erstellt Vorschläge** with an activity indicator; the completed-part count does not advance prematurely.
- When the response arrives, the activity changes to validation before any result appears in the review list. Accepted proposals appear only after the save checkpoint.
- During a rate-limit pause, the reason and retry attempt are visible. The run resumes automatically according to its existing retry policy.
- Closing and reopening the page restores the latest step. Restarting Home Assistant shows the run as interrupted with no active animation.
- A historical or future-provider run without `current_phase` remains readable, and a large project does not create an unbounded activity history.
