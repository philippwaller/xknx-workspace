import asyncio
import builtins
import json
import socket
import subprocess
from io import BytesIO
from pathlib import Path
from subprocess import CompletedProcess

import pytest

import xknx_workspace as ws


@pytest.fixture
def workspace_root() -> Path:
    return Path(__file__).parents[2]


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


def test_documentation_and_skill_contract(workspace_root: Path) -> None:
    skill = workspace_root / ".agents/skills/bootstrap-xknx-workspace/SKILL.md"
    metadata = workspace_root / ".agents/skills/bootstrap-xknx-workspace/agents/openai.yaml"
    assert skill.exists() and metadata.exists()
    assert "name: bootstrap-xknx-workspace" in skill.read_text()
    assert "./dev status --format json" in skill.read_text()
    assert "./bootstrap" in skill.read_text()
    assert 'display_name: "Bootstrap XKNX Workspace"' in metadata.read_text()


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
    executions = []

    async def execute(plan, progress):
        executions.append(plan)
        return 0

    monkeypatch.setattr(ws, "bootstrap_workspace", execute)
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
    assert {job.name for job in executions[0]["repository_jobs"]} == {
        "home-assistant-core", "xknx", "xknxproject", "knx-telegram-store", "knx-frontend"
    }

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
    monkeypatch.setattr(ws, "start_tmux_command_or_message", lambda *args, **kwargs: {"returncode": 0})
    monkeypatch.setattr(ws, "tmux_status", lambda: {"session": "xknx-dev", "running": False, "windows": []})
    monkeypatch.setattr(ws, "stop_tmux", lambda: 0)
    assert ws.main(argv) == 0


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
def test_update_decision_never_repairs_developer_git_state(
    state: dict[str, object], expected: str
) -> None:
    assert ws.update_decision(state) == expected


def test_repository_row_contains_the_complete_git_observation() -> None:
    status = {
        "path": "/workspace/xknx",
        "branch": "feature/colors",
        "default_branch": "trunk",
        "dirty": True,
        "ahead": 3,
        "behind": 1,
        "diverged": True,
        "action": "unchanged",
        "error": None,
    }
    assert ws.repository_row(status) == "! xknx  feature/colors  dirty  ↑3 ↓1  unchanged"


def test_scheduler_caps_jobs_and_finishes_running_work_after_failure(tmp_path: Path) -> None:
    started = []
    active = maximum = 0

    async def run(job):
        nonlocal active, maximum
        started.append(job.name)
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01 if job.name == "two" else 0.05)
        active -= 1
        return ws.Result(job.name, 1 if job.name == "two" else 0, 0.01)

    jobs = [ws.Job(name, tmp_path, ([name],)) for name in ("one", "two", "three", "four")]
    results = asyncio.run(ws.run_jobs(jobs, limit=2, progress=ws.Progress("quiet"), runner=run))
    assert maximum == 2
    assert started == ["one", "two"]
    assert [(result.name, result.status) for result in results] == [
        ("one", "success"), ("two", "error"), ("three", "skipped"), ("four", "skipped")
    ]


