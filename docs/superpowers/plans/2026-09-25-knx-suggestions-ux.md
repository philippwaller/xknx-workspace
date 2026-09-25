# KNX Suggestions UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved compact KNX suggestion review interface, with expandable devices, direct editing, and a separate job menu and progress dialog.

**Architecture:** Keep the existing suggestion snapshots, revision-bound review patches, and apply preview API. The view owns the selected run, presentation state, and mutation coordinator; targeted dialogs reuse the existing editors and subscribe to durable snapshots while open. Extract only presentation logic shared by the view and job dialog; do not introduce a global store.

**Tech Stack:** Existing TypeScript, Lit 3, pinned Home Assistant components, Vitest, and the current KNX WebSocket service.

**Spec:** `docs/superpowers/specs/2026-09-25-knx-suggestions-ux-design.md` (approved 2026-09-25).

## Global Constraints

- „Keine neue Komponentenbibliothek und keine neue Legacy-`mwc-*`-Verwendung.“
- „Farben und Abstände kommen aus den Theme-Variablen und `--ha-space-*`.“
- „Keine neue globale Zustandsverwaltung.“
- „Die vorhandene Backend-Vorschau bleibt maßgeblich.“
- „Ohne Bereich ist nicht automatisch ein Fehler.“
- „Filter verändern eine vorhandene Auswahl nicht.“
- „Es gibt keine erfundene Restzeit oder künstlich steigende Prozentzahl.“
- „Prüfung bei 390 px, 768 px und 1280 px, hell/dunkel und erhöhtem Zoom; vollständiger Ablauf per Tastatur.“
- „Geschlossene Geräte laden keine vollständigen Formularbäume.“
- All product changes belong to `knx-frontend`; no Core, dependency, lockfile, pinned submodule, or compiled-asset changes are required. A persistent timestamped event log and object-level error classification are outside this plan.
- Read the child README, `.github/copilot-instructions.md`, pinned frontend `AGENTS.md`, and its components/styling/text/testing skills before editing. Reuse HA translations first; add missing KNX-specific keys to both existing `de.json` and `en.json`.
- Preserve the current branch and dirty work. The inspected frontend HEAD is `83d2b71`; six dirty files include the start-dialog output-limit work. Do not switch, stash, reset, fetch/update, or stage unrelated changes. Inspect current status again at execution time.
- At execution, use `using-git-worktrees` to determine an appropriate isolated checkout. If isolation is selected, base it on the current feature HEAD, capture the current tracked diff as a non-destructive patch, and apply it only inside the isolated checkout so the accepted baseline includes those changes. Record that inherited diff separately; it is not part of this delivery. Do not create an upstream-main checkout that loses the suggestion feature. A verified existing local-work preference takes precedence.
- Use the workspace public entry points for runtime management. Do not restart the running localhost service from a hidden shell. The planning-time `./dev status --format json` reports a reachable server but no managed tmux session and inconsistent package readiness; resolving deployment into that runtime is distinct from building and testing the UI.
- Commits are repository-local, scoped to completed task changes, and use configured signing. Do not push or create a PR without explicit authorization.

## Review Focus

1. Two runs reuse device IDs or names: choosing one must never apply the other's selection or revision (Task 1).
2. A delayed snapshot or lost connection arrives during editing: retain the draft, reject stale writes, and keep the current focus (Tasks 1 and 4).
3. An entity search matches a closed or previously hidden device: reveal the match without changing selection or permanently changing expansion state (Task 2).
4. A job partially applies a mixed set of device, area, create-entity, and update-entity actions: distinguish created resources from completed actions and retain frozen targets (Tasks 3 and 5).
5. A long translated name, 200% zoom, or reduced-motion preference: preserve actions, text, and status without overflow or animation dependence (Task 6).

## File map and boundaries

All `src/` paths below are relative to `knx-frontend/`. Planning documents remain in the workspace repository.

