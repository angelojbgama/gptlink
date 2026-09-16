"""Per-process approval policy for local mutations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from gptlink.chat.protocol import ChatAction, CommandStartArguments, WriteArguments


class ApprovalDecision(StrEnum):
    ONCE = "once"
    SESSION = "session"
    DENY = "deny"


@dataclass(frozen=True)
class ApprovalRequest:
    conversation_title: str
    workspace: Path
    action: ChatAction


class SessionApprover:
    """Prompt for mutations and remember only in-memory, per-tool grants."""

    def __init__(self, prompt: Callable[[ApprovalRequest], ApprovalDecision]) -> None:
        self._prompt = prompt
        self._session_tools: set[str] = set()

    async def approve(self, request: ApprovalRequest) -> bool:
        if request.action.tool in self._session_tools:
            return True
        decision = await asyncio.to_thread(self._prompt, request)
        if decision is ApprovalDecision.SESSION:
            self._session_tools.add(request.action.tool)
            return True
        return decision is ApprovalDecision.ONCE


def interactive_prompt(request: ApprovalRequest) -> ApprovalDecision:
    """Show the complete mutation and accept Y/A/N from the local terminal."""
    action = request.action
    print("\nGPTLink request")
    print(f"\nConversation:\n{request.conversation_title}")
    print(f"\nWorkspace:\n{request.workspace}")
    print(f"\nAction:\n{action.tool}")
    if isinstance(action.arguments, WriteArguments):
        print(f"\nPath:\n{action.arguments.path}")
    elif isinstance(action.arguments, CommandStartArguments):
        print(f"\nShell:\n{action.arguments.shell}")
        print(f"\nCWD:\n{action.arguments.cwd}")
        print(f"\nCommand:\n{action.arguments.command}")
    print("\nAllow?\n\n[Y] once\n[A] for session\n[N] deny")
    while True:
        answer = input("> ").strip().casefold()
        if answer in {"y", "yes"}:
            return ApprovalDecision.ONCE
        if answer in {"a", "all"}:
            return ApprovalDecision.SESSION
        if answer in {"n", "no", ""}:
            return ApprovalDecision.DENY
        print("Choose Y, A, or N.")
