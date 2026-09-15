"""Security boundaries exercised against real SQLite and real password hashes."""

import asyncio
import base64
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest


@pytest.fixture
async def database(tmp_path):
    from gptlink.persistence.database import Database

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'security.db'}")
    await db.init()
    yield db
    await db.close()


def metadata():
    from gptlink.common.types import Capability
    from gptlink.gateway.pairing import DeviceMetadata

    return DeviceMetadata(
        display_name="My workstation",
        platform="linux",
        hostname="workstation",
        agent_version="0.1.0",
        protocol_version=1,
        capabilities=[Capability.FILESYSTEM_READ],
    )


async def test_pairing_issues_random_credentials_and_only_persists_hashes(database, caplog):
    from sqlalchemy import select

    from gptlink.common.types import DeviceStatus, PermissionLevel
    from gptlink.gateway.auth import DeviceAuthenticator
    from gptlink.gateway.pairing import PairingService
    from gptlink.persistence.models import Device, PairingCode

    service = PairingService(database)
    before = datetime.now(UTC)
    offer = await service.create_code()
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}(?:-[A-HJ-NP-Z2-9]{4}){2}", offer.code)
    assert before + timedelta(minutes=10) <= offer.expires_at
    assert offer.code not in repr(offer)
    credential = await service.redeem(offer.code, metadata())
    assert len(base64.urlsafe_b64decode(credential.token + "=")) >= 32
    assert credential.token not in repr(credential)
    async with database.transaction() as session:
        pairing = (await session.scalars(select(PairingCode))).one()
        device = (await session.scalars(select(Device))).one()
        assert pairing.code_hash.startswith("$argon2id$")
        assert pairing.used_at is not None
        assert pairing.redeemed_device_id == device.device_id == credential.device_id
        assert device.device_token_hash != credential.token
        assert device.display_name == "My workstation"
        assert device.status is DeviceStatus.OFFLINE
        assert device.permission_level is PermissionLevel.READ_ONLY
        # Inspect every persisted field, catching plaintext in ancillary columns too.
        for record in (pairing, device):
            for column in record.__table__.columns:
                value = str(getattr(record, column.name))
                assert offer.code not in value
                assert credential.token not in value
    authenticated = await DeviceAuthenticator(database).authenticate(
        credential.device_id, credential.token
    )
    assert authenticated.device_id == credential.device_id
    second = await service.redeem((await service.create_code()).code, metadata())
    assert second.token != credential.token
    assert second.device_id != credential.device_id
    assert offer.code not in caplog.text
    assert credential.token not in caplog.text


async def test_wrong_code_attempts_commit_and_limit_blocks_correct_code(database):
    from sqlalchemy import select

    from gptlink.gateway.pairing import PairingError, PairingService
    from gptlink.persistence.models import Device, PairingCode

    service = PairingService(database, max_attempts=2)
    offer = await service.create_code()
    wrong = offer.code[:-1] + ("2" if offer.code[-1] != "2" else "3")
    for attempt in range(2):
        with pytest.raises(PairingError) as error:
            await service.redeem(wrong, metadata())
        assert wrong not in str(error.value)
        async with database.transaction() as session:
            assert (await session.scalars(select(PairingCode))).one().attempts == attempt + 1
    with pytest.raises(PairingError):
        await service.redeem(offer.code, metadata())
    async with database.transaction() as session:
        assert (await session.scalars(select(PairingCode))).one().attempts == 2
        assert list(await session.scalars(select(Device))) == []


async def test_last_attempt_can_succeed_and_reuse_fails(database):
    from gptlink.gateway.pairing import PairingError, PairingService

    service = PairingService(database, max_attempts=2)
    offer = await service.create_code()
    wrong = offer.code[:-1] + ("2" if offer.code[-1] != "2" else "3")
    with pytest.raises(PairingError):
        await service.redeem(wrong, metadata())
    await service.redeem(offer.code, metadata())
    with pytest.raises(PairingError):
        await service.redeem(offer.code, metadata())


