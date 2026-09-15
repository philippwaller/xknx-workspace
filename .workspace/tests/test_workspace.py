import json
import subprocess
from io import BytesIO
from pathlib import Path
from subprocess import CompletedProcess

import pytest

import xknx_workspace as ws


class FakeResponse(BytesIO):
    def __init__(
        self,
        payload: bytes,
        url: str = "https://api.github.com/repos/nvm-sh/nvm/releases/latest",
    ) -> None:
        super().__init__(payload)
        self.url = url

    def geturl(self) -> str:
        return self.url


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
    ("uname", "os_release", "expected"),
    [
        ("Darwin", "", "macos"),
        ("Linux", 'ID=ubuntu\nVERSION_ID="24.04"', "ubuntu"),
        ("Linux", 'ID=debian\nVERSION_ID="13"', "debian"),
        ("Linux microsoft-standard-WSL2", "ID=ubuntu\n", "wsl-ubuntu"),
    ],
)
def test_detect_platform(uname: str, os_release: str, expected: str) -> None:
    assert ws.detect_platform(os_release=os_release, uname=uname) == expected


def test_default_platform_detection_includes_the_kernel_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws.platform_module, "system", lambda: "Linux")
    monkeypatch.setattr(ws.platform_module, "release", lambda: "microsoft-standard-WSL2")
    assert ws.detect_platform(os_release="ID=debian\n") == "wsl-debian"


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


def test_versions_are_compared_numerically_and_enforced_only_on_request() -> None:
    assert ws.version_decision("nvm", "0.9.0", "0.40.3", enforce=True)["action"] == "replace"
    assert ws.version_decision("nvm", "0.41.0", "0.40.3", enforce=False)["relation"] == "newer"
    assert ws.version_decision("ruby", "3.4", "3.4.0", enforce=False)["relation"] == "current"


def test_missing_package_source_is_a_manual_action() -> None:
    assert ws.missing_tool_actions(
        "macos", {"git": None}, package_source_available=False
    ) == [
        {
            "kind": "manual",
            "tool": "homebrew",
            "command": [],
            "link": "https://brew.sh/",
        }
    ]


def test_nvm_installer_is_versioned_official_source() -> None:
    assert ws.nvm_install_action("v0.40.3") == {
        "tool": "nvm",
        "version": "0.40.3",
        "command": [
            "sh",
            "-c",
            'tmp=$(mktemp) && trap \'rm -f "$tmp"\' EXIT && '
            "curl -fsS --location --max-redirs 0 --proto '=https' "
            '-o "$tmp" https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh && '
            'PROFILE=/dev/null bash "$tmp"',
        ],
    }


