from __future__ import annotations

import argparse
import asyncio
import codecs
import hashlib
import json
import os
import platform as platform_module
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import tomllib
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
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

DOCKER_START_COMMANDS = {
    "macos": ["open", "-a", "Docker"],
    "ubuntu": ["sudo", "systemctl", "start", "docker"],
    "debian": ["sudo", "systemctl", "start", "docker"],
    "wsl-ubuntu": ["powershell.exe", "-Command", "Start-Process", "Docker Desktop"],
    "wsl-debian": ["powershell.exe", "-Command", "Start-Process", "Docker Desktop"],
}

TOOL_LINKS = {
    "git": "https://git-scm.com/downloads",
    "uv": "https://docs.astral.sh/uv/getting-started/installation/",
    "tmux": "https://github.com/tmux/tmux/wiki/Installing",
}

PACKAGE_SOURCE_LINKS = {
    "macos": ("homebrew", "https://brew.sh/"),
    "ubuntu": ("apt", "https://help.ubuntu.com/community/AptGet/Howto"),
    "debian": ("apt", "https://wiki.debian.org/Apt"),
    "wsl-ubuntu": ("apt", "https://help.ubuntu.com/community/AptGet/Howto"),
    "wsl-debian": ("apt", "https://wiki.debian.org/Apt"),
}

NODE_REPOSITORIES = {"home-assistant-frontend", "knx-frontend", "xknxtoolkit", "home-assistant.io"}
CONFIRMED_PLAN_ENV = "XKNX_WORKSPACE_CONFIRMED_PLAN"
CONFIRMED_PLAN_DIGEST_ENV = "XKNX_WORKSPACE_CONFIRMED_PLAN_SHA256"
NVM_RELEASE_API = "https://api.github.com/repos/nvm-sh/nvm/releases/latest"


@dataclass
class Job:
    name: str
    cwd: Path
    commands: tuple[list[str], ...]


@dataclass
class Result:
    name: str
    returncode: int
    duration: float
    status: str = ""

    def __post_init__(self) -> None:
        if not self.status:
            self.status = "success" if self.returncode == 0 else "error"


def secret_values(environ: Mapping[str, str] = os.environ) -> set[str]:
    return {value for name, value in environ.items() if value and name.upper().endswith(("TOKEN", "PASSWORD", "SECRET", "KEY"))}


def redact(text: str, secrets: set[str]) -> str:
    for value in sorted(filter(None, secrets), key=len, reverse=True):
        for form in (value.replace("'", "'\"'\"'"), value):
            text = text.replace(json.dumps(form)[1:-1], "***")
            text = text.replace(form, "***")
    return text


def display_command(command: Sequence[str], secrets: set[str] | None = None) -> str:
    values = secret_values() | (secrets or set())
    return shlex.join(redact(argument, values) for argument in command)


def write_task_log(
    directory: Path, name: str, *, command: Sequence[object], stdout: str,
    stderr: str, returncode: int, cwd: Path | None = None,
    start: datetime | None = None, end: datetime | None = None,
    duration: float = 0, secrets: set[str] | None = None,
    versions: Mapping[str, object] | None = None, decisions: Sequence[object] = (),
) -> Path:
    end = end or datetime.now(timezone.utc)
    start = start or end
    values = secret_values() | (secrets or set())
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", redact(name, values)).strip("-") or "task"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{end.strftime('%Y%m%dT%H%M%S.%fZ')}-{safe_name}.log"
    content = (
        f"task: {name}\ncwd: {cwd or Path.cwd()}\n"
        f"argv: {json.dumps(command)}\nstart: {start.isoformat()}\nend: {end.isoformat()}\n"
        f"duration: {duration:.3f}s\nreturncode: {returncode}\n"
        f"versions: {json.dumps(versions or {}, default=str)}\n"
        f"decisions: {json.dumps(decisions, default=str)}\nstdout:\n{stdout}\nstderr:\n{stderr}\n"
    )
    with path.open("x") as log:
        log.write(redact(content, values))
        log.flush()
        os.fsync(log.fileno())
    return path


