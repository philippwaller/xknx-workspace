---
name: bootstrap-xknx-workspace
description: Use when a local XKNX and Home Assistant workspace needs bootstrapping, starting, readiness inspection, or smoke testing.
---

# Bootstrap XKNX Workspace

1. Read the workspace root `AGENTS.md`.
2. Select the requested profile, defaulting to `default`. Select `toolkit` only
   when a virtual KNX system is explicitly requested.
3. Run `./dev status --format json` first and use its reported conflicts,
   repository states, package paths, processes, URLs, and actions.
4. Run `./bootstrap <profile>` only when setup is incomplete. Its selected
   profile check is the public smoke path; do not call workspace internals.
5. Never run `./dev update` automatically. Never stop, replace, or restart an
   existing tmux session.
6. Start through `./dev start <profile>` only in a visible developer terminal.
   If the host cannot open one, return only that exact command.
7. Re-read JSON status with bounded backoff until ready, an actionable failure,
   or a short timeout. Do not hide failed process output or repair child Git
   state.
8. Report the selected profile, reported URLs, tmux session/windows,
   local package source paths, repository states, and smoke result. State the
   current skipped toolkit round-trip limitation when relevant.
