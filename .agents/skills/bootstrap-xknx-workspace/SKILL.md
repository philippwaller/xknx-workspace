---
name: bootstrap-xknx-workspace
description: Use when a local XKNX and Home Assistant workspace needs bootstrapping, starting, readiness inspection, or smoke testing.
---

# Bootstrap XKNX Workspace

1. Read root `AGENTS.md`.
2. Use the requested profile or `default`; use `toolkit` only for explicitly
   requested virtual KNX.
3. Run `./dev status --format json` first. Compare the requested profile with
   JSON `profile`. On mismatch, do not claim requested-profile readiness.
   Require the developer to align root `.xknx-dev.toml`; never edit it silently.
4. Run `./bootstrap <profile>` only for incomplete setup. Do not run bootstrap
   solely to produce a smoke result; it can fetch/update.
5. Bootstrap's check is the public smoke path. Without bootstrap, report smoke
   as `not-run` and readiness separately; polling is not a smoke test.
6. Never run `./dev update` automatically or stop, replace, or restart an
   existing tmux session.
7. Run `./dev start <profile>` only in a visible terminal; otherwise return only
   that exact command.
8. Re-read JSON status with bounded backoff until ready, failure, or timeout.
9. Report profile, URLs, tmux session/windows, package source paths, repository
   states, smoke result, and the toolkit round-trip skip when relevant.