def test_nvm_installer_propagates_a_curl_failure(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/sh\nexit 7\n")
    curl.chmod(0o755)

    result = subprocess.run(
        ws.nvm_install_action("v0.40.3")["command"],
        env={"HOME": str(tmp_path), "PATH": f"{bin_dir}:/usr/bin:/bin"},
        check=False,
    )

    assert result.returncode != 0


def test_bootstrap_verifies_nvm_after_the_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = [{"kind": "installer", "tool": "nvm", "command": ["install-nvm"]}]
    monkeypatch.setattr(ws, "build_bootstrap_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(ws, "load_settings", lambda *args: {})
    monkeypatch.setattr(
        ws,
        "inspect_tool",
        lambda name: {"kind": "tool", "tool": name, "executable": None, "installed": None},
    )

    assert ws.run_bootstrap(
        tmp_path,
        "default",
        yes=True,
        argv=["bootstrap", "default", "--yes"],
        environ={},
        runner=lambda command, **kwargs: CompletedProcess(command, 0),
    ) == 2


def test_missing_docker_is_warning_not_failure() -> None:
    result = ws.docker_status("macos", executable=None, daemon_reachable=False)
    assert result["level"] == "warning"
    assert result["required"] is False
    assert result["link"].startswith("https://docs.docker.com/")


def test_unreachable_docker_daemon_has_a_platform_start_action() -> None:
    result = ws.docker_status("ubuntu", executable="/usr/bin/docker", daemon_reachable=False)
    assert result["level"] == "info"
    assert result["start_command"] == ["sudo", "systemctl", "start", "docker"]


def test_nvm_release_is_resolved_from_an_exact_official_tag() -> None:
    opener = lambda *_args, **_kwargs: FakeResponse(b'{"tag_name": "v0.40.3"}')
    assert ws.resolve_nvm_release(opener=opener) == "v0.40.3"


def test_nvm_release_rejects_a_floating_ref() -> None:
    opener = lambda *_args, **_kwargs: FakeResponse(b'{"tag_name": "master"}')
    with pytest.raises(ValueError, match="invalid NVM release"):
        ws.resolve_nvm_release(opener=opener)


def test_nvm_release_rejects_an_api_redirect() -> None:
    opener = lambda *_args, **_kwargs: FakeResponse(
        b'{"tag_name": "v0.40.3"}',
        "https://api.github.com/repositories/612230/releases/latest",
    )
    with pytest.raises(ValueError, match="redirected NVM release API"):
        ws.resolve_nvm_release(opener=opener)


def test_nvm_shell_sources_nvm_only_for_the_child_command() -> None:
    assert ws.nvm_shell(["npm", "run", "build docs"]) == [
        "bash",
        "-lc",
        'export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"; '
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; '
        "nvm install --silent && nvm use --silent && npm run 'build docs'",
    ]


def test_inspect_tool_reports_the_observed_numeric_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(
        ws.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, "git version 2.45.1\n", ""),
    )
    result = ws.inspect_tool("git", "2.40.0")
    assert result["executable"] == "/usr/bin/git"
    assert result["installed"] == "2.45.1"
    assert result["relation"] == "newer"


def test_bootstrap_plan_contains_context_tools_nvm_and_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ws, "detect_platform", lambda: "macos")
    monkeypatch.setattr(ws, "package_source_available", lambda platform: True)
    monkeypatch.setattr(
        ws,
        "inspect_tool",
        lambda name, expectation=None: {
            "kind": "tool",
            "tool": name,
            "executable": None if name in {"uv", "nvm"} else f"/bin/{name}",
            "installed": None,
        },
    )
    monkeypatch.setattr(ws, "resolve_nvm_release", lambda: "v0.40.3")
    monkeypatch.setattr(
        ws,
        "inspect_docker",
        lambda platform: ws.docker_status(platform, executable=None, daemon_reachable=False),
    )

    plan = ws.build_bootstrap_plan(tmp_path, "default", {"profile": "default"})

    assert plan[0] == {
        "kind": "context",
        "platform": "macos",
        "profile": "default",
        "repositories": ws.repositories_for("default"),
        "settings": {"profile": "default"},
        "enforce_tool_versions": False,
    }
    assert any(item.get("command") == ["brew", "install", "uv"] for item in plan)
    assert any(item.get("tool") == "nvm" and item.get("version") == "0.40.3" for item in plan)
    assert any(item.get("tool") == "docker" and item.get("required") is False for item in plan)


def test_docs_prerequisites_are_scoped_and_never_use_mise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for relative in ("xknx/docs/.ruby-version", "home-assistant.io/.ruby-version"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True)
        path.write_text("3.4.5\n")
    monkeypatch.setattr(
        ws,
        "inspect_tool",
        lambda name, expectation=None: {
            "kind": "tool",
            "tool": name,
            "executable": None,
            "installed": None,
        },
    )
    actions = ws.docs_prerequisite_actions(tmp_path, "docs")
    assert {action["tool"] for action in actions if action["kind"] == "manual"} == {"ruby", "bundler"}
    assert all(action.get("scope") == "docs" for action in actions)
    assert "mise" not in repr(actions).lower()


