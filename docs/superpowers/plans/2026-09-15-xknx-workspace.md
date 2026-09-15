# XKNX Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reproducible, transparent, developer-controlled workspace that bootstraps the XKNX/Home Assistant KNX repositories, wires local packages into Home Assistant, and runs visible development processes.

**Architecture:** Keep the root deliberately small: two shell entry points, one stdlib-first Python CLI, root-local configuration, documentation, and normal nested Git repositories. Use fixed repository/profile tables and a few direct phase functions instead of a workflow framework. Rich renders interactive progress; tmux owns long-running processes; Git remains conservative and repository-local.

**Tech Stack:** POSIX shell, Python 3.12+, `uv`, Rich, pytest, Git, NVM/Node, tmux, optional Docker Compose, GitHub Actions, Dependabot.

**Spec:** [`docs/superpowers/specs/2026-09-15-xknx-workspace-design.md`](../specs/2026-09-15-xknx-workspace-design.md)

**Final review corrections:** The implementation uses an existing Python 3.12+
directly for both launchers, ignoring inherited environment selection. It never
synchronizes a CLI environment before confirmation or downloads Python
implicitly; fresh runs use plain output when Rich is unavailable. A missing
root CLI environment is created by an explicit displayed locked sync after
the normal confirmation; later runs automatically use Rich without re-exec.
This supersedes the early launcher/re-exec sketches below.
Global minimums are Git 2.39.0, uv 0.8.17, and tmux 3.2; older installed tools
are reported and retained, and package installs are verified against minimums.
The selected Node environment's Corepack enables project-selected Yarn before
frontend commands. KNX frontend bootstrap/build always reruns, including when
old artifacts exist. Ruby/Bundler checks run in each owning docs directory;
XKNX docs uses port 4001 and HA docs 4000. Real KNX `secure_config_path` validates
only a readable local reference: contributors configure the KNX integration in
Home Assistant explicitly; the workspace never generates or overwrites it.

## Global Constraints

- Preserve nested repositories as normal Git checkouts; do not add them as root submodules.
- Keep `.xknx-dev.toml` and `.xknx-dev.example.toml` in the repository root.
- Keep the implementation in `.workspace/xknx_workspace.py` until that file becomes measurably hard to navigate or test.
- Do not add Click, Typer, `mise`, a daemon, a generic DAG, a manifest language, or a second process manager.
- Never change a nested repository's feature branch, dirty worktree, local commits, or configured remote.
- Never upgrade `knx-frontend/homeassistant-frontend` except through `knx-frontend/script/upgrade-frontend`.
- Stop scheduling work after the first real failure; let already-running package-manager commands finish.
- Treat Docker as optional and never install it automatically.
- Use project-owned version files and setup scripts. Keep the duplicate root `home-assistant-frontend` checkout intentionally independent from the frontend pinned below `knx-frontend`.
- Commit root-repository changes only. Nested repositories keep independent commits and pull requests.

---

## Planned File Structure

```text
bootstrap                                      # stdlib-first setup launcher
dev                                            # thin launcher for day-to-day commands
.workspace/
├── pyproject.toml                             # Rich runtime + pytest development dependency
├── uv.lock                                    # Dependabot-managed CLI environment
├── xknx_workspace.py                          # complete V1 implementation
└── tests/
    ├── conftest.py                            # import and temporary-root helpers
    ├── test_workspace.py                      # fast unit tests
    └── test_integration.py                    # fake Git/process integration tests
.xknx-dev.example.toml                         # committed defaults
.xknx-dev.toml                                 # ignored local settings, created by bootstrap
README.md                                      # human onboarding and command reference
AGENTS.md                                      # repository map and cross-repository routing policy
.agents/skills/bootstrap-xknx-workspace/
├── SKILL.md                                   # agent onboarding workflow
└── agents/openai.yaml                         # skill display metadata
.github/
├── dependabot.yml                             # uv and Actions dependency updates
└── workflows/
    ├── ci.yml                                 # fast unit/integration tests
    └── upstream-smoke.yml                     # scheduled real default bootstrap
```

Generated and ignored state is limited to:

```text
.state/logs/                                   # one log per executed task
.xknx-dev.toml                                 # local non-secret settings
home-assistant-core/                           # nested repositories at root
home-assistant-frontend/
xknx/
xknxproject/
knx-telegram-store/
knx-frontend/
xknxtoolkit/
home-assistant.io/
```

The first implementation exposes these Python seams for tests; they are ordinary functions and small value objects, not a public extension API:

```python
detect_platform(os_release: str | None = None, uname: str | None = None) -> str
load_settings(root: Path, environ: Mapping[str, str]) -> dict[str, object]
repositories_for(profile: str) -> tuple[str, ...]
inspect_tool(name: str, expectation: str | None = None) -> dict[str, object]
build_bootstrap_plan(root: Path, profile: str, settings: dict[str, object]) -> list[dict[str, object]]
git_state(path: Path) -> dict[str, object]
safe_update(path: Path, runner: Callable[..., CompletedProcess[str]]) -> dict[str, object]
run_jobs(jobs: Sequence[Job], limit: int, progress: Progress) -> list[Result]
start_tmux(root: Path, profile: str, settings: dict[str, object]) -> int
collect_status(root: Path, settings: dict[str, object]) -> dict[str, object]
smoke(root: Path, profile: str, settings: dict[str, object]) -> list[Check]
main(argv: Sequence[str] | None = None) -> int
```

## Task 1: Establish the Root CLI and Configuration Contract

**Files:**

- Create: `.workspace/pyproject.toml`
- Create: `.workspace/tests/conftest.py`
- Create: `.workspace/tests/test_workspace.py`
- Create: `.workspace/xknx_workspace.py`
- Create: `.xknx-dev.example.toml`
- Create: `bootstrap`
- Create: `dev`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing tests for profiles, config defaults, environment overrides, and argument parsing**

