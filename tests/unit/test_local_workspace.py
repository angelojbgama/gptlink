from pathlib import Path

import pytest

from gptlink.agent.filesystem.paths import SandboxError
from gptlink.local.workspace import LocalWorkspace


def test_cwd_is_frozen_as_canonical_workspace(tmp_path: Path, monkeypatch) -> None:
    child = tmp_path / "project"
    child.mkdir()
    monkeypatch.chdir(child)

    workspace = LocalWorkspace.from_cwd()
    monkeypatch.chdir(tmp_path)

    assert workspace.root == child.resolve()
    assert workspace.resolve(".", existing=True) == child.resolve()


def test_relative_paths_resolve_under_workspace(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    workspace = LocalWorkspace.from_cwd(tmp_path)

    assert workspace.resolve("src", existing=True) == source.resolve()


def test_parent_escape_is_rejected(tmp_path: Path) -> None:
    workspace_root = tmp_path / "project"
    workspace_root.mkdir()
    workspace = LocalWorkspace.from_cwd(workspace_root)

    with pytest.raises(SandboxError):
        workspace.resolve("../outside")


def test_absolute_outside_root_is_rejected(tmp_path: Path) -> None:
    workspace_root = tmp_path / "project"
    outside = tmp_path / "outside"
    workspace_root.mkdir()
    outside.mkdir()
    workspace = LocalWorkspace.from_cwd(workspace_root)

    with pytest.raises(SandboxError):
        workspace.resolve(outside, existing=True)


def test_symlink_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    workspace_root = tmp_path / "project"
    outside = tmp_path / "outside"
    workspace_root.mkdir()
    outside.mkdir()
    link = workspace_root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires privileges on this platform")
    workspace = LocalWorkspace.from_cwd(workspace_root)

    with pytest.raises(SandboxError):
        workspace.resolve("escape", existing=True)
