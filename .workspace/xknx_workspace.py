from __future__ import annotations

import argparse
import asyncio
import codecs
import errno
import hashlib
import json
import os
import platform as platform_module
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
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
TMUX_SESSION = "xknx-dev"
TMUX_TARGET = f"={TMUX_SESSION}"
TMUX_STATUS_FORMAT = "#{window_name}\t#{pane_dead}\t#{pane_current_command}\t#{pane_dead_status}\t#{pane_start_command}\t#{pane_current_path}"
KNX_FRONTEND_ARTIFACTS = (
    "knx-frontend/knx_frontend/__init__.py", "knx-frontend/knx_frontend/constants.py",
    "knx-frontend/knx_frontend/frontend_latest/manifest.json",
    "knx-frontend/knx_frontend/frontend_es5/manifest.json",
)


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


def redact_structure(value: object, secrets: set[str]) -> object:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {key: redact_structure(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_structure(item, secrets) for item in value)
    return value


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


async def _stop_process(process, completion, *, group: bool = True) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            if group:
                os.killpg(process.pid, sig)
            else:
                process.send_signal(sig)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(completion), 0.5)
            return
        except TimeoutError:
            continue
    await completion


async def run_job(job: Job, progress: Progress, *, command_runner=None, verify=None, env=None) -> Result:
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
                result = command_runner(command, cwd=job.cwd, capture_output=True, text=True, check=False, **({"env": env} if env is not None else {}))
                stdout.append(result.stdout or "")
                stderr.append(result.stderr or "")
                returncode = result.returncode
            else:
                sudo = Path(command[0]).name == "sudo"
                # Keep sudo with the controller for authentication and terminal Ctrl+C.
                process = await asyncio.create_subprocess_exec(
                    *command, cwd=job.cwd, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, start_new_session=not sudo, env=env,
                )
                completion = asyncio.gather(
                    read_output(process.stdout, stdout), read_output(process.stderr, stderr), process.wait()
                )
                try:
                    await asyncio.shield(completion)
                except (asyncio.CancelledError, KeyboardInterrupt):
                    await _stop_process(process, completion, group=not sudo)
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
            Job(name, root, (item["command"],)), progress,
            command_runner=command_runner, verify=verify,
        )
        results.append(result)
        failed = result.returncode != 0
    return results


async def bootstrap_workspace(plan: dict[str, object], progress: Progress) -> int:
    pending = [*plan.get("repository_jobs", ()), *plan.get("project_setup_jobs", ())]
    pending_callbacks = {name for name in ("wire_home_assistant", "smoke_default") if plan.get(name)}
    code = 1
    try:
        configuration = plan.get("configuration")
        if configuration and configuration_action(plan["root"], plan["settings"]) != configuration:
            raise ValueError("home_assistant configuration changed after confirmation; review bootstrap again")
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
        if configuration:
            create_local_configuration(configuration, plan["root"])
        pending = list(plan.get("project_setup_jobs", ()))
        results = await run_jobs(plan.get("repository_jobs", ()), plan.get("jobs", 3), progress)
        if not all(result.returncode == 0 for result in results):
            code = progress.first_returncode or 1
            return code
        if configuration:
            create_home_assistant_configuration(configuration)

        async def setup_runner(job):
            env = {name: value for name, value in os.environ.items() if name not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}}
            return await run_job(job, progress, command_runner=plan.get("command_runner"), env=env)

        blocked_docs = {}
        if plan.get("profile") in {"docs", "all"}:
            for action in docs_prerequisite_actions(plan["root"], plan["profile"]):
                if action.get("blocking"):
                    for repository in (action["repository"],) if action.get("repository") else ("xknx/docs", "home-assistant.io"):
                        blocked_docs[(plan["root"] / repository).resolve()] = action
        setup_jobs = []
        for job in plan.get("project_setup_jobs", ()):
            if action := blocked_docs.get(job.cwd.resolve()):
                progress.emit(job.name, "skipped", f"Requires {action.get('tool', 'Ruby')} {action.get('expected', '')}: {action.get('link') or action.get('message', '')}")
            else:
                setup_jobs.append(job)
        pending = []
        results = await run_jobs(
            setup_jobs, plan.get("jobs", 3), progress,
            expected=plan.get("expected_artifacts"), runner=setup_runner,
        )
        if not all(result.returncode == 0 for result in results):
            code = progress.first_returncode or 1
            return code
        if wire := plan.get("wire_home_assistant"):
            pending_callbacks.remove("wire_home_assistant")
            result = await wire(plan, progress)
            wire_code = int(not result) if isinstance(result, bool) else result
            if wire_code:
                code = wire_code
                return code
        if smoke := plan.get("smoke_default"):
            pending_callbacks.remove("smoke_default")
            result = await smoke(plan, progress)
            smoke_code = int(not result) if isinstance(result, bool) else result
            if smoke_code:
                code = smoke_code
                return code
        code = 2 if blocked_docs else 0
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


