from pathlib import Path

import pytest

from gptlink.chat.protocol import ChatAction, WriteArguments
from gptlink.local.approvals import (
    ApprovalDecision,
    ApprovalRequest,
    SessionApprover,
)


def request(action_id: str = "write-1") -> ApprovalRequest:
    return ApprovalRequest(
        "Conversation",
        Path("/workspace"),
        ChatAction(action_id, "filesystem_write", WriteArguments(path="file.txt", data="x")),
    )


@pytest.mark.asyncio
async def test_approval_once_prompts_again() -> None:
    calls = 0

    def prompt(_request: ApprovalRequest) -> ApprovalDecision:
        nonlocal calls
        calls += 1
        return ApprovalDecision.ONCE

    approver = SessionApprover(prompt)
    assert await approver.approve(request("one")) is True
    assert await approver.approve(request("two")) is True
    assert calls == 2


@pytest.mark.asyncio
async def test_session_approval_is_memory_only_and_skips_later_prompt() -> None:
    calls = 0

    def prompt(_request: ApprovalRequest) -> ApprovalDecision:
        nonlocal calls
        calls += 1
        return ApprovalDecision.SESSION

    approver = SessionApprover(prompt)
    assert await approver.approve(request("one")) is True
    assert await approver.approve(request("two")) is True
    assert calls == 1


@pytest.mark.asyncio
async def test_denial_is_not_cached_as_approval() -> None:
    approver = SessionApprover(lambda _request: ApprovalDecision.DENY)

    assert await approver.approve(request()) is False
