"""Authenticated protocol-v1 Agent transport; untrusted input is never logged.

Private close codes: 4400 malformed/unexpected message, 4401 authentication or
identity failure, 4406 incompatible version, 4408 timeout, 4409 replacement.
"""

import asyncio
import json

import anyio
from fastapi import APIRouter, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from gptlink.gateway.auth import AuthenticationError, DeviceAuthenticator
from gptlink.gateway.registry import Connection, ConnectionRegistry, ConnectionUnavailable
from gptlink.persistence.models import utc_now
from gptlink.protocol.codec import decode_message, encode_message
from gptlink.protocol.messages import (
    AgentHello,
    AgentWelcome,
    AgentWelcomePayload,
    HeartbeatPing,
    HeartbeatPong,
    HeartbeatPongPayload,
    ProtocolMessage,
)

router = APIRouter()


class ProtocolClose(Exception):
    def __init__(self, code: int) -> None:
        self.code = code


async def receive_message(socket: WebSocket) -> ProtocolMessage:
    frame = await socket.receive()
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    data = frame.get("text")
    if data is None:
        raise ProtocolClose(4400)
    try:
        raw = json.loads(data)
    except (ValueError, RecursionError):
        raise ProtocolClose(4400) from None
    if isinstance(raw, dict):
        version = raw.get("protocol_version")
        if type(version) is int and version != 1:
            raise ProtocolClose(4406)
        payload = raw.get("payload")
        if raw.get("type") == "agent.hello" and isinstance(payload, dict):
            versions = payload.get("versions")
            if isinstance(versions, list) and versions != [1]:
                raise ProtocolClose(4406)
    try:
        return decode_message(data)
    except ValidationError:
        raise ProtocolClose(4400) from None


@router.websocket("/agent/ws")
async def agent_websocket(socket: WebSocket) -> None:
    await socket.accept()
    connection: Connection | None = None
    registry: ConnectionRegistry = socket.app.state.registry
    close_code = 1000
    try:
        if not socket.app.state.ready:
            raise ProtocolClose(1013)
        async with asyncio.timeout(socket.app.state.settings.handshake_timeout):
            hello = await receive_message(socket)
        if not isinstance(hello, AgentHello):
            raise ProtocolClose(4400)
        await DeviceAuthenticator(socket.app.state.database).authenticate(
            hello.device_id, hello.payload.token
        )
        welcome = AgentWelcome(
            protocol_version=1,
            type="agent.welcome",
            device_id=hello.device_id,
            request_id=hello.request_id,
            payload=AgentWelcomePayload(selected_version=1),
        )
        await socket.send_text(encode_message(welcome))
        connection = await registry.register(hello.device_id, socket)
        while True:
            message = await receive_message(socket)
            if message.device_id != connection.device_id:
                raise ProtocolClose(4401)
            if not isinstance(message, HeartbeatPing):
                raise ProtocolClose(4400)
            await registry.touch(connection)
            pong = HeartbeatPong(
                protocol_version=1,
                type="heartbeat.pong",
                device_id=message.device_id,
                request_id=message.request_id,
                payload=HeartbeatPongPayload(timestamp=utc_now()),
            )
            await socket.send_text(encode_message(pong))
    except ProtocolClose as error:
        close_code = error.code
    except TimeoutError:
        close_code = 4408
    except AuthenticationError:
        close_code = 4401
    except ConnectionUnavailable:
        close_code = 4409
    except (WebSocketDisconnect, OSError, RuntimeError):
        close_code = 1000
    finally:
        # TestClient/server cancellation must still commit presence cleanup.
        with anyio.CancelScope(shield=True):
            if connection is not None:
                await registry.disconnect(connection)
            try:
                await socket.close(code=close_code)
            except (WebSocketDisconnect, RuntimeError, OSError):
                pass
