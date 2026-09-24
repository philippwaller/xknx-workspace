# KNX project suggestions in Home Assistant Labs

- **Status:** Design for review, 2026-09-24
- **Repositories:** `home-assistant-core` and `knx-frontend`
- **First source:** Home Assistant AI Task
- **Later source:** Native ETS functional block and datapoint application suggestions

## Purpose and boundaries

An administrator should be able to turn an imported ETS project into reviewable proposals for Home Assistant floors, areas, KNX devices, and KNX entities. One controllable or observable functional endpoint becomes one logical Home Assistant device. For example, the south and north covers in one room are separate devices. Each device contains its own cover and associated status, alarm, lock, and direction entities. Physical ETS devices, channels, communication objects, and group addresses remain visible as references; a physical actuator may serve several logical Home Assistant devices.

The administrator chooses what ETS scope to analyze and what to apply. Generation never writes configuration. Existing Home Assistant floors and areas are authoritative; ETS locations are evidence for assigning a proposed device, not a replacement naming scheme for Home Assistant. The review surface is source-neutral, even though the first implementation generates proposals through AI Task. Physical KNX references link to the corresponding ETS device/channel details in the KNX project view where those details exist. A logical device can reference several physical KNX devices, so the first release does not invent a single Home Assistant parent-device relationship.