def test_each_docs_repository_gets_its_own_ruby_version_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requirements = {
        "xknx/docs/.ruby-version": "3.3.0",
        "home-assistant.io/.ruby-version": "3.4.0",
    }
    for relative, version in requirements.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True)
        path.write_text(f"{version}\n")

    def inspect(name: str, expectation: str | None = None) -> dict[str, object]:
        return {
            "kind": "tool",
            "tool": name,
            "executable": f"/bin/{name}",
            "installed": "3.3.0" if name == "ruby" else "2.6.9",
        }

    monkeypatch.setattr(ws, "inspect_tool", inspect)
    actions = ws.docs_prerequisite_actions(tmp_path, "docs")
    ruby = {action["repository"]: action for action in actions if action["tool"] == "ruby"}

    assert ruby["xknx/docs"]["relation"] == "current"
    assert ruby["home-assistant.io"]["expected"] == "3.4.0"
    assert ruby["home-assistant.io"]["kind"] == "manual"
    assert ruby["home-assistant.io"]["blocking"] is True


def test_confirmed_plan_digest_rejects_changed_plan() -> None:
    plan = [{"kind": "package", "command": ["brew", "install", "uv"]}]
    digest = ws.plan_digest(plan)
    ws.validate_plan_digest(plan, digest)
    with pytest.raises(ValueError, match="confirmed plan changed"):
        ws.validate_plan_digest([*plan, {"kind": "manual", "tool": "git"}], digest)