| File | Change and responsibility |
| --- | --- |
| `src/features/suggestions/views/knx-suggestions-view.ts` | Selected run, snapshot synchronization, banner, filters, selection bar, dialog dispatch, existing patch coordination |
| `src/features/suggestions/review-state.ts` | Keep frozen-target logic; share existing lifecycle predicates between view and job dialog |
| `src/features/suggestions/review-state.test.ts` | New focused tests for shared lifecycle gates |
| `src/features/suggestions/components/knx-suggestion-group.ts` | Area heading and expansion; remove always-visible location forms |
| `src/features/suggestions/components/knx-suggestion-device.ts` | Compact row and lazily rendered entity rows; dispatch editing dialogs |
| `src/features/suggestions/components/knx-suggestion-device.test.ts` | New tests for disclosure, direct actions, and frozen-target behavior |
| `src/features/suggestions/dialogs/knx-suggestions-job-dialog.ts` | New job list/detail dialog, live progress, bounded action results, lifecycle requests |
| `src/features/suggestions/dialogs/knx-suggestions-job-dialog.test.ts` | New progress, count, history, unsubscribe, and command tests |
| `src/features/suggestions/dialogs/knx-suggestion-device-dialog.ts` | New device-name/location editing dialog reusing location editor |
| `src/features/suggestions/dialogs/knx-suggestion-device-dialog.test.ts` | New draft/save/cancel/location tests |
| `src/features/suggestions/dialogs/knx-suggestion-entity-dialog.ts` | New entity dialog with fixed `config` or `device` mode; reuse KNX schema form and picker |
| `src/features/suggestions/dialogs/knx-suggestion-entity-dialog.test.ts` | New schema/config/assignment/conflict tests |
| `src/features/suggestions/components/knx-suggestion-location-editor.ts` | Reuse existing draft-only area/floor changes; adapt acknowledgment handling only if needed |
| `src/features/suggestions/dialogs/knx-suggestions-apply-dialog.ts` | Grouped summary and field differences; retain authoritative preview and revision guards |
| Existing view/group/apply tests | Update intentional layout expectations; retain business-rule regressions |
| `src/localize/languages/de.json`, `en.json` | Copy for new UX, translated in the owning task |

No generic dialog factory, custom component library, or new state package. `src/services/suggestions.service.ts` and `src/types/suggestions.ts` retain their current wire contracts, including existing dirty output-limit additions.

## Task 1: Select one job and preserve trustworthy live state

**Files:** Modify view, view tests, `review-state.ts`; create `review-state.test.ts`.

**Interfaces:** Reuse `SuggestionRun`, `PatchSuggestionsRequest`, `ReviewEvent`, `listSuggestions(hass)`, and `subscribeSuggestions(hass, callback)`. Export `isRunActive(run)`, `canResumeRun(run)`, and `canApplyRun(run)` from `review-state.ts`. Add `getRun(id: string): SuggestionRun | undefined` and `review(request: PatchSuggestionsRequest): Promise<void>` as callbacks for dialogs; these are closures over view state, not a global service.

- [ ] **1. Record baseline and run existing focused tests.** Record `git status --short`, `git diff --stat`, HEAD, and inherited patch paths in the execution notes. Run `yarn test src/features/suggestions src/services/suggestions.service.test.ts --exclude '**/.worktrees/**'`. Existing failures are baseline evidence; do not fix unrelated submodule errors under this task.
- [ ] **2. Add failing lifecycle and selected-run tests.** Move the current private lifecycle conditions unchanged into public pure functions. A self-contained lifecycle regression belongs in `review-state.test.ts`:

  ```ts
  import { expect, it } from "vitest";
  import type { SuggestionRun } from "../../types/suggestions";
  import { canResumeRun, canApplyRun } from "./review-state";

  it("does not resume generation once apply actions exist", () => {
    const run: SuggestionRun = {
      id: "r", source: "ai_task", source_entity_id: "ai_task.test",
      project_fingerprint: "p", scope: [], mode: "both", status: "stopped",
      revision: 2, completed_parts: 1, total_parts: 2, current_scope: null,
      errors: [], floor_targets: [], area_targets: [], devices: [], entities: [],
      apply_actions: [{ id: "a", kind: "create_device", status: "done" }],
      created_at: "2026-09-25T08:00:00Z", updated_at: "2026-09-25T08:01:00Z",
    };
    expect(canResumeRun(run)).toBe(false);
    expect(canApplyRun({ ...run, status: "applying" })).toBe(false);
  });
  ```

  Adapt the existing `combines existing and no-area groups across runs...` test into a selected-run regression using its existing `makeRun`, mocked calls, and two snapshots. Set both runs' device IDs equal, give their entities different names, and assert only the selected run's groups render. Trigger `suggestions-review` and assert `callWS` receives the selected `run_id` and revision only. Keep the separate 1000-change chunk and partial-save tests; changing layout does not remove their guarantees.
