"""Transport-neutral chat models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ConversationHistory:
    conversation: Conversation
    messages: tuple[ConversationMessage, ...] = ()


@dataclass(frozen=True)
class ChatResponse:
    content: str
    conversation_id: str
    model: str | None = None
