"""Transport-neutral authorization, lease, replay, and audit boundaries."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from gptlink.common.types import Capability, DeviceStatus, PermissionLevel
from gptlink.gateway.audit import AuditService, redact_secrets
from gptlink.gateway.auth import ReplayError
from gptlink.gateway.leases import LeaseConflictError, LeaseError, LeaseService
from gptlink.gateway.operations import AuthorizationError, GatewayOperations
from gptlink.persistence.database import Database
from gptlink.persistence.models import Device
from gptlink.persistence.repositories import AuditRepository, DeviceRepository


@pytest.fixture
async def database(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'authorization.db'}")
    await database.init()
    try:
        yield database
    finally:
        await database.close()


async def add_device(
    database: Database,
    *,
    status: DeviceStatus = DeviceStatus.ONLINE,
    permission: PermissionLevel = PermissionLevel.READ_WRITE,
    capabilities: list[Capability] | None = None,
):
    device = Device(
        device_id=uuid4(),
        display_name="Workstation",
        platform="linux",
        hostname="workstation",
        agent_version="0.1.0",
        protocol_version=1,
        capabilities=capabilities or [Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE],
        status=status,
        permission_level=permission,
        device_token_hash="unused-in-authorization-tests",
    )
    async with database.transaction() as session:
        await DeviceRepository(session).add(device)
    return device


@pytest.mark.asyncio
async def test_competing_callers_only_get_one_active_lease(database):
    device = await add_device(database)
    service = LeaseService(database)

    results = await asyncio.gather(
        service.acquire(device_id=device.device_id, owner="caller-a", ttl=60),
        service.acquire(device_id=device.device_id, owner="caller-b", ttl=60),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, LeaseConflictError) for result in results) == 1


@pytest.mark.asyncio
async def test_expired_and_released_leases_can_be_reacquired(database):
    device = await add_device(database)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = LeaseService(database, clock=lambda: now)
    expired = await service.acquire(device_id=device.device_id, owner="caller-a", ttl=1)

    now += timedelta(seconds=2)
    reacquired = await service.acquire(device_id=device.device_id, owner="caller-b", ttl=30)
    assert reacquired.lease_id != expired.lease_id

    await service.release(reacquired.lease_id, owner="caller-b")
    released = await service.acquire(device_id=device.device_id, owner="caller-c", ttl=30)
    assert released.owner == "caller-c"


@pytest.mark.asyncio
async def test_wrong_lease_or_owner_does_not_validate(database):
    device = await add_device(database)
    service = LeaseService(database)
    lease = await service.acquire(device_id=device.device_id, owner="caller-a", ttl=60)

    with pytest.raises(LeaseError):
        await service.validate(lease_id=uuid4(), device_id=device.device_id, owner="caller-a")
    with pytest.raises(LeaseError):
        await service.validate(
            lease_id=lease.lease_id, device_id=device.device_id, owner="caller-b"
        )


@pytest.mark.asyncio
async def test_reads_need_no_lease_and_can_run_concurrently(database):
    device = await add_device(database)
    operations = GatewayOperations(database)
    entered = 0
    both_entered = asyncio.Event()

    async def execute():
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        return "ok"

    results = await asyncio.gather(
        operations.read(
            device_id=device.device_id,
            caller="caller-a",
            capability=Capability.FILESYSTEM_READ,
            action="filesystem.read",
            execute=execute,
        ),
        operations.read(
            device_id=device.device_id,
            caller="caller-b",
            capability=Capability.FILESYSTEM_READ,
            action="filesystem.read",
            execute=execute,
        ),
    )
    assert results == ["ok", "ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "permission", "capabilities"),
    [
        (DeviceStatus.ONLINE, PermissionLevel.READ_ONLY, [Capability.FILESYSTEM_WRITE]),
        (DeviceStatus.ONLINE, PermissionLevel.READ_WRITE, [Capability.FILESYSTEM_READ]),
        (DeviceStatus.REVOKED, PermissionLevel.FULL_ACCESS, [Capability.FILESYSTEM_WRITE]),
    ],
)
async def test_mutation_denials_happen_before_dispatch(database, status, permission, capabilities):
    device = await add_device(
        database, status=status, permission=permission, capabilities=capabilities
    )
    operations = GatewayOperations(database)
    dispatched = False

    async def execute():
        nonlocal dispatched
        dispatched = True

    with pytest.raises(AuthorizationError):
        await operations.mutate(
            device_id=device.device_id,
            caller="caller-a",
            request_id=uuid4(),
            lease_id=uuid4(),
            capability=Capability.FILESYSTEM_WRITE,
            action="filesystem.write",
            execute=execute,
        )
    assert dispatched is False


@pytest.mark.asyncio
async def test_wrong_lease_owner_denies_mutation(database):
    device = await add_device(database)
    leases = LeaseService(database)
    lease = await leases.acquire(device_id=device.device_id, owner="caller-a", ttl=60)
    operations = GatewayOperations(database, lease_service=leases)

    with pytest.raises(LeaseError):
        await operations.mutate(
            device_id=device.device_id,
            caller="caller-b",
            request_id=uuid4(),
            lease_id=lease.lease_id,
            capability=Capability.FILESYSTEM_WRITE,
            action="filesystem.write",
            execute=lambda: asyncio.sleep(0),
        )


@pytest.mark.asyncio
async def test_duplicate_request_id_never_repeats_side_effect(database):
    device = await add_device(database)
    leases = LeaseService(database)
    lease = await leases.acquire(device_id=device.device_id, owner="caller-a", ttl=60)
    operations = GatewayOperations(database, lease_service=leases)
    request_id = uuid4()
    calls = 0

    async def execute():
        nonlocal calls
        calls += 1
        return calls

    assert (
        await operations.mutate(
            device_id=device.device_id,
            caller="caller-a",
            request_id=request_id,
            lease_id=lease.lease_id,
            capability=Capability.FILESYSTEM_WRITE,
            action="filesystem.write",
            execute=execute,
        )
        == 1
    )
    with pytest.raises(ReplayError):
        await operations.mutate(
            device_id=device.device_id,
            caller="caller-a",
            request_id=request_id,
            lease_id=lease.lease_id,
            capability=Capability.FILESYSTEM_WRITE,
            action="filesystem.write",
            execute=execute,
        )
    assert calls == 1


@pytest.mark.asyncio
async def test_success_and_error_are_audited_with_nonnegative_duration(database):
    device = await add_device(database)
    operations = GatewayOperations(database)
    await operations.read(
        device_id=device.device_id,
        caller="caller-a",
        capability=Capability.FILESYSTEM_READ,
        action="filesystem.read",
        execute=lambda: asyncio.sleep(0, result="content"),
        details={"password": "do-not-store"},
    )
    with pytest.raises(RuntimeError, match="remote failed"):
        await operations.read(
            device_id=device.device_id,
            caller="caller-a",
            capability=Capability.FILESYSTEM_READ,
            action="filesystem.read",
            execute=lambda: _raise(RuntimeError("remote failed: secret-value")),
        )

    async with database.transaction() as session:
        events = await AuditRepository(session).list_for_device(device.device_id)
    assert [event.result for event in events] == ["SUCCESS", "ERROR"]
    assert all(event.duration >= 0 for event in events)
    assert events[0].details == {"password": "[REDACTED]"}
    assert events[1].details == {"error_type": "RuntimeError"}
    assert "secret-value" not in repr(events[1].details)


async def _raise(error: Exception):
    raise error


def test_redaction_is_recursive_and_case_insensitive():
    source = {
        "Authorization": "Bearer actual-secret",
        "device_token_hash": "hashed-secret",
        "nested": [
            {"api-key": "one", "safe": "visible"},
            ({"SET_COOKIE": "two"}, {"passwd": "three"}),
        ],
    }
    redacted = redact_secrets(source)
    assert redacted == {
        "Authorization": "[REDACTED]",
        "device_token_hash": "[REDACTED]",
        "nested": [
            {"api-key": "[REDACTED]", "safe": "visible"},
            ({"SET_COOKIE": "[REDACTED]"}, {"passwd": "[REDACTED]"}),
        ],
    }


@pytest.mark.asyncio
async def test_audit_service_redacts_before_persistence(database):
    request_id = uuid4()
    await AuditService(database).record(
        request_id=request_id,
        device_id=None,
        action="authorization.denied",
        caller="caller-a",
        result="DENIED",
        duration=0,
        risk_level="LOW",
        details={"client_secret": "hidden", "safe": "visible"},
    )
    async with database.transaction() as session:
        events = await AuditRepository(session).list_for_request(request_id)
    assert events[0].details == {"client_secret": "[REDACTED]", "safe": "visible"}