@pytest.mark.parametrize("limit", [0, -1])
def test_scheduler_rejects_invalid_limits(tmp_path: Path, limit: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        asyncio.run(ws.run_jobs([], limit, ws.Progress("quiet")))


def test_log_redacts_all_fields_and_keeps_unique_safe_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXAMPLE_TOKEN", "env-secret")
    monkeypatch.setenv("EXAMPLE_PASSWORD", "password-secret")
    monkeypatch.setenv("EXAMPLE_SECRET", "other-secret")
    monkeypatch.setenv("EXAMPLE_KEY", "key-secret")
    monkeypatch.setenv("ORDINARY_VALUE", "not-recorded")
    for _ in range(2):
        ws.write_task_log(
            tmp_path, "../../example", cwd=Path("/env-secret"),
            command=["tool", "--token", "explicit-secret"],
            stdout="env-secret password-secret", stderr="other-secret key-secret",
            returncode=7, secrets={"explicit-secret"},
            versions={"tool": "1.2"}, decisions=["keep env-secret"],
        )
    logs = list(tmp_path.glob("*.log"))
    assert len(logs) == 2
    for path in logs:
        content = path.read_text()
        for secret in ("env-secret", "password-secret", "other-secret", "key-secret", "explicit-secret", "not-recorded"):
            assert secret not in content
        for field in ("cwd:", "argv:", "start:", "end:", "duration:", "returncode: 7", "stdout:", "stderr:", "versions:", "decisions:", "***"):
            assert field in content


@pytest.mark.parametrize("mode", ["plain", "json", "quiet", "auto"])
def test_non_tty_progress_never_imports_rich(mode: str, monkeypatch, capsys) -> None:
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("rich"):
            raise AssertionError("Rich must be lazy and TTY-only")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    progress = ws.Progress(mode, secrets={"private-value"})
    progress.emit("xknx", "running", "Installing private-value")
    progress.emit("xknx", "error", "first private-value")
    progress.emit("other", "error", "second")
    progress.emit("bootstrap", "summary", "finished")
    progress.close()
    output = capsys.readouterr().out
    assert "private-value" not in output
    assert "\x1b" not in output
    if mode == "json":
        assert json.loads(output.splitlines()[0]) == {
            "task": "xknx", "status": "running", "detail": "Installing ***"
        }
    if mode == "quiet":
        assert len(output.splitlines()) == 2
        assert "first" in output and "finished" in output


def test_auto_tty_falls_back_when_rich_is_missing(monkeypatch, capsys) -> None:
    original_import = builtins.__import__
    monkeypatch.setattr(ws.sys.stdout, "isatty", lambda: True)

    def missing_rich(name, *args, **kwargs):
        if name.startswith("rich"):
            raise ImportError("Rich is not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_rich)
    progress = ws.Progress("auto")
    progress.emit("xknx", "running", "Installing")
    progress.close()
    assert "Installing" in capsys.readouterr().out


def test_tty_updates_one_stable_row_per_task(capsys) -> None:
    progress = ws.Progress("tty")
    progress.emit("xknx", "running", "Installing")
    progress.emit("xknx", "running", "Building")
    progress.emit("frontend", "running", "Installing")
    progress.emit("xknx", "success", "Done")
    progress.close()
    output = capsys.readouterr().out
    assert output.count("xknx") == 1
    assert "Done" in output and "Building" not in output


def test_bootstrap_stops_phases_and_reports_dependent_jobs_skipped(tmp_path: Path, capsys) -> None:
    async def not_called(plan, progress):
        raise AssertionError("Dependent phase must not start")

    plan = {
        "root": tmp_path, "tool_actions": [], "jobs": 1,
        "repository_jobs": [ws.Job("repository", tmp_path, (["missing-task4-command"],))],
        "project_setup_jobs": [ws.Job("setup", tmp_path, (["must-not-run"],))],
        "wire_home_assistant": not_called,
        "smoke_default": not_called,
    }
    assert asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("json", log_dir=tmp_path / "logs"))) == 127
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(event["task"] == "setup" and event["status"] == "skipped" for event in events)
    assert not any(event["task"] == "setup" and event["status"] == "running" for event in events)
    assert {event["task"] for event in events if event["status"] == "skipped"} == {"setup", "wire_home_assistant", "smoke_default"}


def test_bootstrap_empty_phases_succeed_and_only_enabled_callbacks_run(tmp_path: Path) -> None:
    called = []

    async def wire(plan, progress):
        called.append("wire")
        return True

    async def smoke(plan, progress):
        called.append("smoke")
        return False

    plan = {"root": tmp_path, "tool_actions": [], "repository_jobs": [], "project_setup_jobs": [], "jobs": 3}
    assert asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("quiet"))) == 0
    plan.update(wire_home_assistant=wire, smoke_default=smoke)
    assert asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("quiet"))) == 1
    assert called == ["wire", "smoke"]


def test_bootstrap_forwards_execution_options(monkeypatch) -> None:
    options = {}
    monkeypatch.setattr(ws, "run_bootstrap", lambda *args, **kwargs: options.update(kwargs) or 0)
    assert ws.main(["bootstrap", "--yes", "--jobs", "2", "--progress", "json", "--verbose"]) == 0
    assert options["jobs"] == 2
    assert options["progress_mode"] == "json"
    assert options["verbose"] is True


