"""Centralized audit persistence with recursive secret redaction."""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from gptlink.persistence.database import Database
from gptlink.persistence.models import AuditEvent
from gptlink.persistence.repositories import AuditRepository

REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "token",
    "device_token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "authorization",
    "cookie",
    "set_cookie",
}


def _normalized_key(key: object) -> str:
    return str(key).casefold().replace("-", "_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    if normalized in _SENSITIVE_KEYS:
        return True
    return any(
        marker in normalized
        for marker in ("token", "api_key", "apikey", "password", "passwd", "secret")
    )


def redact_secrets(value: Any) -> Any:
    """Return a deep copy with values under known secret keys removed."""
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(key) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    return value


class AuditService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def record(
        self,
        *,
        request_id: UUID | None,
        device_id: UUID | None,
        action: str,
        caller: str,
        result: str,
        duration: float,
        risk_level: str,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            request_id=request_id,
            device_id=device_id,
            action=action,
            caller=caller,
            result=result,
            duration=max(0.0, duration),
            risk_level=risk_level,
            details=redact_secrets(details or {}),
        )
        async with self.database.transaction() as session:
            return await AuditRepository(session).add(event)