```python
# .workspace/tests/test_workspace.py
from pathlib import Path

import pytest

import xknx_workspace as ws


def test_default_profile_contains_the_complete_ha_knx_stack() -> None:
    assert ws.repositories_for("default") == (
        "home-assistant-core",
        "xknx",
        "xknxproject",
        "knx-telegram-store",
        "knx-frontend",
    )


def test_all_profile_has_each_repository_once() -> None:
    repositories = ws.repositories_for("all")
    assert repositories == tuple(dict.fromkeys(repositories))
    assert {"home-assistant-frontend", "xknxtoolkit", "home-assistant.io"} <= set(repositories)


def test_load_settings_uses_root_defaults_and_environment_override(tmp_path: Path) -> None:
    settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": "9123"})
    assert settings == {
        "profile": "default",
        "home_assistant": {"port": 9123, "config_dir": "home-assistant-core/config"},
        "knx": {"mode": "automatic"},
    }


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown profile"):
        ws.repositories_for("everything")
```

- [ ] **Step 2: Add the minimal uv project and test import path**

```toml
# .workspace/pyproject.toml
[project]
name = "xknx-workspace"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["rich>=14,<15"]

[dependency-groups]
dev = ["pytest>=8,<9"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

```python
# .workspace/tests/conftest.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
```

- [ ] **Step 3: Run the focused tests and confirm they fail because the module does not exist**

Run: `uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'xknx_workspace'`.

- [ ] **Step 4: Implement fixed repository/profile data, TOML loading, overrides, and the small command parser**

Use plain dictionaries because the catalog is fixed and has no plugin contract:

```python
# .workspace/xknx_workspace.py
from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".xknx-dev.toml"

REPOSITORIES = {
    "home-assistant-core": "https://github.com/home-assistant/core.git",
    "home-assistant-frontend": "https://github.com/home-assistant/frontend.git",
    "xknx": "https://github.com/XKNX/xknx.git",
    "xknxproject": "https://github.com/XKNX/xknxproject.git",
    "knx-telegram-store": "https://github.com/XKNX/knx-telegram-store.git",
    "knx-frontend": "https://github.com/XKNX/knx-frontend.git",
    "xknxtoolkit": "https://github.com/XKNX/xknxtoolkit.git",
    "home-assistant.io": "https://github.com/home-assistant/home-assistant.io.git",
}

PROFILES = {
    "default": ("home-assistant-core", "xknx", "xknxproject", "knx-telegram-store", "knx-frontend"),
    "frontend": ("home-assistant-core", "xknx", "xknxproject", "knx-telegram-store", "knx-frontend", "home-assistant-frontend"),
    "toolkit": ("home-assistant-core", "xknx", "xknxproject", "knx-telegram-store", "knx-frontend", "xknxtoolkit"),
    "docs": ("home-assistant-core", "xknx", "xknxproject", "knx-telegram-store", "knx-frontend", "home-assistant.io"),
}
PROFILES["all"] = tuple(dict.fromkeys(sum(PROFILES.values(), ())))

DEFAULTS = {
    "profile": "default",
    "home_assistant": {"port": 8123, "config_dir": "home-assistant-core/config"},
    "knx": {"mode": "automatic"},
}


def repositories_for(profile: str) -> tuple[str, ...]:
    try:
        return PROFILES[profile]
    except KeyError as error:
        raise ValueError(f"unknown profile: {profile}") from error


def load_settings(root: Path = ROOT, environ: Mapping[str, str] = os.environ) -> dict[str, object]:
    settings = {"profile": DEFAULTS["profile"], "home_assistant": dict(DEFAULTS["home_assistant"]), "knx": dict(DEFAULTS["knx"])}
    path = root / ".xknx-dev.toml"
    if path.exists():
        loaded = tomllib.loads(path.read_text())
        settings["profile"] = loaded.get("profile", settings["profile"])
        for section in ("home_assistant", "knx"):
            settings[section].update(loaded.get(section, {}))
    if port := environ.get("XKNX_HA_PORT"):
        settings["home_assistant"]["port"] = int(port)
    return settings
```

The parser must expose only:

```text
bootstrap [profile] [--yes] [--jobs N] [--progress auto|tty|plain|json|quiet]
                    [--verbose] [--enforce-tool-versions]
dev start [profile]
dev status [--format human|json]
dev update [profile] [--progress auto|tty|plain|json|quiet]
dev stop
```

Use `argparse` subparsers internally. Return integer exit codes from `main()` and call `raise SystemExit(main())` only under `if __name__ == "__main__"`.

- [ ] **Step 5: Add the example config and ignore only local/generated state**

```toml
# .xknx-dev.example.toml
profile = "default"

[home_assistant]
port = 8123
config_dir = "home-assistant-core/config"

[knx]
mode = "automatic"
```

Add these entries to the existing root whitelist rules without weakening the deny-all protection:

```gitignore
!/.xknx-dev.example.toml
/.xknx-dev.toml
/.state/
```

- [ ] **Step 6: Add two tiny launchers**

`bootstrap` must use `uv` when present, otherwise use `python3` for the stdlib-only planning phase. If neither exists, it prints the OS-specific one-shot package-manager command for Python and exits without changing the system.

```sh
#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v uv >/dev/null 2>&1; then
  exec uv run --project "$root/.workspace" --locked python "$root/.workspace/xknx_workspace.py" bootstrap "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$root/.workspace/xknx_workspace.py" bootstrap "$@"
fi
printf '%s\n' "Python 3 is required to display the bootstrap plan."
exit 2
```

`dev` requires the completed `uv` bootstrap and never installs anything itself:

```sh
#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v uv >/dev/null 2>&1 || { printf '%s\n' 'Run ./bootstrap first.' >&2; exit 2; }
exec uv run --project "$root/.workspace" --locked python "$root/.workspace/xknx_workspace.py" dev "$@"
```

Make both executable and generate the lockfile:

Run: `chmod +x bootstrap dev && uv lock --project .workspace`

- [ ] **Step 7: Run the tests and syntax checks**

Run: `uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py -q`

Expected: all Task 1 tests pass.

Run: `sh -n bootstrap dev && ./dev --help`

Expected: both scripts are valid and the help lists `start`, `status`, `update`, and `stop`.

- [ ] **Step 8: Commit the root contract**

```sh
git add .gitignore .workspace .xknx-dev.example.toml bootstrap dev
git commit -m "feat: add workspace CLI foundation"
```

## Task 2: Plan Supported Platforms and Prerequisites Safely

**Files:**

- Modify: `.workspace/xknx_workspace.py`
- Modify: `.workspace/tests/test_workspace.py`

- [ ] **Step 1: Add failing table-driven tests for platform detection and installation actions**

```python
@pytest.mark.parametrize(
    ("uname", "os_release", "expected"),
    [
        ("Darwin", "", "macos"),
        ("Linux", 'ID=ubuntu\nVERSION_ID="24.04"', "ubuntu"),
        ("Linux", 'ID=debian\nVERSION_ID="13"', "debian"),
        ("Linux microsoft-standard-WSL2", 'ID=ubuntu\n', "wsl-ubuntu"),
    ],
)
def test_detect_platform(uname: str, os_release: str, expected: str) -> None:
    assert ws.detect_platform(os_release=os_release, uname=uname) == expected


