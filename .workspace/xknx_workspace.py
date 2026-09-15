from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as platform_module
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
import urllib.request
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


def render_plan(plan: Sequence[Mapping[str, object]], print_fn=print) -> None:
    for item in plan:
        if command := item.get("command"):
            print_fn(f"$ {shlex.join(command)}")
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
    environ: Mapping[str, str] = os.environ,
    input_fn=input,
    runner=subprocess.run,
    reexec=os.execvpe,
) -> int:
    marker = environ.get(CONFIRMED_PLAN_DIGEST_ENV)
    carried = environ.get(CONFIRMED_PLAN_ENV)
    if marker or carried:
        if not marker or not carried:
            raise ValueError("incomplete confirmed plan marker")
        confirmed_plan = json.loads(carried)
        validate_plan_digest(confirmed_plan, marker)
        settings = load_settings(root, environ)
        current_plan = build_bootstrap_plan(
            root, profile, settings, enforce_tool_versions=enforce_tool_versions
        )
        validate_reexec_plan(confirmed_plan, current_plan)
        return 0
    settings = load_settings(root, environ)
    plan = build_bootstrap_plan(
        root, profile, settings, enforce_tool_versions=enforce_tool_versions
    )
    render_plan(plan)
    if not yes and input_fn("Execute this plan? [y/N] ").strip().lower() not in {"y", "yes"}:
        return 1
    uv_missing = any(
        item.get("kind") == "tool" and item.get("tool") == "uv" and item.get("executable") is None
        for item in plan
    )
    uv_will_be_installed = uv_missing and any(
        item.get("kind") == "package" and "uv" in item.get("command", ())
        for item in plan
    )
    for item in plan:
        if item.get("kind") not in {"package", "installer"} or not item.get("command"):
            continue
        result = runner(item["command"], check=False)
        if result.returncode:
            return int(result.returncode)
        if item.get("kind") == "installer" and item.get("tool") == "nvm":
            installed = inspect_tool("nvm")["installed"]
            if not isinstance(installed, str) or (
                item.get("version") and installed != item["version"]
            ):
                return 2
    if any(item.get("kind") == "manual" and "scope" not in item for item in plan):
        return 2
    if uv_will_be_installed:
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
        reexec(command[0], command, child_environ)
    return 0


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser("bootstrap")
    bootstrap.add_argument("profile", nargs="?", default="default")
    bootstrap.add_argument("--yes", action="store_true")
    bootstrap.add_argument("--jobs", type=int)
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
                argv=list(argv) if argv is not None else sys.argv[1:],
                enforce_tool_versions=args.enforce_tool_versions,
            )
        except (KeyError, OSError, ValueError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
