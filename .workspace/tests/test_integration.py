import asyncio
import hashlib
import json
import os
import pty
import select
import shlex
import signal
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

import xknx_workspace as ws


@pytest.mark.parametrize("mode", ["plain", "json", "tty"])
@pytest.mark.parametrize("verbose", [False, True])
def test_multiline_secret_never_reaches_live_output_across_chunks(tmp_path: Path, monkeypatch, capsys, mode, verbose) -> None:
    secret = "first-private-line\nsecond-private-line"
    monkeypatch.setenv("FINAL_TEST_SECRET", secret)
    progress = ws.Progress(mode, verbose=verbose, log_dir=tmp_path / "logs")
    rendered = []
    render = progress._render_tty
    def record_render():
        rendered.append(repr(progress.rows))
        render()
    monkeypatch.setattr(progress, "_render_tty", record_render)
    script = (
        "import os, sys, time\n"
        "value = os.environ['FINAL_TEST_SECRET']\n"
        "for stream in (sys.stdout, sys.stderr):\n"
        "    for part in (value[:10], value[10:19], value[19:] + '\\n'):\n"
        "        stream.write(part); stream.flush(); time.sleep(0.03)\n"
        "print('normal build output')\n"
    )
    try:
        assert asyncio.run(ws.run_job(ws.Job("secret", tmp_path, ([sys.executable, "-c", script],)), progress)).returncode == 0
    finally:
        progress.close()
    output = capsys.readouterr()
    content = output.out + output.err + "".join(rendered) + repr(progress.rows) + "".join(path.read_text() for path in (tmp_path / "logs").glob("*.log"))
    assert "first-private-line" not in content and "second-private-line" not in content
    assert "normal build output" in content and "***" in content


