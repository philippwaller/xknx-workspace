# XKNX Development Workspace Design

## Purpose

`philippwaller/xknx-workspace` is a thin development workspace for contributors
working across the XKNX and Home Assistant KNX stack. It makes a fresh checkout
easy to set up, keeps every component as a normal independent Git repository,
and gives developers visible control over all long-running processes.

The workspace supports both newcomers who want a working environment quickly
and maintainers who work on coordinated backend, frontend, parser, persistence,
and documentation changes. It standardizes repository placement, setup,
dependency wiring, diagnostics, and process startup without prescribing an
editor, shell configuration, fork layout, or feature workflow.

## Goals

- Bootstrap the current main branches of the relevant repositories on macOS,
  Ubuntu, Debian, and Ubuntu/Debian under WSL 2.
- Reuse each project's own setup scripts and version files.
- Make local XKNX packages take precedence over Home Assistant's released
  requirements.
- Present a fast Docker-Compose-style progress UI with complete logs.
- Run native Python and Node development processes in visible, developer-owned
  terminal sessions.
- Allow intentional changes across repository boundaries while keeping each
  repository's commits, tests, and pull requests independent.
- Provide a repository-local Agent Skill that can bootstrap, start, inspect,
  and smoke-test the workspace through the same public CLI used by humans.

## Non-goals

- Pinning source repositories to a workspace-wide commit set.
- Replacing project-specific setup scripts, lockfiles, or contributor guides.
- Managing Git branches, stashes, forks, or uncommitted changes on behalf of a
  developer.
- Building a generic workflow engine, plugin system, package manager, terminal
  multiplexer, or persistent process daemon.
- Making Docker mandatory for the default development path.
- Supporting native Windows outside WSL 2.

## Repository Layout

The workspace repository is the root directory. Independent repositories are
normal nested Git checkouts directly beneath it and are ignored by the root
repository:

```text
xknx-workspace/
├── AGENTS.md
├── bootstrap
├── dev
├── pyproject.toml
├── uv.lock
├── .xknx-dev.example.toml
├── home-assistant-core/
├── home-assistant-frontend/
├── xknx/
├── xknxproject/
├── knx-telegram-store/
├── knx-frontend/
│   └── homeassistant-frontend/   # existing pinned build submodule
├── xknxtoolkit/
└── home-assistant.io/
```

The two Home Assistant frontend checkouts have different purposes:

- `home-assistant-frontend/` tracks the current upstream main branch and is used
  for independent Home Assistant frontend development.
- `knx-frontend/homeassistant-frontend/` remains the version pinned by
  `knx-frontend` and is its reproducible build input. The workspace never
  synchronizes these two checkouts automatically.

The `knx-integration` proxy repository is not cloned. It contains issue and
discussion metadata; the integration implementation lives in
`home-assistant-core`.

## Profiles

Profiles are fixed in code and additive:

- `default`: `home-assistant-core`, `xknx`, `xknxproject`,
  `knx-telegram-store`, and `knx-frontend`.
- `frontend`: `default` plus the root `home-assistant-frontend` checkout and
  development process.
- `toolkit`: `default` plus `xknxtoolkit` and its virtual KNX environment.
- `docs`: `default` plus `home-assistant.io` and the XKNX documentation process
  sourced from `xknx`.
- `all`: the union of all profiles.

`default` is used whenever no profile is supplied.

## Implementation Shape

The workspace has two layers:

1. A small POSIX shell entry point detects the operating system and ensures the
   minimum prerequisites needed to launch the workspace CLI.
2. A small Python CLI performs planning, setup, status collection, Git updates,
   smoke tests, and tmux orchestration.

The Python standard library owns argument parsing, concurrency, subprocesses,
paths, JSON, logging, and TOML reading. Rich is the only runtime dependency and
is used solely for interactive terminal rendering. There is no Click, Typer,
`mise`, workflow framework, or custom process manager. The first implementation
may remain in one Python module and should be split only when an actual boundary
becomes difficult to understand or test.

The public command surface is intentionally small:

```text
./bootstrap [profile]       Preview, configure, and set up the workspace
./dev start [profile]       Create or open the visible tmux session
./dev status                Report repository, dependency, and process health
./dev update [profile]      Fetch all repositories and perform safe fast-forwards
./dev stop                  Stop the development tmux session
```

Repeated bootstrap runs replace a dedicated resume command. `dev start` opens
an existing session instead of requiring a separate attach command. `dev
status` includes diagnostics instead of introducing a separate doctor command.

## Platform and Tool Policy

Supported systems are macOS, Ubuntu, Debian, and Ubuntu/Debian under WSL 2.
The bootstrap detects the platform before constructing an installation plan.

Git, `uv`, and `tmux` are checked before project setup. Missing tools are
installed automatically only when a supported package source exists for the
current operating system. Homebrew is used on macOS and APT on Ubuntu, Debian,
and supported WSL distributions. The exact command appears in the plan and the
log before execution.

NVM is the sole package-source exception. When NVM is missing, the bootstrap
resolves the current stable release from the official `nvm-sh/nvm` GitHub
releases, constructs the versioned official installer URL, displays the full
command, records the resolved version, and installs it after the normal plan
confirmation. It never executes `master` or `HEAD` as the installer source.

Existing tools are not replaced automatically:

- A compatible installed version is retained.
- An older or newer version is reported with the workspace expectation.
- A version below a required minimum blocks only dependent setup work.
- `--enforce-tool-versions` explicitly aligns tools for which the workspace has
  an exact expectation, including an intentional upgrade or downgrade.
- Git, `tmux`, and package-managed `uv` use minimum versions rather than exact
  cross-platform pins.

Docker is inspected but never installed automatically. A missing installation
produces an operating-system-specific installation link and marks optional
Docker-backed services unavailable. An installed but unreachable daemon
produces an informational message with the operating-system-specific start
action. Neither condition blocks the default SQLite-based workflow.

Dependency updates supported by Dependabot use normal manifests and lockfiles.
No Renovate installation or `mise` configuration is introduced.

## Configuration

Local settings live in ignored `.xknx-dev.toml`; the repository contains
`.xknx-dev.example.toml`. On first run the CLI proposes safe defaults:

```toml
profile = "default"

[home_assistant]
port = 8123
config_dir = "home-assistant-core/config"

[knx]
mode = "automatic"
```

The CLI creates the file after one confirmation. It asks for a value only when
a real local conflict exists, such as an occupied port, an existing Home
Assistant configuration directory, a non-writable location, or an explicitly
requested real KNX connection. Environment variables may override values for a
single invocation.

Secrets are not accepted as command arguments and are redacted from plans and
logs. Secure KNX material is referenced by local file path rather than copied
into the workspace configuration. A later invocation of `./dev status` shows
effective non-secret configuration and actionable conflicts.

## Bootstrap Plan and Execution

Before mutation, bootstrap displays one complete plan containing:

- detected platform and selected profile;
- missing tools and exact installation commands;
- repositories to clone or inspect;
- project setup scripts to execute;
- editable dependency wiring;
- configuration files and links to create;
- unavailable optional services.

Interactive execution asks once for confirmation. `--yes` accepts the displayed
plan for non-interactive onboarding. `--progress plain` is used automatically
without a TTY.

Bootstrap is idempotent. Every task inspects its expected result before acting,
completed work is reused, and a repeated run resumes by reevaluating the task
list. It does not need a persistent state database.

Repository cloning and independent project setup tasks may run concurrently.
The default limit is three jobs and may be changed with `--jobs`. A small,
hard-coded dependency sequence is sufficient:

```text
platform and tool checks
        ↓
repository clones/fetches (parallel)
        ↓
project-owned setup commands (up to --jobs in parallel)
        ↓
Home Assistant editable dependency wiring
        ↓
smoke test
```

On the first real failure, no new tasks are scheduled. Already running package
installations finish normally to avoid interrupting writes. Dependent tasks are
marked skipped and the first failure determines the exit status. `Ctrl-C`
forwards cancellation to running children and exits cleanly.

## Progress and Logging

The interactive presentation follows Docker Compose and BuildKit conventions:

```text
[+] Bootstrapping default 5/8
 ✔ System tools                 Ready                         2.1s
 ✔ Repositories                Fetched                       8.4s
 ⠼ Home Assistant Core         Running
   └ Installing requirements                                 1m42s
 ⠼ XKNX                        Running
   └ Creating virtual environment                              18s
 ✔ XKNX Project                Ready                         12.3s
 ⠼ KNX Telegram Store          Running
   └ Installing editable package                                9s
 ⠼ KNX Frontend                Running
   └ Installing Yarn dependencies                              48s
 ○ Local package wiring        Waiting
 ○ Smoke test                  Waiting
```

Each top-level task owns one stable row. A running task has one reusable detail
row; completed tasks collapse to a single summary. Full subprocess output is
written to per-task logs under ignored `.state/logs/`.

Supported modes mirror Docker terminology:

- `--progress auto`: interactive TTY when available, otherwise plain.
- `--progress tty`: force the Rich live display.
- `--progress plain`: append-only human-readable output.
- `--progress json`: JSON Lines events for agents and other tools.
- `--progress quiet`: errors and final summary only.

Verbose mode adds full subprocess output to plain output while preserving the
per-task logs.

Each log records the working directory, redacted command, start time, duration,
exit code, stdout, stderr, detected versions, and decisions. Logs never include
secret values.

## Git Update Semantics

`./dev update` always fetches the relevant remote because fetch updates
remote-tracking references without changing the working tree or current branch.

The current repository is fast-forwarded only when all of these conditions hold:

- the currently checked-out branch is the repository's main branch;
- the working tree and index are clean;
- the local branch has no unique commits;
- advancing to the freshly fetched remote branch is a fast-forward.

Feature branches, dirty worktrees, local-only commits, and diverged branches are
never changed. Their status is reported against the freshly fetched remote main
branch. The updater never checks out another branch, stashes changes, merges,
rebases, resets, or provides a general force option.

The `knx-frontend/homeassistant-frontend` submodule is initialized when needed
but is never automatically upgraded. Its version changes only through
`knx-frontend/script/upgrade-frontend`.

## Local Python Dependency Wiring

After Home Assistant's own setup creates `home-assistant-core/.venv`, bootstrap
installs these root checkouts editably into that environment:

- `xknx`
- `xknxproject`
- `knx-telegram-store` with the extras required by Home Assistant
- `knx-frontend`

Home Assistant starts with its existing `--skip-pip-packages` option for those
four distribution names. This prevents the integration manifest's released
requirements from replacing the editable development checkouts.

The status and smoke checks import each package with Home Assistant's Python and
verify that its resolved source path belongs to the expected root checkout.
These checks replace hand-maintained site-packages symlinks.

Node versions come from each repository's `.nvmrc`. NVM is explicitly sourced
inside every relevant subprocess, so it does not need to alter the invoking
developer's parent shell. Python versions come from each repository's
`.python-version` and `requires-python`; `uv` creates a separate `.venv` per
Python repository. Project-owned lockfiles remain authoritative.

## Process Ownership

Long-running native processes run in a named `xknx-dev` tmux session. The
developer can see live output, enter a window, send `Ctrl-C`, modify a command,
restart it, detach, reattach, or stop the session without asking an agent.

Named windows are created only when selected by the profile:

```text
overview
home-assistant
knx-frontend
home-assistant-frontend   # frontend/all
toolkit                   # toolkit/all
xknx-docs                 # docs/all
ha-docs                   # docs/all
```

Libraries without long-running development services do not receive empty tmux
windows. Optional databases and supporting services remain visible through
Docker Compose. Failed commands leave their window available for inspection.

`./dev start` creates and attaches to the session. If it already exists, the
command attaches without restarting anything. A non-interactive agent must not
leave an unseen detached session; it either opens a visible terminal surface or
returns the single start command to the developer.

## Smoke Tests

The default smoke test is hardware-independent and verifies:

- Home Assistant responds on its configured port;
- `xknx`, `xknxproject`, `knx_telegram_store`, and `knx_frontend` resolve to the
  root development checkouts;
