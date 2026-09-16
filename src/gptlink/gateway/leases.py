"""Exclusive, expiring mutation leases for Gateway callers."""

from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

from gptlink.common.errors import GPTLinkError
from gptlink.persistence.database import Database
from gptlink.persistence.models import Lease, utc_now
from gptlink.persistence.repositories import (
    LeaseConflictError as RepositoryLeaseConflictError,
)
from gptlink.persistence.repositories import LeaseRepository


class LeaseError(GPTLinkError):
    """A lease is missing, expired, released, or owned by another caller."""


class LeaseConflictError(LeaseError):
    """A device already has an active mutation lease."""


class LeaseService:
    def __init__(self, database: Database, *, clock: Callable = utc_now) -> None:
        self.database = database
        self.clock = clock

    async def acquire(self, *, device_id: UUID, owner: str, ttl: float | timedelta) -> Lease:
        if not owner:
            raise ValueError("lease owner must not be empty")
        seconds = ttl.total_seconds() if isinstance(ttl, timedelta) else float(ttl)
        if seconds <= 0:
            raise ValueError("lease TTL must be positive")
        now = self.clock()
        lease = Lease(
            device_id=device_id,
            owner=owner,
            created_at=now,
            expires_at=now + timedelta(seconds=seconds),
        )
        try:
            async with self.database.transaction() as session:
                return await LeaseRepository(session).acquire(lease, now=now)
        except RepositoryLeaseConflictError:
            raise LeaseConflictError("device already has an active lease") from None

    async def validate(self, *, lease_id: UUID, device_id: UUID, owner: str) -> Lease:
        now = self.clock()
        async with self.database.transaction() as session:
            lease = await LeaseRepository(session).get(lease_id)
            if (
                lease is None
                or lease.device_id != device_id
                or lease.owner != owner
                or lease.released_at is not None
                or lease.expires_at <= now
            ):
                raise LeaseError("invalid mutation lease")
            return lease

    async def release(self, lease_id: UUID, *, owner: str) -> None:
        now = self.clock()
        async with self.database.transaction() as session:
            repository = LeaseRepository(session)
            lease = await repository.get(lease_id)
            if lease is None or lease.owner != owner or lease.released_at is not None:
                raise LeaseError("invalid mutation lease")
            if not await repository.release(lease_id, now=now):
                raise LeaseError("invalid mutation lease")
