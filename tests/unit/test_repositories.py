"""Persistence contracts exercised against a real, temporary SQLite file."""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
async def database(tmp_path: Path):
    from gptlink.persistence.database import Database

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await db.init()
    await db.init()
    yield db
    await db.close()


def device():
    from gptlink.common.types import Capability, DeviceStatus, PermissionLevel
    from gptlink.persistence.models import Device

    return Device(
        device_id=uuid4(),
        display_name="Development",
        platform="linux",
        hostname="devbox",
        agent_version="0.1.0",
        protocol_version=1,
        capabilities=[Capability.FILESYSTEM_READ, Capability.COMMAND_START],
        status=DeviceStatus.ONLINE,
        permission_level=PermissionLevel.READ_WRITE,
        device_token_hash="salt:hash",
        last_seen=datetime(2026, 9, 15, 9, tzinfo=timezone(timedelta(hours=-3))),
        created_at=datetime(2026, 9, 15, 11, tzinfo=UTC),
        revoked_at=None,
    )


async def test_device_roundtrip_preserves_fields_enums_and_normalizes_utc(database):
    from gptlink.common.types import Capability, DeviceStatus, PermissionLevel
    from gptlink.persistence.repositories import DeviceRepository

    record = device()
    async with database.transaction() as session:
        await DeviceRepository(session).add(record)
    async with database.transaction() as session:
        repo = DeviceRepository(session)
        found = await repo.get(record.device_id)
        assert found is not None
        assert (found.display_name, found.platform, found.hostname) == (
            "Development",
            "linux",
            "devbox",
        )
        assert (found.agent_version, found.protocol_version) == ("0.1.0", 1)
        assert found.capabilities == [Capability.FILESYSTEM_READ, Capability.COMMAND_START]
        assert all(isinstance(value, Capability) for value in found.capabilities)
        assert found.status is DeviceStatus.ONLINE
        assert found.permission_level is PermissionLevel.READ_WRITE
        assert found.device_token_hash == "salt:hash"
        assert found.last_seen == datetime(2026, 9, 15, 12, tzinfo=UTC)
        assert found.last_seen.tzinfo is UTC
        assert found.created_at == datetime(2026, 9, 15, 11, tzinfo=UTC)
        assert found.revoked_at is None
        assert [item.device_id for item in await repo.list()] == [record.device_id]
        found.status = DeviceStatus.REVOKED
        found.revoked_at = datetime(2026, 9, 16, tzinfo=UTC)
    async with database.transaction() as session:
        found = await DeviceRepository(session).get(record.device_id)
        assert found.status is DeviceStatus.REVOKED
        assert found.revoked_at == datetime(2026, 9, 16, tzinfo=UTC)
        assert await DeviceRepository(session).get(uuid4()) is None


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("READ_ONLY", "READ_WRITE"),
        ("READ_WRITE", "READ_ONLY"),
        ("READ_WRITE", "FULL_ACCESS"),
    ],
)
async def test_set_device_permission_changes_only_permission(database, before, after):
    from gptlink.common.types import PermissionLevel
    from gptlink.persistence.repositories import DeviceRepository

    record = device()
    record.permission_level = PermissionLevel(before)
    original = {
        "capabilities": list(record.capabilities),
        "device_token_hash": record.device_token_hash,
        "status": record.status,
        "revoked_at": record.revoked_at,
    }
    async with database.transaction() as session:
        await DeviceRepository(session).add(record)
    async with database.transaction() as session:
        changed = await DeviceRepository(session).set_permission(
            record.device_id, PermissionLevel(after)
        )
        assert changed == (PermissionLevel(before), PermissionLevel(after))
    async with database.transaction() as session:
        found = await DeviceRepository(session).get(record.device_id)
        assert found is not None
        assert found.permission_level is PermissionLevel(after)
        assert found.capabilities == original["capabilities"]
        assert found.device_token_hash == original["device_token_hash"]
        assert found.status is original["status"]
        assert found.revoked_at is original["revoked_at"]


async def test_set_device_permission_handles_unknown_and_rejects_revoked(database):
    from gptlink.common.types import DeviceStatus, PermissionLevel
    from gptlink.persistence.repositories import DevicePermissionError, DeviceRepository

    async with database.transaction() as session:
        assert (
            await DeviceRepository(session).set_permission(uuid4(), PermissionLevel.READ_WRITE)
            is None
        )

    record = device()
    record.status = DeviceStatus.REVOKED
    record.revoked_at = datetime(2026, 9, 16, tzinfo=UTC)
    record.permission_level = PermissionLevel.READ_ONLY
    async with database.transaction() as session:
        await DeviceRepository(session).add(record)
    with pytest.raises(DevicePermissionError, match="revoked"):
        async with database.transaction() as session:
            await DeviceRepository(session).set_permission(
                record.device_id, PermissionLevel.READ_WRITE
            )
    async with database.transaction() as session:
        found = await DeviceRepository(session).get(record.device_id)
        assert found is not None
        assert found.permission_level is PermissionLevel.READ_ONLY


