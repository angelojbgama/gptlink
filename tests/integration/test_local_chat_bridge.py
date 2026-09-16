import json
import os
import re
from pathlib import Path
from uuid import UUID

import pytest

from gptlink.chat.models import ChatResponse, Conversation, ConversationHistory
from gptlink.chat.orchestrator import ChatOrchestrator
from gptlink.common.config import Settings
from gptlink.common.types import PermissionLevel
from gptlink.local.runtime import build_local_runtime
from gptlink.local.workspace import LocalWorkspace


class AllowApprover:
    def __init__(self) -> None:
        self.tools: list[str] = []

    async def approve(self, request) -> bool:
        self.tools.append(request.action.tool)
        return True


class ScriptedBackend:
    def __init__(self) -> None:
        self.step = 0
        self.sent: list[tuple[str, str | None]] = []
        self.results: list[dict] = []
        self.wait_job = None

    async def health(self) -> bool:
        return True

    async def list_conversations(self, limit: int = 20) -> list[Conversation]:
        return [Conversation("conversation-existing", "Existing conversation")]

    async def get_conversation(self, conversation_id: str) -> ConversationHistory:
        return ConversationHistory(Conversation(conversation_id, "Existing conversation"))

    async def send_message(
        self, message: str, *, conversation_id: str | None = None
    ) -> ChatResponse:
        self.sent.append((message, conversation_id))
        if self.step:
            match = re.search(r"<gptlink_result[^>]*>\s*(.*?)\s*</gptlink_result>", message, re.S)
            assert match is not None
            self.results.append(json.loads(match.group(1)))
        scripts = [
            _action("list", "filesystem_list", {"path": "."}),
            _action("read", "filesystem_read", {"path": "hello.txt"}),
            _action("write", "filesystem_write", {"path": "created.txt", "data": "created safely"}),
            _action("escape", "filesystem_read", {"path": "../outside.txt"}),
            _action(
                "command",
                "command_start",
                {"command": _safe_command(), "shell": _shell(), "cwd": "."},
            ),
        ]
        if self.step < len(scripts):
            content = scripts[self.step]
        elif self.step == len(scripts):
            job_id = self.results[-1]["result"]["job_id"]
            assert self.wait_job is not None
            await self.wait_job(UUID(job_id))
            content = _action("output", "command_output", {"job_id": job_id})
        else:
            content = "all done"
        self.step += 1
        return ChatResponse(content, conversation_id or "unexpected")


@pytest.mark.asyncio
async def test_fake_chat_drives_real_local_runtime_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello from workspace", encoding="utf-8")
    approver = AllowApprover()
    runtime = build_local_runtime(
        LocalWorkspace.from_cwd(tmp_path),
        permission=PermissionLevel.READ_WRITE,
        approver=approver,
        settings=Settings(_env_file=None, local_audit_path=tmp_path / "audit.jsonl"),
    )
    backend = ScriptedBackend()
    backend.wait_job = runtime.dispatcher.jobs.wait

    response = await ChatOrchestrator(backend, runtime, max_actions_per_turn=10).run_turn(
        "start session",
        conversation_id="conversation-existing",
        conversation_title="Existing conversation",
    )

    assert response.content == "all done"
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "created safely"
    assert all(item[1] == "conversation-existing" for item in backend.sent)
    assert backend.results[0]["ok"] is True
    assert backend.results[1]["result"]["content"] == "hello from workspace"
    assert backend.results[2]["ok"] is True
    assert backend.results[3]["ok"] is False
    assert backend.results[4]["result"]["job_id"]
    assert "local-ok" in "".join(chunk["data"] for chunk in backend.results[5]["result"])
    assert approver.tools == ["filesystem_write", "command_start"]


def _action(action_id: str, tool: str, arguments: dict) -> str:
    return (
        "<gptlink_action>\n"
        + json.dumps({"id": action_id, "tool": tool, "arguments": arguments})
        + "\n</gptlink_action>"
    )


def _shell() -> str:
    return "powershell" if os.name == "nt" else "bash"


def _safe_command() -> str:
    return "Write-Output local-ok" if os.name == "nt" else "printf local-ok"