def project_setup_jobs(
    root: Path, profile: str, *, reuse_ha_config: bool = False, ha_config_dir: Path | None = None,
) -> list[Job]:
    root = root.resolve()
    default_config = root / "home-assistant-core/config"
    selected_config = (root / ha_config_dir).resolve() if ha_config_dir is not None else default_config
    ha_commands = (["script/setup"],)
    if selected_config != default_config or reuse_ha_config or default_config.exists():
        ha_commands = (["uv", "venv"], ["bash", "-c", ". .venv/bin/activate && script/bootstrap"])
    elif (root / "home-assistant-core/.venv/bin/python").is_file():
        ha_commands = (["bash", "-c", ". .venv/bin/activate && script/setup"],)
    adapters = {
        "home-assistant-core": ("Home Assistant Core", ha_commands),
        "xknx": ("XKNX", (["uv", "sync", "--group", "dev", "--locked"],)),
        "xknxproject": ("XKNX Project", (
            ["uv", "venv"],
            ["uv", "pip", "install", "--python", ".venv/bin/python", "-e", ".", "-r", "requirements_testing.txt"],
        )),
        "knx-telegram-store": ("KNX Telegram Store", (
            ["uv", "venv"],
            ["uv", "pip", "install", "--python", ".venv/bin/python", "-e", ".[dev,sqlite,postgres]"],
        )),
        "knx-frontend": ("KNX Frontend", (nvm_shell(["script/bootstrap"]), nvm_shell(["script/build"]))),
        "home-assistant-frontend": ("Home Assistant Frontend", (nvm_shell(["script/setup"]),)),
        "xknxtoolkit": ("XKNX Toolkit", (["uv", "sync"],)),
        "home-assistant.io": ("Home Assistant Docs", (["bundle", "install"], nvm_shell(["npm", "ci"]))),
    }
    jobs = []
    for repository in repositories_for(profile):
        name, commands = adapters[repository]
        if commands[0] == ["uv", "venv"] and (root / repository / ".venv/bin/python").is_file():
            commands = commands[1:]
        jobs.append(Job(name, root / repository, commands))
    if profile in {"docs", "all"}:
        jobs.append(Job("XKNX Docs", root.resolve() / "xknx/docs", (["bundle", "install"],)))
    return jobs


def project_development_jobs(root: Path, profile: str) -> list[Job]:
    adapters = {
        "knx-frontend": ("knx-frontend", nvm_shell(["script/develop"])),
        "home-assistant-frontend": ("home-assistant-frontend", nvm_shell(["script/develop"])),
        "xknxtoolkit": ("toolkit", ["uv", "run", "python", "-m", "knx_gui.main"]),
        "home-assistant.io": ("ha-docs", ["bundle", "exec", "rake", "preview"]),
    }
    jobs = []
    for name in repositories_for(profile):
        if name == "home-assistant.io":
            jobs.append(Job("xknx-docs", root.resolve() / "xknx/docs", (["bundle", "exec", "jekyll", "serve"],)))
        if name in adapters:
            jobs.append(Job(adapters[name][0], root.resolve() / name, (adapters[name][1],)))
    return jobs


def home_assistant_wiring_command(root: Path) -> list[str]:
    root = root.resolve()
    return [
        "uv", "pip", "install", "--python", str(root / "home-assistant-core/.venv/bin/python"),
        "-e", str(root / "xknx"), "-e", str(root / "xknxproject"),
        "-e", f"{root / 'knx-telegram-store'}[sqlite,postgres]", "-e", str(root / "knx-frontend"),
    ]


def home_assistant_command(root: Path, settings: Mapping[str, object]) -> list[str]:
    root = root.resolve()
    return [
        str(root / "home-assistant-core/.venv/bin/hass"),
        "--config", str((root / str(settings["config_dir"])).resolve()),
        "--skip-pip-packages", "xknx,xknxproject,knx-frontend,knx-telegram-store",
    ]


def tmux_windows(profile: str) -> tuple[str, ...]:
    return (
        "overview",
        "home-assistant",
        *(job.name for job in project_development_jobs(ROOT, profile)),
    )


def _tmux_session_exists(runner=subprocess.run) -> bool:
    return runner(
        ["tmux", "has-session", "-t", TMUX_TARGET],
        capture_output=True,
        text=True,
        check=False,
    ).returncode == 0


def _tmux_process_shell(command: Sequence[str]) -> str:
    command = (
        f"{shlex.join(command)}; dev_exit=$?; "
        '[ "$dev_exit" -eq 0 ] || { printf \'\\nProcess exited with status %s.\\n\' "$dev_exit"; '
        'exec "${SHELL:-/bin/sh}" -l; }'
    )
    return shlex.join(["/bin/sh", "-c", command])


def start_tmux_command_or_message(
    profile: str,
    *,
    root: Path = ROOT,
    settings: Mapping[str, object] | None = None,
    runner=subprocess.run,
    interactive: bool | None = None,
    print_fn=print,
) -> dict[str, object]:
    repositories_for(profile)
    interactive = sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
    if not interactive:
        print_fn(f"./dev start {profile}")
        return {"action": "print-command", "returncode": 0}
    if _tmux_session_exists(runner):
        result = runner(["tmux", "attach-session", "-t", TMUX_TARGET], check=False)
        return {"action": "attach", "returncode": result.returncode}

    root = root.resolve()
    settings = settings or load_settings(root)
    ha_settings = settings["home_assistant"]
    if not isinstance(ha_settings, Mapping):
        raise ValueError("home_assistant must be a TOML table")
    jobs = [
        Job("home-assistant", root / "home-assistant-core", (home_assistant_command(root, ha_settings),)),
        *project_development_jobs(root, profile),
    ]
    commands = [["tmux", "new-session", "-d", "-s", TMUX_SESSION, "-n", "overview", "-c", str(root)]]
    commands.extend(
        [
            "tmux", "new-window", "-d", "-t", f"{TMUX_SESSION}:",
            "-n", job.name, "-c", str(job.cwd), _tmux_process_shell(job.commands[0]),
        ]
        for job in jobs
    )
    created = False
    for command in commands:
        result = runner(command, check=False)
        if result.returncode:
            if created:
                try:
                    runner(["tmux", "kill-session", "-t", TMUX_TARGET], check=False)
                except OSError:
                    pass
            return {"action": "error", "returncode": result.returncode}
        created = True
    result = runner(["tmux", "attach-session", "-t", TMUX_TARGET], check=False)
    return {"action": "create-and-attach", "returncode": result.returncode}


