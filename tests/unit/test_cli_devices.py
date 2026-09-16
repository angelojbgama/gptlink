"""Administrative device CLI validates identities and never reveals credentials."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from typer.testing import CliRunner

from gptlink.cli import main as cli
from gptlink.common.config import Settings
from gptlink.common.types import Capability, DeviceStatus, PermissionLevel
from gptlink.persistence.database import Database
from gptlink.persistence.models import Device
from gptlink.persistence.repositories import DeviceRepository

DEVICE_ID = UUID("d2f1fe05-1dd2-4ba4-820e-5a919a87f444")
TOKEN_HASH = "secret-device-token-hash"


def _output(result) -> str:
    return result.stdout + result.stderr


def _device(
    *,
    permission: PermissionLevel = PermissionLevel.READ_ONLY,
    status: DeviceStatus = DeviceStatus.ONLINE,
) -> Device:
    return Device(
        device_id=DEVICE_ID,
        display_name="DESKTOP-L44U8AL",
        platform="windows",
        hostname="DESKTOP-L44U8AL",
        agent_version="0.1.0",
        protocol_version=1,
        capabilities=[Capability.FILESYSTEM_READ, Capability.SHELL_POWERSHELL],
        status=status,
        permission_level=permission,
        device_token_hash=TOKEN_HASH,
        last_seen=datetime(2026, 9, 16, 16, 54, tzinfo=UTC),
        created_at=datetime(2026, 9, 16, 16, 50, tzinfo=UTC),
        revoked_at=datetime(2026, 9, 16, 16, 55, tzinfo=UTC)
        if status is DeviceStatus.REVOKED
        else None,
    )


def _configure_database(tmp_path, monkeypatch, record: Device | None = None) -> str:
    url = f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"

    async def initialize() -> None:
        database = Database(url)
        await database.init()
        if record is not None:
            async with database.transaction() as session:
                await DeviceRepository(session).add(record)
        await database.close()

    asyncio.run(initialize())
    monkeypatch.setattr(
        cli,
        "configured",
        lambda: Settings(_env_file=None, database_url=url),
    )
    return url


def _read_device(url: str) -> Device | None:
    async def read() -> Device | None:
        database = Database(url)
        try:
            async with database.transaction() as session:
                return await DeviceRepository(session).get(DEVICE_ID)
        finally:
            await database.close()

    return asyncio.run(read())


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (PermissionLevel.READ_ONLY, PermissionLevel.READ_WRITE),
        (PermissionLevel.READ_WRITE, PermissionLevel.READ_ONLY),
        (PermissionLevel.READ_WRITE, PermissionLevel.FULL_ACCESS),
    ],
)
def test_devices_set_permission_reports_transition(tmp_path, monkeypatch, before, after):
    url = _configure_database(tmp_path, monkeypatch, _device(permission=before))

    result = CliRunner().invoke(cli.app, ["devices", "set-permission", str(DEVICE_ID), after.value])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"device {DEVICE_ID} permission {before.value} -> {after.value}"
    record = _read_device(url)
    assert record is not None
    assert record.permission_level is after


def test_devices_set_permission_rejects_invalid_uuid_and_permission(tmp_path, monkeypatch):
    _configure_database(tmp_path, monkeypatch)
    runner = CliRunner()

    invalid_uuid = runner.invoke(cli.app, ["devices", "set-permission", "not-a-uuid", "READ_WRITE"])
    invalid_permission = runner.invoke(
        cli.app, ["devices", "set-permission", str(DEVICE_ID), "OWNER"]
    )

    assert invalid_uuid.exit_code != 0
    assert "not-a-uuid" in _output(invalid_uuid)
    assert invalid_permission.exit_code != 0
    assert "OWNER" in _output(invalid_permission)


def test_devices_set_permission_rejects_unknown_device(tmp_path, monkeypatch):
    _configure_database(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        cli.app, ["devices", "set-permission", str(DEVICE_ID), "READ_WRITE"]
    )

    assert result.exit_code != 0
    assert "unknown device" in _output(result)


def test_devices_set_permission_rejects_revoked_device(tmp_path, monkeypatch):
    url = _configure_database(
        tmp_path,
        monkeypatch,
        _device(permission=PermissionLevel.READ_ONLY, status=DeviceStatus.REVOKED),
    )

    result = CliRunner().invoke(
        cli.app, ["devices", "set-permission", str(DEVICE_ID), "READ_WRITE"]
    )

    assert result.exit_code != 0
    assert "revoked" in _output(result)
    record = _read_device(url)
    assert record is not None
    assert record.permission_level is PermissionLevel.READ_ONLY


def test_devices_show_includes_safe_operational_fields_without_token_hash(tmp_path, monkeypatch):
    _configure_database(tmp_path, monkeypatch, _device(permission=PermissionLevel.READ_WRITE))

    result = CliRunner().invoke(cli.app, ["devices", "show", str(DEVICE_ID)])

    assert result.exit_code == 0
    assert f"device_id={DEVICE_ID}" in result.stdout
    assert "display_name=DESKTOP-L44U8AL" in result.stdout
    assert "platform=windows" in result.stdout
    assert "status=ONLINE" in result.stdout
    assert "permission_level=READ_WRITE" in result.stdout
    assert "capabilities=filesystem.read,shell.powershell" in result.stdout
    assert "last_seen=2026-09-16 16:54:00+00:00" in result.stdout
    assert TOKEN_HASH not in result.stdout
    assert "token" not in result.stdout.casefold()