- the KNX frontend build artifact is present and can be served;
- expected tmux windows are running or have an actionable exit status.

The toolkit smoke test additionally starts the virtual KNX router, connects the
Home Assistant KNX integration, sends a known test telegram through the stack,
and verifies its arrival in the telegram store. The experimental toolkit is
therefore first-class but not part of the default profile.

## Agent Guidance

The root `AGENTS.md` is a workspace map and routing policy rather than a copy of
project documentation. It contains:

- the workspace purpose and supported profiles;
- the real dependency relationships between the repositories;
- ownership guidance for deciding which repository should change;
- permission for focused cross-repository changes when a real contract or data
  flow requires them;
- a requirement to read each affected repository's local `AGENTS.md`, README,
  and relevant documentation before editing;
- workspace commands, source-of-truth boundaries, and Git safety rules;
- documentation locations for XKNX, Home Assistant Core, Home Assistant
  Frontend, and `home-assistant.io`.

Agents begin with the smallest repository scope. When an API, data model, build
artifact, dependency, or UI/backend contract crosses a boundary, they inspect
all direct producers and consumers and may change multiple repositories. Each
repository retains independent verification, commits, and pull requests.

## Bootstrap XKNX Workspace Skill

The repository ships an automatically discoverable skill at:

```text
.agents/skills/bootstrap-xknx-workspace/
├── SKILL.md
└── agents/openai.yaml
```

Its internal name is `bootstrap-xknx-workspace` and its display name is
`Bootstrap XKNX Workspace`. Its description targets requests to bootstrap,
start, inspect, or smoke-test this local multi-repository XKNX and Home Assistant
workspace.

The skill reads the root guidance, inspects `./dev status --format json`, invokes
the same public bootstrap and start commands a human uses, waits for readiness,
runs the selected smoke test, and reports URLs, profile, session, package paths,
and repository state. It defaults to `default`, recommends but never silently
selects `toolkit`, never updates repositories automatically, and never restarts
an existing process session.

The skill contains no duplicate setup, process, or repair implementation. If a
visible terminal cannot be opened, it returns the one command the developer
should run instead of starting hidden background processes.

## Test Strategy

Pull requests run fast tests for platform and WSL detection, version comparison,
profile selection, task dependencies, concurrency limits, Git state decisions,
progress modes, configuration redaction, failure handling, and resumption.

An integration test uses temporary local Git repositories and fake executables
to exercise cloning, fetching, idempotency, parallel scheduling, logging, and
failure behavior without installing Home Assistant.

A scheduled real workflow bootstraps the current upstream `default` profile on
macOS and Ubuntu. This detects incompatible changes in upstream main branches or
setup scripts. Debian and WSL share the APT execution path; their detection and
planning are covered by fast tests. A dedicated WSL CI runner is deferred until
the shared path proves insufficient.

## V1 Boundary

V1 delivers the complete `default` path first: platform checks, plan and
confirmation, clones, project setup, editable wiring, Docker-style progress,
logs, tmux start/status/stop, and the hardware-free smoke test.

The other fixed profiles are then added using the same data and execution path.
They do not introduce new orchestration abstractions. Toolkit telegram roundtrip
testing is implemented only inside the toolkit profile.

No plugin API, generic manifest language, custom daemon, background PID store,
automatic Git repair, or second CLI framework belongs in V1.

## Acceptance Criteria

- A new contributor on each supported platform can see one complete plan and
  bootstrap the default workspace with one confirmation.
- A repeated bootstrap performs no unnecessary setup and safely resumes after a
  previous failure.
- Independent setup tasks run with a default concurrency of three and stop
  scheduling new work after the first failure.
- Existing repositories, branches, remotes, working-tree changes, and compatible
  tools are preserved.
- Home Assistant imports all four local KNX packages from their root checkouts.
- The developer can inspect and control every long-running process in tmux.
- Default smoke testing requires neither Docker nor KNX hardware.
- Toolkit smoke testing proves a virtual telegram roundtrip and persistence.
- Human and agent workflows use the same CLI and receive machine-readable status
  without hidden processes.
- The root guidance enables deliberate multi-repository work without encouraging
  every feature to span repositories.
