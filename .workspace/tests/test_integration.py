import asyncio
import json
import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import xknx_workspace as ws


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
    monkeypatch.setattr(ws, "inspect_tool", lambda name, expectation=None: {
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
        "assert 'server_port: 9123' in pathlib.Path('config/configuration.yaml').read_text()\n"
        "pathlib.Path('setup.done').touch()\n"
    )
    script.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\nimport pathlib\n"
        "assert pathlib.Path('home-assistant-core/setup.done').exists()\n"
        "pathlib.Path('wiring.done').touch()\n"
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(ws, "repository_jobs", lambda *args: [])
    monkeypatch.setattr(ws, "project_setup_jobs", lambda *args, **kwargs: [ws.Job("Core", core, (["script/setup"],))])
    assert ws.run_bootstrap(tmp_path, "default", yes=True, argv=["bootstrap", "--yes"], environ={"XKNX_HA_PORT": "9123"}, progress_mode="quiet") == (1 if outside_import else 0)
    assert (tmp_path / "wiring.done").exists()
    assert len(list((tmp_path / ".state/logs").glob("*.log"))) == 2
