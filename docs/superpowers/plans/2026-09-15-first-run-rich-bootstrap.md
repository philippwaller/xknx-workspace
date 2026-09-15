# First-Run Rich Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the first interactive `./bootstrap` run use a compact, aligned Rich UI while treating the global developer toolchain as prerequisites that the workspace never installs or replaces.

**Architecture:** Keep the existing shell launcher and single Python module. The launcher quietly synchronizes the locked `.workspace` environment before bootstrap, while `dev` only uses an existing environment. The Python CLI reduces prerequisite handling to inspection and guidance, and reuses `Progress` for a compact plan followed by one fixed-column live task table.

**Tech Stack:** POSIX shell, Python 3.12+, `uv`, Rich, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-xknx-workspace-design.md`

## Global Constraints

- Supported systems are macOS, Ubuntu, Debian, and Ubuntu/Debian under WSL 2.
- Required global tools are Git >= 2.39, Python >= 3.12, `uv` >= 0.8.17, `tmux` >= 3.2, and NVM without an exact version constraint.
- Bootstrap never installs, upgrades, or downgrades global developer tools.
- Rich remains the only workspace CLI runtime dependency and comes from `.workspace/uv.lock`.
- `mise`, a second CLI framework, a second progress implementation, and new runtime dependencies are forbidden.
- The default interactive view hides raw commands; `--verbose`, logs, plain mode, and JSON preserve transparency.
- Existing repositories, branches, worktrees, configuration, and project environments must remain untouched until plan confirmation; `.workspace/.venv` is the sole exception.

---

### Task 1: Guarantee Rich on the First Bootstrap Run

**Files:**
- Modify: `bootstrap`
- Modify: `.workspace/tests/test_integration.py`

**Interfaces:**
- Consumes: `.workspace/uv.lock`, a compatible global `python3`, and global `uv`.
- Produces: `./bootstrap` always executes `.workspace/.venv/bin/python`; `./dev` executes it only when already present.

- [ ] **Step 1: Replace the old launcher expectations with failing first-run tests**

Add tests that provide fake `python3` and `uv` executables and assert the exact launcher contract:

```python
def test_first_bootstrap_syncs_locked_controller_before_exec(tmp_path: Path) -> None:
    root = Path(ws.__file__).parents[1]
    (tmp_path / "bootstrap").write_bytes((root / "bootstrap").read_bytes())
    (tmp_path / ".workspace").mkdir()
    (tmp_path / ".workspace/xknx_workspace.py").write_text("")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dirname").symlink_to("/usr/bin/dirname")
    python3 = bin_dir / "python3"
    python3.write_text("#!/bin/sh\ncase \"$*\" in *sys.version_info*) exit 0;; esac\nexit 90\n")
    python3.chmod(0o755)
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > " + shlex.quote(str(tmp_path / "uv.args")) + "\n"
        "mkdir -p " + shlex.quote(str(tmp_path / ".workspace/.venv/bin")) + "\n"
        "printf '#!/bin/sh\\nprintf \"%s\\\\n\" \"$@\"\\n' > "
        + shlex.quote(str(tmp_path / ".workspace/.venv/bin/python")) + "\n"
        "chmod +x " + shlex.quote(str(tmp_path / ".workspace/.venv/bin/python")) + "\n"
    )
    uv.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(tmp_path / "bootstrap"), "default"],
        env={"PATH": str(bin_dir), "UV_PROJECT_ENVIRONMENT": str(tmp_path / "unrelated")},
        capture_output=True, text=True, check=False,
    )
    expected = [
        "sync", "--quiet", "--project", str(tmp_path / ".workspace"), "--locked",
        "--no-dev", "--no-python-downloads", "--python", str(python3),
    ]
    assert result.returncode == 0
    assert (tmp_path / "uv.args").read_text().splitlines() == expected
    assert result.stdout.splitlines()[-2:] == ["bootstrap", "default"]
    assert not (tmp_path / "unrelated").exists()