async def test_expiry_boundary_and_unknown_or_malformed_codes_are_rejected(database):
    from gptlink.gateway.pairing import PairingError, PairingService

    now = datetime(2026, 9, 15, tzinfo=UTC)
    service = PairingService(database, clock=lambda: now)
    offer = await service.create_code()
    now = offer.expires_at
    for code in (offer.code, "2222-2222-2222", "bad", "", "秘密", "x" * 10000):
        with pytest.raises(PairingError):
            await service.redeem(code, metadata())


async def test_concurrent_redemption_emits_exactly_one_token(database):
    from sqlalchemy import select

    from gptlink.gateway.pairing import DeviceCredential, PairingError, PairingService
    from gptlink.persistence.models import Device

    offer = await PairingService(database).create_code()
    results = await asyncio.gather(
        *(PairingService(database).redeem(offer.code, metadata()) for _ in range(4)),
        return_exceptions=True,
    )
    assert sum(isinstance(item, DeviceCredential) for item in results) == 1
    assert sum(isinstance(item, PairingError) for item in results) == 3
    async with database.transaction() as session:
        assert len(list(await session.scalars(select(Device)))) == 1


async def test_concurrent_wrong_attempts_cannot_exceed_budget(database):
    from sqlalchemy import select

    from gptlink.gateway.pairing import PairingError, PairingService
    from gptlink.persistence.models import PairingCode

    service = PairingService(database, max_attempts=2)
    offer = await service.create_code()
    wrong = offer.code[:-1] + ("2" if offer.code[-1] != "2" else "3")
    results = await asyncio.gather(
        *(service.redeem(wrong, metadata()) for _ in range(4)), return_exceptions=True
    )
    assert all(isinstance(item, PairingError) for item in results)
    async with database.transaction() as session:
        assert (await session.scalars(select(PairingCode))).one().attempts == 2


@pytest.mark.parametrize("revocation", ["status", "timestamp"])
async def test_authentication_rejects_invalid_unknown_and_revoked_devices(database, revocation):
    from gptlink.common.types import DeviceStatus
    from gptlink.gateway.auth import AuthenticationError, DeviceAuthenticator
    from gptlink.gateway.pairing import PairingService
    from gptlink.persistence.repositories import DeviceRepository

    service = PairingService(database)
    credential = await service.redeem((await service.create_code()).code, metadata())
    auth = DeviceAuthenticator(database)
    for device_id, token in (
        (credential.device_id, "wrong"),
        (credential.device_id, ""),
        (credential.device_id, "秘密"),
        (uuid4(), credential.token),
    ):
        with pytest.raises(AuthenticationError) as error:
            await auth.authenticate(device_id, token)
        assert credential.token not in str(error.value)
    async with database.transaction() as session:
        record = await DeviceRepository(session).get(credential.device_id)
        if revocation == "status":
            record.status = DeviceStatus.REVOKED
        else:
            record.revoked_at = datetime.now(UTC)
    with pytest.raises(AuthenticationError):
        await auth.authenticate(credential.device_id, credential.token)


async def test_authentication_uses_constant_time_digest_comparison(database, monkeypatch):
    import hmac

    from gptlink.gateway import auth
    from gptlink.gateway.pairing import PairingService

    service = PairingService(database)
    credential = await service.redeem((await service.create_code()).code, metadata())
    original = hmac.compare_digest
    calls = []

    def comparison(left, right):
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(auth.hmac, "compare_digest", comparison)
    assert (
        await auth.DeviceAuthenticator(database).authenticate(
            credential.device_id, credential.token
        )
    ).device_id == credential.device_id
    with pytest.raises(auth.AuthenticationError):
        await auth.DeviceAuthenticator(database).authenticate(credential.device_id, "wrong")
    assert len(calls) == 2
    assert all(len(left) == len(right) == 32 for left, right in calls)


async def test_request_claim_persists_across_service_and_database_restart(database):
    from gptlink.gateway.auth import ReplayError, RequestReplayGuard

    request_id = uuid4()
    await RequestReplayGuard(database).claim(request_id)
    await database.close()
    await database.init()
    with pytest.raises(ReplayError):
        await RequestReplayGuard(database).claim(request_id)
    await RequestReplayGuard(database).claim(uuid4())