class Progress:
    def __init__(
        self, mode: str = "auto", *, log_dir: Path | None = None,
        secrets: set[str] | None = None, verbose: bool = False,
        versions: Mapping[str, object] | None = None, decisions: Sequence[object] = (),
    ) -> None:
        self.mode = ("tty" if sys.stdout.isatty() else "plain") if mode == "auto" else mode
        self.log_dir = log_dir or ROOT / ".state/logs"
        self.secrets = secret_values() | (secrets or set())
        self.verbose = verbose
        self.versions = versions or {}
        self.decisions = decisions
        self.rows: dict[str, tuple[str, str]] = {}
        self.live = None
        self.first_error = False
        self.first_returncode = 0

    def emit(self, task: str, status: str, detail: str) -> None:
        task, detail = (redact(value, self.secrets) for value in (task, detail))
        detail = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", detail)
        detail = " ".join(detail.splitlines())
        self.rows[task] = (status, detail)
        if self.mode == "tty":
            try:
                self._render_tty()
                return
            except ImportError:
                self.mode = "plain"
        if self.mode == "json":
            print(json.dumps({"task": task, "status": status, "detail": detail}), flush=True)
        elif self.mode != "quiet" or status in {"planned", "summary"} or (status == "error" and not self.first_error):
            print(f"{task}: {status}: {detail}", flush=True)
        if status == "error":
            self.first_error = True

    def _render_tty(self) -> None:
        from rich.console import Console
        from rich.live import Live
        from rich.table import Table
        from rich.text import Text

        table = Table("Task", "Status", "Detail", expand=True)
        for task, (status, detail) in self.rows.items():
            table.add_row(Text(task), Text(status), Text(detail))
        if self.live is None:
            self.live = Live(table, console=Console(file=sys.stdout), refresh_per_second=8)
            self.live.start()
        else:
            self.live.update(table)

    def close(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None


async def _stop_process(process, completion) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(completion), 0.5)
            return
        except TimeoutError:
            continue
    await completion


def _set_terminal_group(terminal: int, group: int) -> None:
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        os.tcsetpgrp(terminal, group)
    finally:
        signal.signal(signal.SIGTTOU, previous)


@contextmanager
def _foreground_terminal(process, interactive: bool):
    terminal = None
    restore = False
    try:
        if interactive:
            try:
                terminal = os.open("/dev/tty", os.O_RDWR)
            except OSError:
                pass
        if terminal is not None and os.tcgetpgrp(terminal) == os.getpgrp() and process.returncode is None:
            restore = True
            _set_terminal_group(terminal, process.pid)
            os.killpg(process.pid, signal.SIGCONT)
        yield
    finally:
        if terminal is not None:
            if restore:
                _set_terminal_group(terminal, os.getpgrp())
            os.close(terminal)


async def run_job(job: Job, progress: Progress, *, command_runner=None, verify=None, interactive: bool = False) -> Result:
    start, clock_start = datetime.now(timezone.utc), time.monotonic()
    # ponytail: buffer each task's output; spool to disk if build logs exhaust memory.
    stdout: list[str] = []
    stderr: list[str] = []
    commands: list[list[str]] = []
    returncode = 0

    async def read_output(stream, output: list[str]) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        while chunk := await stream.read(65536):
            text = decoder.decode(chunk)
            output.append(text)
            lines = (pending + text).replace("\r", "\n").split("\n")
            pending = lines.pop()
            for line in lines:
                if line.strip():
                    detail = redact(line, progress.secrets)
                    progress.emit(job.name, "running", detail if progress.verbose else detail[-500:])
        tail = decoder.decode(b"", final=True)
        output.append(tail)
        if pending + tail:
            progress.emit(job.name, "running", pending + tail)

    try:
        for command in job.commands:
            commands.append(command)
            progress.emit(job.name, "running", f"$ {display_command(command, progress.secrets)}")
            if command_runner is not None:
                result = command_runner(command, cwd=job.cwd, capture_output=True, text=True, check=False)
                stdout.append(result.stdout or "")
                stderr.append(result.stderr or "")
                returncode = result.returncode
            else:
                process = await asyncio.create_subprocess_exec(
                    *command, cwd=job.cwd, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, start_new_session=not interactive,
                    process_group=0 if interactive else None,
                )
                completion = asyncio.gather(
                    read_output(process.stdout, stdout), read_output(process.stderr, stderr), process.wait()
                )
                with _foreground_terminal(process, interactive):
                    try:
                        await asyncio.shield(completion)
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        await _stop_process(process, completion)
                        raise
                returncode = 130 if process.returncode == -signal.SIGINT else process.returncode
            if returncode != 0:
                break
        if returncode == 0 and verify is not None:
            if error := verify():
                returncode = 2
                stderr.append(error)
    except OSError as error:
        returncode = 127
        stderr.append(str(error))
    except (asyncio.CancelledError, KeyboardInterrupt):
        returncode = 130
        stderr.append("Interrupted")
        raise
    finally:
        duration = time.monotonic() - clock_start
        path = write_task_log(
            progress.log_dir, job.name, cwd=job.cwd, command=commands,
            stdout="".join(stdout), stderr="".join(stderr), returncode=returncode,
            start=start, duration=duration, secrets=progress.secrets,
            versions=progress.versions, decisions=progress.decisions,
        )
        progress.emit(job.name, "success" if returncode == 0 else "error", f"Exit {returncode} after {duration:.2f}s; log: {path}")
    return Result(job.name, returncode, duration)


