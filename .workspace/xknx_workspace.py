from __future__ import annotations

import argparse
import os
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
        build_parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
