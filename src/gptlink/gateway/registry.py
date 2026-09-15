"""Current connections and serialized, revocation-safe presence transitions."""

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID

import anyio
from starlette.websockets import WebSocket, WebSocketDisconnect

from gptlink.gateway.auth import AuthenticationError
from gptlink.persistence.database import Database
from gptlink.persistence.models import utc_now
from gptlink.persistence.repositories import DeviceRepository
from gptlink.protocol.codec import encode_message
from gptlink.protocol.messages import ProtocolMessage


class ConnectionUnavailable(Exception):
    """Device has disconnected or its connection changed during a send."""


@dataclass(eq=False)
class Connection:
    device_id: UUID
    socket: WebSocket
    last_seen: float = field(default_factory=monotonic)


async def close_connection(connection: Connection, code: int) -> None:
    try:
        await connection.socket.close(code=code)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass  # A simultaneous disconnect/close already retired the transport.


class ConnectionRegistry:
    """One Gateway process owns presence. Network I/O never holds the state lock.

    A send racing replacement can have an ambiguous outcome and raises rather
    than replaying a potentially mutating request on the new connection.
    """

    def __init__(self, database: Database) -> None:
        self.database = database
        self._connections: dict[UUID, Connection] = {}
        self._lock = asyncio.Lock()

    async def register(self, device_id: UUID, socket: WebSocket) -> Connection:
        connection = Connection(device_id, socket)
        async with self._lock:
            async with self.database.transaction() as session:
                if not await DeviceRepository(session).mark_online(device_id, now=utc_now()):
                    raise AuthenticationError("invalid device credential")
            old = self._connections.get(device_id)
            self._connections[device_id] = connection
        if old is not None:
            try:
                await close_connection(old, 4409)
            except asyncio.CancelledError:
                # The caller has not received ownership yet, so retire it here.
                with anyio.CancelScope(shield=True):
                    await self.disconnect(connection)
                    await close_connection(connection, 1001)
                raise
        return connection

    async def touch(self, connection: Connection) -> None:
        async with self._lock:
            if self._connections.get(connection.device_id) is not connection:
                raise ConnectionUnavailable("connection replaced")
            async with self.database.transaction() as session:
                if not await DeviceRepository(session).mark_online(
                    connection.device_id, now=utc_now()
                ):
                    raise AuthenticationError("invalid device credential")
            connection.last_seen = monotonic()

    async def disconnect(self, connection: Connection) -> None:
        async with self._lock:
            if self._connections.get(connection.device_id) is connection:
                async with self.database.transaction() as session:
                    await DeviceRepository(session).mark_offline(connection.device_id)
                del self._connections[connection.device_id]

    async def send(self, device_id: UUID, message: ProtocolMessage) -> None:
        if message.device_id != device_id:
            raise ValueError("message device_id does not match destination")
        connection = self._connections.get(device_id)
        if connection is None:
            raise ConnectionUnavailable("device is offline")
        try:
            await connection.socket.send_text(encode_message(message))
        except (WebSocketDisconnect, RuntimeError, OSError):
            await self.disconnect(connection)
            await close_connection(connection, 1011)
            raise ConnectionUnavailable("device disconnected during send") from None
        if self._connections.get(device_id) is not connection:
            raise ConnectionUnavailable("connection replaced during send")

    async def expire(self, threshold: float) -> None:
        expired = []
        async with self._lock:
            for device_id, connection in list(self._connections.items()):
                if monotonic() - connection.last_seen >= threshold:
                    async with self.database.transaction() as session:
                        await DeviceRepository(session).mark_offline(device_id)
                    del self._connections[device_id]
                    expired.append(connection)
        for connection in expired:
            await close_connection(connection, 4408)

    async def close(self) -> None:
        for connection in list(self._connections.values()):
            await self.disconnect(connection)
            await close_connection(connection, 1001)