async def run_jobs(
    jobs: Sequence[Job], limit: int = 3, progress: Progress | None = None, *,
    runner=None, expected: Mapping[str, Sequence[Path]] | None = None,
) -> list[Result]:
    if limit < 1:
        raise ValueError("jobs must be positive")
    progress = progress or Progress()
    progress.first_returncode = 0
    queue = asyncio.Queue()
    results: dict[int, Result] = {}
    failed = False
    for index, job in enumerate(jobs):
        queue.put_nowait((index, job))

    async def worker() -> None:
        nonlocal failed
        while not failed and not queue.empty():
            index, job = queue.get_nowait()
            artifacts = (expected or {}).get(job.name, ())
            if artifacts and all(path.exists() for path in artifacts):
                results[index] = Result(job.name, 0, 0, "skipped")
                progress.emit(job.name, "skipped", "Expected artifacts already exist")
                continue
            progress.emit(job.name, "running", "Starting")
            try:
                result = await runner(job) if runner else await run_job(job, progress)
            except asyncio.CancelledError:
                failed = True
                results[index] = Result(job.name, 130, 0, "error")
                raise
            except Exception as error:
                result = Result(job.name, 1, 0)
                progress.emit(job.name, "error", str(error))
            results[index] = result
            if result.returncode != 0:
                if result.status != "skipped" and progress.first_returncode == 0:
                    progress.first_returncode = result.returncode
                failed = True

    workers = [asyncio.create_task(worker()) for _ in range(min(limit, len(jobs)))]
    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        failed = True
        for worker_task in workers:
            if not worker_task.cancelling():
                worker_task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    finally:
        while not queue.empty():
            index, job = queue.get_nowait()
            results[index] = Result(job.name, 1, 0, "skipped")
            progress.emit(job.name, "skipped", "An earlier task failed or was interrupted")
    return [results[index] for index in range(len(jobs))]


def repository_jobs(root: Path, repositories: Mapping[str, str]) -> list[Job]:
    return [
        Job(name, root, ([sys.executable, str(Path(__file__).resolve()), "_repository", str(root / name), origin],))
        for name, origin in repositories.items()
    ]


async def run_tool_actions(actions, progress: Progress, *, root: Path = ROOT, command_runner=None) -> list[Result]:
    results = []
    failed = False
    for index, item in enumerate(actions):
        name = f"tool-{item.get('tool', index)}"
        manual = item.get("kind") == "manual" and "scope" not in item
        if not manual and (item.get("kind") not in {"package", "installer"} or not item.get("command")):
            continue
        if failed:
            results.append(Result(name, 1, 0, "skipped"))
            progress.emit(name, "skipped", "An earlier prerequisite failed")
            continue
        if manual:
            progress.emit(name, "error", f"Install {item['tool']}: {item.get('link', '')}")
            results.append(Result(name, 2, 0))
            failed = True
            continue

        def verify():
            if item.get("kind") == "installer" and item.get("tool") == "nvm":
                installed = inspect_tool("nvm")["installed"]
                progress.versions = {**progress.versions, "nvm": installed}
                if not isinstance(installed, str) or (item.get("version") and installed != item["version"]):
                    return "Installed NVM version could not be verified"
            return None

        result = await run_job(
            Job(name, root, (item["command"],)), progress, command_runner=command_runner, verify=verify, interactive=True
        )
        results.append(result)
        failed = result.returncode != 0
    return results