def test_macos_missing_tools_use_one_brew_command() -> None:
    actions = ws.missing_tool_actions("macos", {"git": None, "uv": None, "tmux": None})
    assert [action["command"] for action in actions] == [["brew", "install", "git", "uv", "tmux"]]


def test_apt_does_not_claim_an_unavailable_uv_package() -> None:
    actions = ws.missing_tool_actions(
        "ubuntu", {"git": None, "uv": None, "tmux": None}, apt_packages={"git", "tmux"}
    )
    assert ["sudo", "apt-get", "install", "-y", "git", "tmux"] in [a["command"] for a in actions]
    assert any(a["kind"] == "manual" and a["tool"] == "uv" for a in actions)


def test_existing_tool_is_never_replaced_without_enforcement() -> None:
    decision = ws.version_decision("nvm", installed="0.39.7", expected="0.40.3", enforce=False)
    assert decision == {"action": "keep", "relation": "older", "installed": "0.39.7", "expected": "0.40.3"}
```

- [ ] **Step 2: Run the focused tests and confirm missing functions fail**

Run: `uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py -q`

Expected: failures identify `detect_platform`, `missing_tool_actions`, and `version_decision` as missing.

- [ ] **Step 3: Implement platform detection, semantic numeric version comparison, and package-source planning**

Use `platform.system()`, `/etc/os-release`, `shutil.which`, and `subprocess.run`; do not introduce a distro library. Parse versions into integer tuples after stripping a leading `v`. Supported actions are fixed:

```python
PACKAGE_COMMANDS = {
    "macos": lambda names: ["brew", "install", *names],
    "ubuntu": lambda names: ["sudo", "apt-get", "install", "-y", *names],
    "debian": lambda names: ["sudo", "apt-get", "install", "-y", *names],
    "wsl-ubuntu": lambda names: ["sudo", "apt-get", "install", "-y", *names],
    "wsl-debian": lambda names: ["sudo", "apt-get", "install", "-y", *names],
}

DOCKER_LINKS = {
    "macos": "https://docs.docker.com/desktop/setup/install/mac-install/",
    "ubuntu": "https://docs.docker.com/engine/install/ubuntu/",
    "debian": "https://docs.docker.com/engine/install/debian/",
    "wsl-ubuntu": "https://docs.docker.com/desktop/features/wsl/",
    "wsl-debian": "https://docs.docker.com/desktop/features/wsl/",
}
```

For APT, run `apt-cache show <name>` during planning and include only packages that actually exist. Missing package-source tools produce a manual action with the official project link; they are not piped through a downloaded installer.

Git, `uv`, and `tmux` are global prerequisites. NVM is required by the
Node-backed repositories. The `docs` and `all` profiles additionally check
Ruby and Bundler against `xknx/docs/.ruby-version` and
`home-assistant.io/.ruby-version`. Homebrew/APT may install them only when the
available package satisfies the selected repositories; otherwise show the
official Ruby installation link and stop only the affected docs setup. Do not
use either repository's `mise` configuration.

- [ ] **Step 4: Add failing tests for stable NVM resolution and Docker diagnostics**

```python
def test_nvm_installer_is_versioned_official_source() -> None:
    assert ws.nvm_install_action("v0.40.3") == {
        "tool": "nvm",
        "version": "0.40.3",
        "command": [
            "sh",
            "-c",
            "curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | PROFILE=/dev/null bash",
        ],
    }


def test_missing_docker_is_warning_not_failure() -> None:
    result = ws.docker_status("macos", executable=None, daemon_reachable=False)
    assert result["level"] == "warning"
    assert result["required"] is False
    assert result["link"].startswith("https://docs.docker.com/")
```

- [ ] **Step 5: Resolve NVM only from the official latest release and source it per subprocess**

Fetch `https://api.github.com/repos/nvm-sh/nvm/releases/latest` with `urllib.request`, require a tag matching `v<digits>.<digits>.<digits>`, and construct the exact raw URL from that tag. Do not accept a redirect to `master` or `HEAD`.

Add one helper for every Node command:

```python
def nvm_shell(command: list[str]) -> list[str]:
    quoted = shlex.join(command)
    return [
        "bash",
        "-lc",
        'export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"; '
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
        f"nvm install --silent && nvm use --silent && {quoted}",
    ]
```

This deliberately changes only the child shell. It neither edits the user's shell profile nor expects `nvm use` to persist in the parent shell.

- [ ] **Step 6: Put all prerequisite mutations behind the complete bootstrap plan and one confirmation**

The stdlib-only CLI phase may render plain text before Rich is installed. Its order is:

1. inspect platform, existing tools, Docker, config, and repositories;
2. resolve NVM release when missing;
3. print every planned command and warning;
4. ask once, unless `--yes`;
5. execute package-source actions;
6. if `uv` was newly installed, re-exec the same script through `uv run --locked` with an internal environment marker carrying the already-confirmed plan digest.

Reject the marker if its SHA-256 digest does not match the recomputed plan. This prevents the re-exec from silently executing a different plan without asking again.

- [ ] **Step 7: Run tests**

Run: `uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py -q`

Expected: platform, version, package-source, NVM, and Docker tests pass.

- [ ] **Step 8: Commit prerequisite planning**

```sh
git add .workspace
git commit -m "feat: plan workspace prerequisites safely"
```

## Task 3: Clone, Inspect, and Conservatively Update Repositories

**Files:**

- Modify: `.workspace/xknx_workspace.py`
- Modify: `.workspace/tests/test_workspace.py`
- Create: `.workspace/tests/test_integration.py`