def tmux_status(runner=subprocess.run) -> dict[str, object]:
    if not _tmux_session_exists(runner):
        return {"session": TMUX_SESSION, "running": False, "windows": []}
    result = runner(
        ["tmux", "list-windows", "-t", TMUX_TARGET, "-F", TMUX_STATUS_FORMAT],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return {"session": TMUX_SESSION, "running": False, "windows": [], "error": result.stderr.strip()}
    windows = []
    for line in result.stdout.splitlines():
        name, dead, command, exit_code, start_command, cwd = line.split("\t", 5)
        windows.append({
            "name": name,
            "dead": dead == "1",
            "command": command,
            "exit_code": int(exit_code) if exit_code else None,
            "start_command": start_command,
            "cwd": cwd,
        })
    return {"session": TMUX_SESSION, "running": True, "windows": windows}


def stop_tmux(runner=subprocess.run, print_fn=print) -> int:
    if not _tmux_session_exists(runner):
        print_fn(f"tmux session {TMUX_SESSION} is not running.")
        return 0
    result = runner(["tmux", "kill-session", "-t", TMUX_TARGET], check=False)
    if result.returncode == 0:
        print_fn(f"Stopped tmux session {TMUX_SESSION}.")
    return result.returncode


def print_tmux_status(status: Mapping[str, object], format: str, print_fn=print) -> None:
    if format == "json":
        print_fn(json.dumps(status))
        return
    if not status["running"]:
        print_fn(f"tmux session {status['session']} is not running.")
        return
    for window in status["windows"]:
        state = f"exited ({window['exit_code']})" if window["dead"] else f"running {window['command']}"
        print_fn(f"{window['name']}: {state}")


def configuration_action(root: Path, settings: Mapping[str, object]) -> dict[str, object]:
    validate_settings_schema(settings)
    root = root.resolve()
    ha = {**DEFAULTS["home_assistant"], **settings.get("home_assistant", {})}
    knx = {**DEFAULTS["knx"], **settings.get("knx", {})}
    secure = knx.get("secure_config_path")
    if knx["mode"] == "real" or secure is not None:
        if not isinstance(secure, str) or not secure:
            raise ValueError("knx.secure_config_path must reference a readable local file for real KNX mode")
        secure = (root / secure).resolve()
        if not secure.is_file() or not os.access(secure, os.R_OK):
            raise ValueError("knx.secure_config_path must reference a readable local file")
    port = ha["port"]
    for family, host in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        try:
            with socket.socket(family) as probe:
                probe.bind((host, port))
        except OSError as error:
            if family == socket.AF_INET6 and error.errno in {errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.EPROTONOSUPPORT}:
                continue
            if error.errno == errno.EADDRINUSE:
                processes = process_status(settings.get("profile", "default"), root=root, settings=settings)
                if processes["expected"]["home-assistant"]["state"] == "running" and home_assistant_http({"home_assistant": ha})["ok"]:
                    continue
            raise ValueError(f"home_assistant.port {port} is unavailable; set XKNX_HA_PORT to a free port") from error
    raw_path = ha["config_dir"]
    config_dir = (root / raw_path).resolve()
    if not Path(raw_path).is_absolute() and not config_dir.is_relative_to(root):
        raise ValueError("home_assistant.config_dir must stay inside the workspace; select an explicit absolute path with XKNX_HA_CONFIG_DIR")
    ancestor = next(path for path in (config_dir, *config_dir.parents) if path.exists())
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK | os.X_OK):
        raise ValueError("home_assistant.config_dir / XKNX_HA_CONFIG_DIR must be writable")
    local = root / ".xknx-dev.toml"
    if local.is_symlink() or (local.exists() and not local.is_file()):
        raise ValueError(".xknx-dev.toml must be a regular root-local file")
    return {
        "kind": "configuration", "path": str(local), "action": "reuse" if local.exists() else "create",
        "sha256": hashlib.sha256(local.read_bytes()).hexdigest() if local.exists() else None,
        "config_dir": str(config_dir), "reuse_config": config_dir.exists(),
        "explicit_config": Path(raw_path).is_absolute() or raw_path != DEFAULTS["home_assistant"]["config_dir"],
        "port": port, "knx_mode": knx["mode"], "secure_config_path": str(secure) if secure else None,
    }


def create_local_configuration(action: Mapping[str, object], root: Path) -> None:
    if action["action"] == "create":
        content = (root / ".xknx-dev.example.toml").read_text()
        with Path(action["path"]).open("x") as config:
            config.write(content)