async def bootstrap_workspace(plan: dict[str, object], progress: Progress) -> int:
    pending = [*plan.get("repository_jobs", ()), *plan.get("project_setup_jobs", ())]
    pending_callbacks = {name for name in ("wire_home_assistant", "smoke_default") if plan.get(name)}
    code = 1
    try:
        results = await run_tool_actions(
            plan.get("tool_actions", ()), progress, root=plan["root"], command_runner=plan.get("command_runner")
        )
        if not all(result.returncode == 0 for result in results):
            code = next(result.returncode for result in results if result.returncode != 0)
            return code
        if reexec := plan.get("reexec"):
            reexec()
            pending = []
            pending_callbacks.clear()
            code = 0
            return code
        pending = list(plan.get("project_setup_jobs", ()))
        results = await run_jobs(plan.get("repository_jobs", ()), plan.get("jobs", 3), progress)
        if not all(result.returncode == 0 for result in results):
            code = progress.first_returncode or 1
            return code
        pending = []
        results = await run_jobs(
            plan.get("project_setup_jobs", ()), plan.get("jobs", 3), progress, expected=plan.get("expected_artifacts")
        )
        if not all(result.returncode == 0 for result in results):
            code = progress.first_returncode or 1
            return code
        if wire := plan.get("wire_home_assistant"):
            pending_callbacks.remove("wire_home_assistant")
            if not await wire(plan, progress):
                return 1
        if smoke := plan.get("smoke_default"):
            pending_callbacks.remove("smoke_default")
            if not await smoke(plan, progress):
                return 1
        code = 0
        return code
    except (asyncio.CancelledError, KeyboardInterrupt):
        code = 130
        progress.emit("bootstrap", "error", "Interrupted")
        return code
    finally:
        for job in pending:
            progress.emit(job.name, "skipped", "A prerequisite failed or was interrupted")
        for name in sorted(pending_callbacks):
            progress.emit(name, "skipped", "A prerequisite failed or was interrupted")
        progress.emit("bootstrap", "summary", f"Finished with exit code {code}; logs: {progress.log_dir}")


def detect_platform(os_release: str | None = None, uname: str | None = None) -> str:
    uname = uname if uname is not None else f"{platform_module.system()} {platform_module.release()}"
    if uname.startswith("Darwin"):
        return "macos"
    if not uname.startswith("Linux"):
        return "unsupported"
    if os_release is None:
        try:
            os_release = Path("/etc/os-release").read_text()
        except OSError:
            os_release = ""
    match = re.search(r"^ID=['\"]?([^'\"\n]+)", os_release, re.MULTILINE)
    distribution = match.group(1) if match else "unsupported"
    return f"wsl-{distribution}" if "microsoft" in uname.lower() else distribution


def _version_tuple(version: str) -> tuple[int, ...]:
    match = re.match(r"v?(\d+(?:\.\d+)*)", version)
    parts = list(map(int, match.group(1).split("."))) if match else []
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def version_decision(tool: str, installed: str | None, expected: str, enforce: bool) -> dict[str, str | None]:
    if installed is None:
        return {"action": "install", "relation": "missing", "installed": None, "expected": expected}
    installed_version, expected_version = _version_tuple(installed), _version_tuple(expected)
    relation = "current" if installed_version == expected_version else "older" if installed_version < expected_version else "newer"
    return {
        "action": "keep" if relation == "current" or not enforce else "replace",
        "relation": relation,
        "installed": installed.lstrip("v"),
        "expected": expected.lstrip("v"),
    }


def missing_tool_actions(
    platform: str,
    tools: Mapping[str, str | None],
    apt_packages: set[str] | None = None,
    package_source_available: bool = True,
) -> list[dict[str, object]]:
    missing = [name for name, executable in tools.items() if executable is None]
    if not missing:
        return []
    if not package_source_available:
        tool, link = PACKAGE_SOURCE_LINKS[platform]
        return [{"kind": "manual", "tool": tool, "command": [], "link": link}]
    available = missing
    if platform != "macos":
        if apt_packages is None:
            apt_packages = set()
            for name in missing:
                try:
                    result = subprocess.run(
                        ["apt-cache", "show", name], capture_output=True, text=True, check=False
                    )
                except OSError:
                    break
                if result.returncode == 0:
                    apt_packages.add(name)
        available = [name for name in missing if name in apt_packages]
    actions: list[dict[str, object]] = []
    if available:
        actions.append({"kind": "package", "command": PACKAGE_COMMANDS[platform](available)})
    actions.extend(
        {"kind": "manual", "tool": name, "command": [], "link": TOOL_LINKS[name]}
        for name in missing
        if name not in available
    )
    return actions


def nvm_install_action(tag: str) -> dict[str, object]:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError(f"invalid NVM release: {tag}")
    return {
        "tool": "nvm",
        "version": tag[1:],
        "command": [
            "sh",
            "-c",
            'tmp=$(mktemp) && trap \'rm -f "$tmp"\' EXIT && '
            "curl -fsS --location --max-redirs 0 --proto '=https' "
            f'-o "$tmp" https://raw.githubusercontent.com/nvm-sh/nvm/{tag}/install.sh && '
            'PROFILE=/dev/null bash "$tmp"',
        ],
    }


def resolve_nvm_release(opener=urllib.request.urlopen) -> str:
    request = urllib.request.Request(
        NVM_RELEASE_API,
        headers={"Accept": "application/vnd.github+json"},
    )
    with opener(request, timeout=10) as response:
        if response.geturl() != NVM_RELEASE_API:
            raise ValueError(f"redirected NVM release API: {response.geturl()}")
        tag = json.loads(response.read())["tag_name"]
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError(f"invalid NVM release: {tag}")
    return tag