- [ ] **3. Run the new tests before moving predicates.** Run `yarn test src/features/suggestions/review-state.test.ts src/features/suggestions/views/knx-suggestions-view.test.ts --exclude '**/.worktrees/**'`. Confirm missing predicates and selected-run rendering fail for the intended reasons.
- [ ] **4. Implement selected-run state and guarded callbacks.** Preserve existing predicate logic:

  ```ts
  export const isRunActive = (run: SuggestionRun) =>
    ["running", "applying", "stopping"].includes(run.status);
  export const canResumeRun = (run: SuggestionRun) =>
    ["stopped", "interrupted"].includes(run.status) &&
    run.source === "ai_task" && !run.apply_actions?.length &&
    run.completed_parts < run.total_parts;
  export const canApplyRun = (run: SuggestionRun) =>
    !isRunActive(run) && run.status !== "applied" &&
    (run.entities.some((entity) => entity.selected) ||
      run.apply_actions?.some((action) => action.status !== "done"));
  ```

  Add `_selectedRunId?: string`, `_synchronized = false`, and a private `_selectRun(id: string)` that verifies the ID exists and initializes the new job's presentation state. Initial selection: newest active run, else newest non-applied run, else newest applied run, else empty. Sort a copy by `created_at`, with ID as tie-breaker; retain an existing selected ID across updates. If that run disappears, close dependent editing, show a specific message, and choose the same fallback explicitly. A start acknowledgment selects the new run once its durable snapshot arrives.

  Render groups from the selected run only. Expose the existing acknowledgment path to dialogs without relying on portal event bubbling:

  ```ts
  const getRun = (id: string) => this._runs.find((run) => run.id === id);
  const review = (request: PatchSuggestionsRequest): Promise<void> =>
    new Promise((resolve, reject) => {
      if (!this.hass.connected || !this._synchronized) {
        reject(new Error(this.knx.localize("suggestions_connection_stale")));
        return;
      }
      void this._patchBatches([request], {
        onSuccess: resolve,
        onError: (message) => reject(new Error(message)),
      });
    });
  ```

  Reconcile snapshots by run ID and revision; do not reject same-revision phase updates. When `hass.connected` becomes false, retain displayed data but disable mutations. On reconnection, rely on the existing subscription's resubscription and require a fresh snapshot before enabling mutations. Handle the initial list/subscribe race using the existing connection-generation guard: do not let an older initial response overwrite a newer subscribed snapshot. Unsubscribe and invalidate pending callbacks on disconnect.
- [ ] **5. Pin asynchronous failure behavior in the existing view harness.** Capture the existing mocked subscription callback, publish a newer revision followed by an older one, and assert it never regresses. Publish a same-revision `current_phase` update and assert it does update. Set `hass` to a copied object with `connected: false`, attempt an edit, and assert no patch call; reconnect, deliver a fresh snapshot, then assert writes work. Update mock `hass` objects to declare `connected: true`. Assert a job selection survives insertion of a newer job.
- [ ] **6. Run focused tests and commit only this task's new hunks.** Use the command in step 3 plus `src/services/suggestions.service.test.ts`. Suggested commit: `feat: scope KNX suggestion review to a selected job`.

## Task 2: Compact area/device rows, search, and explicit selection

**Files:** Modify view, group, device, view/group tests, translations; create device tests.

**Interfaces:** Keep `ReviewGroupEntry`, `selectionState`, `areaHeading`, `ReviewEvent`, and `suggestions-review`. View stores expansion by run/device ID so moving a device to another group does not erase its state. Pass `.expanded`, `.searchExpanded`, and the existing run/device data to device rows. A `suggestions-device-expanded` event carries `{ run_id: string; device_id: string; expanded: boolean }`; declare it in the existing global HA event-map pattern.

- [ ] **1. Write a regression for closed-device rendering and selection independence.** In the new device test, use the same HA import mocks as the existing group test; construct a `SuggestionRun` fixture with one device and two entities, one selected. Do not import another `.test.ts` for fixtures. Mount the component with `.run`, `.device`, `.hass` (areas/floors/devices/localize), and `.knx` (localize/config_entry). The assertion sequence is:

  ```ts
  expect(device.shadowRoot!.querySelector("knx-form")).toBeNull();
  expect(device.shadowRoot!.querySelectorAll("[data-entity-id]")).toHaveLength(0);
  expect(device.shadowRoot!.querySelector("ha-checkbox")!.indeterminate).toBe(true);
  device.expanded = true;
  await device.updateComplete;
  expect(device.shadowRoot!.querySelectorAll("[data-entity-id]")).toHaveLength(2);
  expect(device.shadowRoot!.querySelector("knx-form")).toBeNull();
  ```

  Run `yarn test src/features/suggestions/components/knx-suggestion-device.test.ts --exclude '**/.worktrees/**'` and confirm failure before implementation.