def create_home_assistant_configuration(action: Mapping[str, object]) -> None:
    if action["reuse_config"]:
        return
    directory = Path(action["config_dir"])
    if directory.resolve() != directory or directory.exists():
        raise ValueError("home_assistant.config_dir changed after confirmation; review bootstrap again")
    try:
        directory.mkdir(parents=True)
        with (directory / "configuration.yaml").open("x") as config:
            config.write(f"default_config:\nhttp:\n  server_port: {action['port']}\n")
    except FileExistsError:
        raise ValueError("home_assistant.config_dir changed after confirmation; review bootstrap again") from None


LOCAL_PACKAGES = {
    "xknx": "xknx", "xknxproject": "xknxproject",
    "knx_frontend": "knx-frontend", "knx_telegram_store": "knx-telegram-store",
}


def home_assistant_import_command(root: Path) -> list[str]:
    root = root.resolve()
    script = (
        "import json, sys\nfrom pathlib import Path\n"
        "import knx_frontend, knx_telegram_store, xknx, xknxproject\n"
        "modules = (knx_frontend, knx_telegram_store, xknx, xknxproject)\n"
        "paths = {module.__name__: str(Path(module.__file__).resolve()) for module in modules}\n"
        "print(json.dumps(paths, indent=2), flush=True)\n"
        f"repositories = {LOCAL_PACKAGES!r}\n"
        "root = Path(sys.argv[1]).resolve()\n"
        "invalid = [name for name, path in paths.items() if not Path(path).is_relative_to((root / repositories[name]).resolve())]\n"
        "if invalid:\n    sys.exit('Imports outside root checkout: ' + ', '.join(invalid))\n"
    )
    return [str(root / "home-assistant-core/.venv/bin/python"), "-I", "-B", "-c", script, str(root)]


def package_status(root: Path, runner=subprocess.run) -> dict[str, dict[str, object]]:
    paths, error = {}, None
    try:
        result = runner(home_assistant_import_command(root), cwd=root, capture_output=True, text=True, check=False, timeout=30)
        paths = json.loads(result.stdout)
        if not isinstance(paths, dict):
            raise ValueError("expected a JSON object")
        if result.returncode:
            error = f"Home Assistant import check exited {result.returncode}"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        error = "Home Assistant imports unavailable; run bootstrap to set up and wire local packages"
    status = {}
    for module, repository in LOCAL_PACKAGES.items():
        expected = (root / repository).resolve()
        value = paths.get(module) if isinstance(paths, dict) else None
        path = Path(value).resolve() if isinstance(value, str) and Path(value).is_absolute() else None
        local = bool(path and path.is_relative_to(expected) and path.is_file())
        status[module] = {
            "path": str(path) if path else None, "expected": str(expected), "ok": local and error is None,
            "error": ("Import must resolve below its root checkout" if path and not local else error or (None if local else "Import path missing")),
        }
    return status


def _home_assistant_pane_matches(window: Mapping[str, object], root: Path, settings: Mapping[str, object]) -> bool:
    if not re.fullmatch(r"hass|python(?:\d+(?:\.\d+)*)?", Path(window["command"]).name):
        return False
    command = home_assistant_command(root, {**DEFAULTS["home_assistant"], **settings.get("home_assistant", {})})
    try:
        started = shlex.split(window.get("start_command", ""))
        # tmux quotes a single shell-command argument when formatting its argv.
        if len(started) == 1:
            started = shlex.split(started[0])
    except ValueError:
        return False
    return started == command or started == shlex.split(_tmux_process_shell(command))


def process_status(profile: str, *, root: Path = ROOT, settings: Mapping[str, object] | None = None) -> dict[str, object]:
    try:
        status = tmux_status()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        status = {"session": TMUX_SESSION, "running": False, "windows": [], "error": "tmux unavailable; run bootstrap to install it"}
    windows = {window["name"]: window for window in status["windows"]}
    expected = {}
    for name in tmux_windows(profile):
        window = windows.get(name)
        action = f"./dev start {profile}" if not status["running"] else f"tmux select-window -t {TMUX_SESSION}:{name}; inspect the pane and rerun its command"
        if window is None:
            state, detail = "missing", f"{name} window missing; {action}"
            if status["running"]:
                action = f"./dev stop && ./dev start {profile} (restart the session when ready)"
                detail = f"{name} window missing; {action}"
        elif window["dead"]:
            state, detail = "exited", f"{name} exited {window['exit_code']}; {action}"
        elif name == "home-assistant" and not _home_assistant_pane_matches(window, root, settings or DEFAULTS):
            state, detail = "command", f"home-assistant pane does not match this workspace's HA executable/config or current HA process; {action}"
        elif name != "overview" and Path(window["command"]).name in {"sh", "bash", "zsh", "fish", "dash", "ksh"}:
            state, detail = "command", f"{name} has a shell, not a confirmed service; {action}"
        else:
            state, detail, action = "running", f"{name} running {window['command']}", None
        expected[name] = {"state": state, "detail": detail, "action": action}
    return {**status, "profile": profile, "expected": expected}