def nvm_shell(command: list[str]) -> list[str]:
    quoted = shlex.join(command)
    return [
        "bash",
        "-lc",
        'export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"; '
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
        f"nvm install --silent && nvm use --silent && {quoted}",
    ]


def docker_status(platform: str, executable: str | None, daemon_reachable: bool) -> dict[str, object]:
    if executable is None:
        return {
            "tool": "docker",
            "level": "warning",
            "required": False,
            "available": False,
            "link": DOCKER_LINKS[platform],
        }
    return {
        "tool": "docker",
        "level": "ok" if daemon_reachable else "info",
        "required": False,
        "available": daemon_reachable,
        "executable": executable,
        "link": DOCKER_LINKS[platform],
        "start_command": None if daemon_reachable else DOCKER_START_COMMANDS[platform],
    }


def package_source_available(platform: str) -> bool:
    commands = ("brew",) if platform == "macos" else ("apt-cache", "apt-get")
    return all(shutil.which(command) for command in commands)


def inspect_tool(name: str, expectation: str | None = None) -> dict[str, object]:
    executable = shutil.which(name)
    command = [executable, "--version"] if executable else None
    if name == "tmux" and executable:
        command = [executable, "-V"]
    elif name == "nvm":
        nvm_script = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm")) / "nvm.sh"
        if nvm_script.is_file():
            executable = str(nvm_script)
            command = [
                "bash",
                "-lc",
                'export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"; . "$NVM_DIR/nvm.sh"; nvm --version',
            ]
    installed = None
    if command:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0 and (match := re.search(r"\d+(?:\.\d+)+", result.stdout or result.stderr)):
            installed = match.group()
    status: dict[str, object] = {
        "kind": "tool",
        "tool": name,
        "executable": executable,
        "installed": installed,
    }
    if expectation:
        status.update(version_decision(name, installed, expectation, enforce=False))
    return status


def inspect_docker(platform: str) -> dict[str, object]:
    executable = shutil.which("docker")
    reachable = bool(
        executable
        and subprocess.run(
            [executable, "info"], capture_output=True, text=True, check=False
        ).returncode
        == 0
    )
    return docker_status(platform, executable, reachable)


def docs_prerequisite_actions(root: Path, profile: str) -> list[dict[str, object]]:
    if profile not in {"docs", "all"}:
        return []
    requirements = (
        ("xknx/docs", root / "xknx/docs/.ruby-version"),
        ("home-assistant.io", root / "home-assistant.io/.ruby-version"),
    )
    actions: list[dict[str, object]] = []
    ruby = inspect_tool("ruby") if any(path.is_file() for _, path in requirements) else None
    for repository, path in requirements:
        if not path.is_file():
            actions.append(
                {
                    "kind": "diagnostic",
                    "level": "warning",
                    "repository": repository,
                    "scope": "docs",
                    "blocking": True,
                    "message": f"Ruby will be checked after {repository} is available.",
                }
            )
            continue
        expected = path.read_text().strip().lstrip("v")
        decision = version_decision(
            "ruby", ruby["installed"] if ruby and isinstance(ruby["installed"], str) else None, expected, False
        )
        if decision["relation"] == "current":
            actions.append({**ruby, **decision, "repository": repository, "scope": "docs"})
        else:
            actions.append(
                {
                    "kind": "manual",
                    "tool": "ruby",
                    "command": [],
                    **decision,
                    "repository": repository,
                    "scope": "docs",
                    "blocking": True,
                    "link": "https://www.ruby-lang.org/en/documentation/installation/",
                }
            )
    bundler = inspect_tool("bundle")
    if bundler["executable"] is None:
        actions.append(
            {
                "kind": "manual",
                "tool": "bundler",
                "command": [],
                "scope": "docs",
                "blocking": True,
                "link": "https://bundler.io/guides/getting_started.html",
            }
        )
    else:
        actions.append({**bundler, "scope": "docs"})
    return actions