def test_tool_failure_reports_unstarted_actions_and_verification_in_its_log(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(ws, "inspect_tool", lambda name: {"installed": None})
    actions = [
        {"kind": "installer", "tool": "nvm", "command": ["install-nvm"], "version": "0.40.3"},
        {"kind": "package", "command": ["must-not-run"]},
    ]
    progress = ws.Progress("json", log_dir=tmp_path / "logs")
    results = asyncio.run(ws.run_tool_actions(
        actions, progress, root=tmp_path,
        command_runner=lambda command, **kwargs: CompletedProcess(command, 0),
    ))
    assert [(result.returncode, result.status) for result in results] == [(2, "error"), (1, "skipped")]
    log = next((tmp_path / "logs").glob("*.log")).read_text()
    assert "returncode: 2" in log and "NVM" in log
    assert "must-not-run" not in log


def test_log_redacts_secrets_that_need_json_escaping(tmp_path: Path) -> None:
    secret = 'private"value\\end'
    path = ws.write_task_log(tmp_path, "secret", command=["tool", secret], stdout=secret, stderr="", returncode=0, secrets={secret})
    content = path.read_text()
    assert secret not in content
    assert json.dumps(secret)[1:-1] not in content


def test_tty_plan_keeps_every_action_visible_before_confirmation(tmp_path: Path, monkeypatch) -> None:
    plan = [{"kind": "package", "command": ["first-install"]}, {"kind": "installer", "command": ["second-install"]}]
    monkeypatch.setattr(ws, "build_bootstrap_plan", lambda *args, **kwargs: plan)
    frames = []
    monkeypatch.setattr(ws.Progress, "_render_tty", lambda self: frames.append(dict(self.rows)))
    assert ws.run_bootstrap(tmp_path, "default", yes=False, argv=["bootstrap"], input_fn=lambda prompt: "n", progress_mode="tty") == 1
    planned = [detail for status, detail in frames[-1].values() if status == "planned"]
    assert any("first-install" in detail for detail in planned)
    assert any("second-install" in detail for detail in planned)


def test_quiet_plan_is_visible_before_confirmation_and_execution_stays_quiet(tmp_path: Path, monkeypatch, capsys) -> None:
    secret = "head'tail"
    plan = [{"kind": "package", "command": ["first-install", secret]}, {"kind": "installer", "command": ["second-install"]}]
    monkeypatch.setattr(ws, "build_bootstrap_plan", lambda *args, **kwargs: plan)

    def confirm(prompt):
        output = capsys.readouterr().out
        assert "first-install" in output and "second-install" in output
        assert "head" not in output and "tail" not in output
        assert "***" in output and "[y/N]" in prompt
        return "y"

    assert ws.run_bootstrap(
        tmp_path, "default", yes=False, argv=["bootstrap"], progress_mode="quiet",
        environ={"TEST_TOKEN": secret}, input_fn=confirm,
        runner=lambda command, **kwargs: CompletedProcess(command, 0, "detail", ""),
    ) == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1 and "summary" in output


@pytest.mark.parametrize("phase", ["repository_jobs", "project_setup_jobs"])
def test_bootstrap_propagates_first_completed_failure_not_job_order(tmp_path: Path, phase: str, capsys) -> None:
    command = lambda delay, code: [ws.sys.executable, "-c", f"import time, sys; time.sleep({delay}); sys.exit({code})"]
    plan = {"root": tmp_path, "tool_actions": [], "jobs": 2, "repository_jobs": [], "project_setup_jobs": []}
    plan[phase] = [
        ws.Job("later-failure", tmp_path, (command(0.2, 9),)),
        ws.Job("first-failure", tmp_path, (command(0.01, 7),)),
        ws.Job("skipped", tmp_path, (["must-not-run"],)),
    ]
    assert asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("json", log_dir=tmp_path / "logs"))) == 7
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert "exit code 7" in events[-1]["detail"]
    assert any(event["task"] == "skipped" and event["status"] == "skipped" for event in events)


