"""Interface implemented by optional chat transports."""

from typing import Protocol

from gptlink.chat.models import ChatResponse, Conversation, ConversationHistory


class ChatBackend(Protocol):
    async def health(self) -> bool: ...

    async def list_conversations(self, limit: int = 20) -> list[Conversation]: ...

    async def get_conversation(self, conversation_id: str) -> ConversationHistory: ...

    async def send_message(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
    ) -> ChatResponse: ...
