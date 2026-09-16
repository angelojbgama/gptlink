"""Filesystem policy tests use real temporary roots and symlinks."""

from pathlib import Path

import pytest

from gptlink.agent.filesystem.operations import FilesystemOperations
from gptlink.agent.filesystem.paths import SandboxError, SandboxPaths
from gptlink.common.types import Capability, PermissionLevel


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.txt").write_text("hello world", encoding="utf-8")
    return root


def operations(root: Path, permission=PermissionLevel.READ_WRITE) -> FilesystemOperations:
    return FilesystemOperations(
        SandboxPaths([root]),
        permission=permission,
        capabilities={
            Capability.FILESYSTEM_READ,
            Capability.FILESYSTEM_SEARCH,
            Capability.FILESYSTEM_WRITE,
        },
        max_read_bytes=1024,
        max_write_bytes=1024,
        max_search_results=10,
    )


def test_parent_traversal_is_rejected(workspace: Path):
    with pytest.raises(SandboxError):
        SandboxPaths([workspace]).resolve(workspace / ".." / "outside.txt")


def test_symlink_escape_is_rejected(workspace: Path, tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)
    with pytest.raises(SandboxError):
        SandboxPaths([workspace]).resolve(workspace / "link.txt")


def test_read_write_and_search_stay_inside_root(workspace: Path):
    ops = operations(workspace)
    assert ops.read(workspace / "note.txt") == "hello world"
    ops.write(workspace / "new.txt", "new content")
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "new content"
    assert [item.name for item in ops.search(workspace, "new content")] == ["new.txt"]


def test_read_only_rejects_write(workspace: Path):
    with pytest.raises(PermissionError):
        operations(workspace, PermissionLevel.READ_ONLY).write(workspace / "blocked.txt", "x")


def test_limits_are_enforced(workspace: Path):
    ops = FilesystemOperations(
        SandboxPaths([workspace]),
        permission=PermissionLevel.READ_WRITE,
        capabilities={Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE},
        max_read_bytes=3,
        max_write_bytes=3,
        max_search_results=10,
    )
    with pytest.raises(ValueError, match="file read limit"):
        ops.read(workspace / "note.txt")
    with pytest.raises(ValueError, match="file write limit"):
        ops.write(workspace / "too-big.txt", "1234")
