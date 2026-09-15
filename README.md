# XKNX Development Workspace

This repository is a thin workspace for contributors working across the XKNX
and Home Assistant KNX stack. It bootstraps normal upstream checkouts, wires
their local packages into Home Assistant, and keeps long-running development
processes visible in tmux. Supported hosts are macOS, Ubuntu, Debian, and
Ubuntu or Debian under WSL 2; native Windows is not supported.

## Layout

The root owns only the workspace launchers, configuration, documentation, and
tooling. Bootstrap creates the selected repositories beside them:

```text
home-assistant-core/       Home Assistant Core and the KNX integration
home-assistant-frontend/   independent current upstream frontend development
xknx/                      XKNX library and XKNX documentation
xknxproject/               ETS project parser
knx-telegram-store/        telegram persistence
knx-frontend/              KNX panel and its pinned homeassistant-frontend/
xknxtoolkit/               optional virtual KNX system
home-assistant.io/         Home Assistant documentation
```

These directories are independent Git repositories, not root submodules. Each
project keeps its own branches, remotes, commits, tests, and pull requests. The
only submodule managed here is the existing
`knx-frontend/homeassistant-frontend` build input pinned by `knx-frontend`.

## Profiles

| Profile | Adds to the default Home Assistant + KNX stack |
| --- | --- |
| `default` | Nothing; Core, XKNX, parser, telegram store, and KNX frontend |
| `frontend` | Root `home-assistant-frontend` checkout and process |
| `toolkit` | `xknxtoolkit` virtual KNX environment |
| `docs` | Home Assistant and XKNX documentation processes |
| `all` | Everything above |

Omitting a profile selects `default`. The default smoke path needs neither
Docker nor KNX hardware. The current toolkit integration is usable, but its
telegram round-trip acceptance check is reported as skipped: xknxtoolkit does
not yet expose a stable public non-interactive readiness and send interface.

## First run

From the workspace root:

```sh
./bootstrap default
./dev start default
```

Bootstrap first prints the complete plan: platform and tools, repositories,
project-owned setup commands, configuration, editable package wiring, and
smoke checks. It changes nothing until the single confirmation is accepted.
Use `./bootstrap default --yes` only when non-interactive acceptance of that
displayed plan is intentional. A failed or interrupted run is resumed by
running the same bootstrap command again; completed results are reused, so
there is no separate reset or resume command.

## Commands

```sh
./bootstrap default                  # plan, configure, set up, and smoke-test
./dev start default                  # create or attach to the visible session
./dev status --format human          # inspect configuration and readiness
./dev status --format json           # machine-readable inspection
./dev update default                 # fetch and safely fast-forward repositories
./dev stop                           # stop the whole development session
```

Use `./bootstrap --help` and `./dev --help` for progress, concurrency, and
other options; this README does not duplicate the CLI reference.

## Working in tmux

The session is named `xknx-dev`. `./dev start <profile>` creates and attaches
to it, or only attaches when it already exists; it never restarts existing
windows. Common controls are:

- `Ctrl-b w`: choose a window.
- `Ctrl-b d`: detach while leaving processes running.
- `Ctrl-C`: stop the foreground process in the selected pane.
- To restart one process, stop it with `Ctrl-C` and rerun that repository's
  normal development command in the same visible pane.
- `./dev start <profile>` or `tmux attach -t xknx-dev`: attach again.
- `./dev stop`: terminate the entire session.

Failed processes leave their window and shell visible for inspection.

## Configuration, wiring, and logs

Local settings live in ignored `.xknx-dev.toml`, initially copied from
`.xknx-dev.example.toml`. It selects the profile, Home Assistant port and
configuration directory, and hardware-free `knx.mode = "automatic"`. Real KNX
configuration references secure material by path; secrets are not copied into
the root file or logs.

Bootstrap installs `xknx`, `xknxproject`, `knx-telegram-store`, and
`knx-frontend` editably into `home-assistant-core/.venv`. Home Assistant skips
released copies of those packages, and status verifies every imported source
path points back to its root checkout.

There are intentionally two Home Assistant frontend trees. Root
`home-assistant-frontend/` follows current upstream for independent frontend
work. `knx-frontend/homeassistant-frontend/` remains at the commit pinned by
the KNX frontend and is never synchronized with the root checkout.

Full task output is stored in ignored `.state/logs/`. Start troubleshooting
with `./dev status --format json`, then inspect the named tmux window and the
corresponding log. Resolve the reported configuration, tool, repository, or
process issue and rerun the same bootstrap command; do not delete the whole
workspace to reset it.

Docker is optional for the default SQLite workflow. Status reports whether it
is installed and whether its daemon is reachable, with a platform-specific
installation link or start action. Bootstrap never installs or starts Docker.

## Git safety

`./dev update <profile>` fetches selected repositories. It changes a checkout
only when the current branch is the remote default branch, the index and work
tree are clean, there are no local-only commits, and the update is a strict
fast-forward.

| Local state after fetch | Result |
| --- | --- |
| Clean default branch, behind only | Fast-forward |
| Clean default branch, already current | Report only |
| Feature branch | Report only |
| Dirty index or worktree | Report only |
| Local-only commits or divergence | Report only |

The workspace never checks out or switches branches, stashes, rebases, resets,
performs a non-fast-forward merge, or force-updates. The KNX frontend's pinned
submodule is initialized when missing but never automatically upgraded.