- [ ] **Step 1: Write failing unit tests for Git decisions**

```python
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"on_default": True, "clean": True, "ahead": 0, "behind": 2, "diverged": False}, "fast-forward"),
        ({"on_default": False, "clean": True, "ahead": 0, "behind": 2, "diverged": False}, "report-only"),
        ({"on_default": True, "clean": False, "ahead": 0, "behind": 2, "diverged": False}, "report-only"),
        ({"on_default": True, "clean": True, "ahead": 1, "behind": 0, "diverged": False}, "report-only"),
        ({"on_default": True, "clean": True, "ahead": 1, "behind": 1, "diverged": True}, "report-only"),
    ],
)
def test_update_decision_never_repairs_developer_git_state(state: dict[str, object], expected: str) -> None:
    assert ws.update_decision(state) == expected
```

- [ ] **Step 2: Add a failing integration test using only temporary local Git repositories**

Create a bare `origin`, clone it into `upstream` and `workspace/xknx`, advance origin once, and assert:

```python
result = ws.safe_update(workspace / "xknx", run)
assert result["action"] == "fast-forwarded"
assert result["behind"] == 0
```

Then create an uncommitted file, advance origin again, rerun, and assert the local HEAD and file remain unchanged while `behind == 1` is reported.

- [ ] **Step 3: Run tests and confirm they fail on missing Git functions**

Run: `uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py .workspace/tests/test_integration.py -q`

Expected: failures identify missing update behavior.

- [ ] **Step 4: Implement clone and fetch with remote-default-branch discovery**

Clone without `--branch`, so each upstream's symbolic `HEAD` selects its current default branch. For existing repositories:

```text
git -C <path> fetch origin --prune
git -C <path> symbolic-ref --short refs/remotes/origin/HEAD
git -C <path> status --porcelain=v1
git -C <path> rev-list --left-right --count HEAD...refs/remotes/origin/HEAD
git -C <path> merge-base --is-ancestor HEAD refs/remotes/origin/HEAD
```

Only when the current branch equals the leaf of `origin/HEAD`, the worktree/index are clean, `ahead == 0`, and the ancestry check succeeds, run:

```text
git -C <path> merge --ff-only refs/remotes/origin/HEAD
```

Never run checkout, switch, stash, pull, rebase, reset, merge without `--ff-only`, or force operations. If an existing path is not a Git repository or its `origin` differs from the catalog, report a conflict and stop before changing it.

- [ ] **Step 5: Initialize only the KNX frontend's existing pinned submodule**

After cloning `knx-frontend`, run this only if its pinned checkout is missing:

```text
git -C knx-frontend submodule update --init homeassistant-frontend
```

Do not add recursive updates and do not invoke `script/upgrade-frontend` from bootstrap or update.

- [ ] **Step 6: Report branch, dirty state, ahead, behind, and divergence in every progress mode**

The human summary for each repository must fit on one stable row, for example:

```text
✔ xknx  main  clean  ↑0 ↓2  fetched
! knx-frontend  feature/colors  dirty  ↑3 ↓1  unchanged
```

JSON status uses stable keys: `path`, `branch`, `default_branch`, `dirty`, `ahead`, `behind`, `diverged`, `action`, and `error`.

- [ ] **Step 7: Run the Git tests**

Run: `uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py .workspace/tests/test_integration.py -q`

Expected: clean default branches fast-forward; feature, dirty, ahead, and diverged states remain untouched.

- [ ] **Step 8: Commit repository management**

```sh
git add .workspace
git commit -m "feat: manage workspace repositories conservatively"
```

## Task 4: Execute Bootstrap Work with Transparent Progress and Logs

**Files:**

- Modify: `.workspace/xknx_workspace.py`
- Modify: `.workspace/tests/test_workspace.py`
- Modify: `.workspace/tests/test_integration.py`

- [ ] **Step 1: Write failing scheduler tests for the concurrency cap and first-error behavior**

```python
def test_scheduler_caps_parallel_jobs_and_stops_after_first_failure(tmp_path: Path) -> None:
    started: list[str] = []
    active = 0
    maximum = 0

    async def run(job: ws.Job) -> ws.Result:
        nonlocal active, maximum
        started.append(job.name)
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1
        code = 1 if job.name == "two" else 0
        return ws.Result(name=job.name, returncode=code, duration=0.02)

    jobs = [
        ws.Job("one", tmp_path, (["one"],)),
        ws.Job("two", tmp_path, (["two"],)),
        ws.Job("three", tmp_path, (["three"],)),
        ws.Job("four", tmp_path, (["four"],)),
    ]
    results = asyncio.run(ws.run_jobs(jobs, limit=2, progress=ws.Progress("quiet"), runner=run))
    assert maximum == 2
    assert "four" not in started
    assert next(result for result in results if result.name == "four").status == "skipped"
```

`Job` stores only `name`, `cwd`, and a tuple of argv lists to run sequentially.
Keep `Job`, `Result`, and `Progress` as the only small orchestration objects. Do
not introduce task subclasses or a general dependency graph.

- [ ] **Step 2: Add failing tests for redaction, durable logs, and output modes**

```python
def test_log_redacts_secret_values(tmp_path: Path) -> None:
    ws.write_task_log(
        tmp_path,
        "example",
        command=["tool", "--token", "secret-value"],
        stdout="connected with secret-value",
        stderr="",
        returncode=0,
        secrets={"secret-value"},
    )
    content = next(tmp_path.glob("*.log")).read_text()
    assert "secret-value" not in content
    assert "***" in content


def test_json_progress_is_json_lines(capsys: pytest.CaptureFixture[str]) -> None:
    ws.Progress("json").emit("xknx", "running", "Installing")
    assert json.loads(capsys.readouterr().out) == {"task": "xknx", "status": "running", "detail": "Installing"}
```

- [ ] **Step 3: Run the tests and verify scheduler/logging failures**

Run: `uv run --project .workspace --group dev pytest .workspace/tests -q`

Expected: the new tests fail before scheduler and logging code exists.

- [ ] **Step 4: Implement the phase runner with a hard-coded sequence**

Use `asyncio.create_subprocess_exec` and a queue of at most `--jobs` workers. The phase functions remain explicit:

```python
async def bootstrap_workspace(plan: dict[str, object], progress: Progress) -> int:
    if not await run_tool_actions(plan["tool_actions"], progress):
        return 1
    if not await run_jobs(plan["repository_jobs"], plan["jobs"], progress):
        return 1
    if not await run_jobs(plan["project_setup_jobs"], plan["jobs"], progress):
        return 1
    if not await wire_home_assistant(plan, progress):
        return 1
    return 0 if await smoke_default(plan, progress) else 1
```

Do not encode dependencies as configuration. A phase starts only after the preceding phase succeeded. Within a phase, workers stop taking new queue entries once the shared first-error flag is set. `KeyboardInterrupt` and cancellation send `SIGINT`, then `terminate()` after a short grace period, and finally return exit code 130.

- [ ] **Step 5: Implement one progress adapter with five modes**

- `auto`: `tty` only when stdout is a TTY and Rich imports successfully; otherwise `plain`.
- `tty`: one Rich `Live` table, stable task rows, and one mutable detail line for each running task.
- `plain`: append-only start/detail/result lines.
- `json`: one JSON object per event, no ANSI.
- `quiet`: first error and final summary only.

Rich must be imported inside the `tty` renderer, allowing the initial stdlib-only tool plan to run before `uv` is installed.

- [ ] **Step 6: Write one structured text log per task**

Use `.state/logs/<UTC timestamp>-<safe task name>.log`. Include cwd, redacted argv, start/end/duration, return code, stdout, stderr, detected versions, and decisions. Redact exact values from environment variable names ending in `TOKEN`, `PASSWORD`, `SECRET`, or `KEY`, plus explicitly registered values. Do not write the entire environment.

- [ ] **Step 7: Add an integration test with fake executables**

Create executable test scripts in a temporary `bin` directory that append start/end records to a file, sleep briefly, and return configured exit codes. Prepend only that directory to the subprocess `PATH`. Assert:

- no more than three jobs overlap by default;
- task logs exist for started jobs;
- jobs not yet started after the first failure are skipped;
- a second run skips artifacts whose expected result already exists.

- [ ] **Step 8: Run all workspace tests**

Run: `uv run --project .workspace --group dev pytest .workspace/tests -q`

Expected: scheduler, failure, resume, log, and output-mode tests pass.

- [ ] **Step 9: Commit execution and UI behavior**

```sh
git add .workspace
git commit -m "feat: add transparent bootstrap execution"
```

## Task 5: Configure Projects and Wire Local Packages into Home Assistant

**Files:**

- Modify: `.workspace/xknx_workspace.py`
- Modify: `.workspace/tests/test_workspace.py`
- Modify: `.workspace/tests/test_integration.py`

- [ ] **Step 1: Write failing tests for fixed project setup commands**

```python
def test_default_setup_uses_project_owned_commands() -> None:
    jobs = {job.name: job.commands for job in ws.project_setup_jobs(Path("/workspace"), "default")}
    assert jobs["Home Assistant Core"] == (["script/setup"],)
    assert jobs["KNX Frontend"] == (ws.nvm_shell(["script/bootstrap"]),)
    assert jobs["XKNX"] == (["uv", "sync", "--group", "dev", "--locked"],)
    assert jobs["XKNX Project"] == (
        ["uv", "venv"],
        ["uv", "pip", "install", "--python", ".venv/bin/python", "-e", ".", "-r", "requirements_testing.txt"],
    )
    assert jobs["KNX Telegram Store"] == (
        ["uv", "venv"],
        ["uv", "pip", "install", "--python", ".venv/bin/python", "-e", ".[dev,sqlite,postgres]"],
    )


def test_frontend_profile_keeps_root_frontend_independent() -> None:
    setup_jobs = ws.project_setup_jobs(Path("/workspace"), "frontend")
    jobs = {job.cwd.name: job.commands for job in setup_jobs}
    assert jobs["home-assistant-frontend"] == (ws.nvm_shell(["script/setup"]),)
    assert all("knx-frontend/homeassistant-frontend" not in str(job.cwd) for job in setup_jobs)
```

These assertions describe the current upstream commands. During implementation,
confirm them by reading each freshly cloned repository's `README`, version
files, lockfiles, and setup scripts. Keep one explicit adapter entry per known
repository. If upstream changed after this plan was written, update the test and
adapter together to its documented current command; do not add file-detection
heuristics or create a lockfile in a child repository that does not own one.

- [ ] **Step 2: Write failing tests for the exact Home Assistant editable-install command**

```python
def test_home_assistant_wiring_installs_all_local_distributions(tmp_path: Path) -> None:
    command = ws.home_assistant_wiring_command(tmp_path)
    assert command[:4] == ["uv", "pip", "install", "--python"]
    assert command[4] == str(tmp_path / "home-assistant-core/.venv/bin/python")
    assert "-e" in command
    assert str(tmp_path / "xknx") in command
    assert str(tmp_path / "xknxproject") in command
    assert str(tmp_path / "knx-frontend") in command
    assert f"{tmp_path / 'knx-telegram-store'}[sqlite,postgres]" in command


def test_home_assistant_start_skips_released_knx_packages(tmp_path: Path) -> None:
    command = ws.home_assistant_command(tmp_path, {"port": 8123, "config_dir": "home-assistant-core/config"})
    assert "--skip-pip-packages" in command
    assert command[command.index("--skip-pip-packages") + 1] == "xknx,xknxproject,knx-frontend,knx-telegram-store"
```

- [ ] **Step 3: Run the tests and verify setup/wiring failures**

Run: `uv run --project .workspace --group dev pytest .workspace/tests -q`

Expected: project command and wiring tests fail before adapters exist.

- [ ] **Step 4: Implement one explicit setup adapter table**

The table contains a cwd and argv builder per selected repository. XKNX uses
its checked-in `uv.lock`. XKNX Project and KNX Telegram Store get their own
`.venv` via the exact commands tested above, without creating new lockfiles in
those child repositories. Home Assistant and frontend repositories use their
own scripts. Every Node command goes through `nvm_shell()`.

For the docs and toolkit profiles, add only these fixed long-running commands after verifying the checked-out project entry points:

```text
xknxtoolkit:              uv run python -m knx_gui.main
home-assistant.io:        bundle exec rake preview
XKNX docs:                bundle exec jekyll serve       (cwd: xknx/docs)
home-assistant-frontend:  script/develop
knx-frontend:             script/develop
```