def test_sudo_plan_is_executed_once_without_authentication_rewriting(tmp_path: Path) -> None:
    plan = [{"kind": "package", "command": ["sudo", "apt-get", "install", "-y", "git"]}]
    lines = []
    commands = []
    ws.render_plan(plan, lines.append)
    results = asyncio.run(ws.run_tool_actions(
        plan, ws.Progress("quiet", log_dir=tmp_path / "logs"), root=tmp_path,
        command_runner=lambda command, **kwargs: commands.append(command) or CompletedProcess(command, 0),
    ))
    assert lines == ["$ sudo apt-get install -y git"]
    assert commands == [["sudo", "apt-get", "install", "-y", "git"]]
    assert len(results) == 1 and results[0].returncode == 0


def test_default_setup_uses_project_owned_commands() -> None:
    jobs = {job.name: job.commands for job in ws.project_setup_jobs(Path("/workspace"), "default")}
    assert jobs == {
        "Home Assistant Core": (["script/setup"],),
        "KNX Frontend": (ws.nvm_shell(["script/bootstrap"]), ws.nvm_shell(["script/build"])),
        "XKNX": (["uv", "sync", "--group", "dev", "--locked"],),
        "XKNX Project": (["uv", "venv"], ["uv", "pip", "install", "--python", ".venv/bin/python", "-e", ".", "-r", "requirements_testing.txt"]),
        "KNX Telegram Store": (["uv", "venv"], ["uv", "pip", "install", "--python", ".venv/bin/python", "-e", ".[dev,sqlite,postgres]"]),
    }


def test_optional_setup_and_development_commands_keep_frontends_independent(tmp_path: Path) -> None:
    setup = {str(job.cwd.relative_to(tmp_path)): job.commands for job in ws.project_setup_jobs(tmp_path, "all")}
    assert setup["home-assistant-frontend"] == (ws.nvm_shell(["script/setup"]),)
    assert setup["xknxtoolkit"] == (["uv", "sync"],)
    assert setup["xknx/docs"] == (["bundle", "install"],)
    assert setup["home-assistant.io"] == (["bundle", "install"], ws.nvm_shell(["npm", "ci"]))
    development = {str(job.cwd.relative_to(tmp_path)): job.commands for job in ws.project_development_jobs(tmp_path, "all")}
    assert development == {
        "knx-frontend": (ws.nvm_shell(["script/develop"]),),
        "home-assistant-frontend": (ws.nvm_shell(["script/develop"]),),
        "xknxtoolkit": (["uv", "run", "python", "-m", "knx_gui.main"],),
        "xknx/docs": (["bundle", "exec", "jekyll", "serve"],),
        "home-assistant.io": (["bundle", "exec", "rake", "preview"],),
    }
    assert not any("homeassistant-frontend" in path for path in setup | development)


def test_home_assistant_wiring_and_start_use_ha_python_and_root_checkouts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert ws.home_assistant_wiring_command(Path(".")) == [
        "uv", "pip", "install", "--python", str(tmp_path / "home-assistant-core/.venv/bin/python"),
        "-e", str(tmp_path / "xknx"), "-e", str(tmp_path / "xknxproject"),
        "-e", f"{tmp_path / 'knx-telegram-store'}[sqlite,postgres]", "-e", str(tmp_path / "knx-frontend"),
    ]
    assert ws.home_assistant_command(Path("."), {"port": 9123, "config_dir": "home-assistant-core/config"}) == [
        str(tmp_path / "home-assistant-core/.venv/bin/hass"),
        "--config", str(tmp_path / "home-assistant-core/config"), "--skip-pip-packages",
        "xknx,xknxproject,knx-frontend,knx-telegram-store",
    ]


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