class _NoHTTPRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def home_assistant_http(settings: Mapping[str, object], *, timeout: float = 2) -> dict[str, object]:
    url = f"http://127.0.0.1:{settings['home_assistant']['port']}/"
    code = None
    try:
        # A redirect itself proves reachability; never follow it to another host.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoHTTPRedirect())
        with opener.open(url, timeout=timeout) as response:
            code = response.status
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
    except (OSError, ValueError, HTTPException):
        pass
    ok = code in {200, 302, 401}
    detail = f"{url} HTTP {code}" if code is not None else f"{url} is unreachable"
    if not ok:
        detail += f"; inspect {TMUX_SESSION}:home-assistant or .state/logs and verify home_assistant.port / XKNX_HA_PORT"
    return {"ok": ok, "url": url, "http_code": code, "detail": detail}


def frontend_status(root: Path, processes: Mapping[str, object]) -> dict[str, object]:
    artifacts = [root / path for path in KNX_FRONTEND_ARTIFACTS]
    built = all(path.is_file() for path in artifacts)
    pane = processes["expected"]["knx-frontend"]
    # ponytail: the documented Node watcher pane is the development readiness signal;
    # use an upstream health endpoint if the adapter gains one.
    serving = pane["state"] == "running" and any(
        window["name"] == "knx-frontend" and (window["command"].startswith("node") or window["command"].startswith("gulp"))
        for window in processes["windows"]
    )
    return {
        "ok": built or serving, "state": "built" if built else "serving" if serving else "missing",
        "artifacts": [str(path) for path in artifacts],
        "detail": "KNX frontend build artifacts exist" if built else "KNX frontend development watcher is active" if serving else
        f"KNX frontend build missing; run ./bootstrap {processes.get('profile', 'default')} or inspect {TMUX_SESSION}:knx-frontend (script/build / script/develop)",
    }


def check_default_smoke(root: Path, settings: Mapping[str, object], *, processes=None, packages=None, http=None) -> dict[str, object]:
    processes = processes if processes is not None else process_status(settings["profile"], root=root, settings=settings)
    packages = packages if packages is not None else package_status(root)
    failed = [f"{name}: {item['error']}" for name, item in packages.items() if not item["ok"]]
    inactive = [item["detail"] for item in processes["expected"].values() if item["state"] != "running"]
    process_ok = not processes["running"] or not inactive
    checks = {
        "home-assistant": http if http is not None else home_assistant_http(settings),
        "packages": {"ok": not failed, "detail": "; ".join(failed) + "; inspect .state/logs; run bootstrap to wire local packages" if failed else "All four HA imports resolve below their root checkouts"},
        "knx-frontend": frontend_status(root, processes),
        "tmux": {"ok": process_ok, "state": "running" if not inactive else "actionable", "detail": "; ".join(inactive) if inactive else "All expected tmux windows are running"},
    }
    ok = all(item["ok"] for item in checks.values())
    return {"status": "passed" if ok else "failed", "acceptance_satisfied": ok, "checks": checks}


TOOLKIT_GROUP_ADDRESS = "1/1/1"
TOOLKIT_GROUP_VALUE = 1


def toolkit_smoke(root: Path) -> dict[str, object]:
    return {
        "status": "skipped", "acceptance_satisfied": False,
        "group_address": TOOLKIT_GROUP_ADDRESS, "value": TOOLKIT_GROUP_VALUE,
        "reason": "xknxtoolkit has no stable public non-interactive virtual endpoint readiness signal or group-write send command/API for its running pane. VirtualRouter.state/send_cemi are GUI internals; no telegram roundtrip or persistence record was verified.",
        "source": "https://github.com/XKNX/xknxtoolkit/tree/693ed48a3eb5e5ef7c8470a57572abbac24a3935/apps/knx-gui",
    }


async def smoke_default(plan: dict[str, object], progress: Progress, *, timeout: float = 60) -> int:
    root = plan["root"]
    settings = {**plan["settings"], "profile": plan.get("profile", plan["settings"]["profile"])}
    processes = process_status(settings["profile"], root=root, settings=settings)
    command = home_assistant_command(root, settings["home_assistant"])
    process = completion = None
    code, stdout, stderr = 1, b"", b""
    start, clock_start = datetime.now(timezone.utc), time.monotonic()
    unverified_pane = processes["running"] and processes["expected"]["home-assistant"]["state"] != "running"
    try:
        if unverified_pane:
            http = {**home_assistant_http(settings), "ok": False, "detail": processes["expected"]["home-assistant"]["detail"]}
            progress.emit("smoke-home-assistant", "error", http["detail"])
        elif processes["running"]:
            progress.emit("smoke-home-assistant", "running", f"Reusing {TMUX_SESSION}:home-assistant; inspect that pane for output")
        else:
            progress.emit("smoke-home-assistant", "running", f"Starting temporary foreground child: {display_command(command, progress.secrets)}")
            process = await asyncio.create_subprocess_exec(
                *command, cwd=root / "home-assistant-core", stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True,
            )
            completion = asyncio.create_task(process.communicate())
        deadline = time.monotonic() + timeout
        while not unverified_pane:
            http = await asyncio.to_thread(home_assistant_http, settings, timeout=min(2, max(0.01, deadline - time.monotonic())))
            if process is not None and process.returncode is not None:
                code = process.returncode or 1
                http = {**http, "ok": False, "detail": f"Temporary Home Assistant exited {process.returncode}; inspect .state/logs"}
                break
            if http["ok"] or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.1)
        result = check_default_smoke(root, settings, processes=processes, http=http)
        plan["smoke"] = {"status": "completed", "default": result}
        for name, check in result["checks"].items():
            progress.emit(f"smoke-{name}", "success" if check["ok"] else "error", check["detail"])
        if plan.get("profile", settings["profile"]) in {"toolkit", "all"}:
            plan["smoke"]["toolkit"] = toolkit_smoke(root)
            progress.emit("smoke-toolkit", "skipped", plan["smoke"]["toolkit"]["reason"])
        code = 0 if result["acceptance_satisfied"] else code
        return code
    except OSError as error:
        code = 127
        stderr = str(error).encode()
        plan["smoke"] = {"status": "completed", "default": {"status": "failed", "acceptance_satisfied": False, "reason": "Home Assistant could not start; inspect .state/logs and run bootstrap"}}
        return code
    except (asyncio.CancelledError, KeyboardInterrupt):
        code = 130
        raise
    finally:
        if process is not None:
            progress.emit("smoke-home-assistant", "running", "Stopping and reaping temporary foreground Home Assistant child")
            if not completion.done():
                await _stop_process(process, completion)
            stdout, stderr = await completion
        log = write_task_log(
            progress.log_dir, "smoke-home-assistant", command=[command] if process is not None else [],
            cwd=root / "home-assistant-core", stdout=stdout.decode(errors="replace") + "\n" + json.dumps(plan.get("smoke", {})), stderr=stderr.decode(errors="replace"),
            returncode=code, start=start, duration=time.monotonic() - clock_start,
            secrets=progress.secrets, versions=progress.versions, decisions=progress.decisions,
        )
        progress.emit("smoke-home-assistant", "success" if code == 0 else "error", f"Smoke exit {code}; log: {log}")