The setup action for XKNX docs is `bundle install` in `xknx/docs`. The setup
actions for Home Assistant docs are `bundle install` and
`nvm_shell(["npm", "ci"])` in `home-assistant.io`. Do not invent a workspace
docs runner and do not invoke the checked-in `.mise.toml`.

- [ ] **Step 5: Create `.xknx-dev.toml` only after the plan confirmation**

Copy the committed example content when no local file exists. Before writing, validate:

- configured port is in `1..65535` and is not occupied;
- config directory is inside the workspace unless the user explicitly selected another writable path;
- an existing HA config directory is reused only after showing that decision;
- `knx.mode = "automatic"` stays hardware-free;
- real KNX mode references secure material by path and never copies secrets into TOML.

Ask only for an actual conflict. A non-interactive `--yes` invocation with a conflict must fail with the precise setting that needs an explicit override.

- [ ] **Step 6: Wire local packages after Home Assistant setup**

Invoke the HA virtual environment's interpreter and install these exact editable targets in one command:

```text
uv pip install --python home-assistant-core/.venv/bin/python \
  -e ./xknx \
  -e ./xknxproject \
  -e './knx-telegram-store[sqlite,postgres]' \
  -e ./knx-frontend
```

Resolve all paths to absolute paths before execution. Never edit Home Assistant's integration manifest or hand-create site-packages symlinks.

- [ ] **Step 7: Verify imported source paths through Home Assistant's Python**

Run one Python command in the HA environment that imports:

```python
import knx_frontend, knx_telegram_store, xknx, xknxproject
```

Serialize each module's resolved `__file__` to JSON and require it to be below its corresponding root checkout. Include these paths in human and JSON status output.

- [ ] **Step 8: Run tests and a plan-only manual check**

Run: `uv run --project .workspace --group dev pytest .workspace/tests -q`

Expected: all tests pass.

Run: `./bootstrap default --progress plain` and answer `n` at the confirmation.

Expected: a complete non-mutating plan lists tools, five repositories, setup commands, config creation, editable wiring, Docker warning/info, and smoke checks.

- [ ] **Step 9: Commit project setup and wiring**

```sh
git add .workspace
git commit -m "feat: set up projects and wire local KNX packages"
```

## Task 6: Give Developers Visible Process Control with tmux

**Files:**

- Modify: `.workspace/xknx_workspace.py`
- Modify: `.workspace/tests/test_workspace.py`
- Modify: `.workspace/tests/test_integration.py`

- [ ] **Step 1: Write failing tests for profile-specific tmux windows**

```python
@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("default", ("overview", "home-assistant", "knx-frontend")),
        ("frontend", ("overview", "home-assistant", "knx-frontend", "home-assistant-frontend")),
        ("toolkit", ("overview", "home-assistant", "knx-frontend", "toolkit")),
        ("docs", ("overview", "home-assistant", "knx-frontend", "xknx-docs", "ha-docs")),
        ("all", ("overview", "home-assistant", "knx-frontend", "home-assistant-frontend", "toolkit", "xknx-docs", "ha-docs")),
    ],
)
def test_tmux_windows(profile: str, expected: tuple[str, ...]) -> None:
    assert tuple(ws.tmux_windows(profile)) == expected


def test_non_tty_start_does_not_create_hidden_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(ws.sys.stdout, "isatty", lambda: False)
    assert ws.start_tmux_command_or_message("default")["action"] == "print-command"
```

- [ ] **Step 2: Add failing tests for existing-session and stop behavior using a fake tmux binary**

The fake records argv. Assert:

- `dev start` calls `attach-session -t xknx-dev` and creates no windows when the session already exists;
- a new session creates exactly the selected windows;
- `dev stop` calls `kill-session -t xknx-dev` only when it exists;
- no process is started with `nohup`, `&`, or a workspace PID file.

- [ ] **Step 3: Run the tests and confirm tmux behavior is missing**

Run: `uv run --project .workspace --group dev pytest .workspace/tests -q`

Expected: tmux tests fail.

- [ ] **Step 4: Implement the named `xknx-dev` session with direct commands**

Create the first `overview` window, then one window per profile process. Each pane begins in the owning repository and runs a visible shell command. Build commands with `shlex.join`; pass them to tmux as one shell string only at the tmux boundary.

The Home Assistant pane uses:

```text
home-assistant-core/.venv/bin/hass \
  --config <configured path> \
  --skip-pip-packages xknx,xknxproject,knx-frontend,knx-telegram-store
```

The frontend, toolkit, and docs pane commands come from Task 5's fixed adapter table. A failed process leaves its shell/pane visible by ending with a concise exit message and `exec "$SHELL" -l` only for interactive developer starts.

- [ ] **Step 5: Make start attach and status inspect without taking ownership away**

- If `xknx-dev` exists, attach without restart.
- If both stdin and stdout are interactive, create then attach.
- If non-interactive, print exactly `./dev start <profile>` and do not create a detached session.
- `dev status` reads `tmux list-windows -t xknx-dev -F ...` and pane state; it does not restart failed panes.
- `dev stop` sends a normal tmux session termination and clearly states which session stopped.

- [ ] **Step 6: Run the tests**

Run: `uv run --project .workspace --group dev pytest .workspace/tests -q`

Expected: tmux command, attach, visibility, and stop tests pass.

- [ ] **Step 7: Commit tmux orchestration**

```sh
git add .workspace
git commit -m "feat: run development processes in tmux"
```

## Task 7: Add Status and Hardware-Free Smoke Checks

**Files:**

- Modify: `.workspace/xknx_workspace.py`
- Modify: `.workspace/tests/test_workspace.py`
- Modify: `.workspace/tests/test_integration.py`

- [ ] **Step 1: Write failing tests for the stable JSON status schema**

```python
def test_status_json_has_stable_top_level_keys(tmp_path: Path) -> None:
    status = ws.collect_status(tmp_path, ws.load_settings(tmp_path, {}))
    assert set(status) == {"workspace", "profile", "platform", "configuration", "tools", "docker", "repositories", "packages", "processes", "smoke"}


def test_status_never_exposes_secret_configuration(tmp_path: Path) -> None:
    (tmp_path / ".xknx-dev.toml").write_text('[knx]\nmode="real"\npassword="do-not-print"\n')
    encoded = json.dumps(ws.collect_status(tmp_path, ws.load_settings(tmp_path, {})))
    assert "do-not-print" not in encoded
```