def test_non_tty_start_does_not_create_hidden_session(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(ws.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(ws.sys.stdout, "isatty", lambda: False)

    result = ws.start_tmux_command_or_message(
        "default",
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tmux must not run")),
    )

    assert result["action"] == "print-command"
    assert capsys.readouterr().out == "./dev start default\n"


@pytest.mark.parametrize("port", [0, 65536, True, "8123"])
def test_configuration_rejects_invalid_ports_without_writing(tmp_path: Path, port) -> None:
    settings = ws.load_settings(tmp_path, {})
    settings["home_assistant"]["port"] = port
    with pytest.raises(ValueError, match="home_assistant.port"):
        ws.configuration_action(tmp_path, settings)
    assert not list(tmp_path.iterdir())


def test_configuration_rejects_occupied_ports_with_precise_override(tmp_path: Path) -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": str(server.getsockname()[1])})
        with pytest.raises(ValueError, match="XKNX_HA_PORT"):
            ws.configuration_action(tmp_path, settings)


def test_configuration_resolves_and_validates_paths_and_secure_references(tmp_path: Path) -> None:
    settings = ws.load_settings(tmp_path, {})
    settings["home_assistant"]["config_dir"] = "../outside"
    with pytest.raises(ValueError, match="home_assistant.config_dir"):
        ws.configuration_action(tmp_path, settings)
    outside = tmp_path.parent / f"{tmp_path.name}-explicit"
    settings["home_assistant"]["config_dir"] = str(outside)
    assert ws.configuration_action(tmp_path, settings)["config_dir"] == str(outside)
    (tmp_path / "linked").symlink_to(tmp_path.parent, target_is_directory=True)
    settings["home_assistant"]["config_dir"] = "linked/escaped"
    with pytest.raises(ValueError, match="home_assistant.config_dir"):
        ws.configuration_action(tmp_path, settings)
    settings["home_assistant"]["config_dir"] = "config"
    settings["knx"] = {"mode": "real", "password": "never-echo-this"}
    with pytest.raises(ValueError) as error:
        ws.configuration_action(tmp_path, settings)
    assert "never-echo-this" not in str(error.value)
    settings["knx"] = {"mode": "real"}
    with pytest.raises(ValueError, match="knx.secure_config_path"):
        ws.configuration_action(tmp_path, settings)
    secure = tmp_path / "secure.yaml"
    secure.write_text("password: never-echo-this")
    settings["knx"]["secure_config_path"] = str(secure)
    action = ws.configuration_action(tmp_path, settings)
    assert action["secure_config_path"] == str(secure)
    assert "never-echo-this" not in repr(action)


def test_settings_support_only_approved_environment_overrides(tmp_path: Path) -> None:
    settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": "9123", "XKNX_HA_CONFIG_DIR": "my-config", "XKNX_KNX_MODE": "real"})
    assert settings["home_assistant"] == {"port": 9123, "config_dir": "my-config"}
    assert settings["knx"]["mode"] == "real"


def test_configuration_rejects_an_occupied_ipv6_port(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET6) as server:
        server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        server.bind(("::1", 0))
        settings = ws.load_settings(tmp_path, {"XKNX_HA_PORT": str(server.getsockname()[1])})
        with pytest.raises(ValueError, match="XKNX_HA_PORT"):
            ws.configuration_action(tmp_path, settings)


def test_configuration_rejects_secret_fields_in_any_section(tmp_path: Path) -> None:
    settings = ws.load_settings(tmp_path, {})
    settings["home_assistant"]["token"] = "never-echo-this"
    with pytest.raises(ValueError) as error:
        ws.configuration_action(tmp_path, settings)
    assert "never-echo-this" not in str(error.value)


def test_config_creation_rejects_a_directory_symlink_added_after_confirmation(tmp_path: Path) -> None:
    action = ws.configuration_action(tmp_path, ws.load_settings(tmp_path, {}))
    external = tmp_path / "other"
    external.mkdir()
    (tmp_path / "home-assistant-core").mkdir()
    (tmp_path / "home-assistant-core/config").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="config_dir.*changed"):
        ws.create_home_assistant_configuration(action)
    assert not list(external.iterdir())


def test_docs_block_only_their_setup_jobs_after_checkout(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(ws, "docs_prerequisite_actions", lambda root, profile: [{
        "kind": "manual", "scope": "docs", "blocking": True, "tool": "ruby",
        "repository": "xknx/docs", "expected": "9.9.9", "link": "https://example.invalid/ruby",
    }])
    jobs = [ws.Job(name, tmp_path / path, ([ws.sys.executable, "-c", "from pathlib import Path; Path('setup.done').touch()"],)) for name, path in (("Core", "home-assistant-core"), ("XKNX Docs", "xknx/docs"), ("HA Docs", "home-assistant.io"))]
    for job in jobs:
        job.cwd.mkdir(parents=True)
    plan = {"root": tmp_path, "profile": "docs", "repository_jobs": [], "project_setup_jobs": jobs}
    assert asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("plain", log_dir=tmp_path / "logs"))) == 2
    assert (tmp_path / "home-assistant-core/setup.done").exists()
    assert (tmp_path / "home-assistant.io/setup.done").exists()
    assert not (tmp_path / "xknx/docs/setup.done").exists()
    assert "9.9.9" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["setup", "wiring", "smoke", "configuration"])