async def test_concurrent_request_claim_has_one_winner(database):
    from gptlink.gateway.auth import ReplayError, RequestReplayGuard

    request_id = uuid4()
    results = await asyncio.gather(
        *(RequestReplayGuard(database).claim(request_id) for _ in range(4)),
        return_exceptions=True,
    )
    assert results.count(None) == 1
    assert sum(isinstance(item, ReplayError) for item in results) == 3


@pytest.mark.parametrize("kwargs", [{"max_attempts": 0}, {"ttl": timedelta(0)}])
async def test_pairing_rejects_unbounded_or_expired_configuration(database, kwargs):
    from gptlink.gateway.pairing import PairingService

    with pytest.raises(ValueError):
        PairingService(database, **kwargs)


@pytest.mark.parametrize("invalid", ["attempts", "expiry", "locator"])
async def test_pairing_database_constraints_reject_invalid_state(database, invalid):
    from sqlalchemy.exc import IntegrityError

    from gptlink.persistence.models import PairingCode
    from gptlink.persistence.repositories import PairingRepository

    now = datetime.now(UTC)
    with pytest.raises(IntegrityError):
        async with database.transaction() as session:
            repo = PairingRepository(session)
            await repo.add(
                PairingCode(
                    code_hash="first",
                    locator="ABCD",
                    created_at=now,
                    expires_at=now if invalid == "expiry" else now + timedelta(minutes=1),
                    attempts=3 if invalid == "attempts" else 0,
                    max_attempts=2,
                )
            )
            if invalid == "locator":
                await repo.add(
                    PairingCode(
                        code_hash="second",
                        locator="ABCD",
                        created_at=now,
                        expires_at=now + timedelta(minutes=1),
                    )
                )


async def test_locator_collision_retries_without_overwriting_previous_code(database, monkeypatch):
    from gptlink.gateway import pairing

    symbols = iter("ABCD22223333ABCD44445555EFGH66667777")
    monkeypatch.setattr(pairing.secrets, "choice", lambda alphabet: next(symbols))
    service = pairing.PairingService(database)
    first = await service.create_code()
    second = await service.create_code()
    assert first.code == "ABCD-2222-3333"
    assert second.code == "EFGH-6666-7777"
    await service.redeem(first.code, metadata())
    await service.redeem(second.code, metadata())


async def test_corrupted_pairing_hash_fails_closed_and_commits_attempt(database):
    from sqlalchemy import select

    from gptlink.gateway.pairing import PairingError, PairingService
    from gptlink.persistence.models import PairingCode

    service = PairingService(database)
    offer = await service.create_code()
    async with database.transaction() as session:
        record = (await session.scalars(select(PairingCode))).one()
        record.code_hash = "corrupted"
    with pytest.raises(PairingError):
        await service.redeem(offer.code, metadata())
    async with database.transaction() as session:
        assert (await session.scalars(select(PairingCode))).one().attempts == 1


async def test_device_write_failure_rolls_back_redemption(database, monkeypatch):
    from sqlalchemy import select

    from gptlink.gateway.pairing import PairingService
    from gptlink.persistence.models import Device, PairingCode
    from gptlink.persistence.repositories import DeviceRepository

    service = PairingService(database)
    offer = await service.create_code()
    original = DeviceRepository.add

    async def failed_write(self, entity):
        await original(self, entity)
        raise RuntimeError("simulated database write failure")

    with monkeypatch.context() as patch:
        patch.setattr(DeviceRepository, "add", failed_write)
        with pytest.raises(RuntimeError, match="database write failure"):
            await service.redeem(offer.code, metadata())
    async with database.transaction() as session:
        assert list(await session.scalars(select(Device))) == []
        record = (await session.scalars(select(PairingCode))).one()
        assert (record.used_at, record.redeemed_device_id, record.attempts) == (None, None, 0)
    await service.redeem(offer.code, metadata())


async def test_malformed_secret_still_spends_attempt_on_known_locator(database):
    from gptlink.gateway.pairing import PairingError, PairingService

    service = PairingService(database, max_attempts=1)
    offer = await service.create_code()
    with pytest.raises(PairingError):
        await service.redeem(offer.code[:4] + "-bad", metadata())
    with pytest.raises(PairingError):
        await service.redeem(offer.code, metadata())
