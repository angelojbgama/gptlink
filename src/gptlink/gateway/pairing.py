"""Short-lived, attempt-limited pairing with atomic credential issuance."""

import asyncio
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import BaseModel, ConfigDict, Field

from gptlink.common.errors import GPTLinkError
from gptlink.common.types import Capability
from gptlink.gateway.auth import hash_device_token
from gptlink.persistence.database import Database
from gptlink.persistence.models import Device, PairingCode, utc_now
from gptlink.persistence.repositories import DeviceRepository, PairingRepository

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class PairingError(GPTLinkError):
    """Pairing code is invalid, exhausted, expired or already used."""


class DeviceMetadata(BaseModel):
    """Untrusted descriptive metadata; authority and identity are server-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=50)
    hostname: str = Field(min_length=1, max_length=253)
    agent_version: str = Field(min_length=1, max_length=50)
    protocol_version: int = Field(ge=1)
    capabilities: list[Capability] = Field(default_factory=list, max_length=100)


@dataclass(frozen=True)
class PairingOffer:
    code: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class DeviceCredential:
    device_id: UUID
    token: str = field(repr=False)


class PairingService:
    def __init__(
        self,
        database: Database,
        *,
        ttl: timedelta = timedelta(minutes=10),
        max_attempts: int = 5,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if ttl <= timedelta(0) or max_attempts < 1:
            raise ValueError("pairing TTL and attempt limit must be positive")
        self.database = database
        self.ttl = ttl
        self.max_attempts = max_attempts
        self.clock = clock
        self.hasher = PasswordHasher()

    async def create_code(self) -> PairingOffer:
        # Four public locator characters + eight secret characters (40 random bits).
        # Retry a locator collision; never replace an earlier offer.
        for _ in range(20):
            raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
            code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
            code_hash = await asyncio.to_thread(self.hasher.hash, code)
            now = self.clock()
            record = PairingCode(
                code_hash=code_hash,
                locator=raw[:4],
                created_at=now,
                expires_at=now + self.ttl,
                max_attempts=self.max_attempts,
            )
            async with self.database.transaction() as session:
                inserted = await PairingRepository(session).add_unique_locator(record)
            if inserted:
                return PairingOffer(code=code, expires_at=record.expires_at)
        raise PairingError("unable to allocate pairing code")

    async def redeem(self, code: str, metadata: DeviceMetadata) -> DeviceCredential:
        code = code.strip().upper()
        locator = code.split("-", 1)[0]
        if not re.fullmatch(r"[A-HJ-NP-Z2-9]{4}", locator):
            raise PairingError("invalid pairing code")
        credential = None
        async with self.database.transaction() as session:
            record = await PairingRepository(session).begin_attempt(locator, now=self.clock())
            valid = False
            if record is not None and re.fullmatch(
                r"[A-HJ-NP-Z2-9]{4}(?:-[A-HJ-NP-Z2-9]{4}){2}", code
            ):
                try:
                    valid = await asyncio.to_thread(self.hasher.verify, record.code_hash, code)
                except (VerificationError, InvalidHashError):
                    pass
            if valid:
                now = self.clock()
                # A code can expire while its memory-hard verification is running.
                if record.expires_at > now:
                    token = secrets.token_urlsafe(32)
                    device = Device(
                        device_id=uuid4(),
                        **metadata.model_dump(),
                        device_token_hash=hash_device_token(token),
                    )
                    await DeviceRepository(session).add(device)
                    record.used_at = now
                    record.redeemed_device_id = device.device_id
                    credential = DeviceCredential(device.device_id, token)
        # Raise only after commit, so incorrect guesses actually consume attempts.
        if credential is None:
            raise PairingError("invalid pairing code")
        return credential
