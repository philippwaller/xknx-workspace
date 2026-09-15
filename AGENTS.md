# XKNX Workspace Agent Guide

This root is a routing map for the independent repositories in the XKNX and
Home Assistant KNX workspace. Profiles are `default`, `frontend`, `toolkit`,
`docs`, and `all`; use `default` unless the task requires an optional area.

## Ownership map

| Area or consumer | Direct producers and related repositories |
| --- | --- |
| `home-assistant-core/homeassistant/components/knx` | `xknx`, `xknxproject`, `knx-telegram-store`, and the `knx-frontend` distribution |
| `knx-frontend` | Its own pinned `homeassistant-frontend` submodule |
| `home-assistant-frontend` | Independent current upstream frontend development |
| `xknxtoolkit` | Optional virtual KNX system for integration testing |
| XKNX documentation | `xknx` repository |
| Home Assistant documentation | `home-assistant.io` repository |

Start in the smallest repository that owns the requested behavior. Inspect
direct producers and consumers when a real API, data model, build artifact,
dependency, or UI/backend contract crosses a boundary. Cross-repository
changes are allowed when that flow requires them, but must not be assumed.

Before editing any child repository, read its local `AGENTS.md`, README,
contributor guide, and relevant documentation. Those files own project setup,
style, architecture, and test instructions; this root guide does not replace
them.

## Workspace boundaries

- Use `./bootstrap [profile]`, `./dev start [profile]`, `./dev status`,
  `./dev update [profile]`, and `./dev stop`; do not bypass the public entry
  points with workspace internals.
- Read `./dev status --format json` before acting. Do not start or restart an
  existing `xknx-dev` session from a hidden or non-interactive shell.
- Root `.xknx-dev.toml` owns local workspace selection. Child setup scripts,
  version files, manifests, and lockfiles remain authoritative for each
  project.
- Launchers use an existing Python 3.12+ directly. Bootstrap creates a missing
  CLI environment only as a displayed, confirmed action. Daily commands and
  help never synchronize it; plain progress remains usable without Rich.
- Real KNX `secure_config_path` is a local file reference, not an installed
  Home Assistant integration configuration. The developer configures that
  integration explicitly; never copy or print the referenced secret material.
- Keep the root `home-assistant-frontend` checkout independent from
  `knx-frontend/homeassistant-frontend`; the latter changes only through the
  KNX frontend's own upgrade workflow.
- The toolkit telegram round-trip acceptance check is currently skipped
  because no stable public non-interactive readiness/send interface exists.

## Git and delivery safety

Fetching is allowed for an explicitly selected update. Fast-forward a child
checkout only when it is on its remote default branch, its index and worktree
are clean, it has no local-only commits, and ancestry proves the update is a
fast-forward. Feature branches, dirty trees, ahead branches, and diverged
branches are report-only.

Never checkout or switch a developer's branch, stash changes, rebase, reset,
perform a merge without `--ff-only`, force an update, recursively update
submodules, or invoke `knx-frontend/script/upgrade-frontend` automatically.
Do not run `./dev update` without explicit authorization.

Verify each affected repository with its own instructions. Commit each
repository independently, and keep tests and pull requests independent even
when one feature spans several repositories.