def test_reexec_rejects_changed_project_actions(kind: str) -> None:
    confirmed = [{"kind": kind, "command": ["confirmed"]}]
    current = [{"kind": kind, "command": ["different"]}]
    with pytest.raises(ValueError, match="unconfirmed project action"):
        ws.validate_reexec_plan(confirmed, current)


def test_bootstrap_revalidates_changed_local_settings_before_prerequisites(tmp_path: Path) -> None:
    config = tmp_path / ".xknx-dev.toml"
    config.write_text('[home_assistant]\nport = 8123\n')
    settings = ws.load_settings(tmp_path, {})
    action = ws.configuration_action(tmp_path, settings)
    config.write_text('[home_assistant]\nport = 9123\n')
    calls = []
    plan = {
        "root": tmp_path, "configuration": action, "settings": settings,
        "tool_actions": [{"kind": "package", "command": ["must-not-run"]}],
        "command_runner": lambda command, **kwargs: calls.append(command) or CompletedProcess(command, 0),
    }
    with pytest.raises(ValueError, match="configuration changed after confirmation"):
        asyncio.run(ws.bootstrap_workspace(plan, ws.Progress("quiet", log_dir=tmp_path / "logs")))
    assert not calls


def test_reused_ha_config_uses_only_dependency_bootstrap(tmp_path: Path) -> None:
    jobs = ws.project_setup_jobs(tmp_path, "default", reuse_ha_config=True)
    ha = next(job for job in jobs if job.name == "Home Assistant Core")
    assert ha.commands == (["uv", "venv"], ["bash", "-c", ". .venv/bin/activate && script/bootstrap"])


def test_existing_python_environments_are_reused_without_recreation(tmp_path: Path) -> None:
    for repository in ("home-assistant-core", "xknxproject", "knx-telegram-store"):
        interpreter = tmp_path / repository / ".venv/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()
    jobs = {job.name: job.commands for job in ws.project_setup_jobs(tmp_path, "default")}
    assert jobs["Home Assistant Core"] == (["bash", "-c", ". .venv/bin/activate && script/setup"],)
    assert all(["uv", "venv"] not in commands for commands in jobs.values())
    assert jobs["XKNX Project"][0][:3] == ["uv", "pip", "install"]
    jobs = {job.name: job.commands for job in ws.project_setup_jobs(tmp_path, "default", reuse_ha_config=True)}
    assert jobs["Home Assistant Core"] == (["bash", "-c", ". .venv/bin/activate && script/bootstrap"],)


def test_invalid_setting_types_report_the_exact_key_without_echoing_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="XKNX_HA_PORT") as error:
        ws.load_settings(tmp_path, {"XKNX_HA_PORT": "never-echo-this"})
    assert "never-echo-this" not in str(error.value)
    (tmp_path / ".xknx-dev.toml").write_text("knx = 1\n")
    with pytest.raises(ValueError, match="knx.*table"):
        ws.load_settings(tmp_path, {})
    settings = {"knx": {"mode": []}}
    with pytest.raises(ValueError, match="knx.mode"):
        ws.configuration_action(tmp_path, settings)


@pytest.mark.parametrize(("content", "key"), [
    ('profile = true\n', 'profile'),
    ('profile = []\n', 'profile'),
    ('home_assistant = []\n', 'home_assistant'),
    ('[home_assistant]\npassword = "private-marker"\n', 'home_assistant'),
    ('[home_assistant]\nport = "private-marker"\n', 'home_assistant.port'),
    ('[home_assistant]\nport = true\n', 'home_assistant.port'),
    ('[home_assistant]\nconfig_dir = []\n', 'home_assistant.config_dir'),
    ('[knx]\npassword = "private-marker"\n', 'knx'),
    ('[knx]\nmode = []\n', 'knx.mode'),
    ('[knx]\nsecure_config_path = true\n', 'knx.secure_config_path'),
])
def test_load_settings_rejects_unknown_section_keys_and_wrong_types(tmp_path: Path, content: str, key: str) -> None:
    (tmp_path / ".xknx-dev.toml").write_text(content)
    with pytest.raises(ValueError, match=key) as error:
        ws.load_settings(tmp_path, {})
    assert "private-marker" not in str(error.value)