- [ ] **Step 2: Write failing tests for default smoke checks with local HTTP and fake module paths**

Use a temporary HTTP server bound to an ephemeral port and a fake HA Python executable that prints the four expected module paths. Assert all default checks pass without Docker or KNX hardware. Stop the server in test cleanup.

- [ ] **Step 3: Run tests and confirm status/smoke failures**

Run: `uv run --project .workspace --group dev pytest .workspace/tests -q`

Expected: missing status and smoke implementations fail.

- [ ] **Step 4: Implement status as one collected dictionary with two renderers**

`./dev status` renders a compact human table. `./dev status --format json` emits exactly one JSON document. Both use the same collected dictionary and include:

- effective non-secret config and conflicts;
- platform and tool version/relation;
- Docker installed/daemon status;
- each repository's branch, dirty/ahead/behind/diverged state against fresh data already on disk;
- four HA-imported local package paths;
- tmux session/window/pane state;
- last smoke result when run in the current invocation.

Status is read-only and does not fetch, install, start, restart, or stop anything.

- [ ] **Step 5: Implement the default smoke checks**

Use stdlib `urllib.request` with a short timeout for the configured HA URL. Verify:

1. Home Assistant responds on the configured port; HTTP 200, 401, or 302 proves the server is reachable.
2. The four imported module paths resolve below the expected root repositories.
3. the KNX frontend adapter's documented build/serve readiness artifact exists, or its tmux pane is actively serving in development mode;
4. expected tmux windows exist and report running or an actionable command/exit state.

Failure output names the exact check and the log or pane to inspect.

During bootstrap, start Home Assistant as an explicitly planned temporary child
process, stream its state through the normal progress/log path, wait for the
HTTP check, and terminate it cleanly before bootstrap returns. This process is
never detached and never survives bootstrap. If the developer session already
exists, reuse its Home Assistant process instead. Before `./dev start`, the tmux
portion of the check reports the exact next command as its actionable state;
after start, `./dev status` inspects the real windows without changing them.

- [ ] **Step 6: Implement toolkit smoke as a separate fixed sequence**

Only for `toolkit`/`all`:

1. require the toolkit pane to report its virtual KNX endpoint;
2. start HA with the generated local automatic KNX configuration;
3. invoke the toolkit's checked-in command for one known group-write telegram;
4. query the telegram store through its public Python API from the HA environment;
5. assert the telegram address/value and persistence record match.

Keep the adapter's group address and value as two constants next to the toolkit smoke function. Do not add a generic scenario language. If the checked-out toolkit does not yet expose a non-interactive send command or stable virtual-router readiness signal, report that exact missing capability and keep the toolkit smoke check skipped-with-reason; do not scrape its GUI or add private hooks to another repository from the workspace.

- [ ] **Step 7: Run tests and manual status checks**

Run: `uv run --project .workspace --group dev pytest .workspace/tests -q`

Expected: all tests pass.

Run: `./dev status --format json | python3 -m json.tool >/dev/null`

Expected: exit 0 and valid JSON even before repositories are bootstrapped; missing items appear as actionable statuses, not tracebacks.

- [ ] **Step 8: Commit diagnostics and smoke checks**

```sh
git add .workspace
git commit -m "feat: add workspace status and smoke checks"
```

## Task 8: Document Human and Agent Onboarding

**Files:**

- Create: `README.md`
- Create: `AGENTS.md`
- Create: `.agents/skills/bootstrap-xknx-workspace/SKILL.md`
- Create: `.agents/skills/bootstrap-xknx-workspace/agents/openai.yaml`
- Modify: `.workspace/tests/test_workspace.py`

- [ ] **Step 1: Write a failing contract test for discoverable guidance and the skill**

```python
def test_documentation_and_skill_contract(workspace_root: Path) -> None:
    skill = workspace_root / ".agents/skills/bootstrap-xknx-workspace/SKILL.md"
    metadata = workspace_root / ".agents/skills/bootstrap-xknx-workspace/agents/openai.yaml"
    assert skill.exists() and metadata.exists()
    assert "name: bootstrap-xknx-workspace" in skill.read_text()
    assert "./dev status --format json" in skill.read_text()
    assert "./bootstrap" in skill.read_text()
    assert "display_name: Bootstrap XKNX Workspace" in metadata.read_text()
```

Add a `workspace_root` fixture that returns `Path(__file__).parents[2]`.

- [ ] **Step 2: Run the contract test and verify the files are missing**

Run: `uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py -q`

Expected: the documentation/skill contract test fails.

- [ ] **Step 3: Write the concise contributor README**

Include:

- purpose and supported platforms;
- root layout and why nested repositories are not submodules;
- the five public commands with copyable examples;
- profile table;
- first-run plan/confirmation and `--yes` behavior;
- tmux controls for attach, window selection, detach, restart, and `Ctrl-C`;
- local package wiring and duplicate HA frontend explanation;
- Git update safety matrix;
- Docker optionality, config location, logs, troubleshooting, and reset-by-rerun behavior.

Do not reproduce every CLI help string or child repository setup guide.

- [ ] **Step 4: Write the root `AGENTS.md` as a routing map**

State these concrete relationships:

```text
home-assistant-core/homeassistant/components/knx -> xknx, xknxproject,
knx-telegram-store, and the knx-frontend distribution
knx-frontend -> its own pinned homeassistant-frontend submodule
home-assistant-frontend -> independent current upstream frontend development
xknxtoolkit -> optional virtual KNX system for integration testing
xknx docs -> xknx repository
Home Assistant docs -> home-assistant.io repository
```

Require agents to start with the smallest owning repository, then inspect direct producers/consumers when a real API, data, build, or UI/backend contract crosses a boundary. Cross-repository changes are allowed but not assumed. Before editing a child repository, read its local `AGENTS.md`, README, contributor guide, and relevant docs. Verify and commit each repository independently. Repeat the Git safety rules from the spec without duplicating project instructions.

- [ ] **Step 5: Create the onboarding skill**

