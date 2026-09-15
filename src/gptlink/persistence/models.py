"""Portable persisted entities; timestamps cross this boundary as aware UTC."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, ForeignKey, Index, Uuid
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from gptlink.common.types import (
    ApprovalStatus,
    Capability,
    DeviceStatus,
    JobStatus,
    PermissionLevel,
    ShellKind,
    StreamKind,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite stores naive UTC internally, never at the public boundary."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class Capabilities(TypeDecorator[list[Capability]]):
    """Retain central capability enum types through JSON storage."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: list[Capability] | None, dialect: Dialect):
        return [Capability(item).value for item in value] if value is not None else None

    def process_result_value(self, value, dialect: Dialect) -> list[Capability] | None:
        return [Capability(item) for item in value] if value is not None else None


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime(), UUID: Uuid()}


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    display_name: Mapped[str]
    platform: Mapped[str]
    hostname: Mapped[str]
    agent_version: Mapped[str]
    protocol_version: Mapped[int]
    capabilities: Mapped[list[Capability]] = mapped_column(Capabilities(), default=list)
    status: Mapped[DeviceStatus] = mapped_column(Enum(DeviceStatus), default=DeviceStatus.OFFLINE)
    permission_level: Mapped[PermissionLevel] = mapped_column(
        Enum(PermissionLevel), default=PermissionLevel.READ_ONLY
    )
    device_token_hash: Mapped[str]
    last_seen: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    revoked_at: Mapped[datetime | None]


class PairingCode(Base):
    __tablename__ = "pairing_codes"
    __table_args__ = (
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts AND max_attempts > 0"),
        CheckConstraint("expires_at > created_at"),
    )

    code_hash: Mapped[str] = mapped_column(primary_key=True)
    # Public lookup component only; older/internal hash-only records have no locator.
    locator: Mapped[str | None] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=5)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    used_at: Mapped[datetime | None]
    redeemed_device_id: Mapped[UUID | None] = mapped_column(ForeignKey("devices.device_id"))


class RequestClaim(Base):
    """Durable mutation admission, independent of jobs and repeatable audit events."""

    __tablename__ = "request_claims"

    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    claimed_at: Mapped[datetime] = mapped_column(default=utc_now)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (CheckConstraint("timeout_seconds > 0"),)

    job_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(unique=True)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.device_id"), index=True)
    command: Mapped[str]
    shell: Mapped[ShellKind] = mapped_column(Enum(ShellKind))
    cwd: Mapped[str]
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    exit_code: Mapped[int | None]
    timeout_seconds: Mapped[float]
    output_truncated: Mapped[bool] = mapped_column(default=False)


class JobOutput(Base):
    __tablename__ = "job_outputs"
    __table_args__ = (CheckConstraint("sequence_number >= 0 AND bytes_count >= 0"),)

    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.job_id"), primary_key=True)
    sequence_number: Mapped[int] = mapped_column(primary_key=True)
    stream: Mapped[StreamKind] = mapped_column(Enum(StreamKind))
    timestamp: Mapped[datetime] = mapped_column(default=utc_now)
    data: Mapped[str]
    bytes_count: Mapped[int]


class Lease(Base):
    __tablename__ = "leases"
    __table_args__ = (CheckConstraint("expires_at > created_at"),)

    lease_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.device_id"), index=True)
    owner: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    expires_at: Mapped[datetime]
    released_at: Mapped[datetime | None]


# Expired rows are retired by acquire before inserting the next lease.
Index(
    "uq_unreleased_lease_device",
    Lease.device_id,
    unique=True,
    sqlite_where=Lease.released_at.is_(None),
    postgresql_where=Lease.released_at.is_(None),
)


class Approval(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    requested_action: Mapped[str]
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.device_id"), index=True)
    risk_level: Mapped[str]
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    expires_at: Mapped[datetime]


class AuditEvent(Base):
    __tablename__ = "audit_events"

    audit_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(default=utc_now)
    request_id: Mapped[UUID | None] = mapped_column(index=True)
    # Events may refer to unknown/rejected device identities; keep them auditable.
    device_id: Mapped[UUID | None] = mapped_column(index=True)
    action: Mapped[str]
    caller: Mapped[str]
    result: Mapped[str]
    duration: Mapped[float]
    risk_level: Mapped[str]
    details: Mapped[dict] = mapped_column(JSON, default=dict)
