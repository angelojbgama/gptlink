"""Device credentials and durable admission of unique mutation requests."""

import hashlib
import hmac
import secrets
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from gptlink.common.errors import GPTLinkError
from gptlink.common.types import DeviceStatus
from gptlink.persistence.database import Database
from gptlink.persistence.models import Device, RequestClaim
from gptlink.persistence.repositories import DeviceRepository, RequestClaimRepository


class AuthenticationError(GPTLinkError):
    """Device credentials are invalid or revoked."""


class ReplayError(GPTLinkError):
    """A mutation request ID has already been claimed."""


def hash_device_token(token: str) -> str:
    """Salted SHA-256 is suitable for our independently random 256-bit tokens."""
    salt = secrets.token_bytes(16)
    digest = hashlib.sha256(salt + token.encode()).hexdigest()
    return f"sha256${salt.hex()}${digest}"


def _verify_device_token(token: str, stored_hash: str) -> bool:
    try:
        algorithm, salt_hex, digest_hex = stored_hash.split("$")
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if algorithm != "sha256" or len(salt) != 16 or len(expected) != 32:
            return False
    except ValueError:
        return False
    actual = hashlib.sha256(salt + token.encode()).digest()
    return hmac.compare_digest(actual, expected)


class DeviceAuthenticator:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def authenticate(self, device_id: UUID, token: str) -> Device:
        async with self.database.transaction() as session:
            device = await DeviceRepository(session).get(device_id)
            if (
                device is None
                or not _verify_device_token(token, device.device_token_hash)
                or device.status is DeviceStatus.REVOKED
                or device.revoked_at is not None
            ):
                raise AuthenticationError("invalid device credential")
            return device


class RequestReplayGuard:
    """Claim before dispatch; claims survive failures and are never released.

    This is at-most-once admission, not a transaction around a remote side effect.
    A crash after claim requires a new request ID for any operator-approved retry.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    async def claim(self, request_id: UUID) -> None:
        try:
            async with self.database.transaction() as session:
                await RequestClaimRepository(session).add(RequestClaim(request_id=request_id))
        except IntegrityError:
            async with self.database.transaction() as session:
                if await RequestClaimRepository(session).get(request_id) is not None:
                    raise ReplayError("request ID already claimed") from None
            raise