Use this frontmatter and a short imperative workflow:

```markdown
---
name: bootstrap-xknx-workspace
description: Bootstrap, start, inspect, and smoke-test the local multi-repository XKNX and Home Assistant development workspace.
---
```

The skill must:

1. read root `AGENTS.md`;
2. call `./dev status --format json` first;
3. default to `default`, mention `toolkit` only when virtual KNX is requested;
4. run `./bootstrap <profile>` only when setup is incomplete;
5. never call `./dev update` automatically;
6. never restart an existing tmux session;
7. open a visible terminal for `./dev start <profile>` when the host can do so, otherwise return that single command;
8. wait for readiness through repeated status reads with bounded backoff;
9. run the public smoke path and report URLs, profile, tmux session, package source paths, and repository states.

It must not duplicate setup commands, Git repair logic, or process commands from the CLI.

Use metadata:

```yaml
interface:
  display_name: Bootstrap XKNX Workspace
  short_description: Start and verify the local XKNX/Home Assistant workspace
  default_prompt: Bootstrap or inspect this XKNX workspace, keep processes visible, and verify the selected profile.
```

- [ ] **Step 6: Validate the skill and docs**

Run the repository's available skill validator if the installed skill tooling provides one; otherwise parse the YAML frontmatter and metadata with the standard validator supplied by the skill-authoring environment. Also run:

```sh
uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py -q
```

Expected: the discoverability contract passes and validation reports no errors.

- [ ] **Step 7: Commit onboarding guidance**

```sh
git add README.md AGENTS.md .agents .workspace/tests/test_workspace.py
git commit -m "docs: add human and agent workspace onboarding"
```

## Task 9: Automate Dependency Updates and Verification

**Files:**

- Create: `.github/dependabot.yml`
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/upstream-smoke.yml`
- Modify: `.workspace/tests/test_workspace.py`

- [ ] **Step 1: Add a failing static test for update and CI coverage**

```python
def test_automation_covers_uv_actions_and_two_smoke_platforms(workspace_root: Path) -> None:
    dependabot = (workspace_root / ".github/dependabot.yml").read_text()
    ci = (workspace_root / ".github/workflows/ci.yml").read_text()
    smoke = (workspace_root / ".github/workflows/upstream-smoke.yml").read_text()
    assert 'package-ecosystem: "uv"' in dependabot
    assert 'directory: "/.workspace"' in dependabot
    assert 'package-ecosystem: "github-actions"' in dependabot
    assert "pytest .workspace/tests" in ci
    assert "macos-latest" in smoke and "ubuntu-latest" in smoke
    assert "./bootstrap default --yes --progress plain" in smoke
```

- [ ] **Step 2: Run the static test and verify automation files are absent**

Run: `uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py -q`

Expected: missing automation files fail the test.

- [ ] **Step 3: Configure Dependabot, not Renovate**

Use weekly updates for:

```yaml
version: 2
updates:
  - package-ecosystem: "uv"
    directory: "/.workspace"
    schedule:
      interval: "weekly"
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

Do not add `mise`, Renovate, or custom version-scraping jobs. Runtime NVM resolution remains an explicit stable-release plan action; source repositories intentionally track their upstream default branches.

- [ ] **Step 4: Add fast CI**

On Ubuntu and macOS, install `uv`, run `uv sync --project .workspace --locked --group dev`, then:

```text
uv run --project .workspace --group dev pytest .workspace/tests
sh -n bootstrap dev
```

Fast CI must use only temporary fake repositories/processes. It must not clone or install Home Assistant.

- [ ] **Step 5: Add scheduled upstream smoke**

Run weekly and by manual dispatch on `ubuntu-latest` and `macos-latest`:

```text
./bootstrap default --yes --progress plain
./dev status --format json
```

Because bootstrap already runs the default smoke check with a foreground,
temporary Home Assistant child, hosted CI must not call `./dev start`. Collect
`.state/logs` as an artifact on failure and always stop any test child that the
workflow started. Set a job timeout. Do not add a dedicated WSL runner; the fast
detection tests cover WSL's shared APT path until a real failure justifies one.

- [ ] **Step 6: Run the complete local verification suite**

Run:

```sh
uv lock --check --project .workspace
uv run --project .workspace --locked --group dev pytest .workspace/tests -q
sh -n bootstrap dev
./dev --help
./dev status --format json | python3 -m json.tool >/dev/null
git status --short
```

Expected: lockfile is current, all tests pass, launchers parse, status is valid JSON, and only the intended automation changes are uncommitted.

- [ ] **Step 7: Commit automation**

```sh
git add .github .workspace/tests/test_workspace.py .workspace/uv.lock
git commit -m "ci: verify and update the workspace"
```

## Final Acceptance Pass

- [ ] Bootstrap a clean temporary copy with `default` on Ubuntu or macOS and capture the plain log.
- [ ] Confirm the first screen shows the entire mutation plan and only one confirmation.
- [ ] Confirm a second bootstrap is idempotent and performs no unnecessary installs.
- [ ] Introduce one controlled setup failure; verify running package jobs finish, no new jobs start, dependents skip, and rerun resumes.
- [ ] Put one nested repository on a dirty feature branch with a local commit; run update and verify only fetch occurs.
- [ ] Verify HA imports all four packages from the root checkouts.
- [ ] Start the workspace interactively; inspect, interrupt, and restart processes directly in tmux.
- [ ] Verify `default` succeeds without Docker and without KNX hardware.
- [ ] Verify root `home-assistant-frontend` and the pinned `knx-frontend/homeassistant-frontend` remain independent.
- [ ] Run the complete verification command from Task 9 and review `git diff --check`.

## Deliberately Deferred

- A generic workflow/DAG/manifest system: add only if fixed profiles can no longer be expressed clearly in code.
- A custom process daemon or PID database: tmux already gives ownership, logs, restarts, and visibility.
- Workspace-wide source pins: add only if upstream-default-branch development stops being the desired model.
- Dedicated WSL CI: add after a failure that the shared Ubuntu/Debian path cannot reproduce.
- Automated toolkit GUI scraping: require a stable toolkit CLI/readiness surface instead.
- Automatic Git repair, branch switching, stashing, rebasing, or submodule upgrades: intentionally out of scope.