def build_bootstrap_plan(
    root: Path,
    profile: str,
    settings: dict[str, object],
    *,
    enforce_tool_versions: bool = False,
) -> list[dict[str, object]]:
    repositories = repositories_for(profile)
    platform = detect_platform()
    if platform not in PACKAGE_COMMANDS:
        raise ValueError(f"unsupported platform: {platform}")
    plan: list[dict[str, object]] = [
        {
            "kind": "context",
            "platform": platform,
            "profile": profile,
            "repositories": repositories,
            "settings": settings,
            "enforce_tool_versions": enforce_tool_versions,
        }
    ]
    tool_states = [inspect_tool(name) for name in ("git", "uv", "tmux")]
    plan.extend(tool_states)
    plan.extend(
        missing_tool_actions(
            platform,
            {str(item["tool"]): item["executable"] for item in tool_states},
            package_source_available=package_source_available(platform),
        )
    )
    if NODE_REPOSITORIES.intersection(repositories):
        nvm = inspect_tool("nvm")
        plan.append(nvm)
        if nvm["executable"] is None or enforce_tool_versions:
            tag = resolve_nvm_release()
            decision = version_decision(
                "nvm", nvm["installed"] if isinstance(nvm["installed"], str) else None, tag, enforce_tool_versions
            )
            if decision["action"] in {"install", "replace"}:
                plan.append({"kind": "installer", **nvm_install_action(tag), "decision": decision})
    plan.append(inspect_docker(platform))
    plan.extend(docs_prerequisite_actions(root, profile))
    return plan


def _serialized_plan(plan: Sequence[Mapping[str, object]]) -> str:
    return json.dumps(plan, sort_keys=True, separators=(",", ":"), default=str)


def plan_digest(plan: Sequence[Mapping[str, object]]) -> str:
    return hashlib.sha256(_serialized_plan(plan).encode()).hexdigest()


def validate_plan_digest(plan: Sequence[Mapping[str, object]], digest: str) -> None:
    if plan_digest(plan) != digest:
        raise ValueError("confirmed plan changed before execution")


def _package_parts(command: object) -> tuple[tuple[str, ...], set[str]] | None:
    if not isinstance(command, list):
        return None
    for prefix in (("brew", "install"), ("sudo", "apt-get", "install", "-y")):
        if tuple(command[: len(prefix)]) == prefix:
            return prefix, set(command[len(prefix) :])
    return None


def validate_reexec_plan(
    confirmed: Sequence[Mapping[str, object]], current: Sequence[Mapping[str, object]]
) -> None:
    confirmed_context = next((item for item in confirmed if item.get("kind") == "context"), None)
    current_context = next((item for item in current if item.get("kind") == "context"), None)
    if _serialized_plan([confirmed_context]) != _serialized_plan([current_context]):
        raise ValueError("confirmed bootstrap intent changed before re-exec")
    confirmed_actions = [item for item in confirmed if item.get("kind") in {"package", "installer"}]
    for action in (item for item in current if item.get("kind") in {"package", "installer"}):
        if action in confirmed_actions:
            continue
        current_package = _package_parts(action.get("command")) if action.get("kind") == "package" else None
        if current_package and any(
            confirmed_package
            and current_package[0] == confirmed_package[0]
            and current_package[1] <= confirmed_package[1]
            for candidate in confirmed_actions
            if candidate.get("kind") == "package"
            for confirmed_package in [_package_parts(candidate.get("command"))]
        ):
            continue
        raise ValueError("re-exec introduced an unconfirmed prerequisite action")


def render_plan(plan: Sequence[Mapping[str, object]], print_fn=print, *, secrets: set[str] | None = None) -> None:
    for item in plan:
        if command := item.get("command"):
            print_fn(f"$ {display_command(command, secrets)}")
        elif item.get("kind") == "context":
            print_fn(f"Platform: {item['platform']}  Profile: {item['profile']}")
            print_fn(f"Repositories: {', '.join(item['repositories'])}")
        elif item.get("level") in {"warning", "info"}:
            print_fn(f"{str(item['level']).upper()}: {item.get('message') or item.get('link', '')}")
        elif item.get("kind") == "manual":
            print_fn(f"MANUAL: install {item['tool']} — {item['link']}")