async def wire_home_assistant(plan: Mapping[str, object], progress: Progress) -> int:
    root = plan["root"]
    result = await run_job(
        Job("Home Assistant editable packages", root, (home_assistant_wiring_command(root), home_assistant_import_command(root))),
        progress, command_runner=plan.get("command_runner"),
    )
    return result.returncode


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


def collect_status(root: Path, settings: Mapping[str, object] | None = None, *, smoke=None) -> dict[str, object]:
    root = root.resolve()
    conflicts = []
    try:
        settings = load_settings(root) if settings is None else settings
        validate_settings_schema(settings)
    except (OSError, tomllib.TOMLDecodeError):
        conflicts.append("Cannot read .xknx-dev.toml as TOML; repair the local configuration (showing defaults)")
        settings = DEFAULTS
    except ValueError as error:
        conflicts.append(f"{error}; repair .xknx-dev.toml or its environment override (showing defaults)")
        settings = DEFAULTS
    effective = {
        "profile": settings.get("profile", "default"),
        "home_assistant": {**DEFAULTS["home_assistant"], **settings.get("home_assistant", {})},
        "knx": {**DEFAULTS["knx"], **settings.get("knx", {})},
    }
    profile, platform = effective["profile"], detect_platform()
    raw_config = effective["home_assistant"]["config_dir"]
    config_dir = (root / raw_config).resolve()
    if not Path(raw_config).is_absolute() and not config_dir.is_relative_to(root):
        conflicts.append("home_assistant.config_dir escapes the workspace; use an explicit absolute XKNX_HA_CONFIG_DIR")
    if config_dir.exists() and not config_dir.is_dir():
        conflicts.append("home_assistant.config_dir must be a directory")
    secure = effective["knx"].get("secure_config_path")
    if effective["knx"]["mode"] == "real" or secure:
        if not secure or not (root / secure).is_file() or not os.access(root / secure, os.R_OK):
            conflicts.append("knx.secure_config_path must reference a readable local file for real KNX mode")
    local = root / ".xknx-dev.toml"
    if local.is_symlink() or (local.exists() and not local.is_file()):
        conflicts.append(".xknx-dev.toml must be a regular root-local file")

    tools = {}
    names = ["git", "uv", "tmux", "nvm", "node"]
    if profile in {"docs", "all"}:
        names.extend(["ruby", "bundle"])
    for name in names:
        try:
            state = inspect_tool(name)
        except (OSError, subprocess.TimeoutExpired):
            state = {"tool": name, "installed": None, "executable": None, "error": "Version check unavailable; run bootstrap"}
        tools[name] = {**state, "expected": None, "relation": "unconstrained" if state["installed"] else "missing"}
    for name, version_file in (("node", ".nvmrc"), ("ruby", ".ruby-version")):
        if name not in tools:
            continue
        requirements = {}
        for repository in (*repositories_for(profile), *(('xknx/docs',) if profile in {'docs', 'all'} else ())):
            path = root / repository / version_file
            if path.is_file():
                try:
                    expected = path.read_text().strip()
                except OSError:
                    continue
                if re.fullmatch(r"v?\d+(?:\.\d+)*", expected):
                    requirements[repository] = version_decision(name, tools[name]["installed"], expected, False)
                else:
                    requirements[repository] = {"expected": expected, "relation": "unresolved locally"}
        tools[name]["requirements"] = requirements
    try:
        docker = inspect_docker(platform) if platform in DOCKER_LINKS else {
            "tool": "docker", "required": False, "available": False, "level": "info", "error": "Unsupported platform; inspect Docker manually",
        }
    except (OSError, subprocess.TimeoutExpired):
        docker = {"tool": "docker", "required": False, "available": False, "level": "info", "error": "Docker daemon check unavailable"}
    docker = {**docker, "installed": bool(docker.get("executable")), "daemon_reachable": docker["available"]}

    def read_git(command, **kwargs):
        return subprocess.run(command, **kwargs, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}, timeout=10)

    repositories = {}
    for name in REPOSITORIES:
        path = root / name
        if not path.exists():
            next_profile = profile if name in repositories_for(profile) else next(key for key, names in PROFILES.items() if name in names)
            state = _empty_repository_status(path, f"Missing checkout; run ./bootstrap {next_profile}", "missing")
        elif not (path / ".git").exists():
            state = _empty_repository_status(path, "Existing path is not a Git checkout; inspect it before bootstrap", "conflict")
        else:
            try:
                state = {**repository_status(path, read_git), "action": "observed"}
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                state = _empty_repository_status(path, f"Git observation failed; inspect {path}: {error}", "error")
        repositories[name] = {**state, "selected": name in repositories_for(profile), "comparison": "origin default branch from local refs; no fetch"}
    processes = process_status(profile, root=root, settings=effective)
    packages = package_status(root)
    packages["knx_frontend"]["frontend"] = frontend_status(root, processes)
    status = {
        "workspace": str(root), "profile": profile, "platform": platform,
        "configuration": {"effective": effective, "conflicts": conflicts, "config_dir": str(config_dir), "home_assistant": home_assistant_http(effective)},
        "tools": tools, "docker": docker, "repositories": repositories, "packages": packages,
        "processes": processes, "smoke": smoke if smoke is not None else {"status": "not-run"},
    }
    if profile in {"toolkit", "all"} and smoke is None:
        status["smoke"]["toolkit"] = toolkit_smoke(root)
    return redact_structure(status, secret_values())


