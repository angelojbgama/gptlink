"""All persistence queries live here; callers share one transaction session.

Repositories flush without committing. Loaded entities can be modified within
the transaction; SQLAlchemy persists changes when Database.transaction exits.
JSON fields should be replaced as a whole to persist edits.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gptlink.common.types import ApprovalStatus, DeviceStatus, PermissionLevel
from gptlink.persistence.models import (
    Approval,
    AuditEvent,
    Base,
    Device,
    Job,
    JobOutput,
    Lease,
    PairingCode,
    RequestClaim,
)


class Repository[Entity: Base, Key]:
    model: type[Entity]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, entity: Entity) -> Entity:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def get(self, key: Key) -> Entity | None:
        return await self.session.get(self.model, key)


class DevicePermissionError(Exception):
    """A device state forbids an administrative permission change."""


class DeviceRepository(Repository[Device, UUID]):
    model = Device

    async def set_permission(
        self, device_id: UUID, permission: PermissionLevel
    ) -> tuple[PermissionLevel, PermissionLevel] | None:
        device = await self.get(device_id)
        if device is None:
            return None
        if device.status is DeviceStatus.REVOKED or device.revoked_at is not None:
            raise DevicePermissionError("revoked device permission cannot be changed")
        previous = device.permission_level
        device.permission_level = permission
        await self.session.flush()
        return previous, permission

    async def mark_online(self, device_id: UUID, *, now: datetime) -> bool:
        """Presence never overrides concurrent revocation."""
        result = await self.session.scalar(
            update(Device)
            .where(
                Device.device_id == device_id,
                Device.status != DeviceStatus.REVOKED,
                Device.revoked_at.is_(None),
            )
            .values(status=DeviceStatus.ONLINE, last_seen=now)
            .returning(Device.device_id)
        )
        return result is not None

    async def mark_offline(self, device_id: UUID) -> None:
        await self.session.execute(
            update(Device)
            .where(
                Device.device_id == device_id,
                Device.status != DeviceStatus.REVOKED,
                Device.revoked_at.is_(None),
            )
            .values(status=DeviceStatus.OFFLINE)
        )

    async def touch(self, device_id: UUID, *, now: datetime) -> bool:
        """Refresh presence without erasing BUSY/DEGRADED operational state."""
        result = await self.session.scalar(
            update(Device)
            .where(
                Device.device_id == device_id,
                Device.status != DeviceStatus.REVOKED,
                Device.revoked_at.is_(None),
            )
            .values(
                last_seen=now,
                status=case(
                    (Device.status == DeviceStatus.OFFLINE, DeviceStatus.ONLINE),
                    else_=Device.status,
                ),
            )
            .returning(Device.device_id)
        )
        return result is not None

    async def list(self) -> list[Device]:
        return list(
            await self.session.scalars(select(Device).order_by(Device.created_at, Device.device_id))
        )


class PairingRepository(Repository[PairingCode, str]):
    model = PairingCode

    async def get_available(self, code_hash: str, *, now: datetime) -> PairingCode | None:
        """Non-reserving snapshot; redemption must use begin_attempt in a transaction."""
        return await self.session.scalar(
            select(PairingCode).where(
                PairingCode.code_hash == code_hash,
                PairingCode.expires_at > now,
                PairingCode.used_at.is_(None),
                PairingCode.attempts < PairingCode.max_attempts,
            )
        )

    async def add_unique_locator(self, code: PairingCode) -> bool:
        try:
            async with self.session.begin_nested():
                await self.add(code)
            return True
        except IntegrityError:
            if (
                await self.session.scalar(
                    select(PairingCode.code_hash).where(PairingCode.locator == code.locator)
                )
                is not None
            ):
                return False
            raise

    async def begin_attempt(self, locator: str, *, now: datetime) -> PairingCode | None:
        """Atomically spend an attempt and hold the writer lock through redemption.

        A failed verification must commit this transaction before reporting failure.
        This first-statement write serializes concurrent SQLite redemption attempts.
        """
        return await self.session.scalar(
            update(PairingCode)
            .where(
                PairingCode.locator == locator,
                PairingCode.expires_at > now,
                PairingCode.used_at.is_(None),
                PairingCode.attempts < PairingCode.max_attempts,
            )
            .values(attempts=PairingCode.attempts + 1)
            .returning(PairingCode)
        )


class RequestClaimRepository(Repository[RequestClaim, UUID]):
    model = RequestClaim


class JobRepository(Repository[Job, UUID]):
    model = Job

    async def get_by_request_id(self, request_id: UUID) -> Job | None:
        return await self.session.scalar(select(Job).where(Job.request_id == request_id))

    async def list_for_device(self, device_id: UUID) -> list[Job]:
        return list(
            await self.session.scalars(
                select(Job).where(Job.device_id == device_id).order_by(Job.created_at, Job.job_id)
            )
        )

    async def add_output(self, output: JobOutput) -> JobOutput:
        self.session.add(output)
        await self.session.flush()
        return output

    async def get_output(
        self, job_id: UUID, *, after_sequence: int = -1, limit: int = 1000
    ) -> list[JobOutput]:
        if limit < 1:
            raise ValueError("limit must be positive")
        return list(
            await self.session.scalars(
                select(JobOutput)
                .where(JobOutput.job_id == job_id, JobOutput.sequence_number > after_sequence)
                .order_by(JobOutput.sequence_number)
                .limit(limit)
            )
        )


class LeaseConflictError(Exception):
    """Another unreleased lease still owns this device."""


class LeaseRepository(Repository[Lease, UUID]):
    model = Lease

    async def get_active(self, device_id: UUID, *, now: datetime) -> Lease | None:
        return await self.session.scalar(
            select(Lease).where(
                Lease.device_id == device_id, Lease.released_at.is_(None), Lease.expires_at > now
            )
        )

    async def acquire(self, lease: Lease, *, now: datetime) -> Lease:
        """Retire expired ownership and claim the unique slot in one transaction.

        The first statement is a write, so concurrent SQLite writers serialize
        before checking the unique index. The index also protects other callers.
        """
        if lease.expires_at <= now or lease.released_at is not None:
            raise ValueError("lease must be unexpired and unreleased")
        await self.session.execute(
            update(Lease)
            .where(
                Lease.device_id == lease.device_id,
                Lease.released_at.is_(None),
                Lease.expires_at <= now,
            )
            .values(released_at=Lease.expires_at)
        )
        # Check after obtaining the writer lock to report a domain conflict,
        # while leaving unrelated integrity failures visible to the caller.
        if await self.get_active(lease.device_id, now=now) is not None:
            raise LeaseConflictError("device already has an active lease")
        try:
            async with self.session.begin_nested():
                return await self.add(lease)
        except IntegrityError:
            if await self.get_active(lease.device_id, now=now) is not None:
                raise LeaseConflictError("device already has an active lease") from None
            raise

    async def release(self, lease_id: UUID, *, now: datetime) -> bool:
        released = await self.session.scalar(
            update(Lease)
            .where(Lease.lease_id == lease_id, Lease.released_at.is_(None))
            .values(released_at=now)
            .returning(Lease.lease_id)
        )
        return released is not None


class ApprovalRepository(Repository[Approval, UUID]):
    model = Approval

    async def get_pending(self, approval_id: UUID, *, now: datetime) -> Approval | None:
        return await self.session.scalar(
            select(Approval).where(
                Approval.approval_id == approval_id,
                Approval.status == ApprovalStatus.PENDING,
                Approval.expires_at > now,
            )
        )


class AuditRepository(Repository[AuditEvent, int]):
    model = AuditEvent

    async def list_for_device(self, device_id: UUID) -> list[AuditEvent]:
        return list(
            await self.session.scalars(
                select(AuditEvent)
                .where(AuditEvent.device_id == device_id)
                .order_by(AuditEvent.timestamp, AuditEvent.audit_id)
            )
        )

    async def list_for_request(self, request_id: UUID) -> list[AuditEvent]:
        return list(
            await self.session.scalars(
                select(AuditEvent)
                .where(AuditEvent.request_id == request_id)
                .order_by(AuditEvent.timestamp, AuditEvent.audit_id)
            )
        )