def test_dev_without_controller_environment_points_to_bootstrap(tmp_path: Path) -> None:
    root = Path(ws.__file__).parents[1]
    (tmp_path / "bootstrap").write_bytes((root / "bootstrap").read_bytes())
    (tmp_path / "dev").write_bytes((root / "dev").read_bytes())
    (tmp_path / ".workspace").mkdir()
    (tmp_path / ".workspace/xknx_workspace.py").write_text("")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dirname").symlink_to("/usr/bin/dirname")
    python3 = bin_dir / "python3"
    python3.write_text("#!/bin/sh\ncase \"$*\" in *sys.version_info*) exit 0;; esac\nexit 90\n")
    python3.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(tmp_path / "dev"), "status"],
        env={"PATH": str(bin_dir)}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "./bootstrap" in result.stderr
    assert "uv sync" not in result.stdout + result.stderr
```

Keep the existing old-Python OS guidance cases. Split the current parameterized `test_launchers_never_sync_or_use_an_inherited_environment` so only `dev` retains the no-sync assertion.

- [ ] **Step 2: Run the launcher tests to verify they fail**

Run:

```bash
uv run --project .workspace --group dev pytest .workspace/tests/test_integration.py -k 'controller or launcher' -v
```

Expected: the first-bootstrap sync test fails because the current launcher directly executes system Python; the missing-controller `dev` test fails because it currently falls back to system Python.

- [ ] **Step 3: Implement the two launcher paths with no new script**

Keep `bootstrap` POSIX shell. After selecting a compatible global `python3`, branch on `workspace_command`:

```sh
controller_python=$root/.workspace/.venv/bin/python

if [ "$workspace_command" = dev ]; then
  if [ -x "$controller_python" ]; then
    exec "$controller_python" -B "$root/.workspace/xknx_workspace.py" dev "$@"
  fi
  printf '%s\n' 'Workspace UI is not prepared; run ./bootstrap first.' >&2
  exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
  print_uv_guidance
  exit 2
fi

printf '%s\n' 'Preparing workspace UI…'
uv sync --quiet --project "$root/.workspace" --locked --no-dev \
  --no-python-downloads --python "$workspace_python"
exec "$controller_python" -B "$root/.workspace/xknx_workspace.py" bootstrap "$@"
```

Retain the existing Python 3.12 platform guidance in a small `print_python_guidance` shell function. Add `print_uv_guidance` using `brew install uv` on macOS, the official uv installation link on Linux/WSL where APT does not provide the required package, and no automatic execution. Continue unsetting `UV_PROJECT_ENVIRONMENT` and `VIRTUAL_ENV` before any check or sync.

- [ ] **Step 4: Run the launcher tests to verify they pass**

Run:

```bash
uv run --project .workspace --group dev pytest .workspace/tests/test_integration.py -k 'controller or launcher' -v
sh -n bootstrap dev
```

Expected: all selected tests pass and both launchers are valid POSIX shell.

- [ ] **Step 5: Commit the launcher behavior**

```bash
git add bootstrap .workspace/tests/test_integration.py
git commit -m "feat: prepare rich before bootstrap"
```

---

### Task 2: Turn Tool Installation Into Prerequisite Guidance

**Files:**
- Modify: `.workspace/xknx_workspace.py`
- Modify: `.workspace/tests/test_workspace.py`
- Modify: `.workspace/tests/test_integration.py`

**Interfaces:**
- Consumes: `detect_platform()`, `inspect_tool()`, `PACKAGE_COMMANDS`, `TOOL_LINKS`, and profile repository selection.
- Produces: `prerequisite_guidance(platform, tools) -> list[dict[str, object]]`; `build_bootstrap_plan(root, profile, settings) -> list[dict[str, object]]` with no executable global-tool actions.

- [ ] **Step 1: Write failing tests for the prerequisite-only contract**

Replace installer tests with these assertions:

```python
def test_missing_macos_prerequisites_are_guidance_not_jobs() -> None:
    guidance = ws.prerequisite_guidance(
        "macos", {"git": None, "uv": None, "tmux": None}
    )
    assert guidance == [{
        "kind": "manual",
        "blocking": True,
        "tools": ("git", "uv", "tmux"),
        "command": ["brew", "install", "git", "uv", "tmux"],
    }]