def test_yaml_appearing_during_config_creation_requires_a_new_plan(tmp_path: Path, monkeypatch) -> None:
    action = ws.configuration_action(tmp_path, ws.load_settings(tmp_path, {}))
    directory = Path(action["config_dir"])
    mkdir = Path.mkdir

    def raced_mkdir(path, *args, **kwargs):
        mkdir(path, *args, **kwargs)
        if path == directory:
            (path / "configuration.yaml").write_text("developer-owned\n")

    monkeypatch.setattr(Path, "mkdir", raced_mkdir)
    with pytest.raises(ValueError, match="config_dir.*changed"):
        ws.create_home_assistant_configuration(action)
    assert (directory / "configuration.yaml").read_text() == "developer-owned\n"


def test_status_has_stable_schema_and_missing_items_are_actionable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    monkeypatch.setenv("NVM_DIR", str(tmp_path / "missing-nvm"))
    status = ws.collect_status(tmp_path, ws.load_settings(tmp_path, {}))
    assert set(status) == {"workspace", "profile", "platform", "configuration", "tools", "docker", "repositories", "packages", "processes", "smoke"}
    assert status["tools"]["git"]["relation"] == "missing"
    assert status["repositories"]["xknx"]["action"] == "missing"
    assert "./bootstrap toolkit" in status["repositories"]["xknxtoolkit"]["error"]
    assert not status["packages"]["xknx"]["ok"]
    assert status["processes"]["expected"]["home-assistant"]["action"] == "./dev start default"
    assert status["smoke"]["status"] == "not-run"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("content", ['[knx]\nmode="real"\npassword="do-not-print"\n', '[knx]\nmode="do-not-print"\n', 'password = "do-not-print'])
def test_status_json_reports_invalid_configuration_without_secrets(tmp_path: Path, monkeypatch, capsys, content: str) -> None:
    (tmp_path / ".xknx-dev.toml").write_text(content)
    monkeypatch.setattr(ws, "ROOT", tmp_path)
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    monkeypatch.setenv("NVM_DIR", str(tmp_path / "missing-nvm"))
    assert ws.main(["dev", "status", "--format", "json"]) == 0
    output = capsys.readouterr()
    status = json.loads(output.out)
    assert status["configuration"]["conflicts"]
    assert "do-not-print" not in output.out + output.err


def test_status_reports_config_paths_and_conflicts_without_reading_secure_material(tmp_path: Path, monkeypatch) -> None:
    secure = tmp_path / "secure.yaml"
    secure.write_text("password: do-not-print\n")
    settings = ws.load_settings(tmp_path, {})
    settings["knx"] = {"mode": "real", "secure_config_path": str(secure)}
    settings["home_assistant"]["config_dir"] = "../outside"
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    monkeypatch.setenv("NVM_DIR", str(tmp_path / "missing-nvm"))
    status = ws.collect_status(tmp_path, settings)
    assert "config_dir" in " ".join(status["configuration"]["conflicts"])
    assert status["configuration"]["effective"]["knx"]["secure_config_path"] == str(secure)
    assert "do-not-print" not in json.dumps(status)


def test_toolkit_smoke_never_claims_an_unavailable_roundtrip(tmp_path: Path) -> None:
    result = ws.toolkit_smoke(tmp_path)
    assert result["status"] == "skipped"
    assert result["acceptance_satisfied"] is False
    assert "public" in result["reason"] and "readiness" in result["reason"] and "send" in result["reason"]
    assert not list(tmp_path.iterdir())