def run_bootstrap(
    root: Path,
    profile: str,
    *,
    yes: bool,
    argv: Sequence[str],
    enforce_tool_versions: bool = False,
    jobs: int = 3,
    progress_mode: str = "auto",
    verbose: bool = False,
    environ: Mapping[str, str] = os.environ,
    input_fn=input,
    runner=None,
    reexec=os.execvpe,
) -> int:
    if jobs < 1:
        raise ValueError("jobs must be positive")
    marker = environ.get(CONFIRMED_PLAN_DIGEST_ENV)
    carried = environ.get(CONFIRMED_PLAN_ENV)
    if marker or carried:
        if not marker or not carried:
            raise ValueError("incomplete confirmed plan marker")
        confirmed_plan = json.loads(carried)
        validate_plan_digest(confirmed_plan, marker)
        settings = load_settings(root, environ)
        plan = build_bootstrap_plan(
            root, profile, settings, enforce_tool_versions=enforce_tool_versions
        )
        validate_reexec_plan(confirmed_plan, plan)
    else:
        settings = load_settings(root, environ)
        plan = build_bootstrap_plan(
            root, profile, settings, enforce_tool_versions=enforce_tool_versions
        )
    uv_missing = any(
        item.get("kind") == "tool" and item.get("tool") == "uv" and item.get("executable") is None
        for item in plan
    )
    uv_will_be_installed = uv_missing and any(
        item.get("kind") == "package" and "uv" in item.get("command", ())
        for item in plan
    )

    def reexec_with_plan() -> None:
        child_environ = dict(environ)
        child_environ[CONFIRMED_PLAN_ENV] = _serialized_plan(plan)
        child_environ[CONFIRMED_PLAN_DIGEST_ENV] = plan_digest(plan)
        command = [
            shutil.which("uv") or "uv",
            "run",
            "--project",
            str(root / ".workspace"),
            "--locked",
            "python",
            str(Path(__file__).resolve()),
            *argv,
        ]
        progress.close()
        reexec(command[0], command, child_environ)

    progress = Progress(
        progress_mode, log_dir=root / ".state/logs", secrets=secret_values(environ), verbose=verbose,
        versions={str(item["tool"]): item.get("installed") for item in plan if item.get("kind") == "tool"},
        decisions=plan,
    )
    context = next((item for item in plan if item.get("kind") == "context"), {})
    execution = {
        "root": root, "profile": profile, "settings": settings,
        "tool_actions": plan, "jobs": jobs, "command_runner": runner,
        "repository_jobs": repository_jobs(root, {name: REPOSITORIES[name] for name in context.get("repositories", ())}),
        "project_setup_jobs": [],
        "reexec": reexec_with_plan if uv_will_be_installed else None,
    }
    try:
        if not marker:
            lines = []
            render_plan(plan, lines.append, secrets=progress.secrets)
            for index, line in enumerate(lines):
                progress.emit(f"plan-{index + 1}", "planned", line)
            if not yes:
                progress.close()
                prompt = "Execute this plan? [y/N] "
                if progress_mode == "json":
                    print(prompt, file=sys.stderr)
                    prompt = ""
                if input_fn(prompt).strip().lower() not in {"y", "yes"}:
                    progress.emit("bootstrap", "summary", "Plan declined")
                    return 1
        return asyncio.run(bootstrap_workspace(execution, progress))
    except KeyboardInterrupt:
        progress.emit("bootstrap", "summary", "Interrupted; exit code 130")
        return 130
    finally:
        progress.close()


def repositories_for(profile: str) -> tuple[str, ...]:
    try:
        return PROFILES[profile]
    except KeyError as error:
        raise ValueError(f"unknown profile: {profile}") from error


def update_decision(state: Mapping[str, object]) -> str:
    return (
        "fast-forward"
        if state["on_default"]
        and state["clean"]
        and state["ahead"] == 0
        and state["behind"] > 0
        and not state["diverged"]
        else "report-only"
    )