def render_status(status: Mapping[str, object], format: str = "human", print_fn=print) -> None:
    if format == "json":
        print_fn(json.dumps(status))
        return
    rows = [
        ("Workspace", status["workspace"]), ("Profile / platform", f"{status['profile']} / {status['platform']}"),
        ("Configuration", json.dumps(status["configuration"]["effective"])),
        ("Conflicts", "; ".join(status["configuration"]["conflicts"]) or "none"),
        ("Home Assistant", status["configuration"]["home_assistant"]["detail"]),
    ]
    for name, item in status["tools"].items():
        requirements = "; ".join(f"{repo}: expects {value['expected']} ({value['relation']})" for repo, value in item.get("requirements", {}).items())
        rows.append((name, f"{item['installed'] or 'unavailable'} ({item['relation']})" + (f"; {requirements}" if requirements else "")))
    docker = status["docker"]
    rows.append(("Docker (optional)", "daemon reachable" if docker["available"] else f"daemon unavailable; {docker.get('error') or docker.get('link', '')}"))
    for name, item in status["repositories"].items():
        rows.append((name, item["error"] or f"{item['branch']} {'dirty' if item['dirty'] else 'clean'} ↑{item['ahead']} ↓{item['behind']} diverged={item['diverged']} (local refs)"))
    for name, item in status["packages"].items():
        rows.append((f"HA import {name}", f"{item['path'] or 'missing'}" + (f"; {item['error']}" if item['error'] else "")))
    rows.append(("KNX frontend", status["packages"]["knx_frontend"]["frontend"]["detail"]))
    rows.extend((f"tmux {name}", item["detail"]) for name, item in status["processes"]["expected"].items())
    rows.append(("Smoke", json.dumps(status["smoke"])))
    width = max(len(str(name)) for name, _ in rows)
    for name, detail in rows:
        print_fn(f"{name:<{width}}  {detail}")


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
    validate_settings_schema(settings)
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
    configuration = configuration_action(root, settings)
    plan.append(configuration)
    plan.extend(
        {"kind": "setup", "name": job.name, "cwd": str(job.cwd), "commands": job.commands}
        for job in project_setup_jobs(
            root, profile, reuse_ha_config=configuration["reuse_config"], ha_config_dir=Path(configuration["config_dir"]),
        )
    )
    plan.append({"kind": "wiring", "command": home_assistant_wiring_command(root)})
    plan.append({"kind": "smoke", "command": home_assistant_import_command(root)})
    plan.append({"kind": "smoke", "command": home_assistant_command(root, {**DEFAULTS["home_assistant"], **settings.get("home_assistant", {})}), "lifecycle": "temporary foreground Home Assistant; wait for HTTP, then terminate and reap before bootstrap returns; reuse an existing tmux Home Assistant pane"})
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
    project_kinds = {"configuration", "setup", "wiring", "smoke"}
    if _serialized_plan([item for item in confirmed if item.get("kind") in project_kinds]) != _serialized_plan([item for item in current if item.get("kind") in project_kinds]):
        raise ValueError("re-exec introduced an unconfirmed project action")
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
            label = f"{item['kind']}: " if item.get("kind") in {"wiring", "smoke"} else ""
            print_fn(f"{label}$ {display_command(command, secrets)}")
            if lifecycle := item.get("lifecycle"):
                print_fn(lifecycle)
        elif item.get("kind") == "context":
            print_fn(f"Platform: {item['platform']}  Profile: {item['profile']}")
            print_fn(f"Repositories: {', '.join(item['repositories'])}")
        elif item.get("kind") == "tool":
            print_fn(f"Tool {item['tool']}: {item.get('installed') or 'unavailable'}")
        elif item.get("kind") == "setup":
            for command in item["commands"]:
                print_fn(f"Setup {item['name']} ({item['cwd']}): $ {display_command(command, secrets)}")
        elif item.get("kind") == "configuration":
            print_fn(f"{str(item['action']).capitalize()} local configuration: {item['path']}")
            verb = "Reuse existing" if item["reuse_config"] else "Create"
            print_fn(f"{verb} Home Assistant config: {item['config_dir']} (expected port {item['port']})")
            print_fn(f"KNX mode: {item['knx_mode']}" + (" (hardware-free; no KNX connection is created)" if item["knx_mode"] == "automatic" else f"; secure material reference: {item['secure_config_path']}"))
            if item["reuse_config"]:
                print_fn("WARNING: Existing Home Assistant configuration is reused; verify its HTTP port and configured integrations before starting.")
        elif item.get("level") in {"warning", "info"}:
            label = "Docker (optional): " if item.get("tool") == "docker" else ""
            print_fn(f"{str(item['level']).upper()}: {label}{item.get('message') or item.get('link', '')}")
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
    configuration = next((item for item in plan if item.get("kind") == "configuration"), None)
    execution = {
        "root": root, "profile": profile, "settings": settings,
        "tool_actions": plan, "jobs": jobs, "command_runner": runner,
        "repository_jobs": repository_jobs(root, {name: REPOSITORIES[name] for name in context.get("repositories", ())}),
        "project_setup_jobs": [Job(item["name"], Path(item["cwd"]), tuple(item["commands"])) for item in plan if item.get("kind") == "setup"],
        "configuration": configuration,
        "wire_home_assistant": wire_home_assistant if any(item.get("kind") == "wiring" for item in plan) else None,
        "smoke_default": smoke_default if any(item.get("kind") == "smoke" for item in plan) else None,
        "expected_artifacts": {"KNX Frontend": [root / path for path in KNX_FRONTEND_ARTIFACTS]},
        "reexec": reexec_with_plan if uv_will_be_installed else None,
    }
    try:
        if not marker:
            lines = []
            render_plan(plan, lines.append, secrets=progress.secrets)
            for index, line in enumerate(lines):
                progress.emit(f"plan-{index + 1}", "planned", line)
            if yes and configuration and configuration["reuse_config"] and not configuration["explicit_config"]:
                raise ValueError("home_assistant.config_dir already exists; confirm reuse interactively or explicitly select its absolute path with XKNX_HA_CONFIG_DIR")
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