async def test_transaction_rollback_and_naive_timestamp_rejection(database):
    from sqlalchemy.exc import StatementError

    from gptlink.persistence.repositories import DeviceRepository

    record = device()
    with pytest.raises(RuntimeError, match="abort"):
        async with database.transaction() as session:
            await DeviceRepository(session).add(record)
            raise RuntimeError("abort")
    async with database.transaction() as session:
        assert await DeviceRepository(session).get(record.device_id) is None
    record = device()
    record.created_at = datetime(2026, 9, 15)
    with pytest.raises(StatementError, match="timezone-aware"):
        async with database.transaction() as session:
            await DeviceRepository(session).add(record)


@pytest.mark.parametrize("unavailable", ["expired", "used", "attempts", "available"])
async def test_pairing_fields_and_availability_boundary(database, unavailable):
    from gptlink.persistence.models import PairingCode
    from gptlink.persistence.repositories import PairingRepository

    now = datetime(2026, 9, 15, tzinfo=UTC)
    async with database.transaction() as session:
        await PairingRepository(session).add(
            PairingCode(
                code_hash="hash-only",
                expires_at=now if unavailable == "expired" else now + timedelta(minutes=5),
                attempts=3 if unavailable == "attempts" else 1,
                max_attempts=3,
                created_at=now - timedelta(minutes=1),
                used_at=now if unavailable == "used" else None,
            )
        )
    async with database.transaction() as session:
        repo = PairingRepository(session)
        found = await repo.get("hash-only")
        assert found.code_hash == "hash-only"
        assert found.max_attempts == 3
        assert found.attempts == (3 if unavailable == "attempts" else 1)
        assert found.created_at == now - timedelta(minutes=1)
        assert found.expires_at.tzinfo is UTC
        assert found.used_at == (now if unavailable == "used" else None)
        assert (await repo.get_available("hash-only", now=now) is not None) == (
            unavailable == "available"
        )
        assert await repo.get_available("missing", now=now) is None


async def test_job_fields_ordered_output_and_duplicate_sequences(database):
    from sqlalchemy.exc import IntegrityError

    from gptlink.common.types import JobStatus, ShellKind, StreamKind
    from gptlink.persistence.models import Job, JobOutput
    from gptlink.persistence.repositories import DeviceRepository, JobRepository

    now = datetime(2026, 9, 15, tzinfo=UTC)
    host = device()
    job_id, request_id = uuid4(), uuid4()
    async with database.transaction() as session:
        await DeviceRepository(session).add(host)
        repo = JobRepository(session)
        await repo.add(
            Job(
                job_id=job_id,
                request_id=request_id,
                device_id=host.device_id,
                command="printf hello",
                shell=ShellKind.BASH,
                cwd="/workspace",
                status=JobStatus.SUCCEEDED,
                created_at=now,
                started_at=now + timedelta(seconds=1),
                finished_at=now + timedelta(seconds=2),
                exit_code=0,
                timeout_seconds=30,
                output_truncated=True,
            )
        )
        for sequence, data, stream in [
            (2, "é", StreamKind.STDOUT),
            (0, "hi", StreamKind.STDOUT),
            (1, "!", StreamKind.STDERR),
        ]:
            await repo.add_output(
                JobOutput(
                    job_id=job_id,
                    sequence_number=sequence,
                    stream=stream,
                    timestamp=now,
                    data=data,
                    bytes_count=len(data.encode()),
                )
            )
    async with database.transaction() as session:
        repo = JobRepository(session)
        job = await repo.get(job_id)
        assert (job.request_id, job.device_id) == (request_id, host.device_id)
        assert (job.command, job.shell, job.cwd) == ("printf hello", ShellKind.BASH, "/workspace")
        assert job.shell is ShellKind.BASH
        assert job.status is JobStatus.SUCCEEDED
        assert job.created_at == now
        assert job.started_at == now + timedelta(seconds=1)
        assert job.finished_at == now + timedelta(seconds=2)
        assert (job.exit_code, job.timeout_seconds, job.output_truncated) == (0, 30, True)
        assert (await repo.get_by_request_id(request_id)).job_id == job_id
        chunks = await repo.get_output(job_id)
        assert [(c.sequence_number, c.stream, c.data, c.bytes_count) for c in chunks] == [
            (0, StreamKind.STDOUT, "hi", 2),
            (1, StreamKind.STDERR, "!", 1),
            (2, StreamKind.STDOUT, "é", 2),
        ]
        assert all(c.timestamp == now and isinstance(c.stream, StreamKind) for c in chunks)
        assert [
            c.sequence_number for c in await repo.get_output(job_id, after_sequence=0, limit=1)
        ] == [1]
        assert [j.job_id for j in await repo.list_for_device(host.device_id)] == [job_id]
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            await JobRepository(session).add_output(
                JobOutput(
                    job_id=job_id,
                    sequence_number=0,
                    stream=StreamKind.STDOUT,
                    timestamp=now,
                    data="duplicate",
                    bytes_count=9,
                )
            )


