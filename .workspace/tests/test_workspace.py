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


@pytest.mark.parametrize(
    ("argv",),
    [
        (["bootstrap", "default", "--yes", "--jobs", "2", "--progress", "plain", "--verbose", "--enforce-tool-versions"],),
        (["dev", "start", "frontend"],),
        (["dev", "status", "--format", "json"],),
        (["dev", "update", "docs", "--progress", "quiet"],),
        (["dev", "stop"],),
    ],
)
def test_main_accepts_the_public_command_surface(argv: list[str]) -> None:
    assert ws.main(argv) == 0
