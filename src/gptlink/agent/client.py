"""Outbound Agent credential storage and transport skeleton."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from gptlink.common.config import Settings
from gptlink.common.types import Capability
from gptlink.protocol.codec import decode_message, encode_message
from gptlink.protocol.messages import (
    AgentHello,
    AgentHelloPayload,
    AgentWelcome,
    Error,
    ErrorPayload,
    HeartbeatPing,
    HeartbeatPingPayload,
    HeartbeatPong,
    ProtocolMessage,
)

from .dispatcher import AgentDispatcher


@dataclass(frozen=True)
class AgentCredential:
    gateway: str
    device_id: str
    token: str = field(repr=False)
    display_name: str


class PairMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str
    platform: str = "linux"
    hostname: str
    agent_version: str = "0.1.0"
    protocol_version: int = 1
    capabilities: list[Capability] = Field(default_factory=list)


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def save(self, credential: AgentCredential) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(self.path.parent.stat().st_mode | stat.S_IRWXU)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                if hasattr(os, "fchmod"):
                    os.fchmod(handle.fileno(), 0o600)
                json.dump(asdict(credential), handle, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self) -> AgentCredential:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            credential = AgentCredential(
                gateway=str(raw["gateway"]),
                device_id=str(raw["device_id"]),
                token=str(raw["token"]),
                display_name=str(raw["display_name"]),
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise ValueError("invalid agent credential file") from error
        if not all(
            (credential.gateway, credential.device_id, credential.token, credential.display_name)
        ):
            raise ValueError("invalid agent credential file")
        return credential


class AgentClient:
    """Outbound-only Agent transport shared by supported platforms."""

    def __init__(
        self,
        settings: Settings,
        store: CredentialStore,
        *,
        dispatcher: AgentDispatcher | None = None,
        capabilities: set[Capability] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.dispatcher = dispatcher
        self.capabilities = capabilities or set()

    async def pair(self, gateway: str, code: str, metadata: PairMetadata) -> AgentCredential:
        url = gateway.rstrip("/") + "/api/v1/pair"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                json={"code": code, "metadata": metadata.model_dump(mode="json")},
            )
        if response.status_code != 201:
            raise ValueError("pairing failed")
        try:
            data = response.json()
            credential = AgentCredential(
                gateway=gateway,
                device_id=str(UUID(data["device_id"])),
                token=str(data["token"]),
                display_name=str(data["display_name"]),
            )
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("invalid pairing response") from error
        self.store.save(credential)
        return credential

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        credential = self.store.load()
        stop_event = stop_event or asyncio.Event()
        backoff_attempt = 0
        from gptlink.agent.reconnect import Backoff

        backoff = Backoff()
        while not stop_event.is_set():
            try:
                await self._connected(credential, stop_event)
                backoff_attempt = 0
            except (OSError, TimeoutError, ConnectionError, WebSocketException):
                if stop_event.is_set():
                    break
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=backoff.delay(backoff_attempt)
                    )
                except TimeoutError:
                    pass
                backoff_attempt += 1

    async def _connected(self, credential: AgentCredential, stop_event: asyncio.Event) -> None:
        parsed = urlparse(credential.gateway)
        if parsed.scheme == "https":
            scheme = "wss"
        elif parsed.scheme == "http" and self.settings.env != "production":
            scheme = "ws"
        else:
            raise ValueError("insecure Gateway URL is not allowed")
        ws_url = f"{scheme}://{parsed.netloc}/agent/ws"
        device_id = UUID(credential.device_id)
        async with connect(ws_url, open_timeout=self.settings.handshake_timeout) as socket:
            hello = AgentHello(
                protocol_version=1,
                type="agent.hello",
                request_id=uuid4(),
                device_id=device_id,
                payload=AgentHelloPayload(
                    token=credential.token,
                    versions=(1,),
                    capabilities=tuple(sorted(self.capabilities, key=lambda item: item.value)),
                ),
            )
            await socket.send(encode_message(hello))
            welcome = decode_message(
                await asyncio.wait_for(_receive_text(socket), self.settings.handshake_timeout)
            )
            if not isinstance(welcome, AgentWelcome) or welcome.device_id != device_id:
                raise ConnectionError("invalid Gateway welcome")
            heartbeat = asyncio.create_task(self._heartbeat(socket, device_id, stop_event))
            try:
                while not stop_event.is_set():
                    message = await asyncio.wait_for(
                        _receive_text(socket), self.settings.heartbeat_interval * 2
                    )
                    decoded = decode_message(message)
                    if decoded.device_id != device_id:
                        raise ConnectionError("Gateway identity mismatch")
                    if isinstance(decoded, HeartbeatPong):
                        continue
                    response: ProtocolMessage
                    if self.dispatcher is None:
                        response = Error(
                            protocol_version=1,
                            type="error",
                            request_id=decoded.request_id,
                            device_id=device_id,
                            payload=ErrorPayload(
                                code="operation_unavailable",
                                message="Agent operations are not configured",
                                retryable=False,
                            ),
                        )
                    else:
                        response = await self.dispatcher.dispatch(decoded)
                    await socket.send(encode_message(response))
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat

    async def _heartbeat(
        self, socket: ClientConnection, device_id: UUID, stop_event: asyncio.Event
    ) -> None:
        while not stop_event.is_set():
            await asyncio.sleep(self.settings.heartbeat_interval)
            ping = HeartbeatPing(
                protocol_version=1,
                type="heartbeat.ping",
                request_id=uuid4(),
                device_id=device_id,
                payload=HeartbeatPingPayload(timestamp=datetime.now(UTC)),
            )
            await socket.send(encode_message(ping))


def urlparse(value: str):
    from urllib.parse import urlparse as parse

    return parse(value)


async def _receive_text(socket: ClientConnection) -> str:
    message = await socket.recv()
    if not isinstance(message, str):
        raise ConnectionError("binary Gateway message is not allowed")
    return message