def test_missing_nvm_is_blocking_and_never_resolves_or_runs_an_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ws, "detect_platform", lambda: "macos")
    monkeypatch.setattr(
        ws,
        "inspect_tool",
        lambda name, **kwargs: {
            "kind": "tool", "tool": name,
            "executable": None if name == "nvm" else f"/bin/{name}",
            "installed": None if name == "nvm" else "99.0",
        },
    )
    monkeypatch.setattr(
        ws, "inspect_docker",
        lambda platform: ws.docker_status(platform, executable=None, daemon_reachable=False),
    )
    plan = ws.build_bootstrap_plan(tmp_path, "default", {"profile": "default"})
    nvm = next(item for item in plan if item.get("tool") == "nvm")
    assert nvm["blocking"] is True
    assert nvm.get("command") is None
    assert nvm["link"] == "https://github.com/nvm-sh/nvm#installing-and-updating"


def test_prerequisite_failure_changes_no_workspace_state(tmp_path: Path) -> None:
    plan = {
        "root": tmp_path,
        "tool_actions": [{
            "kind": "tool", "tool": "git", "installed": None,
            "minimum": "2.39.0", "blocking": True,
        }],
        "repository_jobs": [ws.Job("must-not-run", tmp_path, (["false"],))],
    }
    progress = ws.Progress("quiet", log_dir=tmp_path / "logs")
    before = sorted(tmp_path.rglob("*"))
    code = asyncio.run(ws.bootstrap_workspace(plan, progress))
    assert code == 2
    assert sorted(tmp_path.rglob("*")) == before
```

Delete tests for downloading, executing, and verifying the NVM installer, package installation, sudo authentication, controller planning, and `--enforce-tool-versions`.

- [ ] **Step 2: Run the prerequisite tests to verify they fail**

Run:

```bash
uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py .workspace/tests/test_integration.py -k 'tool or prerequisite or nvm or controller or sudo' -v
```

Expected: failures reference the missing `prerequisite_guidance` function and the still-present installer/controller actions.

- [ ] **Step 3: Delete the installation paths and keep the existing inspection path**

In `.workspace/xknx_workspace.py`:

- Delete `NVM_RELEASE_API`, `nvm_install_action()`, `resolve_nvm_release()`, controller plan entries, installer verification, and the `urllib` imports.
- Remove `--enforce-tool-versions` from `build_parser()`, `main()`, `run_bootstrap()`, and `build_bootstrap_plan()`.
- Keep numeric minimum comparison in `inspect_tool()`; mark missing executables as blocking as well as old/unreadable versions.
- Add NVM's official installation URL to `TOOL_LINKS`.
- Replace `missing_tool_actions()` with `prerequisite_guidance()`. It may inspect APT availability, but it returns display-only `manual` records. Homebrew or APT commands are never passed to `run_job()`.
- Reduce `run_tool_actions()` to emitting the first blocking prerequisite and skipped dependents; it must not execute a command.

The essential blocking rule is:

```python
if minimum:
    status.update(
        minimum=minimum,
        blocking=(
            executable is None
            or installed is None
            or _version_tuple(installed) < _version_tuple(minimum)
        ),
    )