- [ ] **2. Replace always-visible forms with rows.** Use `ha-checkbox`, native HA icon buttons/tooltips, and a separately focusable disclosure button. Render entity rows only when `expanded || searchExpanded`; do not instantiate their config forms. Keep names as normal text and metadata in secondary theme color. Display at most two groups of addresses followed by a translated additional-count label; full addresses remain in the technical disclosure. Preserve logical endpoints sharing physical refs. Area sections start open and retain their selected run's collapse state. Move location forms out of group/device bodies into Task 4 dialogs; retain the group heading's location-edit action.

  ```ts
  const entities = this.run.entities.filter(
    (entity) => entity.device_target_id === this.device.id,
  );
  const selection = selectionState(entities.filter(
    (entity) => !frozenTargets(this.run).has(entity.id),
  ));
  const reveal = this.expanded || this.searchExpanded;
  // In render(): checkbox + disclosure are siblings, never nested buttons.
  // Keep entity rendering keyed by entity.id using Lit repeat.
  ```

  Add direct buttons with `data-action="edit-device"`, `edit-entity`, and `assign-entity`. Until Task 4 moves the editors, keep their existing action handlers operational; do not ship inert actions between tasks.
- [ ] **3. Add search/filter state without touching persisted selection.** Use private view fields `_query`, `_areaFilter`, and `_statusFilter`. Filter on entity names, platform, device names, physical refs, and groups; include associated device rows and auto-expand their parent area during a search. Use `Intl.Collator(this.hass.locale.language, { numeric: true, sensitivity: "base" })` for names and ID tie-breakers. Use `areaHeading` to preserve same-named rooms on different floors. No heuristic parsing of job error strings.

  ```ts
  const query = this._query.trim().toLocaleLowerCase();
  const matchingEntities = run.entities.filter((entity) =>
    [entity.config.entity && (entity.config.entity as { name?: string }).name,
      entity.entity_id, entity.platform, ...entity.group_addresses]
      .filter(Boolean).join(" ").toLocaleLowerCase().includes(query),
  );
  const matchedDeviceIds = new Set(matchingEntities.map((e) => e.device_target_id));
  ```

  Reuse the existing `EntityData` type for config access in implementation; the expression above shows the search fields. Match device metadata as well. A device-name match includes all its entities; an entity-only match includes matching entities with an explicit shown/total count. Clearing search restores manual expansion. Status filters use `entity.action`, target IDs, and confirmed apply actions; `none` area is neutral. Keep selectable non-frozen counts separate from historical applied selection.
- [ ] **4. Add the selection bar and stable pending-results behavior.** Derive selected count from the complete current run, hidden count from the visible entity-ID set. Put `data-action="review-selection"` on the primary button; call the existing `_openApply` only after `canApplyRun`. Keep every mutation disabled during reconnect or the existing pending mutation. New devices/entities arriving while a nonempty list is in use are queued as IDs, with a translated count and `show-new-results` button; already-visible objects update from the newest snapshot. The first nonempty result batch becomes visible immediately. Applying a user-requested sort/filter/new-results refresh reorders the display deliberately. No second stale copy of server objects.
- [ ] **5. Test search, hidden selection, and live stability in the view harness.** Search for a group address under a manually closed area, assert the match is visible and original checkbox values unchanged, clear the query and assert the prior collapse state returns. With two selected entities and one filtered out, assert total `2`, hidden `1`. Deliver a new entity snapshot, assert it is pending until `show-new-results`, and verify an existing row DOM reference remains equal across updates. Move a device's area in a snapshot and assert its expansion survives.
- [ ] **6. Run view/group/device tests and inspect 390/1280 layouts.** Use `yarn test src/features/suggestions/views src/features/suggestions/components --exclude '**/.worktrees/**'`. Suggested commit: `feat: add compact expandable KNX suggestion review`.

## Task 3: Job menu, truthful activity banner, and live detail dialog

**Files:** Modify view and translations; create job dialog and tests. Reuse `review-state.ts` lifecycle functions.

**Interfaces:** Define in the new job dialog:

```ts
export interface SuggestionsJobDialogParams {
  knx: KNX;
  hass: HomeAssistant;
  narrow: boolean;
  runId?: string; // absent opens the complete job list
  onSelectRun: (runId: string) => void;
  onAction: (runId: string, action: "stop" | "resume" | "discard") => Promise<void>;
}
```

The view's `onAction` resolves the current run and invokes existing `_lifecycle`; refactor it to reject on failure so dialogs show the error without claiming success. The dialog subscribes through `subscribeSuggestions` while open and unsubscribes on removal. Store selected ID, not a permanently copied run. A missing run shows a specific unavailable state and only navigation/close actions.

- [ ] **1. Add failing count/phase tests before moving render code.** Export a small `countCompletedActions(actions: ApplyAction[], kind: string): number` from the job dialog module for summary derivation. Test mixed statuses:

  ```ts
  const actions = [
    { id: "d1", kind: "create_device", status: "done" },
    { id: "d2", kind: "create_device", status: "failed" },
    { id: "e", kind: "update_entity", status: "done" },
    { id: "a", kind: "create_area", status: "done" },
  ];
  expect(countCompletedActions(actions, "create_device")).toBe(1);
  expect(countCompletedActions(actions, "create_entity")).toBe(0);
  expect(countCompletedActions(actions, "update_entity")).toBe(1);
  ```

  Move the existing phase, legacy, terminal-spinner, and permanently mounted live-region tests from the large view test into the job dialog tests, retaining their existing snapshots and assertions. Run `yarn test src/features/suggestions/dialogs/knx-suggestions-job-dialog.test.ts --exclude '**/.worktrees/**'` to confirm the new rendering path is absent.
- [ ] **2. Build menu and dialog with native HA components.** Use `ha-dropdown` and `ha-dropdown-item` in the subpage toolbar. Show up to five jobs, active first, newest next; the final item opens the same dialog without `runId`. In list mode, selecting a row sets the dialog's selected ID; in detail mode, “Vorschläge ansehen” calls `onSelectRun` and closes. Use localized scope summary/start date for titles; put IDs and AI entity references in a technical disclosure. Keep the start button reachable with zero results.

  ```ts
  export const countCompletedActions = (actions: ApplyAction[], kind: string) =>
    actions.filter((action) => action.kind === kind && action.status === "done").length;
  // Use this separately for create_device, create_entity, update_entity,
  // create_area, and create_floor. The overall action bar counts all done actions.
  ```

  Move `_renderActivity` and `_renderProgress` presentation out of the view, replacing the outer always-open expansion with dialog sections. Preserve optional phase fallback and permanently mounted `aria-live="polite"`. Each phase sequence describes the current part. Show `completed_parts/total_parts`; use indeterminate progress when total is unknown. Treat `partial` as partially applied, not complete. Derive result labels from confirmed action kinds and statuses, never entity-array length.
- [ ] **3. Bound results and errors.** The detail body shows summary counts, then failed actions first and at most five other action rows. “Alle Ergebnisse” toggles a bounded scrollable area with all actions. Show known action target names from proposal/result metadata; unknown kinds use a localized generic label plus technical detail. Preserve full error text under disclosures; do not invent warning severity or timestamps. Link confirmed device/entity result IDs using existing HA navigation utilities and only after identifying the action kind.
- [ ] **4. Render one compact global activity banner.** For one active job, show the saved phase/count and “Job öffnen”; for multiple active jobs, show an aggregate and open list mode. Completion emits one existing HA toast per status transition observed in this session, not on initial loading of historical results. Interrupted/failed actionable jobs retain a compact dismissible notice; their history remains in the menu. Check `isRunActive` before showing activity, including stale `current_phase` on terminal jobs.
- [ ] **5. Preserve stop/resume/discard semantics.** Use the shared lifecycle predicates and current snapshot immediately before sending commands. Confirm discard with the existing HA confirmation dialog and text explaining that created resources remain. Stop is not discard. Disable repeated commands while pending. Closing the progress dialog only unsubscribes and never calls stop. Test menu detail opening sends no mutation, double stop sends one request, discard never appears for applying, legacy data renders, all five count kinds are distinguished, and removing the dialog invokes unsubscribe once.
- [ ] **6. Run job/view tests and commit.** `yarn test src/features/suggestions/dialogs/knx-suggestions-job-dialog.test.ts src/features/suggestions/views/knx-suggestions-view.test.ts src/features/suggestions/review-state.test.ts --exclude '**/.worktrees/**'`. Suggested commit: `feat: move KNX suggestion jobs into progress dialogs`.

