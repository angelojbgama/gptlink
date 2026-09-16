"""Safe read-only Git and process operations."""

import subprocess
import sys
from pathlib import Path

import pytest

from gptlink.agent.filesystem.paths import SandboxPaths
from gptlink.agent.read_operations import GitReadService, ProcessReadService
from gptlink.common.types import Capability


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "file.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    return repo


@pytest.mark.asyncio
async def test_git_status_and_diff_are_read_only(repository: Path):
    (repository / "file.txt").write_text("two\n", encoding="utf-8")
    service = GitReadService(SandboxPaths([repository]), {Capability.GIT_READ})
    status = await service.status(repository)
    diff = await service.diff(repository)
    assert "file.txt" in status
    assert "-one" in diff and "+two" in diff


@pytest.mark.asyncio
async def test_git_rejects_path_outside_root(repository: Path, tmp_path: Path):
    service = GitReadService(SandboxPaths([repository]), {Capability.GIT_READ})
    with pytest.raises(ValueError):
        await service.status(tmp_path)


@pytest.mark.asyncio
async def test_git_requires_capability(repository: Path):
    service = GitReadService(SandboxPaths([repository]), set())
    with pytest.raises(PermissionError):
        await service.status(repository)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only /proc process listing")
def test_process_list_is_bounded_and_requires_capability():
    service = ProcessReadService({Capability.PROCESS_READ}, max_results=20)
    processes = service.list()
    assert 1 <= len(processes) <= 20
    assert all(process.pid > 0 and process.command for process in processes)
    with pytest.raises(PermissionError):
        ProcessReadService(set(), max_results=20).list()
