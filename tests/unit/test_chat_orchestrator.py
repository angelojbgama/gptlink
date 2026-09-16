from dataclasses import dataclass, field

import pytest

from gptlink.chat.models import ChatResponse
from gptlink.chat.orchestrator import ChatOrchestrator
from gptlink.chat.protocol import ChatAction


@dataclass
class FakeBackend:
    responses: list[str]
    sent: list[tuple[str, str | None]] = field(default_factory=list)

    async def health(self) -> bool:
        return True

    async def list_conversations(self, limit: int = 20):
        return []

    async def get_conversation(self, conversation_id: str):
        raise NotImplementedError

    async def send_message(self, message: str, *, conversation_id: str | None = None):
        self.sent.append((message, conversation_id))
        return ChatResponse(self.responses.pop(0), conversation_id or "created", "fake")


@dataclass
class FakeRuntime:
    actions: list[ChatAction] = field(default_factory=list)

    async def execute(self, action, *, conversation_id: str, conversation_title: str):
        self.actions.append(action)
        return {"listed": True}


@pytest.mark.asyncio
async def test_action_result_returns_to_same_existing_conversation() -> None:
    backend = FakeBackend(
        [
            '<gptlink_action>{"id":"one","tool":"filesystem_list",'
            '"arguments":{"path":"."}}</gptlink_action>',
            "finished",
        ]
    )
    runtime = FakeRuntime()
    orchestrator = ChatOrchestrator(backend, runtime)

    response = await orchestrator.run_turn(
        "start", conversation_id="conversation-x", conversation_title="Existing"
    )

    assert response.content == "finished"
    assert len(runtime.actions) == 1
    assert [conversation_id for _, conversation_id in backend.sent] == [
        "conversation-x",
        "conversation-x",
    ]
    assert '<gptlink_result id="one">' in backend.sent[1][0]


@pytest.mark.asyncio
async def test_action_loop_stops_normally_without_an_action() -> None:
    backend = FakeBackend(["plain final response"])
    runtime = FakeRuntime()

    response = await ChatOrchestrator(backend, runtime).run_turn(
        "hello", conversation_id="same", conversation_title="Title"
    )

    assert response.content == "plain final response"
    assert runtime.actions == []


@pytest.mark.asyncio
async def test_action_loop_has_a_hard_limit() -> None:
    responses = [
        f'<gptlink_action>{{"id":"a{index}","tool":"process_list","arguments":{{}}}}</gptlink_action>'
        for index in range(4)
    ]
    backend = FakeBackend(responses)
    runtime = FakeRuntime()

    response = await ChatOrchestrator(backend, runtime, max_actions_per_turn=3).run_turn(
        "start", conversation_id="same", conversation_title="Title"
    )

    assert response.content == "GPTLink action limit reached (3)"
    assert len(runtime.actions) == 3
    assert len(backend.sent) == 4