## Task 4: Direct revision-safe editing dialogs

**Files:** Create device/entity dialogs and their tests; modify device/group components and location editor only as required; translations.

**Interfaces:** Both dialogs use `DialogMixin` and the established HA contexts. Define the base fields directly in each file rather than adding a shared factory:

```ts
// Device dialog params:
export interface SuggestionDeviceDialogParams {
  knx: KNX; hass: HomeAssistant; narrow: boolean;
  runId: string; deviceId: string;
  getRun: (runId: string) => SuggestionRun | undefined;
  review: (request: PatchSuggestionsRequest) => Promise<void>;
}
// Entity dialog adds entityId and mode instead of deviceId:
export interface SuggestionEntityDialogParams {
  knx: KNX; hass: HomeAssistant; narrow: boolean;
  runId: string; entityId: string; mode: "config" | "device";
  getRun: (runId: string) => SuggestionRun | undefined;
  review: (request: PatchSuggestionsRequest) => Promise<void>;
}
```

Pass view callbacks through group/device props. Dialogs must not rely on events bubbling through the original row after portal mounting. Subscribe to snapshots while open to detect deleted targets, new revisions, frozen targets, or disconnection; clean up on close. Do not replace unsaved input when a snapshot arrives.

- [ ] **1. Add failing callback-based save tests using the apply-dialog context harness.** Provide `review = vi.fn().mockRejectedValue(new Error("Conflict"))` and mutable `getRun`. Fill an editor field, click save, and assert the callback receives the original revision and a targeted patch:

  ```ts
  expect(review).toHaveBeenCalledWith({
    run_id: "run", revision: 2,
    changes: [{ kind: "name", target_id: "south", name: "Office light" }],
  });
  expect(dialog.shadowRoot!.textContent).toContain("Conflict");
  expect(dialog.shadowRoot!.querySelector("ha-input")!.value).toBe("Office light");
  ```

  Test Cancel with unchanged fields sends zero patches. Run `yarn test src/features/suggestions/dialogs/knx-suggestion-device-dialog.test.ts src/features/suggestions/dialogs/knx-suggestion-entity-dialog.test.ts --exclude '**/.worktrees/**'` before implementing.
- [ ] **2. Move device editing into its dialog.** Reuse existing name and area-change construction, `ha-area-picker`, and `knx-suggestion-location-editor`. Draft all simple field changes until Save. The location editor's `suggestions-review` event must be intercepted inside the dialog, stopped, and routed through `params.review`; call its existing success/error callbacks. Creating an area/floor remains draft-only. If location creation is a separately saved operation, label its action explicitly as saving the proposed location and refresh the dialog revision after acknowledgment; do not imply the outer Cancel reverses that acknowledged operation. Existing-device/frozen-target restrictions are checked both when rendering and before sending. Empty names keep focus and show validation.
- [ ] **3. Move schema-based entity configuration without rewriting it.** Transfer `_edit`, schema loading, config cloning, translated form labels, category handling, and `_knxConfig` from the current device component. Preserve `EntityData` and `knx-form`. Config mode opens directly on common fields, with advanced/technical disclosures. Device mode reuses `knx-device-picker` and existing proposed-device choices; validate the chosen existing device belongs to `knx.config_entry.entry_id` before sending `device_target`. Only the selected entity moves.

  ```ts
  const request: PatchSuggestionsRequest = {
    run_id: this.params!.runId,
    revision: this._editingRevision!,
    changes: [{ kind: "entity_config", target_id: this.params!.entityId,
      config: structuredClone(this._config!) as unknown as Record<string, unknown> }],
  };
  await this.params!.review(request);
  this._dirty = false;
  this.closeDialog();
  ```

  `_editingRevision?: number`, `_config?: EntityData`, `_dirty`, `_saving`, and `_error` are dialog-local state. Bind revision when opening, not at the instant of Save. On failure, keep the dialog, config, and revision. A “Neu laden und prüfen” action reads the current run, preserves local edits for comparison, and requires another explicit Save; never automatically resubmit.
