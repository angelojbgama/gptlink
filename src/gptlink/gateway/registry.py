"""Current connections and serialized, revocation-safe presence transitions."""

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID

import anyio
from sqlalchemy.exc import SQLAlchemyError
from starlette.websockets import WebSocket, WebSocketDisconnect

from gptlink.gateway.auth import AuthenticationError
from gptlink.persistence.database import Database
from gptlink.persistence.models import utc_now
from gptlink.persistence.repositories import DeviceRepository
from gptlink.protocol.codec import encode_message
from gptlink.protocol.messages import Error, OperationResult, ProtocolMessage

logger = logging.getLogger(__name__)


class ConnectionUnavailable(Exception):
    """Device has disconnected or its connection changed during a send."""


class RemoteOperationError(Exception):
    """The Agent rejected an operation without exposing its untrusted message."""

    def __init__(self, code: str) -> None:
        super().__init__("Agent rejected operation")
        self.code = code


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
        self._pending: dict[tuple[UUID, UUID], tuple[Connection, asyncio.Future[object]]] = {}
        self._lock = asyncio.Lock()

    async def register(self, device_id: UUID, socket: WebSocket) -> Connection:
        connection = Connection(device_id, socket)
        # Commit and publication run in one shielded task. A caller cancelled
        # inside SQLAlchemy's commit/exit must observe the final outcome before
        # cleanup, rather than abandoning an already committed ONLINE device.
        publication = asyncio.create_task(self._publish(connection))
        try:
            with anyio.CancelScope(shield=True):
                old = await asyncio.shield(publication)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                while not publication.done():
                    try:
                        await asyncio.shield(publication)
                    except asyncio.CancelledError:
                        continue
                old = publication.result()
                await self.disconnect(connection)
                await close_connection(connection, 1001)
                if old is not None:
                    await close_connection(old, 4409)
            raise
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

    async def _publish(self, connection: Connection) -> Connection | None:
        device_id = connection.device_id
        async with self._lock:
            async with self.database.transaction() as session:
                if not await DeviceRepository(session).mark_online(device_id, now=utc_now()):
                    raise AuthenticationError("invalid device credential")
            old = self._connections.get(device_id)
            self._connections[device_id] = connection
            return old

    async def touch(self, connection: Connection) -> None:
        async with self._lock:
            if self._connections.get(connection.device_id) is not connection:
                raise ConnectionUnavailable("connection replaced")
            async with self.database.transaction() as session:
                if not await DeviceRepository(session).touch(connection.device_id, now=utc_now()):
                    raise AuthenticationError("invalid device credential")
            connection.last_seen = monotonic()

    async def disconnect(self, connection: Connection) -> None:
        pending: list[asyncio.Future[object]] = []
        async with self._lock:
            if self._connections.get(connection.device_id) is connection:
                async with self.database.transaction() as session:
                    await DeviceRepository(session).mark_offline(connection.device_id)
                del self._connections[connection.device_id]
            for key, (owner, future) in list(self._pending.items()):
                if owner is connection:
                    del self._pending[key]
                    pending.append(future)
        for future in pending:
            if not future.done():
                future.set_exception(ConnectionUnavailable("device disconnected"))

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

    async def request(
        self, device_id: UUID, message: ProtocolMessage, *, timeout: float = 30
    ) -> object:
        """Send once and await the response with the same request ID."""
        connection = self._connections.get(device_id)
        if connection is None:
            raise ConnectionUnavailable("device is offline")
        key = (device_id, message.request_id)
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        async with self._lock:
            if key in self._pending:
                raise ValueError("request ID is already pending")
            self._pending[key] = (connection, future)
        try:
            await self.send(device_id, message)
            async with asyncio.timeout(timeout):
                return await future
        finally:
            async with self._lock:
                current = self._pending.get(key)
                if current is not None and current[1] is future:
                    del self._pending[key]
            if future.done() and not future.cancelled():
                future.exception()
            elif not future.done():
                future.cancel()

    async def resolve(self, connection: Connection, message: ProtocolMessage) -> bool:
        if not isinstance(message, (OperationResult, Error)):
            return False
        key = (connection.device_id, message.request_id)
        async with self._lock:
            pending = self._pending.get(key)
            if pending is None:
                return True  # A valid response may arrive just after its caller timed out.
            if pending[0] is not connection:
                return True
            del self._pending[key]
        future = pending[1]
        if future.done():
            return True
        if isinstance(message, Error):
            future.set_exception(RemoteOperationError(message.payload.code))
        else:
            future.set_result(message.payload.result)
        return True

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
        pending: list[asyncio.Future[object]] = []
        async with self._lock:
            connections = list(self._connections.values())
            for connection in connections:
                try:
                    async with self.database.transaction() as session:
                        await DeviceRepository(session).mark_offline(connection.device_id)
                except SQLAlchemyError as error:
                    logger.warning("Shutdown presence update failed (%s)", type(error).__name__)
                finally:
                    del self._connections[connection.device_id]
            pending = [future for _, future in self._pending.values()]
            self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(ConnectionUnavailable("Gateway is shutting down"))
        for connection in connections:
            await close_connection(connection, 1001)
