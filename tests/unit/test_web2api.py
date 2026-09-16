import json

import httpx
import pytest

from gptlink.chat.web2api import (
    ChatBackendCapabilityError,
    ChatBackendError,
    Web2APIChatBackend,
    validate_loopback_url,
)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8081", "http://localhost:8081/", "http://[::1]:8081"],
)
def test_loopback_urls_are_allowed(url: str) -> None:
    assert validate_loopback_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8081",
        "http://192.168.1.10:8081",
        "http://example.com:8081",
        "http://user:secret@localhost:8081",
        "http://localhost:8081/v1",
    ],
)
def test_non_loopback_or_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        validate_loopback_url(url)


@pytest.mark.asyncio
async def test_health_and_existing_conversation_are_preserved() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "cdp_connected": True})
        body = json.loads(request.content)
        assert body["conversation_id"] == "existing-id"
        return httpx.Response(
            200,
            json={
                "model": "auto",
                "conversation_id": "existing-id",
                "choices": [{"message": {"content": "done"}}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://localhost")
    backend = Web2APIChatBackend(client=client)

    assert await backend.health() is True
    response = await backend.send_message("hello", conversation_id="existing-id")
    assert response.content == "done"
    assert response.conversation_id == "existing-id"
    assert [request.url.path for request in requests] == ["/health", "/v1/chat/completions"]
    await client.aclose()


@pytest.mark.asyncio
async def test_changed_conversation_id_is_rejected() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "conversation_id": "new-id",
                    "choices": [{"message": {"content": "oops"}}],
                },
            )
        ),
        base_url="http://localhost",
    )
    backend = Web2APIChatBackend(client=client)
    with pytest.raises(ChatBackendError, match="preserve"):
        await backend.send_message("hello", conversation_id="existing-id")
    await client.aclose()


@pytest.mark.asyncio
async def test_v020_conversation_reads_report_missing_rest_capability() -> None:
    backend = Web2APIChatBackend()
    with pytest.raises(ChatBackendCapabilityError):
        await backend.list_conversations()
    with pytest.raises(ChatBackendCapabilityError):
        await backend.get_conversation("id")
    await backend.aclose()