- [ ] **4. Protect dismissal and asynchronous transitions.** Use the actual pinned `ha-dialog` hide/close hooks plus `DialogMixin.closeDialog` so Escape, backdrop, header close, and footer Cancel all pass the same dirty check. Ask the HA confirmation dialog before discarding unsaved edits; do not ask after a successful save. Resolve schema loading only if the dialog still exists and the request belongs to its current target. A target becoming frozen or disappearing makes Save unavailable and explains why; local edits remain copyable.
- [ ] **5. Add the loss-prevention tests.** Keep an input edited while publishing a newer revision; assert the value stays unchanged and old revision is not silently replaced. Publish applied status and assert no write. Reject schema loading and assert an error without a half-open editor. Double-click Save and assert one patch. Cancel a dirty-dialog confirmation and assert the editor stays open. Try a foreign config-entry device and assert zero patches. Verify a location-editor acknowledgment reaches its callback even though the dialog is outside the device row.
- [ ] **6. Run all suggestion component/dialog tests and commit.** Use `yarn test src/features/suggestions/components src/features/suggestions/dialogs --exclude '**/.worktrees/**'`. Suggested commit: `feat: add focused KNX suggestion editing dialogs`.

## Task 5: Compact authoritative apply preview and recovery

**Files:** Modify apply dialog/test, view integration, translations.

**Interfaces:** Preserve `SuggestionsApplyDialogParams.request: SuggestionRevisionRequest`, `previewSuggestionApply`, `applySuggestions`, and `ApplyAction` before/after/dependencies. Add only optional `run?: SuggestionRun` for display-name/group resolution and `onStarted?: (runId: string) => void` for returning to the running-job UI. The preview's ordered actions remain the execution contract; display grouping does not reorder execution.

- [ ] **1. Extend the existing preview tests before changing rendering.** Keep invalid preview, cancel, duplicate confirm, stale revision, and delayed closed-dialog tests. Use the existing `preview` fixture, whose kinds are `create_floor`, `create_area`, `create_device`, `update_entity`. Assert summary counters are `1,1,1,1` by kind, and no action is labeled completed while its status is pending. Add an unchanged field to both `before` and `after`; assert it is omitted from the concise difference rows but remains in technical details.

  ```ts
  expect(dialog.shadowRoot!.querySelectorAll("[data-diff-field='name']")).toHaveLength(1);
  expect(dialog.shadowRoot!.querySelectorAll("[data-diff-field='unchanged']")).toHaveLength(0);
  expect(control("#confirm").disabled).toBe(false);
  ```

  Run `yarn test src/features/suggestions/dialogs/knx-suggestions-apply-dialog.test.ts --exclude '**/.worktrees/**'` and observe the new summary/diff assertions fail.
- [ ] **2. Implement concise summaries and differences.** Count planned actions by kind above grouped device sections. Keep required floor/area actions visible in their own dependencies section when they cannot be assigned to one device. Reuse `deepEqual` from `@ha/common/util/deep-equal`, already used in `src/components/knx-payload-selector.ts`, to select changed top-level configuration fields:

  ```ts
  import { deepEqual } from "@ha/common/util/deep-equal";

  const before = action.before ?? {};
  const after = action.after ?? {};
  const changedKeys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter((key) => !deepEqual(before[key], after[key]));
  ```

  Render one `data-diff-field` row per changed key, distinguishing added/deleted values. Nested changed objects may use a compact before/after block; complete before/after/source/ETS data stays in a disclosure. Escape through Lit templates, never `unsafeHTML` for names or error text.
- [ ] **3. Retain explicit revision confirmation.** Initial preview binds the request revision. A conflict blocks Confirm, retains the explanation, and offers loading a new preview; the renewed preview requires explicit confirmation. Never auto-apply after refresh. After `applySuggestions` acknowledgment call `onStarted` and close. Progress and created-resource counts come from subsequent durable snapshots, not from the acknowledgment.
- [ ] **4. Test partial recovery with completed dependencies.** Add a `done` device action plus one failed entity and one pending entity. Assert the completed device is labeled complete, frozen entities are not selectable, and only backend-provided outstanding actions can be retried. Assert cancel/dismiss never writes, a refreshed preview does not call apply, and the eventual request uses the newly reviewed revision exactly once. Preserve all existing source/before/after test coverage by opening the corresponding disclosure in tests.
- [ ] **5. Run apply/view regression tests and commit.** `yarn test src/features/suggestions/dialogs/knx-suggestions-apply-dialog.test.ts src/features/suggestions/views/knx-suggestions-view.test.ts --exclude '**/.worktrees/**'`. Suggested commit: `feat: streamline KNX suggestion apply review`.

## Task 6: HA visual integration, empty states, and final verification

**Files:** Only the components/dialogs touched above and existing translations/tests. No generated bundle is committed.

