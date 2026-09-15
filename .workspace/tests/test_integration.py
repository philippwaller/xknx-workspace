import subprocess
from pathlib import Path

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
