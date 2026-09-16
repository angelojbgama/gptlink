"""Bounded action loop between a chat backend and the local runtime."""

from __future__ import annotations

from typing import Protocol

from gptlink.chat.backend import ChatBackend
from gptlink.chat.models import ChatResponse
from gptlink.chat.protocol import ActionProtocolError, ChatAction, parse_action, render_result
from gptlink.local.runtime import LocalActionError


class ActionExecutor(Protocol):
    async def execute(
        self,
        action: ChatAction,
        *,
        conversation_id: str,
        conversation_title: str,
    ) -> object: ...


class ChatOrchestrator:
    def __init__(
        self,
        backend: ChatBackend,
        runtime: ActionExecutor,
        *,
        max_actions_per_turn: int = 20,
    ) -> None:
        if max_actions_per_turn < 1:
            raise ValueError("max_actions_per_turn must be positive")
        self.backend = backend
        self.runtime = runtime
        self.max_actions_per_turn = max_actions_per_turn

    async def run_turn(
        self,
        message: str,
        *,
        conversation_id: str,
        conversation_title: str,
    ) -> ChatResponse:
        response = await self._send(message, conversation_id)
        seen: set[str] = set()
        for _ in range(self.max_actions_per_turn):
            try:
                action = parse_action(response.content)
            except ActionProtocolError as error:
                return ChatResponse(
                    content=f"GPTLink rejected malformed action: {error}",
                    conversation_id=conversation_id,
                    model=response.model,
                )
            if action is None:
                return response
            if action.id in seen:
                rendered = render_result(
                    action.id,
                    error_type="duplicate_action",
                    error_message="action id was already handled in this turn",
                )
            else:
                seen.add(action.id)
                try:
                    result = await self.runtime.execute(
                        action,
                        conversation_id=conversation_id,
                        conversation_title=conversation_title,
                    )
                except LocalActionError as error:
                    rendered = render_result(
                        action.id,
                        error_type="local_action_error",
                        error_message=str(error),
                    )
                except Exception:
                    rendered = render_result(
                        action.id,
                        error_type="internal_error",
                        error_message="local action failed",
                    )
                else:
                    rendered = render_result(action.id, result=result)
            response = await self._send(rendered, conversation_id)
        return ChatResponse(
            content=f"GPTLink action limit reached ({self.max_actions_per_turn})",
            conversation_id=conversation_id,
            model=response.model,
        )

    async def _send(self, message: str, conversation_id: str) -> ChatResponse:
        response = await self.backend.send_message(message, conversation_id=conversation_id)
        if response.conversation_id != conversation_id:
            raise RuntimeError("chat backend changed the attached conversation_id")
        return response
