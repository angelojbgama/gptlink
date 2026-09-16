"""HTTP-only adapter for the external ChatGPT-Web2API service."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from gptlink.chat.models import (
    ChatResponse,
    Conversation,
    ConversationHistory,
    ConversationMessage,
)


class ChatBackendError(RuntimeError):
    """The configured chat backend failed or returned an invalid response."""


class ChatBackendCapabilityError(ChatBackendError):
    """The backend version does not expose a requested operation over REST."""


def validate_loopback_url(value: str) -> str:
    """Accept explicit HTTP loopback URLs only."""
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("chat URL must use HTTP on 127.0.0.1, localhost, or ::1")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("chat URL must not contain credentials, query parameters, or fragments")
    if parsed.path not in {"", "/"}:
        raise ValueError("chat URL must not contain a path")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("chat URL has an invalid port") from error
    return value.rstrip("/")


class Web2APIChatBackend:
    """Use the public REST surface from ChatGPT-Web2API v0.2.x.

    Version 0.2.0 does not expose conversation list/history over REST. Those
    methods deliberately report a capability error instead of guessing private
    or future endpoints.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8081",
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 120,
        model: str = "auto",
    ) -> None:
        self.base_url = validate_loopback_url(base_url)
        self.model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def __aenter__(self) -> Web2APIChatBackend:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get(self._url("/health"))
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return False
        return bool(payload.get("status") == "ok" and payload.get("cdp_connected", True))

    async def list_conversations(self, limit: int = 20) -> list[Conversation]:
        del limit
        raise ChatBackendCapabilityError(
            "ChatGPT-Web2API 0.2.0 does not expose conversation listing over REST; "
            "pass --conversation UUID"
        )

    async def get_conversation(self, conversation_id: str) -> ConversationHistory:
        del conversation_id
        raise ChatBackendCapabilityError(
            "ChatGPT-Web2API 0.2.0 does not expose conversation history over REST"
        )

    async def send_message(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
    ) -> ChatResponse:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": message}],
            "stream": False,
        }
        if conversation_id is not None:
            request["conversation_id"] = conversation_id
        try:
            response = await self._client.post(self._url("/v1/chat/completions"), json=request)
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            returned_id = payload["conversation_id"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ChatBackendError("ChatGPT-Web2API returned an invalid response") from error
        if not isinstance(content, str) or not isinstance(returned_id, str) or not returned_id:
            raise ChatBackendError(
                "ChatGPT-Web2API response is missing chat content or conversation_id"
            )
        if conversation_id is not None and returned_id != conversation_id:
            raise ChatBackendError("ChatGPT-Web2API did not preserve the attached conversation_id")
        model = payload.get("model")
        return ChatResponse(content, returned_id, model if isinstance(model, str) else None)

    def _url(self, path: str) -> str:
        if self._client.base_url == httpx.URL(""):
            return f"{self.base_url}{path}"
        return path


def history_from_payload(payload: dict[str, Any]) -> ConversationHistory:
    """Parse a future/alternate REST history shape without coupling the adapter to it."""
    conversation = Conversation(id=str(payload["id"]), title=str(payload.get("title", "Untitled")))
    messages = tuple(
        ConversationMessage(role=str(item["role"]), content=str(item["content"]))
        for item in payload.get("messages", [])
    )
    return ConversationHistory(conversation, messages)