**Interfaces:** No new wire or shared interfaces. Validate the actual HA-rendered components rather than copying the standalone mockup's HTML/CSS.

- [ ] **1. Close all state gaps from the spec.** Explicitly render loading, zero jobs, no project, no AI-task entity, no search results, selected run removed, disconnected/stale data, and invalid revision. Reuse current start-dialog prerequisite and output-limit validation. Use contextual actions and existing routes rather than guessing new URLs. Add view tests for zero jobs and filtered-empty with unchanged selection.
- [ ] **2. Apply the HA theme/layout conventions.** Use current `ha-*` components, spacing/font/theme tokens, logical CSS properties, and simple focus-visible styling. Standard dialog widths: `medium` for edit/job details, `large` for apply preview, `full` when narrow. Use a fixed/sticky selection footer with matching content bottom padding and safe-area inset. On mobile retain visible edit and expand targets; shorten metadata and wrap names before hiding essential actions.

  ```css
  .content { padding: var(--ha-space-4); padding-block-end: calc(96px + env(safe-area-inset-bottom)); }
  .device-name { min-width: 0; overflow-wrap: anywhere; color: var(--primary-text-color); }
  .metadata { color: var(--secondary-text-color); }
  .selection-bar { padding-block-end: max(var(--ha-space-3), env(safe-area-inset-bottom)); }
  @media (prefers-reduced-motion: reduce) {
    .activity-spinner { display: none; }
  }
  ```

  Keep textual activity visible when the spinner is hidden. Do not use motion as the only active-state signal. Read the actual parent scrolling behavior before choosing sticky versus fixed positioning.
- [ ] **3. Verify the five review-focus classes.** Run the targeted tests added in Tasks 1–5. Confirm exact expectations for overlapping IDs, stale snapshots, search expansion restoration, mixed action counts, and dirty edit preservation. Translate complete phrases with placeholders and proper pluralization, including 0/1/many entities and jobs.
- [ ] **4. Perform visual and keyboard acceptance in the real panel.** At widths 390, 768, and 1280 px, test light/dark themes, 200% zoom, reduced motion, long German names, missing areas, 6+ jobs, and 100+ proposals. Observe the first screen: device rows must be visible without dismissing a completed job. Check tab order, disclosure with keyboard, dialog focus trapping/Escape/return, progress announcements, footer overlap, scroll stability on snapshots, and no horizontal page overflow. Record screenshots of collapsed overview, expanded device, active banner/menu, job detail, entity editor, and apply preview. Runtime access follows workspace rules; use the authorized running development setup or arrange an isolated preview without restarting the user's service.
- [ ] **5. Run final repository checks once after the final edits.** From the implementation checkout:

  ```sh
  yarn test --exclude '**/.worktrees/**'
  yarn lint
  yarn build
  git diff --check
  git status --short
  ```

  Record actual results. Do not call a failing check passed; distinguish reproduced baseline failures from new failures with the baseline from Task 1. Do not broaden into unrelated pinned-frontend fixes. No tests are required in Core because Core is unchanged.
- [ ] **6. Review scoped diff, commit final polish, and deliver.** Compare against the recorded inherited dirty baseline and confirm no unrelated hunks/submodule pointer/build artifacts are staged. Suggested commit: `style: align KNX suggestion review with Home Assistant`. Report the implemented behavior, validation results, and any concrete remaining limitation. Keep local commits separate from permission to push or open a PR.

## Spec coverage and execution handoff

| Spec area | Owner |
| --- | --- |
| One selected job, durable state, disappearing job and reconnect | Task 1 |
| Overview, areas/devices/entities, search/filter/selection, live stability | Task 2 |
| Jobs menu/history, banner, phases, result counts/log, lifecycle actions | Task 3 |
| Name/location/config/device assignment, dirty edits and revision conflicts | Task 4 |
| Preview, dependencies, before/after, apply and partial recovery | Task 5 |
| Native HA styling, localization, accessibility, responsive and empty/error states | Task 6 |

The visible prototype is an interaction reference, not production code. Preserve the approved information architecture while using native component behavior.

Recommended execution: **Native**, implementing these six sequential tasks in this session, followed by an independent whole-diff review. Their state and dialog interfaces are closely connected, so separate fresh implementers per task add coordination cost. Alternative: **Subagent-driven**, a fresh implementer and reviewer per task, then whole-branch review.

Design approval has been received. Implementation starts after the user reviews this plan and chooses the execution method, as required by the invoked brainstorming/writing-plans workflow.
