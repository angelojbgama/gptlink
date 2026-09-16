import os
import subprocess
from pathlib import Path
from uuid import UUID

import pytest

from gptlink.chat.protocol import (
    ChatAction,
    CommandOutputArguments,
    CommandStartArguments,
    PathArguments,
    SearchArguments,
    WriteArguments,
)
from gptlink.common.config import Settings
from gptlink.common.types import PermissionLevel
from gptlink.local.runtime import LocalActionError, build_local_runtime
from gptlink.local.workspace import LocalWorkspace


class RecordingApprover:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.requests = []

    async def approve(self, request) -> bool:
        self.requests.append(request)
        return self.allowed


def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_audit_path=tmp_path / "audit.jsonl")


@pytest.mark.asyncio
async def test_real_workspace_list_read_search_and_escape(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello unique-word", encoding="utf-8")
    runtime = build_local_runtime(
        LocalWorkspace.from_cwd(tmp_path),
        permission=PermissionLevel.READ_ONLY,
        approver=RecordingApprover(),
        settings=settings(tmp_path),
    )

    listing = await runtime.execute(
        ChatAction("list", "filesystem_list", PathArguments(path=".")),
        conversation_id="conversation",
        conversation_title="Test",
    )
    assert any(item["name"] == "hello.txt" for item in listing["items"])

    read = await runtime.execute(
        ChatAction("read", "filesystem_read", PathArguments(path="hello.txt")),
        conversation_id="conversation",
        conversation_title="Test",
    )
    assert read == {"content": "hello unique-word"}

    search = await runtime.execute(
        ChatAction(
            "search",
            "filesystem_search",
            SearchArguments(path=".", query="unique-word"),
        ),
        conversation_id="conversation",
        conversation_title="Test",
    )
    assert any(item["name"] == "hello.txt" for item in search["items"])

    with pytest.raises(LocalActionError):
        await runtime.execute(
            ChatAction("escape", "filesystem_read", PathArguments(path="../outside.txt")),
            conversation_id="conversation",
            conversation_title="Test",
        )


@pytest.mark.asyncio
async def test_read_only_blocks_write_and_command_without_prompt(tmp_path: Path) -> None:
    approver = RecordingApprover()
    runtime = build_local_runtime(
        LocalWorkspace.from_cwd(tmp_path),
        permission=PermissionLevel.READ_ONLY,
        approver=approver,
        settings=settings(tmp_path),
    )
    actions = [
        ChatAction("write", "filesystem_write", WriteArguments(path="new.txt", data="secret")),
        ChatAction(
            "command",
            "command_start",
            CommandStartArguments(command="echo no", shell=_shell(), cwd="."),
        ),
    ]

    for action in actions:
        with pytest.raises(LocalActionError, match="READ_ONLY"):
            await runtime.execute(action, conversation_id="conversation", conversation_title="Test")
    assert approver.requests == []
    assert not (tmp_path / "new.txt").exists()


@pytest.mark.asyncio
async def test_read_write_still_asks_and_denial_prevents_write(tmp_path: Path) -> None:
    approver = RecordingApprover(allowed=False)
    runtime = build_local_runtime(
        LocalWorkspace.from_cwd(tmp_path),
        permission=PermissionLevel.READ_WRITE,
        approver=approver,
        settings=settings(tmp_path),
    )

    with pytest.raises(LocalActionError, match="denied"):
        await runtime.execute(
            ChatAction("write", "filesystem_write", WriteArguments(path="new.txt", data="no")),
            conversation_id="conversation",
            conversation_title="Test",
        )
    assert len(approver.requests) == 1
    assert not (tmp_path / "new.txt").exists()


@pytest.mark.asyncio
async def test_approved_write_and_command_use_real_existing_executors(tmp_path: Path) -> None:
    approver = RecordingApprover()
    runtime = build_local_runtime(
        LocalWorkspace.from_cwd(tmp_path),
        permission=PermissionLevel.READ_WRITE,
        approver=approver,
        settings=settings(tmp_path),
    )
    await runtime.execute(
        ChatAction(
            "write-secret",
            "filesystem_write",
            WriteArguments(path="new.txt", data="super-secret-value"),
        ),
        conversation_id="conversation",
        conversation_title="Test",
    )
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "super-secret-value"

    start = await runtime.execute(
        ChatAction(
            "command-secret",
            "command_start",
            CommandStartArguments(command=_safe_command(), shell=_shell(), cwd="."),
        ),
        conversation_id="conversation",
        conversation_title="Test",
    )
    job_id = UUID(start["job_id"])
    await runtime.dispatcher.jobs.wait(job_id)
    output = await runtime.execute(
        ChatAction(
            "output",
            "command_output",
            CommandOutputArguments(job_id=str(job_id)),
        ),
        conversation_id="conversation",
        conversation_title="Test",
    )
    assert "local-ok" in "".join(chunk["data"] for chunk in output)
    assert len(approver.requests) == 2

    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "super-secret-value" not in audit
    assert _safe_command() not in audit
    assert "write-secret" in audit
    assert "command-secret" in audit


@pytest.mark.asyncio
async def test_git_status_uses_workspace_repository(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "untracked.txt").write_text("x", encoding="utf-8")
    runtime = build_local_runtime(
        LocalWorkspace.from_cwd(tmp_path),
        permission=PermissionLevel.READ_ONLY,
        approver=RecordingApprover(),
        settings=settings(tmp_path),
    )

    result = await runtime.execute(
        ChatAction("git", "git_status", PathArguments(path=".")),
        conversation_id="conversation",
        conversation_title="Test",
    )
    assert "untracked.txt" in result["output"]


def _shell():
    return "powershell" if os.name == "nt" else "bash"


def _safe_command() -> str:
    return "Write-Output local-ok" if os.name == "nt" else "printf local-ok"