def test_process_status_distinguishes_missing_dead_and_recovery_shells(monkeypatch) -> None:
    monkeypatch.setattr(ws, "tmux_status", lambda: {
        "session": "xknx-dev", "running": True, "windows": [
            {"name": "overview", "dead": False, "command": "zsh", "exit_code": None},
            {"name": "home-assistant", "dead": True, "command": "python", "exit_code": 7},
            {"name": "knx-frontend", "dead": False, "command": "zsh", "exit_code": None},
        ],
    })
    status = ws.process_status("toolkit")
    assert status["expected"]["overview"]["state"] == "running"
    assert status["expected"]["home-assistant"]["state"] == "exited"
    assert "7" in status["expected"]["home-assistant"]["detail"]
    assert status["expected"]["knx-frontend"]["state"] == "command"
    assert status["expected"]["toolkit"]["state"] == "missing"
    assert all(item["action"] for name, item in status["expected"].items() if name != "overview")


def test_status_reports_optional_docker_installed_and_unreachable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ws, "inspect_docker", lambda platform: ws.docker_status(platform, "/test/docker", False))
    monkeypatch.setattr(ws, "inspect_tool", lambda name: {"installed": None, "executable": None})
    status = ws.collect_status(tmp_path, ws.load_settings(tmp_path, {}))
    assert status["docker"]["installed"] is True
    assert status["docker"]["daemon_reachable"] is False
    assert status["docker"]["required"] is False


def test_status_handles_unsupported_platform_and_inspector_failures(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ws, "detect_platform", lambda: "unsupported")

    def unavailable(*args):
        raise OSError("not installed")

    monkeypatch.setattr(ws, "inspect_tool", unavailable)
    monkeypatch.setattr(ws, "tmux_status", unavailable)
    status = ws.collect_status(tmp_path, ws.load_settings(tmp_path, {}))
    assert status["platform"] == "unsupported"
    assert status["processes"]["error"]
    assert status["tools"]["git"]["error"]


def test_default_smoke_keeps_docker_out_of_the_required_path(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*args):
        raise AssertionError("Default smoke must never depend on Docker")

    monkeypatch.setattr(ws, "inspect_docker", forbidden)
    assert ws.check_default_smoke(tmp_path, ws.load_settings(tmp_path, {}))["status"] == "failed"


def test_status_preserves_this_invocations_smoke_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ws, "inspect_tool", lambda name: {"installed": None, "executable": None})
    monkeypatch.setattr(ws, "inspect_docker", lambda platform: ws.docker_status(platform, None, False))
    smoke = {"status": "completed", "default": {"status": "passed", "acceptance_satisfied": True}}
    status = ws.collect_status(tmp_path, ws.load_settings(tmp_path, {}), smoke=smoke)
    assert status["smoke"] == smoke


@pytest.mark.parametrize("secret", ["false", "8123", "profile"])
def test_status_redacts_string_values_without_changing_schema_or_scalar_types(tmp_path: Path, monkeypatch, capsys, secret: str) -> None:
    monkeypatch.setattr(ws, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "inspect_tool", lambda name: {"installed": None, "executable": None})
    monkeypatch.setattr(ws, "inspect_docker", lambda platform: ws.docker_status(platform, None, False))
    monkeypatch.setenv("STATUS_TEST_SECRET", secret)
    smoke = {"status": "completed", "profile": [secret, False, 8123, None, {"profile": secret}]}
    status = ws.collect_status(tmp_path, ws.load_settings(tmp_path, {}), smoke=smoke)
    assert set(status) == {"workspace", "profile", "platform", "configuration", "tools", "docker", "repositories", "packages", "processes", "smoke"}
    assert status["configuration"]["effective"]["home_assistant"]["port"] == 8123
    assert status["docker"]["required"] is False
    assert status["smoke"]["profile"] == ["***", False, 8123, None, {"profile": "***"}]
    assert smoke["profile"][0] == secret
    ws.render_status(status, "json")
    assert json.loads(capsys.readouterr().out) == status
    assert ws.main(["dev", "status", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["profile"] == "default"
    monkeypatch.setattr(ws, "ensure_repository", lambda *args: {"error": None, "dirty": False, "ahead": 8123, "profile": secret})
    assert ws.main(["_repository", str(tmp_path / "xknx"), "unused"]) == 0
    assert json.loads(capsys.readouterr().out) == {"error": None, "dirty": False, "ahead": 8123, "profile": "***"}