def test_multiline_secret_split_between_output_streams_keeps_common_words(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FINAL_TEST_SECRET", "on\nprivate-second-line")
    script = "import os, sys; first, second = os.environ['FINAL_TEST_SECRET'].splitlines(); print(first, flush=True); print(second, file=sys.stderr, flush=True); print('condition is on')"
    progress = ws.Progress("plain", log_dir=tmp_path / "logs")
    assert asyncio.run(ws.run_job(ws.Job("split", tmp_path, ([sys.executable, "-c", script],)), progress)).returncode == 0
    output = capsys.readouterr().out
    content = output + "".join(path.read_text() for path in (tmp_path / "logs").glob("*.log"))
    assert "private-second-line" not in content
    assert "split: running: on\n" not in content
    assert "condition is on" in output


def test_cancellation_kills_descendants_after_direct_child_closes_pipes(tmp_path: Path) -> None:
    async def exercise():
        child = (
            "import os, signal, time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "Path('descendant.pid').write_text(str(os.getpid()))\ntime.sleep(30)\n"
        )
        parent = (
            "import subprocess, sys, time\nfrom pathlib import Path\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "while not Path('descendant.pid').exists(): time.sleep(0.01)\n"
            "Path('parent.ready').touch()\ntime.sleep(30)\n"
        )
        task = asyncio.create_task(ws.run_job(ws.Job("tree", tmp_path, ([sys.executable, "-c", parent],)), ws.Progress("quiet", log_dir=tmp_path / "logs")))
        pid = None
        try:
            async with asyncio.timeout(5):
                while not (tmp_path / "parent.ready").exists():
                    await asyncio.sleep(0.01)
            pid = int((tmp_path / "descendant.pid").read_text())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert not process_is_alive(pid)
        finally:
            if pid and process_is_alive(pid):
                os.kill(pid, signal.SIGKILL)
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(exercise())


@pytest.mark.parametrize("entry,args", [("bootstrap", ["--help"]), ("dev", ["--help"]), ("dev", ["start"]), ("dev", ["stop"]), ("dev", ["update"])])
@pytest.mark.parametrize("existing_environment", [False, True])
def test_launchers_never_sync_or_use_an_inherited_environment(tmp_path: Path, entry, args, existing_environment) -> None:
    root = Path(ws.__file__).parents[1]
    for name in ("bootstrap", "dev"):
        (tmp_path / name).write_bytes((root / name).read_bytes())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dirname").symlink_to("/usr/bin/dirname")
    interpreter = tmp_path / ".workspace/.venv/bin/python" if existing_environment else bin_dir / "python3"
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in *'sys.version_info'*) exit 0;; esac\n"
        "[ -z \"${UV_PROJECT_ENVIRONMENT:-}\" ] || exit 78\n"
        "printf 'direct interpreter: %s\\n' \"$*\"\n"
    )
    interpreter.chmod(0o755)
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\nprintf 'unexpected uv call\\n'\nexit 79\n")
    uv.chmod(0o755)
    result = subprocess.run(["/bin/sh", str(tmp_path / entry), *args], env={"PATH": str(bin_dir), "UV_PROJECT_ENVIRONMENT": str(tmp_path / "unrelated")}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "direct interpreter:" in result.stdout and "unexpected uv" not in result.stdout
    assert not (tmp_path / "unrelated").exists()


@pytest.mark.parametrize("platform,release,guidance", [
    ("Darwin", "", "brew install python@3.12"),
    ("Linux", 'ID=ubuntu\nVERSION_ID="24.04"\n', "apt-get install -y python3"),
    ("Linux", 'ID=debian\nVERSION_ID="13"\n', "apt-get install -y python3"),
    ("Linux microsoft-standard-WSL2", 'ID=ubuntu\nVERSION_ID="24.04"\n', "apt-get install -y python3"),
    ("Linux microsoft-standard-WSL2", 'ID=debian\nVERSION_ID="13"\n', "apt-get install -y python3"),
    ("Linux microsoft-standard-WSL2", 'ID=ubuntu\nVERSION_ID="22.04"\n', "python.org/downloads"),
    ("Linux microsoft-standard-WSL2", 'ID=debian\nVERSION_ID="12"\n', "python.org/downloads"),
])
def test_launcher_rejects_old_python_before_import_with_os_guidance(tmp_path: Path, platform, release, guidance) -> None:
    root = Path(ws.__file__).parents[1]
    (tmp_path / "bootstrap").write_bytes((root / "bootstrap").read_bytes())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dirname").symlink_to("/usr/bin/dirname")
    for name, body in {
        "python3": "case \"$*\" in *'sys.version_info'*) exit 1;; esac\nprintf 'unsafe module import\\n'; exit 90",
        "uname": f"printf '%s\\n' {shlex.quote(platform)}",
        "cat": f"printf '%s' {shlex.quote(release)}",
        "brew": "exit 91", "apt-get": "exit 92",
    }.items():
        executable = bin_dir / name
        executable.write_text(f"#!/bin/sh\n{body}\n")
        executable.chmod(0o755)
    result = subprocess.run(["/bin/sh", str(tmp_path / "bootstrap")], env={"PATH": str(bin_dir)}, capture_output=True, text=True)
    assert result.returncode == 2
    assert "Python 3.12+" in result.stderr
    assert guidance in result.stderr
    assert "unsafe module import" not in result.stdout + result.stderr


def test_selected_node_enables_project_yarn_before_frontend_command(tmp_path: Path) -> None:
    nvm_dir = tmp_path / "nvm"
    node_bin = tmp_path / "selected-node/bin"
    node_bin.mkdir(parents=True)
    nvm_dir.mkdir()
    (nvm_dir / "nvm.sh").write_text(f"nvm() {{ export NVM_BIN={shlex.quote(str(node_bin))}; export PATH=\"$NVM_BIN:$PATH\"; }}\n")
    corepack = node_bin / "corepack"
    corepack.write_text(
        "#!/bin/sh\n[ \"$*\" = 'enable yarn' ] || exit 71\n"
        "[ -f package.json ] || exit 72\n"
        f"ln -s /usr/bin/true {shlex.quote(str(node_bin / 'yarn'))}\n"
    )
    corepack.chmod(0o755)
    (tmp_path / "package.json").write_text('{"packageManager":"yarn@4.9.2"}')
    result = subprocess.run(ws.nvm_shell(["yarn", "--version"]), cwd=tmp_path, env={**os.environ, "NVM_DIR": str(nvm_dir)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (node_bin / "yarn").is_symlink()


def test_docs_prerequisites_observe_directory_specific_ruby_and_bundler(tmp_path: Path, monkeypatch) -> None:
    for repo, version in (("xknx/docs", "3.3.9"), ("home-assistant.io", "3.4.4")):
        directory = tmp_path / repo
        directory.mkdir(parents=True)
        (directory / ".ruby-version").write_text(version)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ruby = bin_dir / "ruby"
    ruby.write_text("#!/bin/sh\nprintf 'ruby '; /bin/cat .ruby-version\n")
    ruby.chmod(0o755)
    bundle = bin_dir / "bundle"
    bundle.write_text("#!/bin/sh\n[ -f .ruby-version ] || exit 4\n[ ! -f missing-bundler ] || exit 5\nprintf 'Bundler version 2.7.1\\n'\n")
    bundle.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    actions = ws.docs_prerequisite_actions(tmp_path, "docs")
    assert not any(action.get("blocking") for action in actions)
    assert {a["repository"]: a["installed"] for a in actions if a.get("tool") == "ruby"} == {"xknx/docs": "3.3.9", "home-assistant.io": "3.4.4"}
    (tmp_path / "xknx/docs/missing-bundler").touch()
    blocked = [a for a in ws.docs_prerequisite_actions(tmp_path, "docs") if a.get("blocking")]
    assert len(blocked) == 1 and blocked[0]["repository"] == "xknx/docs"
    assert blocked[0]["tool"] == "bundler"
    status = ws.collect_status(tmp_path, {**ws.load_settings(tmp_path, {}), "profile": "docs"})
    assert status["tools"]["ruby"]["requirements"]["xknx/docs"]["installed"] == "3.3.9"
    assert status["tools"]["ruby"]["requirements"]["home-assistant.io"]["installed"] == "3.4.4"
    assert status["tools"]["bundle"]["requirements"]["xknx/docs"]["installed"] is None


def test_hanging_docker_is_bounded_in_the_complete_bootstrap_plan(tmp_path: Path, monkeypatch) -> None:
    inspect_docker = ws.inspect_docker
    fake_planning_tools(monkeypatch)
    monkeypatch.setattr(ws, "inspect_docker", inspect_docker)
    executable = tmp_path / "docker"
    executable.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    executable.chmod(0o755)
    monkeypatch.setattr(ws.shutil, "which", lambda name: str(executable) if name == "docker" else None)
    monkeypatch.setattr(ws, "DIAGNOSTIC_TIMEOUT", 0.1)
    start = time.monotonic()
    plan = ws.build_bootstrap_plan(tmp_path, "default", ws.load_settings(tmp_path, {}))
    assert time.monotonic() - start < 2
    docker = next(item for item in plan if item.get("tool") == "docker")
    assert docker["level"] == "info" and docker["available"] is False
    assert any(item.get("kind") == "setup" for item in plan)


def test_missing_controller_environment_is_planned_confirmed_and_created_locally(tmp_path: Path, monkeypatch, capsys) -> None:
    fake_planning_tools(monkeypatch)
    executable = tmp_path / "uv"
    executable.write_text(
        f"#!{sys.executable}\nimport os, pathlib, sys\n"
        "assert 'UV_PROJECT_ENVIRONMENT' not in os.environ and 'VIRTUAL_ENV' not in os.environ\n"
        "assert sys.argv[1] == 'sync' and '--locked' in sys.argv and '--no-python-downloads' in sys.argv\n"
        "project = pathlib.Path(sys.argv[sys.argv.index('--project') + 1])\n"
        "interpreter = project / '.venv/bin/python'\ninterpreter.parent.mkdir(parents=True)\ninterpreter.touch()\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "unrelated"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "unrelated"))
    build = ws.build_bootstrap_plan
    actions = build(tmp_path, "default", ws.load_settings(tmp_path, {}))
    controller = [item for item in actions if item.get("kind") == "controller"]
    assert len(controller) == 1
    command = controller[0]["command"]
    assert command[command.index("--project") + 1] == str(tmp_path / ".workspace")
    assert command[command.index("--python") + 1] == sys.executable
    monkeypatch.setattr(ws, "build_bootstrap_plan", lambda *args, **kwargs: controller)
    prompts = []
    def confirm(prompt):
        prompts.append(prompt)
        output = capsys.readouterr().out
        assert "uv sync" in output and "--locked" in output and ".workspace" in output
        assert not (tmp_path / ".workspace").exists()
        return "y"
    assert ws.run_bootstrap(tmp_path, "default", yes=False, argv=["bootstrap"], input_fn=confirm, progress_mode="plain") == 0
    assert len(prompts) == 1 and (tmp_path / ".workspace/.venv/bin/python").is_file()
    assert not (tmp_path / "unrelated").exists()
    assert not any(item.get("kind") == "controller" for item in build(tmp_path, "default", ws.load_settings(tmp_path, {})))


def test_bootstrap_private_repository_propagates_exact_git_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    origin, _, checkout = create_checkout(tmp_path)
    original = subprocess.run
    def failure(command, **kwargs):
        if command[3:5] == ["fetch", "origin"]:
            return subprocess.CompletedProcess(command, 23, "", "fetch failed\n")
        return original(command, **kwargs)
    monkeypatch.setattr(ws.subprocess, "run", failure)
    assert ws.main(["_repository", str(checkout), str(origin)]) == 23
    assert "fetch failed" in capsys.readouterr().err


def test_bootstrap_logs_the_actual_clone_failure_without_starting_another_repo(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "git"
    executable.write_text("#!/bin/sh\nprintf 'clone failure\\n' >&2\nexit 37\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    progress = ws.Progress("quiet", log_dir=tmp_path / "logs")
    plan = {"root": tmp_path, "jobs": 1, "repository_jobs": ws.repository_jobs(tmp_path, {"xknx": "absent", "xknxproject": "absent"})}
    assert asyncio.run(ws.bootstrap_workspace(plan, progress)) == 37
    logs = list((tmp_path / "logs").glob("*.log"))
    assert len(logs) == 1 and "returncode: 37" in logs[0].read_text()
    assert not (tmp_path / "xknxproject").exists()


def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def git(*args: object) -> subprocess.CompletedProcess[str]:
    result = run(["git", *map(str, args)])
    assert result.returncode == 0, result.stderr
    return result


def commit(repository: Path, message: str, filename: str) -> None:
    (repository / filename).write_text(message)
    git("-C", repository, "add", filename)
    git("-C", repository, "commit", "-m", message)


def create_knx_frontend_origin(tmp_path: Path) -> tuple[Path, str]:
    frontend_origin = tmp_path / "frontend.git"
    frontend_upstream = tmp_path / "frontend-upstream"
    git("init", "--bare", frontend_origin)
    git("-C", frontend_origin, "symbolic-ref", "HEAD", "refs/heads/trunk")
    git("clone", frontend_origin, frontend_upstream)
    git("-C", frontend_upstream, "config", "user.name", "Test User")
    git("-C", frontend_upstream, "config", "user.email", "test@example.invalid")
    git("-C", frontend_upstream, "config", "commit.gpgsign", "false")
    commit(frontend_upstream, "frontend", "frontend.txt")
    git("-C", frontend_upstream, "push", "origin", "trunk")
    pinned = git("-C", frontend_upstream, "rev-parse", "HEAD").stdout.strip()

    origin = tmp_path / "knx-frontend.git"
    upstream = tmp_path / "knx-frontend-upstream"
    git("init", "--bare", origin)
    git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/trunk")
    git("clone", origin, upstream)
    git("-C", upstream, "config", "user.name", "Test User")
    git("-C", upstream, "config", "user.email", "test@example.invalid")
    git("-C", upstream, "config", "commit.gpgsign", "false")
    git("-C", upstream, "submodule", "add", frontend_origin, "homeassistant-frontend")
    git("-C", upstream, "commit", "-m", "pin frontend")
    git("-C", upstream, "push", "origin", "trunk")
    return origin, pinned


def create_checkout(tmp_path: Path) -> tuple[Path, Path, Path]:
    origin = tmp_path / "origin.git"
    upstream = tmp_path / "upstream"
    checkout = tmp_path / "xknx"
    git("init", "--bare", origin)
    git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/trunk")
    git("clone", origin, upstream)
    git("-C", upstream, "config", "user.name", "Test User")
    git("-C", upstream, "config", "user.email", "test@example.invalid")
    git("-C", upstream, "config", "commit.gpgsign", "false")
    commit(upstream, "initial", "initial.txt")
    git("-C", upstream, "push", "origin", "trunk")
    git("clone", origin, checkout)
    return origin, upstream, checkout


def test_safe_update_only_fast_forwards_a_clean_default_branch(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    upstream = tmp_path / "upstream"
    workspace = tmp_path / "workspace"
    checkout = workspace / "xknx"

    git("init", "--bare", origin)
    git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/trunk")
    git("clone", origin, upstream)
    git("-C", upstream, "config", "user.name", "Test User")
    git("-C", upstream, "config", "user.email", "test@example.invalid")
    git("-C", upstream, "config", "commit.gpgsign", "false")
    commit(upstream, "initial", "initial.txt")
    git("-C", upstream, "push", "origin", "trunk")
    workspace.mkdir()
    git("clone", origin, checkout)

    commit(upstream, "first update", "first.txt")
    git("-C", upstream, "push")
    result = ws.safe_update(checkout, run)
    assert result["action"] == "fast-forwarded"
    assert result["default_branch"] == "trunk"
    assert result["behind"] == 0

    local_head = git("-C", checkout, "rev-parse", "HEAD").stdout.strip()
    git("-C", checkout, "config", "status.showUntrackedFiles", "no")
    local_file = checkout / "local.txt"
    local_file.write_text("mine")
    commit(upstream, "second update", "second.txt")
    git("-C", upstream, "push")

    result = ws.safe_update(checkout, run)
    assert result["action"] == "unchanged"
    assert result["dirty"] is True
    assert result["behind"] == 1
    assert git("-C", checkout, "rev-parse", "HEAD").stdout.strip() == local_head
    assert local_file.read_text() == "mine"


def test_main_update_fast_forwards_an_existing_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    origin, upstream, checkout = create_checkout(tmp_path)
    commit(upstream, "update", "update.txt")
    git("-C", upstream, "push")
    old_head = git("-C", checkout, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(ws, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "repositories_for", lambda profile: ("xknx",))
    monkeypatch.setitem(ws.REPOSITORIES, "xknx", str(origin))

    assert ws.main(["dev", "update", "default", "--progress", "json"]) == 0
    assert git("-C", checkout, "rev-parse", "HEAD").stdout.strip() != old_head
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["task"] for event in events] == ["xknx", "update-summary"]
    assert events[0]["status"] == "success"
    assert events[-1]["status"] == "summary"
    log = next((tmp_path / ".state/logs").glob("*.log")).read_text()
    assert f"cwd: {checkout}" in log
    assert 'argv: [["git", "-C"' in log
    assert "returncode: 0" in log
    assert "start:" in log and "end:" in log and "duration:" in log
    assert "stdout:" in log and "stderr:" in log


def test_ensure_repository_clones_the_remote_default_branch(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    upstream = tmp_path / "upstream"
    checkout = tmp_path / "workspace" / "xknx"
    git("init", "--bare", origin)
    git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/trunk")
    git("clone", origin, upstream)
    git("-C", upstream, "config", "user.name", "Test User")
    git("-C", upstream, "config", "user.email", "test@example.invalid")
    git("-C", upstream, "config", "commit.gpgsign", "false")
    commit(upstream, "initial", "initial.txt")
    git("-C", upstream, "push", "origin", "trunk")

    result = ws.ensure_repository(checkout, str(origin), run)

    assert result["action"] == "cloned"
    assert result["branch"] == result["default_branch"] == "trunk"
    assert set(result) == {
        "path",
        "branch",
        "default_branch",
        "dirty",
        "ahead",
        "behind",
        "diverged",
        "action",
        "error",
    }


def test_ensure_repository_rejects_owned_paths_before_fetch(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    git("init", parent)
    path = parent / "xknx"
    path.mkdir()
    marker = path / "mine.txt"
    marker.write_text("mine")

    result = ws.ensure_repository(path, "https://example.invalid/xknx.git", run)

    assert result["action"] == "conflict"
    assert "not a Git repository" in str(result["error"])
    assert marker.read_text() == "mine"


def test_ensure_repository_rejects_a_different_origin_before_fetch(tmp_path: Path) -> None:
    path = tmp_path / "xknx"
    git("init", path)
    git("-C", path, "remote", "add", "origin", "https://example.invalid/mine.git")

    result = ws.ensure_repository(path, "https://example.invalid/upstream.git", run)

    assert result["action"] == "conflict"
    assert "origin differs" in str(result["error"])


def test_safe_update_reports_fetch_failure_with_the_stable_status_keys(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    upstream = tmp_path / "upstream"
    checkout = tmp_path / "xknx"
    git("init", "--bare", origin)
    git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/trunk")
    git("clone", origin, upstream)
    git("-C", upstream, "config", "user.name", "Test User")
    git("-C", upstream, "config", "user.email", "test@example.invalid")
    git("-C", upstream, "config", "commit.gpgsign", "false")
    commit(upstream, "initial", "initial.txt")
    git("-C", upstream, "push", "origin", "trunk")
    git("clone", origin, checkout)
    origin.rename(tmp_path / "unavailable.git")

    result = ws.safe_update(checkout, run)

    assert result["action"] == "error"
    assert result["error"]
    assert set(result) == {
        "path",
        "branch",
        "default_branch",
        "dirty",
        "ahead",
        "behind",
        "diverged",
        "action",
        "error",
    }


def test_main_update_returns_and_logs_the_real_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, _, _ = create_checkout(tmp_path)
    origin.rename(tmp_path / "unavailable.git")
    monkeypatch.setattr(ws, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "repositories_for", lambda profile: ("xknx",))
    monkeypatch.setitem(ws.REPOSITORIES, "xknx", str(origin))

    assert ws.main(["dev", "update", "default", "--progress", "quiet"]) == 128
    log = next((tmp_path / ".state/logs").glob("*.log")).read_text()
    assert "returncode: 128" in log
    assert '"fetch", "origin", "--prune"' in log


def test_safe_update_reports_missing_remote_head_without_guessing(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    upstream = tmp_path / "upstream"
    checkout = tmp_path / "xknx"
    git("init", "--bare", origin)
    git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/trunk")
    git("clone", origin, upstream)
    git("-C", upstream, "config", "user.name", "Test User")
    git("-C", upstream, "config", "user.email", "test@example.invalid")
    git("-C", upstream, "config", "commit.gpgsign", "false")
    commit(upstream, "initial", "initial.txt")
    git("-C", upstream, "push", "origin", "trunk")
    git("clone", origin, checkout)
    git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/missing")
    git("-C", checkout, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")

    result = ws.safe_update(checkout, run)

    assert result["action"] == "error"
    assert result["default_branch"] is None
    assert "origin/HEAD" in str(result["error"])


def test_clone_reports_missing_remote_head_without_guessing(tmp_path: Path) -> None:
    origin, _, _ = create_checkout(tmp_path)
    checkout = tmp_path / "other" / "xknx"
    git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/missing")

    result = ws.ensure_repository(checkout, str(origin), run)

    assert result["action"] == "error"
    assert result["default_branch"] is None
    assert "origin/HEAD" in str(result["error"])


def test_safe_update_reports_fast_forward_failure(tmp_path: Path) -> None:
    _, upstream, checkout = create_checkout(tmp_path)
    commit(upstream, "update", "update.txt")
    git("-C", upstream, "push")
    (checkout / ".git" / "refs" / "heads" / "trunk.lock").write_text("locked")

    result = ws.safe_update(checkout, run)

    assert result["action"] == "error"
    assert result["behind"] == 1
    assert "cannot lock ref" in str(result["error"])


def test_safe_update_reports_post_merge_inspection_failure(tmp_path: Path) -> None:
    _, upstream, checkout = create_checkout(tmp_path)
    commit(upstream, "update", "update.txt")
    git("-C", upstream, "push")
    hook = checkout / ".git" / "hooks" / "post-merge"
    hook.write_text("#!/bin/sh\ngit symbolic-ref --delete refs/remotes/origin/HEAD\n")
    hook.chmod(0o755)
    expected_head = git("-C", upstream, "rev-parse", "HEAD").stdout.strip()

    result = ws.safe_update(checkout, run)

    assert git("-C", checkout, "rev-parse", "HEAD").stdout.strip() == expected_head
    assert result["action"] == "error"
    assert result["error"]


def test_knx_frontend_clone_initializes_only_its_pinned_frontend_submodule(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    origin, pinned = create_knx_frontend_origin(tmp_path)
    checkout = tmp_path / "workspace" / "knx-frontend"

    result = ws.ensure_repository(checkout, str(origin), run)

    assert result["action"] == "cloned"
    assert (checkout / "homeassistant-frontend" / ".git").is_file()
    assert git("-C", checkout / "homeassistant-frontend", "rev-parse", "HEAD").stdout.strip() == pinned


def test_status_cannot_hide_a_dirty_submodule_with_git_config(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    origin, _ = create_knx_frontend_origin(tmp_path)
    checkout = tmp_path / "knx-frontend"
    ws.ensure_repository(checkout, str(origin), run)
    git("-C", checkout, "config", "diff.ignoreSubmodules", "all")
    (checkout / "homeassistant-frontend" / "frontend.txt").write_text("mine")

    assert ws.repository_status(checkout, run)["dirty"] is True


def test_existing_knx_frontend_initializes_missing_submodule_without_upgrading_it(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    origin, pinned = create_knx_frontend_origin(tmp_path)
    checkout = tmp_path / "knx-frontend"
    git("clone", origin, checkout)
    assert not (checkout / "homeassistant-frontend" / ".git").exists()

    result = ws.ensure_repository(checkout, str(origin), run)

    assert result["error"] is None
    assert (checkout / "homeassistant-frontend" / ".git").is_file()
    assert git("-C", checkout / "homeassistant-frontend", "rev-parse", "HEAD").stdout.strip() == pinned


    frontend_upstream = tmp_path / "frontend-upstream"
    commit(frontend_upstream, "newer frontend", "newer.txt")
    git("-C", frontend_upstream, "push")
    result = ws.ensure_repository(checkout, str(origin), run)

    assert result["error"] is None
    assert git("-C", checkout / "homeassistant-frontend", "rev-parse", "HEAD").stdout.strip() == pinned


def test_fake_jobs_cap_concurrency_log_failures_and_resume_expected_artifacts(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "task4-fake"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys, time\n"
        "name, code, delay = sys.argv[1:]\n"
        "with open('events', 'a') as out: out.write(json.dumps([name, 'start', time.monotonic()]) + '\\n')\n"
        "print('working ' + name, flush=True)\n"
        "time.sleep(float(delay))\n"
        "with open('events', 'a') as out: out.write(json.dumps([name, 'end', time.monotonic()]) + '\\n')\n"
        "if code == '0': pathlib.Path(name + '.done').touch()\n"
        "sys.exit(int(code))\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    jobs = [ws.Job(str(index), tmp_path, (["task4-fake", str(index), "1" if index == 1 else "0", "0.02" if index == 1 else "0.15"],)) for index in range(6)]
    expected = {job.name: [tmp_path / f"{job.name}.done"] for job in jobs}
    progress = ws.Progress("quiet", log_dir=tmp_path / ".state/logs")

    results = asyncio.run(ws.run_jobs(jobs, progress=progress, expected=expected))

    events = [json.loads(line) for line in (tmp_path / "events").read_text().splitlines()]
    active = maximum = 0
    for _, event, _ in sorted(events, key=lambda event: event[2]):
        active += 1 if event == "start" else -1
        maximum = max(maximum, active)
    assert maximum == 3 and active == 0
    assert {name for name, event, _ in events if event == "start"} == {"0", "1", "2"}
    assert [result.status for result in results] == ["success", "error", "success", "skipped", "skipped", "skipped"]
    assert len(list((tmp_path / ".state/logs").glob("*.log"))) == 3

    (tmp_path / "events").unlink()
    resumed = asyncio.run(ws.run_jobs([jobs[0], jobs[2]], progress=progress, expected=expected))
    assert [result.status for result in resumed] == ["skipped", "skipped"]
    assert all(result.returncode == 0 for result in resumed)
    assert not (tmp_path / "events").exists()


def test_job_runs_commands_sequentially_and_logs_a_missing_executable(tmp_path: Path) -> None:
    progress = ws.Progress("quiet", log_dir=tmp_path / "logs")
    command = [sys.executable, "-c", "import sys; print('before failure'); print('failure detail', file=sys.stderr); sys.exit(5)"]
    job = ws.Job("sequence", tmp_path, (command, [sys.executable, "-c", "raise AssertionError('must not run')"]))
    result = asyncio.run(ws.run_jobs([job], progress=progress))[0]
    assert result.returncode == 5
    log = next((tmp_path / "logs").glob("*.log")).read_text()
    assert "before failure" in log and "failure detail" in log
    assert "must not run" not in log
    missing = ws.Job("missing", tmp_path, (["no-such-task4-executable"],))
    assert asyncio.run(ws.run_jobs([missing], progress=progress))[0].returncode == 127
    assert len(list((tmp_path / "logs").glob("*.log"))) == 2


def test_cancellation_interrupts_then_terminates_and_logs_started_job(tmp_path: Path) -> None:
    async def exercise():
        command = [sys.executable, "-c", "import pathlib, signal, time; signal.signal(signal.SIGINT, lambda *args: pathlib.Path('interrupted').touch()); pathlib.Path('started').touch(); time.sleep(30)"]
        plan = {"root": tmp_path, "tool_actions": [], "jobs": 1, "repository_jobs": [ws.Job("slow", tmp_path, (command,)), ws.Job("pending", tmp_path, (["must-not-run"],))], "project_setup_jobs": []}
        execution = asyncio.create_task(ws.bootstrap_workspace(plan, ws.Progress("quiet", log_dir=tmp_path / "logs")))
        async with asyncio.timeout(5):
            while not (tmp_path / "started").exists():
                await asyncio.sleep(0.01)
        execution.cancel()
        return await asyncio.wait_for(execution, 5)

    assert asyncio.run(exercise()) == 130
    assert (tmp_path / "interrupted").exists()
    log = next((tmp_path / "logs").glob("*.log")).read_text()
    assert "returncode: 130" in log
    assert not list((tmp_path / "logs").glob("*pending*"))


def test_repository_jobs_reuse_safe_git_updates_and_log_decisions(tmp_path: Path) -> None:
    origin, upstream, checkout = create_checkout(tmp_path)
    (checkout / "mine.txt").write_text("mine")
    commit(upstream, "new upstream", "new.txt")
    git("-C", upstream, "push")
    before = git("-C", checkout, "rev-parse", "HEAD").stdout.strip()
    jobs = ws.repository_jobs(tmp_path, {"xknx": str(origin)})
    results = asyncio.run(ws.run_jobs(jobs, progress=ws.Progress("quiet", log_dir=tmp_path / "logs")))
    assert all(result.returncode == 0 for result in results)
    assert git("-C", checkout, "rev-parse", "HEAD").stdout.strip() == before
    assert (checkout / "mine.txt").read_text() == "mine"
    assert "unchanged" in next((tmp_path / "logs").glob("*.log")).read_text()


def test_bootstrap_runs_prerequisites_before_local_repository_jobs_with_json_output(tmp_path: Path, monkeypatch, capsys) -> None:
    origin, _, _ = create_checkout(tmp_path)
    root = tmp_path / "workspace"
    root.mkdir()
    plan = [
        {"kind": "context", "platform": "macos", "profile": "default", "repositories": ["xknx"], "settings": {}},
        {"kind": "package", "command": [sys.executable, "-c", "from pathlib import Path; Path('prerequisite.done').touch(); print('ready')"]},
    ]
    monkeypatch.setattr(ws, "build_bootstrap_plan", lambda *args, **kwargs: plan)
    monkeypatch.setitem(ws.REPOSITORIES, "xknx", str(origin))
    assert ws.run_bootstrap(root, "default", yes=True, argv=["bootstrap", "--yes"], progress_mode="json") == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    tool_done = next(index for index, event in enumerate(events) if event["task"] == "tool-1" and event["status"] == "success")
    repository_start = next(index for index, event in enumerate(events) if event["task"] == "xknx" and event["status"] == "running")
    assert tool_done < repository_start
    assert events[-1]["status"] == "summary" and "exit code 0" in events[-1]["detail"]
    assert (root / "prerequisite.done").exists()
    assert (root / "xknx/.git").exists()
    assert len(list((root / ".state/logs").glob("*.log"))) == 2


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_cancellation_reaps_fast_and_sigint_ignoring_children(tmp_path: Path) -> None:
    async def exercise():
        jobs = []
        pids = []
        for name in ("fast", "stubborn"):
            command = [sys.executable, "-c", "import os, pathlib, signal, sys, time; name = sys.argv[1]; signal.signal(signal.SIGINT, lambda *args: sys.exit(0) if name == 'fast' else None); pathlib.Path(name + '.pid').write_text(str(os.getpid())); time.sleep(30)", name]
            jobs.append(ws.Job(name, tmp_path, (command,)))
        plan = {"root": tmp_path, "tool_actions": [], "jobs": 2, "repository_jobs": jobs, "project_setup_jobs": []}
        execution = asyncio.create_task(ws.bootstrap_workspace(plan, ws.Progress("quiet", log_dir=tmp_path / "logs")))
        try:
            async with asyncio.timeout(5):
                while not all((tmp_path / f"{name}.pid").exists() for name in ("fast", "stubborn")):
                    await asyncio.sleep(0.01)
            pids = [int((tmp_path / f"{name}.pid").read_text()) for name in ("fast", "stubborn")]
            execution.cancel()
            assert await asyncio.wait_for(execution, 5) == 130
            assert not any(process_is_alive(pid) for pid in pids)
            assert len(list((tmp_path / "logs").glob("*.log"))) == 2
        finally:
            for pid in pids:
                if process_is_alive(pid):
                    os.kill(pid, signal.SIGKILL)
            await asyncio.sleep(0.05)

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["zero-timeout", "nopasswd", "ctrl-c", "auth-ctrl-c"])
def test_sudo_authentication_preserves_terminal_and_ctrl_c_reaps_package_work(tmp_path: Path, mode: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    package_work = (
        "import os, pathlib, signal, sys, time\n"
        "assert os.isatty(0) and os.tcgetpgrp(0) == os.getpgrp()\n"
        "signal.signal(signal.SIGINT, lambda *args: pathlib.Path('ignored-sigint').touch())\n"
        "def terminate(*args):\n"
        "    pathlib.Path('terminated').touch()\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, terminate)\n"
        "pathlib.Path('package.pid').write_text(str(os.getpid()))\n"
        "if os.environ['TASK4_TEST_MODE'] == 'ctrl-c': time.sleep(30)\n"
    )
    sudo.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "mode = os.environ['TASK4_TEST_MODE']\n"
        "with open('sudo-commands', 'a') as out: out.write(json.dumps(args) + '\\n')\n"
        "if args == ['-v'] and mode == 'nopasswd': sys.exit(11)\n"
        "if args[0] == '-n': sys.exit(12)\n"
        "assert args in (['-v'], ['apt-get', 'install', '-y', 'git'])\n"
        "if mode != 'nopasswd':\n"
        "    pathlib.Path('auth.pid').write_text(str(os.getpid()))\n"
        "    tty = os.open('/dev/tty', os.O_RDWR)\n"
        "    assert os.tcgetpgrp(tty) == os.getpgrp()\n"
        "    os.write(tty, b'AUTH PASSWORD\\n')\n"
        "    assert os.read(tty, 100).strip() == b'answer'\n"
        "if args == ['-v']: sys.exit(0)\n"
        f"os.execv(sys.executable, [sys.executable, '-c', {package_work!r}])\n"
    )
    sudo.chmod(0o755)
    controller = (
        "import os, pathlib, sys\n"
        f"sys.path.insert(0, {str(Path(ws.__file__).parent)!r})\n"
        "import xknx_workspace as ws\n"
        f"root = pathlib.Path({str(tmp_path)!r})\n"
        f"os.environ['PATH'] = {str(bin_dir)!r} + os.pathsep + os.environ['PATH']\n"
        f"os.environ['TASK4_TEST_MODE'] = {mode!r}\n"
        "ws.build_bootstrap_plan = lambda *args, **kwargs: [{'kind': 'package', 'command': ['sudo', 'apt-get', 'install', '-y', 'git']}]\n"
        "code = ws.run_bootstrap(root, 'default', yes=False, argv=['bootstrap'], progress_mode='quiet')\n"
        f"assert code == {0 if mode in {'zero-timeout', 'nopasswd'} else 130}\n"
        "assert os.tcgetpgrp(0) == os.getpgrp()\n"
        "for path in root.glob('*.pid'):\n"
        "    try: os.kill(int(path.read_text()), 0)\n"
        "    except ProcessLookupError: pass\n"
        "    else: raise AssertionError('test child survived bootstrap')\n"
        "assert input('TERMINAL READY\\n') == 'done'\n"
    )
    pid, terminal = pty.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, "-c", controller])
    output = b""
    confirmed = False
    answered = False
    interrupted = False
    usable = False
    reaped = False
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if mode == "ctrl-c" and not interrupted and (tmp_path / "package.pid").exists():
                os.write(terminal, b"\x03")
                interrupted = True
            if select.select([terminal], [], [], 0.05)[0]:
                try:
                    chunk = os.read(terminal, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                if b"Execute this plan?" in output and not confirmed:
                    os.write(terminal, b"y\n")
                    confirmed = True
                if b"AUTH PASSWORD\r\n" in output and not answered:
                    os.write(terminal, b"\x03" if mode == "auth-ctrl-c" else b"answer\n")
                    answered = True
                if b"TERMINAL READY\r\n" in output and not usable:
                    os.write(terminal, b"done\n")
                    usable = True
            finished, status = os.waitpid(pid, os.WNOHANG)
            if finished:
                reaped = True
                break
        if not reaped:
            while time.monotonic() < deadline:
                finished, status = os.waitpid(pid, os.WNOHANG)
                if finished:
                    reaped = True
                    break
                time.sleep(0.01)
        assert reaped and os.waitstatus_to_exitcode(status) == 0, output.decode(errors="replace")
        assert confirmed and usable
        assert answered == (mode != "nopasswd")
        assert b"sudo apt-get install -y git" in output
        assert b"sudo -v" not in output and b"sudo -n" not in output
        commands = [json.loads(line) for line in (tmp_path / "sudo-commands").read_text().splitlines()]
        assert commands == [["apt-get", "install", "-y", "git"]]
        log = next((tmp_path / ".state/logs").glob("*.log")).read_text()
        assert 'argv: [["sudo", "apt-get", "install", "-y", "git"]]' in log
        if mode == "auth-ctrl-c":
            assert not (tmp_path / "package.pid").exists()
        if mode == "ctrl-c":
            assert (tmp_path / "ignored-sigint").exists()
            assert (tmp_path / "terminated").exists()
            assert "returncode: 130" in log
    finally:
        if not reaped:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        for child_pid in tmp_path.glob("*.pid"):
            if process_is_alive(int(child_pid.read_text())):
                os.kill(int(child_pid.read_text()), signal.SIGKILL)
        os.close(terminal)


def test_quoted_secrets_are_redacted_from_job_and_private_repository_output(tmp_path: Path, monkeypatch, capsys) -> None:
    secret = "privatehead'privatetail"
    monkeypatch.setenv("TASK4_TOKEN", secret)
    progress = ws.Progress("plain", log_dir=tmp_path / "logs", secrets={secret})
    job = ws.Job("quoted", tmp_path, ([sys.executable, "-c", "import sys; print(sys.argv[1])", secret],))
    assert asyncio.run(ws.run_jobs([job], progress=progress))[0].returncode == 0
    repository_job = ws.repository_jobs(tmp_path, {"xknx": str(tmp_path / secret / "missing.git")})
    assert asyncio.run(ws.run_jobs(repository_job, progress=progress))[0].returncode != 0
    content = capsys.readouterr().out + "".join(path.read_text() for path in (tmp_path / "logs").glob("*.log"))
    assert "privatehead" not in content and "privatetail" not in content
    assert "***" in content


def fake_planning_tools(monkeypatch) -> None:
    monkeypatch.setattr(ws, "detect_platform", lambda: "macos")
    monkeypatch.setattr(ws, "package_source_available", lambda platform: True)
    monkeypatch.setattr(ws, "inspect_tool", lambda name, expectation=None, **kwargs: {
        "kind": "tool", "tool": name, "installed": "1.0.0", "executable": f"/bin/{name}",
    })
    monkeypatch.setattr(ws, "inspect_docker", lambda platform: ws.docker_status(platform, None, False))


def fake_ha_packages(root: Path) -> dict[str, str]:
    paths = {}
    for module, repository in {"xknx": "xknx", "xknxproject": "xknxproject", "knx_frontend": "knx-frontend", "knx_telegram_store": "knx-telegram-store"}.items():
        path = root / repository / module / "__init__.py"
        path.parent.mkdir(parents=True)
        path.write_text("")
        paths[module] = str(path)
    interpreter = root / "home-assistant-core/.venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"sys.path[:0] = {[str(Path(path).parent.parent) for path in paths.values()]!r}\n"
        "sys.dont_write_bytecode = '-B' in sys.argv\n"
        "index = sys.argv.index('-c')\ncode = sys.argv[index + 1]\nsys.argv = sys.argv[index + 1:]\nexec(code)\n"
    )
    interpreter.chmod(0o755)
    return paths


def test_plan_decline_lists_setup_configuration_wiring_and_smoke_without_mutation(tmp_path: Path, monkeypatch, capsys) -> None:
    fake_planning_tools(monkeypatch)

    def decline(prompt):
        output = capsys.readouterr().out
        for item in ("script/setup", "script/bootstrap", "uv sync", "requirements_testing.txt", "sqlite,postgres", ".xknx-dev.toml", "smoke", "Docker", "8123", "git"):
            assert item in output
        assert not list(tmp_path.iterdir())
        return "n"

    assert ws.run_bootstrap(tmp_path, "default", yes=False, argv=["bootstrap"], input_fn=decline, progress_mode="plain") == 1
    assert not list(tmp_path.iterdir())


def test_yes_rejects_existing_default_config_and_explicit_path_allows_shown_reuse(tmp_path: Path, monkeypatch, capsys) -> None:
    fake_planning_tools(monkeypatch)

    async def no_execution(plan, progress):
        return 0

    monkeypatch.setattr(ws, "bootstrap_workspace", no_execution)
    config = tmp_path / "home-assistant-core/config"
    config.mkdir(parents=True)
    marker = config / "configuration.yaml"
    marker.write_text("default_config:\nhttp:\n  server_port: 9999\n")
    with pytest.raises(ValueError, match="XKNX_HA_CONFIG_DIR"):
        ws.run_bootstrap(tmp_path, "default", yes=True, argv=["bootstrap", "--yes"], environ={})
    assert not (tmp_path / ".xknx-dev.toml").exists()

    async def execute(plan, progress):
        ws.create_local_configuration(plan["configuration"], tmp_path)
        return 0

    monkeypatch.setattr(ws, "bootstrap_workspace", execute)
    (tmp_path / ".xknx-dev.example.toml").write_text((ws.ROOT / ".xknx-dev.example.toml").read_text())
    assert ws.run_bootstrap(tmp_path, "default", yes=True, argv=["bootstrap", "--yes"], environ={"XKNX_HA_CONFIG_DIR": str(config)}) == 0
    assert "Reuse existing Home Assistant config" in capsys.readouterr().out
    assert marker.read_text() == "default_config:\nhttp:\n  server_port: 9999\n"


def test_configuration_creation_follows_confirmation_and_does_not_overwrite(tmp_path: Path, monkeypatch) -> None:
    fake_planning_tools(monkeypatch)
    example = (ws.ROOT / ".xknx-dev.example.toml").read_text()
    (tmp_path / ".xknx-dev.example.toml").write_text(example)
    prompts = []

    def confirm(prompt):
        assert not (tmp_path / ".xknx-dev.toml").exists()
        prompts.append(prompt)
        return "y"

    async def execute(plan, progress):
        ws.create_local_configuration(plan["configuration"], tmp_path)
        ws.create_home_assistant_configuration(plan["configuration"])
        return 0

    monkeypatch.setattr(ws, "bootstrap_workspace", execute)
    assert ws.run_bootstrap(tmp_path, "default", yes=False, argv=["bootstrap"], input_fn=confirm, environ={"XKNX_HA_PORT": "9123"}) == 0
    assert len(prompts) == 1
    assert (tmp_path / ".xknx-dev.toml").read_text() == example
    yaml = tmp_path / "home-assistant-core/config/configuration.yaml"
    assert yaml.read_text() == "default_config:\nhttp:\n  server_port: 9123\n"
    yaml.write_text("developer-owned\n")
    action = ws.configuration_action(tmp_path, ws.load_settings(tmp_path, {}))
    ws.create_local_configuration(action, tmp_path)
    ws.create_home_assistant_configuration(action)
    assert yaml.read_text() == "developer-owned\n"


def test_package_status_imports_with_ha_python_and_rejects_sibling_and_symlink_paths(tmp_path: Path, monkeypatch) -> None:
    paths = fake_ha_packages(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "wrong-pythonpath"))
    status = ws.package_status(tmp_path)
    assert {name: item["path"] for name, item in status.items()} == paths
    assert all(item["ok"] for item in status.values())
    assert not list(tmp_path.rglob("__pycache__"))
    outside = tmp_path / "xknx-other.py"
    outside.write_text("")
    Path(paths["xknx"]).unlink()
    Path(paths["xknx"]).symlink_to(outside)
    status = ws.package_status(tmp_path)
    assert not status["xknx"]["ok"]
    assert status["xknx"]["path"] == str(outside)
    assert "root checkout" in status["xknx"]["error"]


def test_setup_environment_is_local_then_wiring_verifies_all_packages(tmp_path: Path, monkeypatch, capsys) -> None:
    paths = fake_ha_packages(tmp_path)
    monkeypatch.setenv("VIRTUAL_ENV", "/workspace/controller-venv")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/workspace/controller-venv")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        "assert pathlib.Path('setup.done').exists()\n"
        "pathlib.Path('wired.json').write_text(json.dumps(sys.argv[1:]))\n"
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    setup = ws.Job("setup", tmp_path, ([sys.executable, "-c", "import os, pathlib; assert 'VIRTUAL_ENV' not in os.environ; assert 'UV_PROJECT_ENVIRONMENT' not in os.environ; pathlib.Path('setup.done').touch()"],))
    plan = {"root": tmp_path, "tool_actions": [], "repository_jobs": [], "project_setup_jobs": [setup], "wire_home_assistant": ws.wire_home_assistant}
    assert asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("json", log_dir=tmp_path / "logs"))) == 0
    assert json.loads((tmp_path / "wired.json").read_text()) == [
        "pip", "install", "--python", str(tmp_path / "home-assistant-core/.venv/bin/python"),
        "-e", str(tmp_path / "xknx"), "-e", str(tmp_path / "xknxproject"),
        "-e", f"{tmp_path / 'knx-telegram-store'}[sqlite,postgres]", "-e", str(tmp_path / "knx-frontend"),
    ]
    output = capsys.readouterr().out
    assert all(path in output for path in paths.values())
    assert len(list((tmp_path / "logs").glob("*.log"))) == 2
    assert os.environ["VIRTUAL_ENV"] == "/workspace/controller-venv"


@pytest.mark.parametrize("outside_import", [False, True])
def test_confirmed_bootstrap_creates_config_sets_up_then_checks_imports(tmp_path: Path, monkeypatch, outside_import: bool) -> None:
    fake_planning_tools(monkeypatch)
    with ws.socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    paths = fake_ha_packages(tmp_path)
    if outside_import:
        outside = tmp_path / "outside.py"
        outside.write_text("")
        Path(paths["xknx"]).unlink()
        Path(paths["xknx"]).symlink_to(outside)
    (tmp_path / ".xknx-dev.example.toml").write_text((ws.ROOT / ".xknx-dev.example.toml").read_text())
    core = tmp_path / "home-assistant-core"
    script = core / "script/setup"
    script.parent.mkdir()
    script.write_text(
        f"#!{sys.executable}\nimport os, pathlib\n"
        "assert 'VIRTUAL_ENV' not in os.environ\n"
        "assert pathlib.Path('../.xknx-dev.toml').exists()\n"
        f"assert 'server_port: {port}' in pathlib.Path('config/configuration.yaml').read_text()\n"
        "pathlib.Path('setup.done').touch()\n"
    )
    script.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\nimport pathlib, sys\n"
        "if sys.argv[1] == 'sync':\n"
        "    assert not pathlib.Path('.xknx-dev.toml').exists()\n"
        "    interpreter = pathlib.Path('.workspace/.venv/bin/python')\n"
        "    interpreter.parent.mkdir(parents=True)\n    interpreter.touch()\n    raise SystemExit(0)\n"
        "assert pathlib.Path('.workspace/.venv/bin/python').exists()\n"
        "assert pathlib.Path('home-assistant-core/setup.done').exists()\n"
        "pathlib.Path('wiring.done').touch()\n"
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(ws, "repository_jobs", lambda *args: [])
    monkeypatch.setattr(ws, "project_setup_jobs", lambda *args, **kwargs: [ws.Job("Core", core, (["script/setup"],))])
    fake_knx_build(tmp_path)
    hass = core / ".venv/bin/hass"
    hass.write_text(
        f"#!{sys.executable}\nfrom http.server import HTTPServer, BaseHTTPRequestHandler\n"
        f"HTTPServer(('127.0.0.1', {port}), type('Handler', (BaseHTTPRequestHandler,), {{'do_GET': lambda self: (self.send_response(401), self.end_headers())}})).serve_forever()\n"
    )
    hass.chmod(0o755)
    monkeypatch.setattr(ws, "tmux_status", lambda: {"session": "xknx-dev", "running": False, "windows": []})
    assert ws.run_bootstrap(tmp_path, "default", yes=True, argv=["bootstrap", "--yes"], environ={"XKNX_HA_PORT": str(port)}, progress_mode="quiet") == (1 if outside_import else 0)
    assert (tmp_path / "wiring.done").exists()
    assert len(list((tmp_path / ".state/logs").glob("*.log"))) == (3 if outside_import else 4)


@pytest.mark.parametrize("with_yaml", [False, True])
def test_config_appearing_during_checkout_stops_before_setup(tmp_path: Path, capsys, with_yaml: bool) -> None:
    settings = ws.load_settings(tmp_path, {})
    action = ws.configuration_action(tmp_path, settings)
    (tmp_path / ".xknx-dev.example.toml").write_text((ws.ROOT / ".xknx-dev.example.toml").read_text())
    checkout = ws.Job("checkout", tmp_path, ([sys.executable, "-c",
        "from pathlib import Path; config = Path('home-assistant-core/config'); config.mkdir(parents=True); "
        + ("(config / 'configuration.yaml').write_text('developer-owned\\n')" if with_yaml else ""),
    ],))
    plan = {
        "root": tmp_path, "settings": settings, "configuration": action,
        "repository_jobs": [checkout], "project_setup_jobs": ws.project_setup_jobs(tmp_path, "default"),
    }
    with pytest.raises(ValueError, match="config_dir.*changed"):
        asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("json", log_dir=tmp_path / "logs")))
    config = tmp_path / "home-assistant-core/config"
    assert list(config.iterdir()) == ([config / "configuration.yaml"] if with_yaml else [])
    if with_yaml:
        assert (config / "configuration.yaml").read_text() == "developer-owned\n"
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert not any(event["task"] == "Home Assistant Core" and event["status"] == "running" for event in events)
    assert len(list((tmp_path / "logs").glob("*.log"))) == 1


@pytest.mark.parametrize("content", [
    'password = "private-config-marker"\n',
    '[unknown]\npassword = "private-config-marker"\n',
    '[profile]\npassword = "private-config-marker"\n',
    'profile = "private-config-marker"\n',
])
def test_secret_bearing_invalid_root_config_never_reaches_plan_or_logs(tmp_path: Path, monkeypatch, capsys, content: str) -> None:
    (tmp_path / ".xknx-dev.toml").write_text(content)
    monkeypatch.setattr(ws, "ROOT", tmp_path)

    def forbidden_plan(*args, **kwargs):
        raise AssertionError("Invalid settings reached the plan builder")

    monkeypatch.setattr(ws, "build_bootstrap_plan", forbidden_plan)
    assert ws.main(["bootstrap", "--yes", "--progress", "json"]) == 2
    output = capsys.readouterr()
    assert "private-config-marker" not in output.out + output.err
    assert "Error:" in output.err
    assert not (tmp_path / ".state/logs").exists()


@pytest.mark.parametrize("failure", [7, 127])
def test_wiring_preserves_command_exit_code_and_skips_smoke(tmp_path: Path, monkeypatch, capsys, failure: int) -> None:
    command = [sys.executable, "-c", "import sys; sys.exit(7)"] if failure == 7 else [str(tmp_path / "missing-uv")]
    monkeypatch.setattr(ws, "home_assistant_wiring_command", lambda root: command)

    async def forbidden_smoke(plan, progress):
        raise AssertionError("Smoke must not run after wiring fails")

    plan = {"root": tmp_path, "wire_home_assistant": ws.wire_home_assistant, "smoke_default": forbidden_smoke}
    assert asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("json", log_dir=tmp_path / "logs"))) == failure
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert f"exit code {failure}" in events[-1]["detail"]
    assert any(event["task"] == "smoke_default" and event["status"] == "skipped" for event in events)
    log = next((tmp_path / "logs").glob("*.log")).read_text()
    assert f"returncode: {failure}" in log


@pytest.mark.parametrize("existing", [False, True])
def test_custom_config_plan_always_selects_dependency_bootstrap(tmp_path: Path, monkeypatch, existing: bool) -> None:
    fake_planning_tools(monkeypatch)
    custom = tmp_path / "custom-config"
    if existing:
        custom.mkdir()
    settings = ws.load_settings(tmp_path, {"XKNX_HA_CONFIG_DIR": str(custom)})
    plan = ws.build_bootstrap_plan(tmp_path, "default", settings)
    ha = next(item for item in plan if item.get("kind") == "setup" and item["name"] == "Home Assistant Core")
    assert ha["commands"] == (["uv", "venv"], ["bash", "-c", ". .venv/bin/activate && script/bootstrap"])


@pytest.mark.parametrize("appears_during", ["checkout", "custom-creation"])
def test_custom_config_bootstrap_never_touches_late_fixed_config(tmp_path: Path, monkeypatch, appears_during: str) -> None:
    fake_planning_tools(monkeypatch)
    core = tmp_path / "home-assistant-core"
    custom = tmp_path / "custom-config"
    fixed = core / "config"
    (core / "script").mkdir(parents=True)
    (core / ".venv/bin").mkdir(parents=True)
    (core / ".venv/bin/python").touch()
    (core / ".venv/bin/activate").touch()
    for name, body in {"bootstrap": "touch bootstrap.started", "setup": "touch setup.started; echo modified > config/configuration.yaml"}.items():
        script = core / "script" / name
        script.write_text("#!/bin/sh\n" + body + "\n")
        script.chmod(0o755)
    (tmp_path / ".xknx-dev.example.toml").write_text((ws.ROOT / ".xknx-dev.example.toml").read_text())
    settings = ws.load_settings(tmp_path, {"XKNX_HA_CONFIG_DIR": str(custom)})
    planned = ws.build_bootstrap_plan(tmp_path, "default", settings)
    configuration = next(item for item in planned if item.get("kind") == "configuration")
    ha = next(item for item in planned if item.get("kind") == "setup" and item["name"] == "Home Assistant Core")
    repositories = []
    if appears_during == "checkout":
        repositories.append(ws.Job("checkout", tmp_path, ([sys.executable, "-c",
            "from pathlib import Path; config = Path('home-assistant-core/config'); config.mkdir(); (config / 'configuration.yaml').write_text('developer-owned\\n')",
        ],)))
    else:
        mkdir = Path.mkdir

        def raced_mkdir(path, *args, **kwargs):
            mkdir(path, *args, **kwargs)
            if path == custom:
                fixed.mkdir()
                (fixed / "configuration.yaml").write_text("developer-owned\n")

        monkeypatch.setattr(Path, "mkdir", raced_mkdir)
    execution = {
        "root": tmp_path, "settings": settings, "configuration": configuration,
        "repository_jobs": repositories,
        "project_setup_jobs": [ws.Job(ha["name"], Path(ha["cwd"]), ha["commands"])],
    }
    assert asyncio.run(ws.bootstrap_workspace(execution, ws.Progress("quiet", log_dir=tmp_path / "logs"))) == 0
    assert (core / "bootstrap.started").exists()
    assert not (core / "setup.started").exists()
    assert (fixed / "configuration.yaml").read_text() == "developer-owned\n"
    assert (custom / "configuration.yaml").read_text() == "default_config:\nhttp:\n  server_port: 8123\n"


def fake_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    events = tmp_path / "tmux-events.jsonl"
    state = tmp_path / "tmux-session"
    executable = bin_dir / "tmux"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "events = pathlib.Path(os.environ['FAKE_TMUX_EVENTS'])\n"
        "state = pathlib.Path(os.environ['FAKE_TMUX_STATE'])\n"
        "with events.open('a') as output: output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "command = sys.argv[1]\n"
        "if command == 'set-option':\n"
        "    phase = 'start' if sys.argv[-1] else 'clear'\n"
        "    if phase == os.environ.get('FAKE_TMUX_MARKER_FAIL'): raise SystemExit(43)\n"
        "    pathlib.Path(os.environ['FAKE_TMUX_MARKER']).write_text(sys.argv[-1])\n"
        "if command == 'has-session': raise SystemExit(0 if state.exists() else 1)\n"
        "if command == 'new-session': state.touch()\n"
        "elif command == 'new-window' and sys.argv[sys.argv.index('-n') + 1] == os.environ.get('FAKE_TMUX_FAIL_WINDOW'):\n"
        "    raise SystemExit(int(os.environ['FAKE_TMUX_WINDOW_EXIT']))\n"
        "elif command == 'list-windows':\n"
        "    for line in os.environ.get('FAKE_TMUX_WINDOWS', '').splitlines():\n"
        "        fields = line.split('\\t')\n        print('\\t'.join(fields + [''] * (7 - len(fields))))\n"
        "elif command == 'kill-session':\n"
        "    if code := int(os.environ.get('FAKE_TMUX_KILL_EXIT', '0')): raise SystemExit(code)\n"
        "    state.unlink()\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_TMUX_EVENTS", str(events))
    monkeypatch.setenv("FAKE_TMUX_STATE", str(state))
    return events, state


def tmux_events(path: Path) -> list[list[str]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def ha_tmux_line(root: Path, settings: dict) -> str:
    command = ws.home_assistant_command(root, settings["home_assistant"])
    start = shlex.quote(ws._tmux_process_shell(command, track_ha=True))
    marker = hashlib.sha256(json.dumps(command).encode()).hexdigest()
    return f"home-assistant\t0\tpython\t\t{start}\t{root / 'home-assistant-core'}\t{marker}"


def test_existing_tmux_session_attaches_without_restarting_windows(tmp_path: Path, monkeypatch) -> None:
    events, state = fake_tmux(tmp_path, monkeypatch)
    state.touch()

    result = ws.start_tmux_command_or_message("all", root=tmp_path, settings=ws.load_settings(tmp_path, {}), interactive=True)

    assert result["action"] == "attach"
    assert tmux_events(events) == [
        ["has-session", "-t", "=xknx-dev"],
        ["attach-session", "-t", "=xknx-dev"],
    ]


def test_new_tmux_session_creates_only_selected_visible_process_windows(tmp_path: Path, monkeypatch) -> None:
    events, _ = fake_tmux(tmp_path, monkeypatch)

    result = ws.start_tmux_command_or_message("docs", root=tmp_path, settings=ws.load_settings(tmp_path, {}), interactive=True)

    assert result["action"] == "create-and-attach"
    calls = tmux_events(events)
    assert calls[0] == ["has-session", "-t", "=xknx-dev"]
    assert calls[-1] == ["attach-session", "-t", "=xknx-dev"]
    creations = [call for call in calls if call[0] in {"new-session", "new-window"}]
    assert [call[call.index("-n") + 1] for call in creations] == [
        "overview", "home-assistant", "knx-frontend", "xknx-docs", "ha-docs"
    ]
    assert [call[call.index("-c") + 1] for call in creations] == [
        str(tmp_path),
        str(tmp_path / "home-assistant-core"),
        str(tmp_path / "knx-frontend"),
        str(tmp_path / "xknx/docs"),
        str(tmp_path / "home-assistant.io"),
    ]
    process_shells = [call[-1] for call in creations[1:]]
    assert str(tmp_path / "home-assistant-core/.venv/bin/hass") in process_shells[0]
    assert "--skip-pip-packages" in process_shells[0]
    assert "@xknx-ha-active" in process_shells[0]
    assert all("@xknx-ha-active" not in command for command in process_shells[1:])
    assert all(shlex.split(command)[:2] == ["/bin/sh", "-c"] for command in process_shells)
    assert "exec \"${SHELL:-/bin/sh}\" -l" in shlex.split(process_shells[0])[2]
    assert all("nohup" not in command and "&" not in command.replace("&&", "") and ".pid" not in command for command in process_shells)
    assert not list(tmp_path.glob("*.pid")) and not list((tmp_path / ".state").glob("*.pid"))


def test_tmux_status_is_read_only_and_stop_only_kills_an_existing_session(tmp_path: Path, monkeypatch, capsys) -> None:
    events, state = fake_tmux(tmp_path, monkeypatch)
    state.touch()
    monkeypatch.setenv("FAKE_TMUX_WINDOWS", "overview\t0\tzsh\t\t\t/tmp/workspace\t\nhome-assistant\t1\tpython\t7\thass --config /tmp/ha-config\t/tmp/workspace/home-assistant-core\tfailed")

    status = ws.tmux_status()
    assert status == {
        "session": "xknx-dev",
        "running": True,
        "windows": [
            {"name": "overview", "dead": False, "command": "zsh", "exit_code": None, "start_command": "", "cwd": "/tmp/workspace", "ha_active": ""},
            {"name": "home-assistant", "dead": True, "command": "python", "exit_code": 7, "start_command": "hass --config /tmp/ha-config", "cwd": "/tmp/workspace/home-assistant-core", "ha_active": "failed"},
        ],
    }
    assert ws.stop_tmux() == 0
    assert "Stopped tmux session xknx-dev." in capsys.readouterr().out
    assert ws.stop_tmux() == 0
    assert tmux_events(events) == [
        ["has-session", "-t", "=xknx-dev"],
        ["list-windows", "-t", "=xknx-dev", "-F", "#{window_name}\t#{pane_dead}\t#{pane_current_command}\t#{pane_dead_status}\t#{pane_start_command}\t#{pane_current_path}\t#{@xknx-ha-active}"],
        ["has-session", "-t", "=xknx-dev"],
        ["kill-session", "-t", "=xknx-dev"],
        ["has-session", "-t", "=xknx-dev"],
    ]


@pytest.mark.parametrize(("cleanup_exit", "session_remains"), [(0, False), (31, True)])
def test_new_window_failure_cleans_up_only_its_session_without_hiding_original_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup_exit: int, session_remains: bool
) -> None:
    events, state = fake_tmux(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_TMUX_FAIL_WINDOW", "knx-frontend")
    monkeypatch.setenv("FAKE_TMUX_WINDOW_EXIT", "23")
    monkeypatch.setenv("FAKE_TMUX_KILL_EXIT", str(cleanup_exit))

    result = ws.start_tmux_command_or_message(
        "default", root=tmp_path, settings=ws.load_settings(tmp_path, {}), interactive=True
    )

    assert result == {"action": "error", "returncode": 23}
    assert state.exists() is session_remains
    assert tmux_events(events)[-1] == ["kill-session", "-t", "=xknx-dev"]
    assert not any(call[0] == "attach-session" for call in tmux_events(events))


@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/zsh"])
def test_tmux_failure_wrapper_runs_portably_from_supported_login_shells(
    tmp_path: Path, shell: str
) -> None:
    if not Path(shell).is_file():
        pytest.skip(f"{shell} is unavailable")
    recovery = tmp_path / "recovery-shell"
    recovery.write_text("#!/bin/sh\nexit 0\n")
    recovery.chmod(0o755)

    result = subprocess.run(
        [shell, "-c", ws._tmux_process_shell([sys.executable, "-c", "raise SystemExit(7)"])],
        env={**os.environ, "SHELL": str(recovery)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "\nProcess exited with status 7.\n"


@pytest.fixture
def ha_http_server():
    class Handler(BaseHTTPRequestHandler):
        status = 200

        def do_GET(self):
            self.send_response(self.status)
            if self.status == 302:
                self.send_header("Location", "/redirect-loop")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def fake_knx_build(root: Path) -> None:
    for name in ("constants.py", "frontend_latest/manifest.json", "frontend_es5/manifest.json"):
        artifact = root / "knx-frontend/knx_frontend" / name
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("{}")


@pytest.mark.parametrize("http_code", [200, 302, 401, 500])
def test_default_smoke_with_local_http_and_local_ha_imports(tmp_path: Path, monkeypatch, ha_http_server, http_code: int) -> None:
    server, handler = ha_http_server
    handler.status = http_code
    fake_ha_packages(tmp_path)
    fake_knx_build(tmp_path)
    events, _ = fake_tmux(tmp_path, monkeypatch)
    settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": str(server.server_port)})
    result = ws.check_default_smoke(tmp_path, settings)
    assert (result["status"] == "passed") is (http_code != 500)
    assert result["checks"]["home-assistant"]["http_code"] == http_code
    assert result["checks"]["packages"]["ok"]
    assert result["checks"]["knx-frontend"]["ok"]
    assert result["checks"]["tmux"]["state"] == "actionable"
    assert "./dev start default" in result["checks"]["tmux"]["detail"]
    assert all(call[0] in {"has-session", "list-windows"} for call in tmux_events(events))


def test_smoke_names_package_frontend_and_pane_failures(tmp_path: Path, monkeypatch, ha_http_server) -> None:
    server, _ = ha_http_server
    paths = fake_ha_packages(tmp_path)
    Path(paths["xknx"]).unlink()
    events, state = fake_tmux(tmp_path, monkeypatch)
    state.touch()
    monkeypatch.setenv("FAKE_TMUX_WINDOWS", "overview\t0\tzsh\t\nhome-assistant\t0\tpython\t\nknx-frontend\t0\tzsh\t")
    settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": str(server.server_port)})
    result = ws.check_default_smoke(tmp_path, settings)
    assert result["status"] == "failed"
    assert "xknx" in result["checks"]["packages"]["detail"]
    assert not result["checks"]["knx-frontend"]["ok"]
    assert "knx-frontend" in result["checks"]["knx-frontend"]["detail"]
    assert "knx-frontend" in result["checks"]["tmux"]["detail"]
    monkeypatch.setenv("FAKE_TMUX_WINDOWS", "overview\t0\tzsh\t\nhome-assistant\t0\tpython\t\nknx-frontend\t0\tnode\t")
    assert ws.check_default_smoke(tmp_path, settings)["checks"]["knx-frontend"]["ok"]
    assert all(call[0] in {"has-session", "list-windows"} for call in tmux_events(events))


def test_collected_status_reads_git_from_disk_without_fetching_or_writing(tmp_path: Path, monkeypatch) -> None:
    _, upstream, checkout = create_checkout(tmp_path)
    old_remote = git("-C", checkout, "rev-parse", "origin/trunk").stdout
    commit(upstream, "remote change", "remote.txt")
    git("-C", upstream, "push")
    (checkout / "local.txt").write_text("developer change")
    fake_planning_tools(monkeypatch)
    events, _ = fake_tmux(tmp_path, monkeypatch)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    status = ws.collect_status(tmp_path, ws.load_settings(tmp_path, {}))
    assert status["repositories"]["xknx"]["dirty"] is True
    assert status["repositories"]["xknx"]["behind"] == 0
    assert status["repositories"]["xknx"]["action"] == "observed"
    assert git("-C", checkout, "rev-parse", "origin/trunk").stdout == old_remote
    after = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file() and path != events}
    assert after == before
    assert all(call[0] in {"has-session", "list-windows"} for call in tmux_events(events))
    lines = []
    ws.render_status(status, "human", lines.append)
    assert "xknx" in "\n".join(lines) and "dirty" in "\n".join(lines)
    lines.clear()
    ws.render_status(status, "json", lines.append)
    assert len(lines) == 1 and json.loads(lines[0]) == status


def temporary_smoke_plan(tmp_path: Path, monkeypatch, body: str) -> dict:
    fake_ha_packages(tmp_path)
    fake_knx_build(tmp_path)
    fake_tmux(tmp_path, monkeypatch)
    with ws.socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": str(port)})
    child = tmp_path / "home-assistant-core/.venv/bin/hass"
    child.write_text(
        f"#!{sys.executable}\nimport os, signal, time\nfrom pathlib import Path\n"
        f"Path({str(tmp_path / 'child.pid')!r}).write_text(str(os.getpid()))\n"
        f"port = {port}\n" + body
    )
    child.chmod(0o755)
    return {"root": tmp_path, "settings": settings, "profile": "default", "smoke_default": ws.smoke_default}


@pytest.mark.parametrize(("body", "expected"), [
    ("from http.server import BaseHTTPRequestHandler\nfrom socketserver import TCPServer\nTCPServer(('127.0.0.1', port), type('Handler', (BaseHTTPRequestHandler,), {'do_GET': lambda self: (self.send_response(401), self.end_headers())})).serve_forever()\n", 0),
    ("raise SystemExit(7)\n", 7),
    ("signal.signal(signal.SIGINT, signal.SIG_IGN)\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n", 1),
])
def test_bootstrap_smoke_always_reaps_its_foreground_child(tmp_path: Path, monkeypatch, capsys, body: str, expected: int) -> None:
    plan = temporary_smoke_plan(tmp_path, monkeypatch, body)
    assert asyncio.run(ws.smoke_default(plan, ws.Progress("json", log_dir=tmp_path / "logs"), timeout=0.4)) == expected
    pid = int((tmp_path / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert plan["smoke"]["default"]["acceptance_satisfied"] is (expected == 0)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any("temporary" in event["detail"].lower() for event in events)
    assert any("log:" in event["detail"] for event in events)
    log = next((tmp_path / "logs").glob("*.log")).read_text()
    assert f'"acceptance_satisfied": {str(expected == 0).lower()}' in log


def test_bootstrap_smoke_cancellation_reaps_child(tmp_path: Path, monkeypatch) -> None:
    plan = temporary_smoke_plan(tmp_path, monkeypatch, "time.sleep(30)\n")

    async def exercise():
        task = asyncio.create_task(ws.smoke_default(plan, ws.Progress("quiet", log_dir=tmp_path / "logs")))
        for _ in range(100):
            if (tmp_path / "child.pid").exists():
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "child.pid").read_text()), 0)


def test_bootstrap_smoke_reuses_existing_tmux_ha_without_signalling_it(tmp_path: Path, monkeypatch, ha_http_server) -> None:
    server, _ = ha_http_server
    fake_ha_packages(tmp_path)
    fake_knx_build(tmp_path)
    events, state = fake_tmux(tmp_path, monkeypatch)
    state.touch()
    settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": str(server.server_port)})
    ha_window = ha_tmux_line(tmp_path, settings)
    monkeypatch.setenv("FAKE_TMUX_WINDOWS", f"overview\t0\tzsh\t\n{ha_window}\nknx-frontend\t0\tnode\t\ntoolkit\t0\tpython\t")
    plan = {"root": tmp_path, "settings": settings, "profile": "toolkit"}
    assert asyncio.run(ws.smoke_default(plan, ws.Progress("quiet", log_dir=tmp_path / "logs"))) == 0
    assert plan["smoke"]["toolkit"]["status"] == "skipped"
    assert plan["smoke"]["toolkit"]["acceptance_satisfied"] is False
    assert state.exists() and server.fileno() != -1
    assert all(call[0] in {"has-session", "list-windows"} for call in tmux_events(events))
    monkeypatch.setenv("FAKE_TMUX_WINDOWS", f"overview\t0\tzsh\t\n{ha_window}\nknx-frontend\t0\tnode\t")
    assert asyncio.run(ws.smoke_default(plan, ws.Progress("quiet", log_dir=tmp_path / "logs"))) == 1
    assert "toolkit" in plan["smoke"]["default"]["checks"]["tmux"]["detail"]


def test_bootstrap_plans_foreground_smoke_and_build_artifact_before_execution(tmp_path: Path, monkeypatch, capsys) -> None:
    fake_planning_tools(monkeypatch)
    observed = {}

    async def capture(plan, progress):
        observed.update(plan)
        return 0

    monkeypatch.setattr(ws, "bootstrap_workspace", capture)
    assert ws.run_bootstrap(tmp_path, "default", yes=True, argv=["bootstrap", "--yes"], environ={}) == 0
    output = capsys.readouterr().out
    assert "script/build" in output and "temporary foreground" in output and "terminate" in output
    assert observed["smoke_default"] is ws.smoke_default
    assert not observed["expected_artifacts"]


def test_bootstrap_reuses_occupied_port_only_for_running_ha_pane(tmp_path: Path, monkeypatch, ha_http_server) -> None:
    server, _ = ha_http_server
    _, state = fake_tmux(tmp_path, monkeypatch)
    state.touch()
    settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": str(server.server_port)})
    monkeypatch.setenv("FAKE_TMUX_WINDOWS", ha_tmux_line(tmp_path, settings))
    assert ws.configuration_action(tmp_path, settings)["port"] == server.server_port
    monkeypatch.setenv("FAKE_TMUX_WINDOWS", "home-assistant\t0\tzsh\t")
    with pytest.raises(ValueError, match="XKNX_HA_PORT"):
        ws.configuration_action(tmp_path, settings)


def test_knx_setup_rebuilds_existing_artifacts_after_every_bootstrap(tmp_path: Path, monkeypatch) -> None:
    fake_planning_tools(monkeypatch)
    monkeypatch.setattr(ws, "nvm_shell", lambda command: command)
    scripts = tmp_path / "knx-frontend/script"
    scripts.mkdir(parents=True)
    (scripts.parent / "node_modules").mkdir()
    bootstrap = scripts / "bootstrap"
    bootstrap.write_text("#!/bin/sh\nexit 0\n")
    bootstrap.chmod(0o755)
    build = scripts / "build"
    build.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        "counter = Path('build-count')\ncounter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else '1')\n"
        "for name in ('__init__.py', 'constants.py', 'frontend_latest/manifest.json', 'frontend_es5/manifest.json'):\n"
        "    path = Path('knx_frontend') / name\n    path.parent.mkdir(parents=True, exist_ok=True)\n    path.write_text('{}')\n"
    )
    build.chmod(0o755)
    captured = {}

    async def capture(plan, progress):
        captured.update(plan)
        return 0

    controller = ws.bootstrap_workspace
    monkeypatch.setattr(ws, "bootstrap_workspace", capture)
    assert ws.run_bootstrap(tmp_path, "default", yes=True, argv=["bootstrap", "--yes"], environ={}) == 0
    plan = {"root": tmp_path, "project_setup_jobs": [job for job in captured["project_setup_jobs"] if job.name == "KNX Frontend"], "expected_artifacts": captured["expected_artifacts"]}
    progress = ws.Progress("quiet", log_dir=tmp_path / "logs")
    assert asyncio.run(controller(plan, progress)) == 0
    assert (scripts.parent / "build-count").read_text() == "1"
    assert asyncio.run(controller(plan, progress)) == 0
    assert (scripts.parent / "build-count").read_text() == "2"
    (scripts.parent / "knx_frontend/frontend_latest/manifest.json").unlink()
    assert asyncio.run(controller(plan, progress)) == 0
    assert (scripts.parent / "build-count").read_text() == "3"


def test_bootstrap_smoke_failure_code_reaches_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    plan = temporary_smoke_plan(tmp_path, monkeypatch, "raise SystemExit(7)\n")
    assert asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("json", log_dir=tmp_path / "logs"))) == 7
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert "exit code 7" in events[-1]["detail"]


@pytest.mark.parametrize("python_available", [True, False])
def test_status_launcher_uses_only_existing_interpreters_without_sync(tmp_path: Path, python_available: bool) -> None:
    controller = tmp_path / ".workspace"
    controller.mkdir()
    (controller / "xknx_workspace.py").write_text((ws.ROOT / ".workspace/xknx_workspace.py").read_text())
    launcher = tmp_path / "dev"
    launcher.write_text((ws.ROOT / "dev").read_text())
    (tmp_path / "bootstrap").write_text((ws.ROOT / "bootstrap").read_text())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dirname").symlink_to("/usr/bin/dirname")
    python = bin_dir / "python3"
    if python_available:
        python.symlink_to(sys.executable)
    else:
        python.write_text("#!/bin/sh\nexit 1\n")
        python.chmod(0o755)
        interpreter = controller / ".venv/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "if sys.argv[1:] == ['--version']: print('uv 0.11.0'); raise SystemExit(0)\n"
        "raise AssertionError('launcher must not use uv to execute Python')\n"
    )
    uv.chmod(0o755)
    before = sorted(str(path) for path in tmp_path.rglob("*"))
    result = subprocess.run(["/bin/sh", str(launcher), "status", "--format", "json"], env={"PATH": str(bin_dir), "NVM_DIR": str(tmp_path / "missing-nvm"), "UV_PROJECT_ENVIRONMENT": str(tmp_path / "unrelated")}, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["profile"] == "default"
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


def test_status_launcher_without_python_or_environment_gives_actionable_error(tmp_path: Path) -> None:
    launcher = tmp_path / "dev"
    launcher.write_text((ws.ROOT / "dev").read_text())
    (tmp_path / "bootstrap").write_text((ws.ROOT / "bootstrap").read_text())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dirname").symlink_to("/usr/bin/dirname")
    result = subprocess.run(["/bin/sh", str(launcher), "status", "--format", "json"], env={"PATH": str(bin_dir)}, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "Python 3.12+" in result.stderr and "python.org" in result.stderr
    assert not (tmp_path / ".workspace").exists()


def test_malformed_http_response_is_an_actionable_check_and_valid_status_json(tmp_path: Path, monkeypatch, capsys, ha_http_server) -> None:
    server, handler = ha_http_server
    handler.do_GET = lambda self: self.wfile.write(b"NOT HTTP\r\n\r\n")
    settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": str(server.server_port)})
    check = ws.home_assistant_http(settings)
    assert check["ok"] is False and check["http_code"] is None
    assert "home-assistant" in check["detail"] and "XKNX_HA_PORT" in check["detail"]
    fake_planning_tools(monkeypatch)
    monkeypatch.setattr(ws, "ROOT", tmp_path)
    monkeypatch.setenv("XKNX_HA_PORT", str(server.server_port))
    assert ws.main(["dev", "status", "--format", "json"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["configuration"]["home_assistant"]["ok"] is False
    assert "Traceback" not in output.out + output.err


def test_bootstrap_malformed_http_response_fails_and_reaps_child(tmp_path: Path, monkeypatch, capsys) -> None:
    plan = temporary_smoke_plan(tmp_path, monkeypatch,
        "from http.server import BaseHTTPRequestHandler\nfrom socketserver import TCPServer\n"
        f"TCPServer(('127.0.0.1', port), type('Handler', (BaseHTTPRequestHandler,), {{'do_GET': lambda self: (Path({str(tmp_path / 'bad-http-served')!r}).touch(), self.wfile.write(b'NOT HTTP\\r\\n\\r\\n'))}})).serve_forever()\n",
    )

    async def smoke(plan, progress):
        return await ws.smoke_default(plan, progress, timeout=2)

    plan["smoke_default"] = smoke
    assert asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("json", log_dir=tmp_path / "logs"))) == 1
    assert plan["smoke"]["default"]["checks"]["home-assistant"]["ok"] is False
    assert (tmp_path / "bad-http-served").exists()
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert "exit code 1" in events[-1]["detail"]
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "child.pid").read_text()), 0)


def test_unrelated_tmux_command_with_http_200_is_not_reusable_ha(tmp_path: Path, monkeypatch, capsys, ha_http_server) -> None:
    server, _ = ha_http_server
    fake_ha_packages(tmp_path)
    fake_knx_build(tmp_path)
    events, state = fake_tmux(tmp_path, monkeypatch)
    state.touch()
    monkeypatch.setenv("FAKE_TMUX_WINDOWS", "overview\t0\tzsh\t\nhome-assistant\t0\tsleep\t\nknx-frontend\t0\tnode\t")
    settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": str(server.server_port)})
    with pytest.raises(ValueError, match="XKNX_HA_PORT"):
        ws.configuration_action(tmp_path, settings)
    plan = {"root": tmp_path, "settings": settings, "profile": "default"}
    assert asyncio.run(ws.smoke_default(plan, ws.Progress("json", log_dir=tmp_path / "logs"))) == 1
    assert plan["smoke"]["default"]["acceptance_satisfied"] is False
    assert "Reusing" not in capsys.readouterr().out
    assert state.exists() and server.fileno() != -1
    assert all(call[0] in {"has-session", "list-windows"} for call in tmux_events(events))


@pytest.mark.parametrize("launch", ["direct", "wrapped", "tmux-quoted", "other-workspace", "other-config", "unrelated", "replaced-process", "unmarked", "failed-marker", "foreign-marker", "legacy-wrapper"])
def test_ha_pane_identity_matches_executable_and_resolved_config(tmp_path: Path, monkeypatch, ha_http_server, launch: str) -> None:
    root = tmp_path / "workspace with spaces"
    root.mkdir()
    server, _ = ha_http_server
    fake_ha_packages(root)
    fake_knx_build(root)
    fake_planning_tools(monkeypatch)
    settings = ws.load_settings(root, {"XKNX_HA_PORT": str(server.server_port), "XKNX_HA_CONFIG_DIR": "custom config"})
    command = [str(root / "home-assistant-core/.venv/bin/hass"), "--config", str(root / "custom config"), "--skip-pip-packages", "xknx,xknxproject,knx-frontend,knx-telegram-store"]
    marker = hashlib.sha256(json.dumps(command).encode()).hexdigest()
    if launch in {"unmarked", "legacy-wrapper"}:
        marker = ""
    elif launch == "failed-marker":
        marker = "failed"
    elif launch == "foreign-marker":
        marker = "0" * 64
    if launch == "other-workspace":
        command[0] = str(tmp_path / "other/home-assistant-core/.venv/bin/hass")
    elif launch == "other-config":
        command[2] = str(root / "different config")
    elif launch == "unrelated":
        command = ["sleep", "10000"]
    start = shlex.join(command) if launch == "direct" else ws._tmux_process_shell(command, track_ha=True)
    if launch == "legacy-wrapper":
        start = shlex.join(["/bin/sh", "-c", (
            f"{shlex.join(command)}; dev_exit=$?; "
            '[ "$dev_exit" -eq 0 ] || { printf \'\\nProcess exited with status %s.\\n\' "$dev_exit"; '
            'exec "${SHELL:-/bin/sh}" -l; }'
        )])
    if launch == "tmux-quoted":
        start = shlex.quote(start)
    window = {"name": "home-assistant", "dead": False, "command": "sleep" if launch == "replaced-process" else "python3.14", "exit_code": None, "start_command": start, "cwd": str(root / "home-assistant-core"), "ha_active": marker}
    monkeypatch.setattr(ws, "tmux_status", lambda: {"session": "xknx-dev", "running": True, "windows": [
        {"name": "overview", "dead": False, "command": "zsh", "exit_code": None}, window,
        {"name": "knx-frontend", "dead": False, "command": "node", "exit_code": None},
    ]})
    matches = launch in {"direct", "wrapped", "tmux-quoted"}
    status = ws.collect_status(root, settings)
    assert (status["processes"]["expected"]["home-assistant"]["state"] == "running") is matches
    assert status["processes"]["windows"][1]["start_command"] == start
    assert status["processes"]["windows"][1]["cwd"] == str(root / "home-assistant-core")
    if matches:
        assert ws.configuration_action(root, settings)["port"] == server.server_port
    else:
        with pytest.raises(ValueError, match="XKNX_HA_PORT"):
            ws.configuration_action(root, settings)
    plan = {"root": root, "settings": settings, "profile": "default"}
    assert asyncio.run(ws.smoke_default(plan, ws.Progress("quiet", log_dir=root / "logs"))) == (0 if matches else 1)


@pytest.mark.parametrize("shell", ["/bin/sh", "/bin/zsh"])
@pytest.mark.parametrize("marker_failure", [None, "start", "clear"])
@pytest.mark.parametrize("service_exit", [0, 7])
def test_ha_wrapper_marker_lifecycle_and_failures(tmp_path: Path, monkeypatch, shell: str, marker_failure: str | None, service_exit: int) -> None:
    if not Path(shell).is_file():
        pytest.skip(f"{shell} is unavailable")
    events, _ = fake_tmux(tmp_path, monkeypatch)
    marker = tmp_path / "pane-marker"
    marker.write_text("")
    monkeypatch.setenv("FAKE_TMUX_MARKER", str(marker))
    if marker_failure:
        monkeypatch.setenv("FAKE_TMUX_MARKER_FAIL", marker_failure)
    monkeypatch.setenv("TMUX_PANE", "%42")
    command = [sys.executable, "-c", f"from pathlib import Path; Path('service-marker').write_text(Path('pane-marker').read_text()); raise SystemExit({service_exit})"]
    expected = hashlib.sha256(json.dumps(command).encode()).hexdigest()
    recovery = tmp_path / "recovery-shell"
    recovery.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath('recovery-marker').write_text(Path('pane-marker').read_text())\n")
    recovery.chmod(0o755)

    result = subprocess.run(
        [shell, "-c", ws._tmux_process_shell(command, track_ha=True)], cwd=tmp_path,
        env={**os.environ, "SHELL": str(recovery)}, capture_output=True, text=True, timeout=5,
    )

    assert result.returncode == (service_exit if marker_failure == "clear" else 0)
    assert ("Process exited with status 7." in result.stdout) is bool(service_exit)
    assert (tmp_path / "service-marker").read_text() == ("" if marker_failure == "start" else expected)
    if marker_failure == "clear" or not service_exit:
        assert not (tmp_path / "recovery-marker").exists()
    else:
        assert (tmp_path / "recovery-marker").read_text() == ""
    assert ("lifecycle marker" in result.stdout) is bool(marker_failure)
    assert tmux_events(events) == [
        ["set-option", "-p", "-t", "%42", "@xknx-ha-active", expected],
        ["set-option", "-p", "-t", "%42", "@xknx-ha-active", ""],
    ]
    assert len(expected) == 64 and command[-1] not in marker.read_text()


def test_failed_ha_recovery_python_http_server_is_not_reusable(tmp_path: Path, monkeypatch, capsys) -> None:
    fake_ha_packages(tmp_path)
    fake_knx_build(tmp_path)
    events, state = fake_tmux(tmp_path, monkeypatch)
    state.touch()
    marker = tmp_path / "pane-marker"
    monkeypatch.setenv("FAKE_TMUX_MARKER", str(marker))
    monkeypatch.setenv("TMUX_PANE", "%42")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": str(port)})
    command = ws.home_assistant_command(tmp_path, settings["home_assistant"])
    hass = Path(command[0])
    hass.write_text("#!/bin/sh\nexit 7\n")
    hass.chmod(0o755)
    recovery = tmp_path / "recovery-shell"
    recovery.write_text(
        f"#!{sys.executable}\nfrom http.server import BaseHTTPRequestHandler\nfrom socketserver import TCPServer\n"
        f"TCPServer(('127.0.0.1', {port}), type('Handler', (BaseHTTPRequestHandler,), {{'do_GET': lambda self: (self.send_response(200), self.end_headers())}})).serve_forever()\n"
    )
    recovery.chmod(0o755)
    start = ws._tmux_process_shell(command, track_ha=True)
    child = subprocess.Popen(
        shlex.split(start), cwd=tmp_path, env={**os.environ, "SHELL": str(recovery)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ws.home_assistant_http(settings, timeout=0.1)["ok"] and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ws.home_assistant_http(settings)["http_code"] == 200
        assert marker.read_text() == ""
        monkeypatch.setenv("FAKE_TMUX_WINDOWS", f"overview\t0\tzsh\t\nhome-assistant\t0\tpython\t\t{shlex.quote(start)}\t{tmp_path / 'home-assistant-core'}\t{marker.read_text()}\nknx-frontend\t0\tnode\t")
        status = ws.process_status("default", root=tmp_path, settings=settings)
        assert status["expected"]["home-assistant"]["state"] == "command"
        assert "lifecycle marker" in status["expected"]["home-assistant"]["detail"]
        assert "./dev stop && ./dev start default" in status["expected"]["home-assistant"]["action"]
        with pytest.raises(ValueError, match="XKNX_HA_PORT"):
            ws.configuration_action(tmp_path, settings)
        plan = {"root": tmp_path, "settings": settings, "profile": "default"}
        assert asyncio.run(ws.smoke_default(plan, ws.Progress("json", log_dir=tmp_path / "logs"))) == 1
        assert plan["smoke"]["default"]["acceptance_satisfied"] is False
        assert "Reusing" not in capsys.readouterr().out
        assert child.poll() is None and state.exists()
        assert ws.home_assistant_http(settings)["ok"]
        assert all(call[0] in {"set-option", "has-session", "list-windows"} for call in tmux_events(events))
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
        child.communicate(timeout=5)
