"""Administrative Gateway CLI."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import typer
import uvicorn

from gptlink.common.config import Settings
from gptlink.common.types import DeviceStatus, PermissionLevel
from gptlink.gateway.pairing import PairingService
from gptlink.persistence.database import Database
from gptlink.persistence.repositories import DevicePermissionError, DeviceRepository

app = typer.Typer(help="GPTLink Gateway administration")
gateway_app = typer.Typer(help="Run the Gateway")
pairing_app = typer.Typer(help="Manage pairing codes")
devices_app = typer.Typer(help="Manage devices")
jobs_app = typer.Typer(help="Inspect jobs")
audit_app = typer.Typer(help="Inspect audit events")
db_app = typer.Typer(help="Database administration")
app.add_typer(gateway_app, name="gateway")
app.add_typer(pairing_app, name="pairing")
app.add_typer(devices_app, name="devices")
app.add_typer(jobs_app, name="jobs")
app.add_typer(audit_app, name="audit")
app.add_typer(db_app, name="db")


def configured() -> Settings:
    return Settings()


async def _init_db() -> Database:
    db = Database(configured().database_url)
    await db.init()
    return db


@gateway_app.command("run")
def gateway_run() -> None:
    from gptlink.gateway.app import create_app

    value = configured()
    uvicorn.run(create_app(value), host=value.gateway_host, port=value.gateway_port, reload=False)


@db_app.command("init")
def db_init() -> None:
    async def run() -> None:
        db = await _init_db()
        await db.close()

    asyncio.run(run())
    typer.echo("database initialized")


@pairing_app.command("create")
def pairing_create() -> None:
    async def run() -> str:
        db = await _init_db()
        try:
            offer = await PairingService(db).create_code()
            return f"Pairing code: {offer.code}\nExpires at: {offer.expires_at.isoformat()}"
        finally:
            await db.close()

    typer.echo(asyncio.run(run()))


@devices_app.command("list")
def devices_list() -> None:
    async def run() -> list[str]:
        db = await _init_db()
        try:
            async with db.transaction() as session:
                devices = await DeviceRepository(session).list()
                return [f"{d.device_id} {d.platform} {d.status.value}" for d in devices]
        finally:
            await db.close()

    typer.echo("DEVICE PLATFORM STATUS")
    for row in asyncio.run(run()):
        typer.echo(row)


@devices_app.command("show")
def devices_show(device: UUID) -> None:
    async def run():
        db = await _init_db()
        try:
            async with db.transaction() as session:
                return await DeviceRepository(session).get(device)
        finally:
            await db.close()

    record = asyncio.run(run())
    if record is None:
        raise typer.BadParameter("unknown device")
    capabilities = ",".join(capability.value for capability in record.capabilities)
    typer.echo(
        f"device_id={record.device_id}\n"
        f"display_name={record.display_name}\n"
        f"platform={record.platform}\n"
        f"status={record.status.value}\n"
        f"permission_level={record.permission_level.value}\n"
        f"capabilities={capabilities}\n"
        f"last_seen={record.last_seen}"
    )


@devices_app.command("set-permission")
def devices_set_permission(device: UUID, level: PermissionLevel) -> None:
    async def run() -> tuple[PermissionLevel, PermissionLevel]:
        db = await _init_db()
        try:
            async with db.transaction() as session:
                changed = await DeviceRepository(session).set_permission(device, level)
                if changed is None:
                    raise ValueError("unknown device")
                return changed
        finally:
            await db.close()

    try:
        previous, current = asyncio.run(run())
    except (DevicePermissionError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"device {device} permission {previous.value} -> {current.value}")


@devices_app.command("revoke")
def devices_revoke(device: str) -> None:
    async def run() -> None:
        db = await _init_db()
        try:
            async with db.transaction() as session:
                record = await DeviceRepository(session).get(UUID(device))
                if record is None:
                    raise ValueError("unknown device")
                record.status = DeviceStatus.REVOKED
                record.revoked_at = datetime.now(UTC)
        finally:
            await db.close()

    try:
        asyncio.run(run())
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo("device revoked")


@jobs_app.command("list")
def jobs_list() -> None:
    typer.echo("JOB DEVICE STATUS")


@audit_app.command("list")
def audit_list() -> None:
    typer.echo("TIMESTAMP ACTION RESULT")
