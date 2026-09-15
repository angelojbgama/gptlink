"""Real ASGI WebSockets and SQLite exercise the Gateway transport boundary."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gptlink.common.config import Settings
from gptlink.common.types import DeviceStatus
from gptlink.gateway.auth import hash_device_token
from gptlink.persistence.database import Database
from gptlink.persistence.models import Device
from gptlink.persistence.repositories import DeviceRepository


@pytest.fixture
def gateway(tmp_path):
    from gptlink.gateway.app import create_app

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}",
        handshake_timeout=0.15,
        heartbeat_interval=0.05,
        offline_threshold=0.5,
    )
    database = Database(settings.database_url)
    app = create_app(settings, database=database)
    with TestClient(app) as client:
        device_id = uuid4()
        token = "test-credential-that-must-not-appear-in-logs"

        async def seed():
            async with database.transaction() as session:
                await DeviceRepository(session).add(
                    Device(
                        device_id=device_id,
                        display_name="Laptop",
                        platform="linux",
                        hostname="laptop",
                        agent_version="0.1.0",
                        protocol_version=1,
                        device_token_hash=hash_device_token(token),
                    )
                )

        client.portal.call(seed)
        yield client, app, database, device_id, token


def envelope(device_id, kind="agent.hello", **payload):
    return {
        "protocol_version": 1,
        "type": kind,
        "device_id": str(device_id),
        "request_id": str(uuid4()),
        "payload": payload,
    }


def hello(device_id, token):
    return envelope(device_id, token=token, versions=[1], capabilities=[])


def read_device(gateway):
    client, _, database, device_id, _ = gateway

    async def read():
        async with database.transaction() as session:
            return await DeviceRepository(session).get(device_id)

    return client.portal.call(read)


def revoke(gateway):
    client, _, database, device_id, _ = gateway

    async def write():
        async with database.transaction() as session:
            device = await DeviceRepository(session).get(device_id)
            device.status = DeviceStatus.REVOKED
            device.revoked_at = datetime.now(UTC)

    client.portal.call(write)


def test_health_readiness_and_database_lifecycle(tmp_path):
    from gptlink.gateway.app import create_app

    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    app = create_app(settings)
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 503
    with client:
        assert client.get("/ready").json() == {"status": "ready"}
    assert client.get("/ready").status_code == 503


def test_hello_heartbeat_and_disconnect_update_device(gateway, caplog):
    client, _, _, device_id, token = gateway
    with client.websocket_connect("/agent/ws") as ws:
        message = hello(device_id, token)
        ws.send_json(message)
        welcome = ws.receive_json()
        assert welcome == {
            **message,
            "type": "agent.welcome",
            "payload": {"selected_version": 1},
        }
        connected = read_device(gateway)
        assert connected.status is DeviceStatus.ONLINE
        assert connected.last_seen is not None
        ping = envelope(
            device_id,
            "heartbeat.ping",
            timestamp=(datetime.now(UTC) - timedelta(days=10)).isoformat(),
        )
        before = datetime.now(UTC)
        ws.send_json(ping)
        pong = ws.receive_json()
        assert pong["type"] == "heartbeat.pong"
        assert pong["device_id"] == str(device_id)
        assert pong["request_id"] == ping["request_id"]
        assert read_device(gateway).last_seen >= before
    wait_for_status(gateway, DeviceStatus.OFFLINE)
    assert token not in caplog.text


def wait_for_status(gateway, status):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if read_device(gateway).status is status:
            return
        time.sleep(0.01)
    assert read_device(gateway).status is status


@pytest.mark.parametrize(
    "failure,code",
    [
        ("token", 4401),
        ("revoked", 4401),
        ("identity", 4401),
        ("version", 4406),
        ("versions", 4406),
        ("malformed", 4400),
        ("prehello", 4400),
        ("binary", 4400),
    ],
)
def test_invalid_handshake_is_closed_without_credentials(gateway, caplog, failure, code):
    client, _, _, device_id, token = gateway
    message = hello(device_id, token)
    if failure == "token":
        message["payload"]["token"] = "wrong"
    elif failure == "revoked":
        revoke(gateway)
    elif failure == "identity":
        message["device_id"] = str(uuid4())
    elif failure == "version":
        message["protocol_version"] = 2
    elif failure == "versions":
        message["payload"]["versions"] = [2]
    elif failure == "malformed":
        message["payload"]["extra"] = token
    elif failure == "prehello":
        message = envelope(device_id, "heartbeat.ping", timestamp=datetime.now(UTC).isoformat())
    with client.websocket_connect("/agent/ws") as ws:
        if failure == "binary":
            ws.send_bytes(b"binary")
        else:
            ws.send_json(message)
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == code
        assert token not in error.value.reason
    assert read_device(gateway).status is (
        DeviceStatus.REVOKED if failure == "revoked" else DeviceStatus.OFFLINE
    )
    assert token not in caplog.text


def test_handshake_timeout(gateway):
    client, *_ = gateway
    with client.websocket_connect("/agent/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 4408


def test_heartbeat_timeout_marks_offline(gateway):
    client, _, _, device_id, token = gateway
    with client.websocket_connect("/agent/ws") as ws:
        ws.send_json(hello(device_id, token))
        ws.receive_json()
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 4408
        assert read_device(gateway).status is DeviceStatus.OFFLINE


def test_replacement_and_registry_send_preserve_new_connection(gateway):
    from gptlink.protocol.codec import decode_message

    client, app, _, device_id, token = gateway
    with client.websocket_connect("/agent/ws") as old:
        old.send_json(hello(device_id, token))
        old.receive_json()
        with client.websocket_connect("/agent/ws") as current:
            current.send_json(hello(device_id, token))
            current.receive_json()
            with pytest.raises(WebSocketDisconnect) as error:
                old.receive_json()
            assert error.value.code == 4409
            assert read_device(gateway).status is DeviceStatus.ONLINE
            message = envelope(device_id, "process.list")
            client.portal.call(
                app.state.registry.send, device_id, decode_message(json.dumps(message))
            )
            assert current.receive_json() == message
    wait_for_status(gateway, DeviceStatus.OFFLINE)


def test_disconnect_preserves_revocation(gateway):
    client, _, _, device_id, token = gateway
    with client.websocket_connect("/agent/ws") as ws:
        ws.send_json(hello(device_id, token))
        ws.receive_json()
        revoke(gateway)
    assert read_device(gateway).status is DeviceStatus.REVOKED


@pytest.mark.parametrize("failure", ["identity", "malformed", "hello", "revoked"])
def test_invalid_established_message_closes_and_preserves_status(gateway, failure):
    client, _, _, device_id, token = gateway
    with client.websocket_connect("/agent/ws") as ws:
        ws.send_json(hello(device_id, token))
        ws.receive_json()
        ping = envelope(device_id, "heartbeat.ping", timestamp=datetime.now(UTC).isoformat())
        if failure == "identity":
            ping["device_id"] = str(uuid4())
        elif failure == "malformed":
            ping["payload"] = {}
        elif failure == "hello":
            ping = hello(device_id, token)
        else:
            revoke(gateway)
        ws.send_json(ping)
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == (4401 if failure in {"identity", "revoked"} else 4400)
    wait_for_status(gateway, DeviceStatus.REVOKED if failure == "revoked" else DeviceStatus.OFFLINE)


def test_registry_checks_destination_and_missing_device(gateway):
    from gptlink.gateway.registry import ConnectionUnavailable
    from gptlink.protocol.codec import decode_message

    client, app, _, device_id, _ = gateway
    message = decode_message(json.dumps(envelope(device_id, "process.list")))
    with pytest.raises(ValueError, match="device_id"):
        client.portal.call(app.state.registry.send, uuid4(), message)
    with pytest.raises(ConnectionUnavailable):
        client.portal.call(app.state.registry.send, device_id, message)


@pytest.mark.parametrize("replace,fail", [(False, True), (True, True), (True, False)])
def test_send_during_replacement_or_transport_failure(gateway, replace, fail):
    """A suspended transport exercises actual registry concurrency, without network timing."""
    from gptlink.gateway.registry import ConnectionUnavailable
    from gptlink.protocol.codec import decode_message

    client, app, _, device_id, _ = gateway

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        class Socket:
            async def send_text(self, text):
                started.set()
                await release.wait()
                if fail:
                    raise OSError("transport failed")

            async def close(self, code):
                pass

        old = await app.state.registry.register(device_id, Socket())
        message = decode_message(json.dumps(envelope(device_id, "process.list")))
        task = asyncio.create_task(app.state.registry.send(device_id, message))
        await started.wait()
        if replace:
            # Would deadlock if send held the registry lock during network I/O.
            async with asyncio.timeout(1):
                current = await app.state.registry.register(device_id, Socket())
        release.set()
        with pytest.raises(ConnectionUnavailable):
            await task
        await app.state.registry.disconnect(old)
        async with app.state.database.transaction() as session:
            device = await DeviceRepository(session).get(device_id)
            assert device.status is (DeviceStatus.ONLINE if replace else DeviceStatus.OFFLINE)
        if replace:
            await app.state.registry.disconnect(current)

    client.portal.call(exercise)


def test_database_startup_failure_keeps_readiness_false(tmp_path):
    from gptlink.gateway.app import create_app

    settings = Settings(
        _env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 'absent' / 'db'}"
    )
    app = create_app(settings)
    from sqlalchemy.exc import OperationalError

    with pytest.raises(OperationalError):
        with TestClient(app):
            pytest.fail("Database startup must fail before readiness")
    assert TestClient(app).get("/ready").status_code == 503


def test_cancellation_during_replacement_does_not_leave_orphan_online(gateway):
    client, app, _, device_id, _ = gateway

    async def exercise():
        closing = asyncio.Event()

        class Socket:
            def __init__(self, block=False):
                self.block = block

            async def close(self, code):
                if self.block:
                    closing.set()
                    await asyncio.Event().wait()

        await app.state.registry.register(device_id, Socket(block=True))
        task = asyncio.create_task(app.state.registry.register(device_id, Socket()))
        await closing.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with app.state.database.transaction() as session:
            device = await DeviceRepository(session).get(device_id)
            assert device.status is DeviceStatus.OFFLINE

    client.portal.call(exercise)