This is a **KNX Labs preview feature**, disabled by default. Its initial release covers AI generation and a source-neutral review model. It does not depend on the draft Core PR [#180891](https://github.com/home-assistant/core/pull/180891) or the related KNX frontend PR [#448](https://github.com/XKNX/knx-frontend/pull/448). Native suggestions can be connected later without making the first release wait for those PRs.

## User flow

1. The administrator enables **KNX project suggestions** in Home Assistant Labs. The KNX menu then exposes **Vorschläge**. A direct URL is also subject to the Labs check.
2. The Suggestions page loads saved proposal drafts and active run status. Opening it does not call AI Task or a native suggestion provider.
3. **AI Task starten** opens one Home Assistant dialog. In it the administrator selects the AI Task entity, either the entire ETS project or selected ETS rooms, and whether to propose new entities, updates to existing entities, or both. A rough input-token estimate and the expected number of analysis parts update with scope. The dialog identifies the selected AI Task as the destination for the selected project context.
4. Starting the run closes the dialog and returns a run ID promptly. An expandable progress section at the top of Suggestions shows completed/total parts, the current ETS scope, errors, and the count of saved proposals. Existing drafts remain usable. Results from completed parts appear in the review list as they are saved. Proposals from an active run can be reviewed, but applied only after that run completes or stops.
5. The administrator reviews proposals grouped by **target Home Assistant area**, selects rooms, devices, or individual entities, edits configurations, changes area and device targets, and then applies the chosen subset. A final summary lists new floors and areas, new or existing devices, and entity creates or updates.
6. New runs supplement existing drafts. A draft remains until its proposals are applied or the administrator discards it. Reopening Suggestions never regenerates it.

## Suggestions page and dialog

The page has one flat expandable section per relevant Home Assistant area, labeled with the full Home Assistant floor/area path, such as `EG / Flur` and `OG / Flur`. Existing and proposed new areas use the same layout, with an explicit **new floor** or **new area** marker where needed. There is one **Ohne HA-Raum** section for proposals whose editable preselection is no area. ETS paths appear as provenance on device or entity proposals, not as room-group headings. Only areas containing proposals need to appear.

The page provides **Alle Räume öffnen** and **Alle Räume schließen**. Each area, device, and entity has a checkbox. Selecting an area selects its descendant devices and entities; selecting a device selects its entities. Partially selected parents use the indeterminate state. A new floor or area is created only if the applied selection contains an entity whose device uses it. An empty new device is not created. Device cards are distinct by functional endpoint rather than combined by room or physical actuator. Entities can be moved to another proposed logical KNX device or an existing KNX device belonging to the same integration. A moved entity follows the target device into its Home Assistant area group.

Each proposal shows **Erzeugt von** separately from **KNX-Bezug**. The former identifies the generating run/source; the latter identifies ETS/KNX evidence. Entity rows expose the proposed KNX configuration and matched group addresses. The area row explains the target mapping and lets the administrator choose another existing area, a new area, or no area. The source-neutral layout leaves room for future source filtering, but v1 has no source filter.

Use existing Home Assistant UI components: `ha-dialog` and `ha-dialog-footer` for the start form, HA selectors for the AI Task and modes, `ha-checkbox` and `ha-expansion-panel` for selection and review, `ha-area-picker`/`ha-floor-picker` with immediate creation disabled when choosing existing targets, and `ha-progress-bar` for run progress. The ETS scope selector is a small composition of these components because there is no general native checkbox tree matching this use case. On narrow screens the dialog may use Home Assistant's full-width mode. The Suggestions page remains the sole landing and review view; there is no separate preparation page or step-by-step wizard.

## Home Assistant floor and area mapping

Every proposed device has an editable preselected area target: an existing Home Assistant area, a proposed new area (with existing or proposed floor), or explicitly no area. Existing Home Assistant names, aliases, floors, and area IDs are never changed merely because ETS uses another term. `ETS: EG / Badezimmer` can therefore preselect `HA: EG / Bad`, with a visible reason; applying assigns the device to `Bad` without renaming it or adding an alias.

The mapping uses Home Assistant floor/area names and aliases, ETS space paths and types, ETS functions, group-address semantics, and the physical-channel references as evidence. A unique strong match is preselected. Where several areas are plausible, the best candidate is still preselected but marked for careful review. When no comparable area exists, a new Home Assistant area is preselected; a missing floor can likewise be proposed. If there are no useful location clues, no area is the preselection. Names for new Home Assistant areas/floors are editable and validated against existing registry entries, including the fact that area names must be unique even when floors differ. Existing HA areas are never silently renamed, moved, or assigned aliases. The UI shows the full path to distinguish repeated ETS names such as `Flur`.

ETS installation location alone must not determine the logical device area: an actuator in a cabinet may control endpoints in several rooms. The mapping should prefer functional and group-address evidence where available. If evidence is weak, the preselection stays visible and correctable.

## Generation and data validation

The KNX backend, not the browser, orchestrates AI Task generation through Home Assistant's `async_generate_data` with a structured result schema. It prepares only the selected project context plus the minimum Home Assistant floor/area and existing KNX configuration context needed for matching and duplicate checks. A whole-project selection is automatically divided into bounded parts, generally by ETS functional room and then by endpoint group if one room is too large. Relevant cross-room KNX references remain available to each part; splitting must not equate physical device location with functional room. Completed parts are durably added to the same run.

The token estimate is computed from the context that would be sent for the selected scope and reports an approximate **input** total and part count. It is labeled model-dependent; no exact usage, output-token, duration, or price prediction is promised. AI Task currently returns structured data and a conversation ID, not a generic live token counter. Overall progress therefore counts completed parts. The active AI call uses an indeterminate indicator rather than a fabricated time percentage.

AI output is untrusted proposal data. Before saving it as a usable proposal, Core verifies referenced ETS project IDs and KNX group addresses, platform and configuration shape against the KNX entity store schema, target registry IDs, and whether a requested update belongs to a KNX UI-created entity. Invalid entries remain visible as per-part errors or rejected proposal details; they never reach apply unchanged. YAML-configured entities can appear as overlaps but are read-only and cannot be updated by this feature. A selected update displays a before/after configuration review.

## Persisted review model and provider separation

KNX Core stores versioned suggestion runs. A run records its source (`ai_task` in v1), source entity, ETS project identity/fingerprint, chosen ETS scope and mode, timestamps, status, completed/total parts, errors, and stable proposal IDs. The editable review state stores selected items, corrected area/device targets, edited names/configurations, and per-action apply results. Proposal types are:

- **Floor target:** existing floor ID or editable new floor proposal.
- **Area target:** existing area ID, editable new area proposal referencing a floor target, or no area.
- **Device proposal:** one functional endpoint, new or existing KNX device target, area target, source and ETS/physical references.
- **Entity proposal:** one create or UI-entity update, platform and validated KNX configuration, device target, matched group addresses, source and ETS references.

Multiple runs coexist. Proposal IDs remain stable across page reloads. New results do not overwrite review edits. Exact duplicate candidates from another pending run are linked or marked and are not selected by default; partial group-address overlaps receive a review warning rather than an automatic merge or blanket rejection. Matching considers platform, KNX configuration roles, endpoint/channel references, and group-address sets: sharing a group address alone does not prove two entities are duplicates. Proposed creation of the same new area across runs is reconciled before apply so only one Home Assistant area is created and its ID is reused.

The draft [native suggestion PR](https://github.com/home-assistant/core/pull/180891) computes all registered providers when `knx/get_entity_suggestions` is called. Its frontend currently calls that endpoint when its view opens and groups `group_id` by physical individual address. It reports already configured entity IDs, but does not know about pending AI drafts. For this feature, opening the common review page never invokes that endpoint. A later explicit **native suggestions refresh** can call it, normalize its entity outputs into the same persisted review model, and preserve its physical `group_id` as provenance rather than a Home Assistant device or room group. Stable source IDs plus project fingerprint prevent refresh from re-adding the same native candidate; edited drafts remain intact. When integrating that PR, its standalone review entry should route into this shared experience instead of creating a second apply path. The native adapter and button are outside v1 implementation scope.

## Apply, conflict checks, and recovery

Apply begins with a fresh check of the ETS project fingerprint, Home Assistant floor/area and device registries, KNX entity store, existing entities, and other pending drafts. Stale or overlapping items show an actionable warning. Selected YAML overlaps cannot become updates. Applying a proposal never happens automatically because it was generated, preselected, or because a page loaded.

The administrator sees a final action summary before writing. For accepted items, Core creates required floors, then areas, then new KNX devices, then creates or updates selected entities through existing KNX validation and create/update paths. The Suggestions page shows completed/total apply actions because large selections can take time. Existing Home Assistant area entries are used by ID and left unchanged. If a device target changes during review, its area grouping and selected entities are recalculated before applying. The feature records created IDs and completed actions as each operation succeeds, allowing safe retry of the remaining actions. There is no cross-registry transaction or silent rollback. A failure blocks dependent actions, leaves unrelated selected actions available, and reports what succeeded and what remains.

If the browser closes, the Core-owned run continues. Closing the start dialog after obtaining the run ID does not cancel it. Stopping a run stops scheduling after the current AI Task call and retains completed parts. After a Home Assistant restart, an unfinished run is marked interrupted; its completed proposals remain, and further AI calls require an explicit resume. Saved proposals from a stopped or interrupted run can be reviewed and applied after normal validation. A completed previous run remains reviewable while a new run is generating.

## Labs behavior and delivery

The KNX integration declares one Labs preview feature keyed `project_suggestions` in its manifest and translations. The feature is opt-in and can be toggled without restarting Home Assistant. The KNX frontend subscribes to its status: when disabled it removes the Suggestions menu entry and blocks the direct route; feature-specific backend draft reads, generation, draft mutation, and apply commands also check the flag. Disabling the feature stops scheduling active generation/apply work at a safe boundary and retains drafts for re-enabling. It does not remove Home Assistant floors, areas, devices, or entities that the administrator previously approved and created. The Labs description and documentation state that clearly. The feature must be fully functional and tested despite its preview status.

Implementation work starts from clean upstream checkouts of `home-assistant/core` and `XKNX/knx-frontend` in separate isolated worktrees. Existing local feature branches and dirty checkouts are not changed. Each repository receives its own checks and commit/PR. The initial release does not depend on or stack on the draft native suggestion PRs; its review schema and explicit source field provide the future connection point.

## Acceptance scenarios

- With Labs off, the Suggestions entry and feature-specific operations are unavailable; enabling it shows the page immediately without restart. Turning it off during a run preserves completed draft work and prevents additional AI calls.
- `ETS: EG / Badezimmer` preselects existing `HA: EG / Bad` when it is the best match. Applying creates no duplicate area and changes no property of `Bad`.
- Two ETS locations called `Flur` on different floors display distinct full paths and target distinct Home Assistant areas or editable new-area proposals.
- Two covers in one HA room become two logical KNX devices. Each cover's status, alarm, lock, and direction entity suggestions remain individually selectable and movable; the same physical actuator may be referenced by both devices.
- The room and device checkboxes show full, empty, and partial selection correctly. Moving an entity to another KNX device updates its displayed HA room group.
- A whole-project run is divided into parts, displays a rough input-token estimate, persists progress and completed proposals, and survives browser reload. A Home Assistant restart leaves it interrupted rather than silently spending more AI calls.
- A new AI run adds to existing drafts. Neither loading the page nor disabling/re-enabling Labs triggers regeneration. Overlaps with pending or configured entities are visible and do not silently create duplicates.
- Updates are limited to KNX UI-created entities. YAML overlaps are read-only. A partial apply records completed floor/area/device/entity IDs, reports failures, and a retry does not recreate successful objects.
- The native provider can later supply results through an explicit refresh and source adapter without changing the Home Assistant area-based review hierarchy.