def validate_settings_schema(settings: Mapping[str, object]) -> None:
    if set(settings) - {"profile", "home_assistant", "knx"}:
        raise ValueError("root configuration accepts only profile, home_assistant, and knx")
    profile = settings.get("profile", DEFAULTS["profile"])
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError("profile must be one of: " + ", ".join(PROFILES))
    for section, keys in (("home_assistant", {"port", "config_dir"}), ("knx", {"mode", "secure_config_path"})):
        values = settings.get(section, {})
        if not isinstance(values, dict):
            raise ValueError(f"{section} must be a TOML table")
        if set(values) - keys:
            raise ValueError(f"{section} accepts only " + ", ".join(sorted(keys)))
        for key, value in values.items():
            if key == "port":
                if type(value) is not int or not 1 <= value <= 65535:
                    raise ValueError("home_assistant.port / XKNX_HA_PORT must be an integer in 1..65535")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"{section}.{key} must be a nonempty string")
            elif key == "mode" and value not in {"automatic", "real"}:
                raise ValueError("knx.mode / XKNX_KNX_MODE must be automatic or real")


def load_settings(root: Path = ROOT, environ: Mapping[str, str] = os.environ) -> dict[str, object]:
    settings = {"profile": DEFAULTS["profile"], "home_assistant": dict(DEFAULTS["home_assistant"]), "knx": dict(DEFAULTS["knx"])}
    path = root / ".xknx-dev.toml"
    if path.exists():
        loaded = tomllib.loads(path.read_text())
        validate_settings_schema(loaded)
        settings["profile"] = loaded.get("profile", settings["profile"])
        for section in ("home_assistant", "knx"):
            settings[section].update(loaded.get(section, {}))
    if port := environ.get("XKNX_HA_PORT"):
        try:
            settings["home_assistant"]["port"] = int(port)
        except ValueError:
            raise ValueError("home_assistant.port / XKNX_HA_PORT must be an integer in 1..65535") from None
    if config_dir := environ.get("XKNX_HA_CONFIG_DIR"):
        settings["home_assistant"]["config_dir"] = config_dir
    if mode := environ.get("XKNX_KNX_MODE"):
        settings["knx"]["mode"] = mode
    validate_settings_schema(settings)
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
        print(json.dumps(redact_structure(status, secret_values())), flush=True)
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
    if args.dev_command == "start":
        try:
            return int(start_tmux_command_or_message(args.profile, root=ROOT)["returncode"])
        except (KeyError, OSError, ValueError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
    if args.dev_command == "status":
        render_status(collect_status(ROOT), args.format)
        return 0
    if args.dev_command == "stop":
        try:
            return stop_tmux()
        except OSError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