def test_bootstrap_asks_once_then_executes_only_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = [
        {"kind": "package", "command": ["brew", "install", "git"]},
        {"kind": "diagnostic", "tool": "docker", "level": "warning"},
        {"kind": "installer", "command": ["install-nvm"]},
    ]
    monkeypatch.setattr(ws, "build_bootstrap_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(ws, "load_settings", lambda *args: {})
    prompts: list[str] = []
    commands: list[list[str]] = []

    result = ws.run_bootstrap(
        tmp_path,
        "default",
        yes=False,
        argv=["bootstrap", "default"],
        environ={},
        input_fn=lambda prompt: prompts.append(prompt) or "y",
        runner=lambda command, **kwargs: commands.append(command) or CompletedProcess(command, 0),
        reexec=lambda *args: None,
    )

    assert result == 0
    assert len(prompts) == 1
    assert commands == [["brew", "install", "git"], ["install-nvm"]]


def test_new_uv_reexecs_the_same_script_with_the_confirmed_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = [
        {"kind": "tool", "tool": "uv", "executable": None, "installed": None},
        {"kind": "package", "command": ["brew", "install", "uv"]},
    ]
    monkeypatch.setattr(ws, "build_bootstrap_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(ws, "load_settings", lambda *args: {})
    monkeypatch.setattr(ws.shutil, "which", lambda name: "/opt/homebrew/bin/uv")
    reexecs: list[tuple[str, list[str], dict[str, str]]] = []

    assert ws.run_bootstrap(
        tmp_path,
        "default",
        yes=True,
        argv=["bootstrap", "default", "--yes"],
        environ={"PATH": "/opt/homebrew/bin"},
        runner=lambda command, **kwargs: CompletedProcess(command, 0),
        reexec=lambda executable, command, environ: reexecs.append((executable, command, environ)),
    ) == 0

    executable, command, child_environ = reexecs[0]
    assert executable == "/opt/homebrew/bin/uv"
    assert command[:6] == [
        "/opt/homebrew/bin/uv",
        "run",
        "--project",
        str(tmp_path / ".workspace"),
        "--locked",
        "python",
    ]
    carried_plan = json.loads(child_environ[ws.CONFIRMED_PLAN_ENV])
    ws.validate_plan_digest(carried_plan, child_environ[ws.CONFIRMED_PLAN_DIGEST_ENV])


def test_missing_uv_requires_manual_installation_instead_of_reexec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = [
        {"kind": "tool", "tool": "uv", "executable": None, "installed": None},
        {"kind": "manual", "tool": "uv", "command": [], "link": "https://example.invalid"},
    ]
    monkeypatch.setattr(ws, "build_bootstrap_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(ws, "load_settings", lambda *args: {})
    reexecs: list[object] = []

    assert ws.run_bootstrap(
        tmp_path,
        "default",
        yes=True,
        argv=["bootstrap", "default", "--yes"],
        environ={},
        runner=lambda command, **kwargs: CompletedProcess(command, 0),
        reexec=lambda *args: reexecs.append(args),
    ) == 2
    assert reexecs == []


def test_reexec_rejects_a_tampered_confirmed_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_inspection(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("a re-exec must validate its carried plan before inspecting again")

    monkeypatch.setattr(ws, "build_bootstrap_plan", unexpected_inspection)
    monkeypatch.setattr(ws, "load_settings", lambda *args: {})
    with pytest.raises(ValueError, match="confirmed plan changed"):
        ws.run_bootstrap(
            tmp_path,
            "default",
            yes=True,
            argv=["bootstrap", "default", "--yes"],
            environ={
                ws.CONFIRMED_PLAN_ENV: '[{"kind":"installer"}]',
                ws.CONFIRMED_PLAN_DIGEST_ENV: "0" * 64,
            },
        )


def test_reexec_accepts_completed_prerequisites_but_rejects_changed_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = {
        "kind": "context",
        "platform": "macos",
        "profile": "default",
        "repositories": ws.repositories_for("default"),
        "settings": {"profile": "default"},
        "enforce_tool_versions": False,
    }
    confirmed = [
        context,
        {"kind": "tool", "tool": "uv", "executable": None, "installed": None},
        {"kind": "package", "command": ["brew", "install", "uv"]},
    ]
    current = [
        context,
        {"kind": "tool", "tool": "uv", "executable": "/opt/homebrew/bin/uv", "installed": "0.8.17"},
    ]
    monkeypatch.setattr(ws, "load_settings", lambda *args: {"profile": "default"})
    monkeypatch.setattr(ws, "build_bootstrap_plan", lambda *args, **kwargs: current)
    environ = {
        ws.CONFIRMED_PLAN_ENV: ws._serialized_plan(confirmed),
        ws.CONFIRMED_PLAN_DIGEST_ENV: ws.plan_digest(confirmed),
    }

    assert ws.run_bootstrap(
        tmp_path,
        "default",
        yes=True,
        argv=["bootstrap", "default", "--yes"],
        environ=environ,
    ) == 0

    current[0] = {**context, "profile": "docs", "repositories": ws.repositories_for("docs")}
    with pytest.raises(ValueError, match="confirmed bootstrap intent changed"):
        ws.run_bootstrap(
            tmp_path,
            "docs",
            yes=True,
            argv=["bootstrap", "docs", "--yes"],
            environ=environ,
        )


def test_reexec_rejects_a_new_unconfirmed_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = {
        "kind": "context",
        "platform": "macos",
        "profile": "default",
        "repositories": ws.repositories_for("default"),
        "settings": {},
        "enforce_tool_versions": False,
    }
    confirmed = [context, {"kind": "package", "command": ["brew", "install", "uv"]}]
    current = [context, {"kind": "installer", "command": ["sh", "-c", "unexpected"]}]
    monkeypatch.setattr(ws, "load_settings", lambda *args: {})
    monkeypatch.setattr(ws, "build_bootstrap_plan", lambda *args, **kwargs: current)

    with pytest.raises(ValueError, match="unconfirmed prerequisite action"):
        ws.run_bootstrap(
            tmp_path,
            "default",
            yes=True,
            argv=["bootstrap", "default", "--yes"],
            environ={
                ws.CONFIRMED_PLAN_ENV: ws._serialized_plan(confirmed),
                ws.CONFIRMED_PLAN_DIGEST_ENV: ws.plan_digest(confirmed),
            },
        )


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
def test_main_accepts_the_public_command_surface(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws, "run_bootstrap", lambda *args, **kwargs: 0, raising=False)
    assert ws.main(argv) == 0