def _git(path: Path, runner, *args: str) -> subprocess.CompletedProcess[str]:
    return runner(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_output(path: Path, runner, *args: str) -> str:
    result = _git(path, runner, *args)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def repository_status(path: Path, runner=subprocess.run) -> dict[str, object]:
    default_ref = _git_output(path, runner, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    branch_result = _git(path, runner, "symbolic-ref", "--short", "HEAD")
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "HEAD"
    dirty = bool(
        _git_output(
            path,
            runner,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        )
    )
    ahead, behind = map(
        int,
        _git_output(
            path,
            runner,
            "rev-list",
            "--left-right",
            "--count",
            f"HEAD...{default_ref}",
        ).split(),
    )
    default_branch = default_ref.removeprefix("origin/")
    return {
        "path": str(path),
        "branch": branch,
        "default_branch": default_branch,
        "dirty": dirty,
        "ahead": ahead,
        "behind": behind,
        "diverged": ahead > 0 and behind > 0,
        "action": "fetched",
        "error": None,
    }


def safe_update(path: Path, runner=subprocess.run) -> dict[str, object]:
    status = None
    try:
        _git_output(path, runner, "fetch", "origin", "--prune")
        status = repository_status(path, runner)
        decision = update_decision(
            {
                **status,
                "on_default": status["branch"] == status["default_branch"],
                "clean": not status["dirty"],
            }
        )
        if decision == "fast-forward":
            ancestor = _git(
                path,
                runner,
                "merge-base",
                "--is-ancestor",
                "HEAD",
                "refs/remotes/origin/HEAD",
            )
            if ancestor.returncode > 1:
                raise RuntimeError(ancestor.stderr.strip() or "git merge-base failed")
            if ancestor.returncode:
                status["action"] = "unchanged"
                return status
            _git_output(path, runner, "merge", "--ff-only", "refs/remotes/origin/HEAD")
            status = repository_status(path, runner)
            status["action"] = "fast-forwarded"
            return status
        status["action"] = "unchanged" if decision == "report-only" else "fetched"
        return status
    except RuntimeError as error:
        if status is None:
            try:
                status = repository_status(path, runner)
            except RuntimeError:
                return _empty_repository_status(path, str(error), "error")
        status.update(action="error", error=str(error))
        return status


def _empty_repository_status(
    path: Path, error: str, action: str = "conflict"
) -> dict[str, object]:
    return {
        "path": str(path),
        "branch": None,
        "default_branch": None,
        "dirty": None,
        "ahead": None,
        "behind": None,
        "diverged": None,
        "action": action,
        "error": error,
    }


def ensure_repository(path: Path, origin: str, runner=subprocess.run) -> dict[str, object]:
    if path.exists():
        top_level = _git(path, runner, "rev-parse", "--show-toplevel")
        if top_level.returncode or Path(top_level.stdout.strip()).resolve() != path.resolve():
            return _empty_repository_status(path, "existing path is not a Git repository")
        actual_origin = _git(path, runner, "remote", "get-url", "origin")
        if actual_origin.returncode or actual_origin.stdout.strip() != origin:
            return _empty_repository_status(path, "existing repository origin differs from catalog")
        status = safe_update(path, runner)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        clone = runner(
            ["git", "clone", origin, str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if clone.returncode:
            return _empty_repository_status(
                path, clone.stderr.strip() or "git clone failed", "error"
            )
        try:
            status = repository_status(path, runner)
        except RuntimeError as error:
            return _empty_repository_status(path, str(error), "error")
        status["action"] = "cloned"
    if (
        status["error"] is None
        and path.name == "knx-frontend"
        and not (path / "homeassistant-frontend" / ".git").exists()
    ):
        try:
            _git_output(path, runner, "submodule", "update", "--init", "homeassistant-frontend")
        except RuntimeError as error:
            status.update(action="error", error=str(error))
    return status


def repository_row(status: Mapping[str, object]) -> str:
    name = Path(str(status["path"])).name
    dirty = "dirty" if status["dirty"] else "clean"
    marker = "✔" if status["action"] in {"cloned", "fetched", "fast-forwarded"} else "!"
    return (
        f"{marker} {name}  {status['branch']}  {dirty}  "
        f"↑{status['ahead']} ↓{status['behind']}  {status['action']}"
    )


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser("bootstrap")
    bootstrap.add_argument("profile", nargs="?", default="default")
    bootstrap.add_argument("--yes", action="store_true")
    bootstrap.add_argument("--jobs", type=int, default=3)
    bootstrap.add_argument("--progress", choices=("auto", "tty", "plain", "json", "quiet"), default="auto")
    bootstrap.add_argument("--verbose", action="store_true")
    bootstrap.add_argument("--enforce-tool-versions", action="store_true")

    dev = commands.add_parser("dev")
    dev_commands = dev.add_subparsers(dest="dev_command", required=True)
    start = dev_commands.add_parser("start")
    start.add_argument("profile", nargs="?", default="default")
    status = dev_commands.add_parser("status")
    status.add_argument("--format", choices=("human", "json"), default="human")
    update = dev_commands.add_parser("update")
    update.add_argument("profile", nargs="?", default="default")
    update.add_argument("--progress", choices=("auto", "tty", "plain", "json", "quiet"), default="auto")
    dev_commands.add_parser("stop")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    if argv and argv[0] == "_repository":
        if len(argv) != 3:
            return 2

        def logged_git(command, **kwargs):
            values = secret_values()
            print(f"$ {display_command(command, values)}", flush=True)
            result = subprocess.run(command, **kwargs)
            if result.stdout:
                print(redact(result.stdout, values), end="", flush=True)
            if result.stderr:
                print(redact(result.stderr, values), end="", file=sys.stderr, flush=True)
            return result

        status = ensure_repository(Path(argv[1]), argv[2], logged_git)
        print(redact(json.dumps(status), secret_values()), flush=True)
        return 1 if status["error"] else 0
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    if args.command == "bootstrap":
        try:
            return run_bootstrap(
                ROOT,
                args.profile,
                yes=args.yes,
                argv=argv,
                enforce_tool_versions=args.enforce_tool_versions,
                jobs=args.jobs,
                progress_mode=args.progress,
                verbose=args.verbose,
            )
        except (KeyError, OSError, ValueError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
