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
running the same bootstrap command again. Environments are reused; the KNX
frontend runs its own idempotent bootstrap and build every time so old build
files cannot hide source or dependency changes.

The launchers require an existing Python 3.12+ and never synchronize or download
anything before confirmation, including for `--help` and daily `dev` commands.
They ignore inherited `UV_PROJECT_ENVIRONMENT` and `VIRTUAL_ENV`. If Python is too
old, they print the matching Homebrew/APT command or the official Python link.
Global minimum versions are Git 2.39.0, uv 0.8.17, and tmux 3.2. Older installed
tools block setup and remain untouched; update them explicitly. These are
minimums, not exact package-manager pins, even with `--enforce-tool-versions`.

A fresh run uses complete plain progress when Rich is unavailable. If the root
CLI environment is missing, the plan includes its locked setup after tool
installation and the normal confirmation, using the current Python without
downloading another interpreter. Later runs automatically use that environment
and its interactive renderer; there is no extra setup command or confirmation.
An existing root CLI environment is left untouched.

Frontend subprocesses select their project's Node through NVM and enable Yarn
through that Node installation's Corepack. The project declarations select
Yarn's version; no global Yarn installation or parent-shell change is needed.

Standalone status reports readiness but leaves smoke as `not-run`; the public
smoke path runs as part of bootstrap. Do not rerun bootstrap solely for an
unrequested smoke result because it may fetch or safely update repositories.
Status uses the profile configured in root `.xknx-dev.toml`, so align that file
with the intended profile before claiming its readiness.

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
- For panes other than Home Assistant, restart by stopping the process with
  `Ctrl-C` and rerunning that repository's normal command in the visible pane.
- Do not rerun the Home Assistant pane command; that bypasses the workspace's
  lifecycle identity. Inspect it without restarting, or, only after the
  developer chooses, restart the whole session with `./dev stop` and then
  `./dev start <profile>`. Never stop or restart it automatically.
- `./dev start <profile>` or `tmux attach -t xknx-dev`: attach again.
- `./dev stop`: terminate the entire session.

Failed processes leave their window and shell visible for inspection.

The `docs` and `all` profiles serve XKNX docs at
[localhost:4001](http://127.0.0.1:4001/) and Home Assistant docs at
[localhost:4000](http://127.0.0.1:4000/). Both addresses appear in status. Ruby
and Bundler are inspected separately in each docs directory so version-manager
shims can honor each `.ruby-version`; an incompatible environment blocks only
its own docs setup.

## Configuration, wiring, and logs

Local settings live in ignored `.xknx-dev.toml`, initially copied from
`.xknx-dev.example.toml`. It selects the profile, Home Assistant port and
configuration directory, and hardware-free `knx.mode = "automatic"`. Real KNX
configuration references secure material through `knx.secure_config_path`;
bootstrap validates that the local file is readable. It does not generate or
overwrite Home Assistant's KNX integration configuration. Configure that
integration explicitly in Home Assistant. Secrets are not copied into the root
file or logs.

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