async def test_lease_exclusivity_expiry_release_and_history(database):
    from gptlink.persistence.models import Lease
    from gptlink.persistence.repositories import (
        DeviceRepository,
        LeaseConflictError,
        LeaseRepository,
    )

    now = datetime(2026, 9, 15, tzinfo=UTC)
    host = device()
    first_id, second_id = uuid4(), uuid4()
    async with database.transaction() as session:
        await DeviceRepository(session).add(host)
        await LeaseRepository(session).acquire(
            Lease(
                lease_id=first_id,
                device_id=host.device_id,
                owner="caller",
                created_at=now,
                expires_at=now + timedelta(seconds=10),
            ),
            now=now,
        )
    with pytest.raises(LeaseConflictError):
        async with database.transaction() as session:
            await LeaseRepository(session).acquire(
                Lease(
                    lease_id=uuid4(),
                    device_id=host.device_id,
                    owner="other",
                    created_at=now,
                    expires_at=now + timedelta(seconds=20),
                ),
                now=now,
            )
    async with database.transaction() as session:
        repo = LeaseRepository(session)
        lease = await repo.get_active(host.device_id, now=now)
        assert (
            lease.lease_id,
            lease.owner,
            lease.created_at,
            lease.expires_at,
            lease.released_at,
        ) == (first_id, "caller", now, now + timedelta(seconds=10), None)
        assert await repo.get_active(host.device_id, now=now + timedelta(seconds=10)) is None
        await repo.acquire(
            Lease(
                lease_id=second_id,
                device_id=host.device_id,
                owner="other",
                created_at=now + timedelta(seconds=10),
                expires_at=now + timedelta(seconds=20),
            ),
            now=now + timedelta(seconds=10),
        )
        assert (await repo.get(first_id)).expires_at == now + timedelta(seconds=10)
        assert (
            await repo.get_active(host.device_id, now=now + timedelta(seconds=10))
        ).lease_id == second_id
        assert await repo.release(second_id, now=now + timedelta(seconds=11))
        assert not await repo.release(second_id, now=now + timedelta(seconds=12))
        assert await repo.get_active(host.device_id, now=now + timedelta(seconds=11)) is None


async def test_concurrent_lease_acquisition_has_one_winner(database):
    from gptlink.persistence.models import Lease
    from gptlink.persistence.repositories import (
        DeviceRepository,
        LeaseConflictError,
        LeaseRepository,
    )

    host = device()
    now = datetime.now(UTC)
    async with database.transaction() as session:
        await DeviceRepository(session).add(host)

    async def acquire(owner):
        try:
            async with database.transaction() as session:
                await LeaseRepository(session).acquire(
                    Lease(
                        lease_id=uuid4(),
                        device_id=host.device_id,
                        owner=owner,
                        created_at=now,
                        expires_at=now + timedelta(minutes=1),
                    ),
                    now=now,
                )
            return True
        except LeaseConflictError:
            return False

    assert sorted(await asyncio.gather(acquire("a"), acquire("b"))) == [False, True]


async def test_approval_and_audit_roundtrip_and_expiry(database):
    from gptlink.common.types import ApprovalStatus
    from gptlink.persistence.models import Approval, AuditEvent
    from gptlink.persistence.repositories import (
        ApprovalRepository,
        AuditRepository,
        DeviceRepository,
    )

    host = device()
    now = datetime(2026, 9, 15, tzinfo=UTC)
    approval_id, request_id = uuid4(), uuid4()
    async with database.transaction() as session:
        await DeviceRepository(session).add(host)
        await ApprovalRepository(session).add(
            Approval(
                approval_id=approval_id,
                requested_action="command.start",
                device_id=host.device_id,
                risk_level="high",
                status=ApprovalStatus.PENDING,
                created_at=now,
                expires_at=now + timedelta(minutes=1),
            )
        )
        await AuditRepository(session).add(
            AuditEvent(
                timestamp=now,
                request_id=request_id,
                device_id=host.device_id,
                action="command.start",
                caller="operator",
                result="denied",
                duration=0.125,
                risk_level="high",
                details={"reason": "lease required"},
            )
        )
    async with database.transaction() as session:
        repo = ApprovalRepository(session)
        approval = await repo.get(approval_id)
        assert (approval.requested_action, approval.device_id, approval.risk_level) == (
            "command.start",
            host.device_id,
            "high",
        )
        assert approval.status is ApprovalStatus.PENDING
        assert (approval.created_at, approval.expires_at) == (now, now + timedelta(minutes=1))
        assert await repo.get_pending(approval_id, now=now) is not None
        assert await repo.get_pending(approval_id, now=now + timedelta(minutes=1)) is None
        approval.status = ApprovalStatus.REJECTED
        assert await repo.get_pending(approval_id, now=now) is None
        events = await AuditRepository(session).list_for_device(host.device_id)
        assert len(events) == 1
        event = events[0]
        assert (event.timestamp, event.request_id, event.device_id) == (
            now,
            request_id,
            host.device_id,
        )
        assert (event.action, event.caller, event.result, event.duration, event.risk_level) == (
            "command.start",
            "operator",
            "denied",
            0.125,
            "high",
        )
        assert event.details == {"reason": "lease required"}
        assert len(await AuditRepository(session).list_for_request(request_id)) == 1
