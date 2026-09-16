"""Transport-neutral authorization and orchestration for remote operations."""

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, TypeVar
from uuid import UUID

from gptlink.common.errors import GPTLinkError
from gptlink.common.types import Capability, DeviceStatus, PermissionLevel
from gptlink.gateway.audit import AuditService
from gptlink.gateway.auth import ReplayError, RequestReplayGuard
from gptlink.gateway.leases import LeaseError, LeaseService
from gptlink.persistence.database import Database
from gptlink.persistence.models import Device
from gptlink.persistence.repositories import DeviceRepository

Result = TypeVar("Result")


class AuthorizationError(GPTLinkError):
    """A caller may not perform the requested operation on the device."""


class GatewayOperations:
    """Apply shared policy before invoking a transport-specific callback."""

    def __init__(
        self,
        database: Database,
        *,
        lease_service: LeaseService | None = None,
        audit_service: AuditService | None = None,
        replay_guard: RequestReplayGuard | None = None,
    ) -> None:
        self.database = database
        self.leases = lease_service or LeaseService(database)
        self.audit = audit_service or AuditService(database)
        self.replay = replay_guard or RequestReplayGuard(database)

    async def read(
        self,
        *,
        device_id: UUID,
        caller: str,
        capability: Capability,
        action: str,
        execute: Callable[[], Awaitable[Result]],
        request_id: UUID | None = None,
        risk_level: str = "LOW",
        details: dict[str, Any] | None = None,
    ) -> Result:
        started = perf_counter()
        try:
            await self._authorize(device_id, capability, mutation=False)
            result = await execute()
        except Exception as error:
            await self._record_error(
                started=started,
                request_id=request_id,
                device_id=device_id,
                action=action,
                caller=caller,
                risk_level=risk_level,
                details=details,
                error=error,
            )
            raise
        await self.audit.record(
            request_id=request_id,
            device_id=device_id,
            action=action,
            caller=caller,
            result="SUCCESS",
            duration=perf_counter() - started,
            risk_level=risk_level,
            details=details,
        )
        return result

    async def mutate(
        self,
        *,
        device_id: UUID,
        caller: str,
        request_id: UUID,
        lease_id: UUID,
        capability: Capability,
        action: str,
        execute: Callable[[], Awaitable[Result]],
        risk_level: str = "MEDIUM",
        details: dict[str, Any] | None = None,
    ) -> Result:
        started = perf_counter()
        try:
            if not isinstance(request_id, UUID):
                raise ValueError("mutation request_id must be a UUID")
            await self._authorize(device_id, capability, mutation=True)
            await self.leases.validate(lease_id=lease_id, device_id=device_id, owner=caller)
            await self.replay.claim(request_id)
            result = await execute()
        except Exception as error:
            await self._record_error(
                started=started,
                request_id=request_id if isinstance(request_id, UUID) else None,
                device_id=device_id,
                action=action,
                caller=caller,
                risk_level=risk_level,
                details=details,
                error=error,
            )
            raise
        await self.audit.record(
            request_id=request_id,
            device_id=device_id,
            action=action,
            caller=caller,
            result="SUCCESS",
            duration=perf_counter() - started,
            risk_level=risk_level,
            details=details,
        )
        return result

    async def _authorize(
        self, device_id: UUID, capability: Capability, *, mutation: bool
    ) -> Device:
        async with self.database.transaction() as session:
            device = await DeviceRepository(session).get(device_id)
            if device is None:
                raise AuthorizationError("device is not authorized")
            if device.status is DeviceStatus.REVOKED or device.revoked_at is not None:
                raise AuthorizationError("device is revoked")
            if capability not in device.capabilities:
                raise AuthorizationError("required capability is unavailable")
            if mutation and device.permission_level not in {
                PermissionLevel.READ_WRITE,
                PermissionLevel.FULL_ACCESS,
            }:
                raise AuthorizationError("device permission level forbids mutation")
            return device

    async def _record_error(
        self,
        *,
        started: float,
        request_id: UUID | None,
        device_id: UUID,
        action: str,
        caller: str,
        risk_level: str,
        details: dict[str, Any] | None,
        error: Exception,
    ) -> None:
        result = (
            "DENIED"
            if isinstance(error, (AuthorizationError, LeaseError, ReplayError))
            else "ERROR"
        )
        safe_details = dict(details or {})
        safe_details["error_type"] = type(error).__name__
        await self.audit.record(
            request_id=request_id,
            device_id=device_id,
            action=action,
            caller=caller,
            result=result,
            duration=perf_counter() - started,
            risk_level=risk_level,
            details=safe_details,
        )