```

For NVM, set `blocking=True` when its script cannot be sourced. Keep `nvm_shell()` unchanged because installing the Node version from each repository's `.nvmrc` is project setup, not global tool installation.

- [ ] **Step 4: Run the prerequisite tests to verify they pass**

Run:

```bash
uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py .workspace/tests/test_integration.py -k 'tool or prerequisite or nvm or controller or sudo' -v
```

Expected: all retained tests pass; no test performs a global tool installation.

- [ ] **Step 5: Commit the prerequisite policy**

```bash
git add .workspace/xknx_workspace.py .workspace/tests/test_workspace.py .workspace/tests/test_integration.py
git commit -m "refactor: require global workspace tools"
```

---

### Task 3: Replace the Plan Table With the Aligned Rich Experience

**Files:**
- Modify: `.workspace/xknx_workspace.py`
- Modify: `.workspace/tests/test_workspace.py`
- Modify: `.workspace/tests/test_integration.py`

**Interfaces:**
- Consumes: the existing plan dictionaries and `Progress.emit(task, status, detail)` calls.
- Produces: `Progress.show_plan(lines, *, profile, platform) -> None`, `Progress.emit(task, status, detail, duration=None) -> None`, and `Progress.finish(returncode, *, profile, urls=()) -> None`.

- [ ] **Step 1: Write failing tests for compact planning and fixed columns**

Add focused tests without snapshotting ANSI control sequences:

```python
def test_default_plan_is_compact_and_verbose_plan_reveals_commands() -> None:
    plan = [
        {"kind": "context", "platform": "macos", "profile": "default", "repositories": ("a", "b", "c", "d", "e")},
        {"kind": "setup", "name": "XKNX", "cwd": "/workspace/xknx", "commands": (["uv", "sync"],)},
    ]
    compact: list[str] = []
    verbose: list[str] = []
    ws.render_plan(plan, compact.append, verbose=False)
    ws.render_plan(plan, verbose.append, verbose=True)
    assert not any("$ " in line for line in compact)
    assert any("5 repositories" in line for line in compact)
    assert any("$ " in line for line in verbose)


def test_tty_rows_use_fixed_component_result_and_duration_columns(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    progress = ws.Progress("tty")
    progress.emit("XKNX", "running", "Installiert Abhängigkeiten")
    progress.emit("Home Assistant Core", "success", "Eingerichtet", duration=42.1)
    table = progress._task_table()
    assert [column.header for column in table.columns] == ["", "Komponente", "Ergebnis", "Dauer"]
    assert table.columns[2].min_width > 0
    assert table.columns[3].justify == "right"


def test_running_output_reuses_result_cell_without_detail_rows() -> None:
    progress = ws.Progress("tty")
    progress.emit("XKNX", "running", "Installiert Abhängigkeiten")
    progress.emit("XKNX", "running", "Prüft Installation")
    assert list(progress.rows) == ["XKNX"]
    assert progress.rows["XKNX"][1] == "Prüft Installation"


def test_finish_renders_actionable_failure_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    rendered: list[str] = []
    progress = ws.Progress("tty")
    monkeypatch.setattr(progress, "_render_summary_tty", lambda lines, error: rendered.extend(lines))
    progress.emit("KNX Frontend", "error", "Exit 3; log: /workspace/.state/logs/frontend.log")
    progress.finish(3, profile="default")
    assert "KNX Frontend" in " ".join(rendered)
    assert "Exit 3" in " ".join(rendered)
    assert "/workspace/.state/logs/frontend.log" in " ".join(rendered)
    assert "./bootstrap default" in " ".join(rendered)

    rendered.clear()
    progress = ws.Progress("tty")
    monkeypatch.setattr(progress, "_render_summary_tty", lambda lines, error: rendered.extend(lines))
    progress.finish(0, profile="default", urls=("http://localhost:8123",))
    assert "./dev start default" in " ".join(rendered)
    assert "http://localhost:8123" in " ".join(rendered)
```

- [ ] **Step 2: Run the UI tests to verify they fail**

Run:

```bash
uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py .workspace/tests/test_integration.py -k 'progress or plan or summary or first_completed_failure' -v
```

Expected: tests fail because the current UI stores plan lines as `plan-N` tasks and exposes only `Task / Status / Detail` columns.

- [ ] **Step 3: Implement the minimum Rich presentation inside `Progress`**

Keep Rich imports lazy inside TTY-only methods. Store rows as:

```python
self.rows: dict[str, tuple[str, str, float | None]] = {}
```

Build one borderless table with four fixed roles:

```python
table = Table.grid(expand=True)
table.add_column(width=2)
table.add_column("Komponente", min_width=24, ratio=2)
table.add_column("Ergebnis", min_width=28, ratio=3)
table.add_column("Dauer", width=8, justify="right")
```

Render `Spinner("dots")` only for `running`, green `✔` for `success`, red `✘` for `error`, and dim `○` for waiting/skipped. Put the current substep directly in the result cell. Pass `duration` from `run_job()` instead of embedding it in the detail text.

Add `show_plan()` so `run_bootstrap()` displays one Rich header and compact grouped plan before confirmation rather than emitting `plan-1`, `plan-2`, and so on. Close that live render before reading confirmation, clear `rows`, then use the same `Progress` instance for execution.

Change `render_plan(plan, print_fn, secrets=None, verbose=False)` to summarize context, prerequisites, repository count/names, configuration, wiring, smoke test, and warnings. Append exact redacted commands only when `verbose=True`.

During `run_job()`, use the executable or script name as the stable current substep. Do not stream subprocess lines into the TTY result cell; keep them in the log. Plain verbose mode may continue printing those lines.

Add `finish()` and call it from `bootstrap_workspace()`'s `finally` block. It renders one Rich success or error panel and delegates to the current line/JSON event behavior outside TTY mode.

- [ ] **Step 4: Run the focused UI tests to verify they pass**

Run:

```bash
uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py .workspace/tests/test_integration.py -k 'progress or plan or summary or first_completed_failure' -v
```

Expected: compact-plan, fixed-column, concurrent-row, and summary tests pass.

- [ ] **Step 5: Commit the Rich UI**

```bash
git add .workspace/xknx_workspace.py .workspace/tests/test_workspace.py .workspace/tests/test_integration.py
git commit -m "feat: present aligned rich bootstrap progress"
```

---

### Task 4: Align Contributor Guidance and Verify the Whole Workspace

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Test: `.workspace/tests/test_workspace.py`

**Interfaces:**
- Consumes: the completed launcher, prerequisite policy, and Rich behavior.
- Produces: accurate first-run documentation for humans and agents.

- [ ] **Step 1: Write a failing documentation-contract test**

Extend the existing repository documentation test:

```python
def test_first_run_docs_match_the_locked_rich_bootstrap(workspace_root: Path) -> None:
    readme = (workspace_root / "README.md").read_text()
    agents = (workspace_root / "AGENTS.md").read_text()
    assert "Git 2.39" in readme and "Python 3.12" in readme
    assert "uv 0.8.17" in readme and "tmux 3.2" in readme and "NVM" in readme
    assert "never installs" in readme
    assert ".workspace/uv.lock" in readme
    assert "first interactive" in readme and "Rich" in readme
    assert "./bootstrap" in agents and "never installs" in agents
    assert "plain progress remains usable without Rich" not in agents
```

- [ ] **Step 2: Run the documentation test to verify it fails**

Run:

```bash
uv run --project .workspace --group dev pytest .workspace/tests/test_workspace.py -k first_run_docs -v
```

Expected: it fails on the old promise that nothing is synchronized before confirmation and that the first run may be plain.

- [ ] **Step 3: Update only the affected documentation**

In `README.md`, replace the old first-run paragraphs with the prerequisite list, the locked Rich preloader exception, `dev`'s no-sync behavior, default command hiding, and `--verbose` escape hatch. In `AGENTS.md`, replace the old launcher invariant with the same concise policy. Do not rewrite historical implementation plans.

- [ ] **Step 4: Run all verification**

Run:

```bash
uv run --project .workspace --group dev pytest .workspace/tests -q
sh -n bootstrap dev
./bootstrap --help
./dev status --format json
git diff --check
```

Expected: the full test suite passes, shell syntax is valid, bootstrap help is rendered through the locked environment, status returns valid JSON, and the diff has no whitespace errors.

- [ ] **Step 5: Perform one real PTY visual check**

Run `./bootstrap default`, inspect the compact plan, and answer `n`. Verify visually that no `plan-N` rows or raw commands appear, result text begins in one aligned column, and declining causes no project mutation.

- [ ] **Step 6: Commit documentation and final verification**

```bash
git add README.md AGENTS.md .workspace/tests/test_workspace.py
git commit -m "docs: explain first-run rich bootstrap"
```
