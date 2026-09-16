"""Administrative Gateway CLI."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import typer
import uvicorn

from gptlink.common.config import Settings
from gptlink.common.types import DeviceStatus, PermissionLevel
from gptlink.gateway.pairing import PairingService
from gptlink.persistence.database import Database
from gptlink.persistence.repositories import DevicePermissionError, DeviceRepository

app = typer.Typer(help="GPTLink remote administration and local workspace bridge")
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


@app.command("local")
def local_command(
    chat: bool = typer.Option(False, "--chat", help="Connect this workspace to a chat backend."),
    conversation: str | None = typer.Option(
        None, "--conversation", metavar="UUID", help="Attach an existing conversation."
    ),
    read_only: bool = typer.Option(
        False, "--read-only", help="Forbid writes and command starts (the default)."
    ),
    write: bool = typer.Option(
        False, "--write", help="Allow approved writes and command starts for this process."
    ),
    chat_backend: str = typer.Option(
        "web2api", "--chat-backend", help="Chat backend (currently: web2api)."
    ),
    chat_url: str | None = typer.Option(
        None, "--chat-url", help="Loopback ChatGPT-Web2API base URL."
    ),
) -> None:
    """Run GPTLink directly against the current directory; use --chat for the bridge."""
    if not chat:
        raise typer.BadParameter("local mode currently requires --chat")
    if read_only and write:
        raise typer.BadParameter("choose either --read-only or --write")
    if chat_backend != "web2api":
        raise typer.BadParameter("only the web2api chat backend is currently supported")

    from gptlink.chat.models import Conversation
    from gptlink.chat.orchestrator import ChatOrchestrator
    from gptlink.chat.prompt import build_session_prompt, collect_workspace_context
    from gptlink.chat.web2api import (
        ChatBackendCapabilityError,
        ChatBackendError,
        Web2APIChatBackend,
    )
    from gptlink.local.approvals import SessionApprover, interactive_prompt
    from gptlink.local.runtime import build_local_runtime
    from gptlink.local.workspace import LocalWorkspace

    try:
        workspace = LocalWorkspace.from_cwd(Path.cwd())
        settings = configured()
        endpoint = chat_url or settings.web2api_url
        permission = PermissionLevel.READ_WRITE if write else PermissionLevel.READ_ONLY
        backend = Web2APIChatBackend(endpoint)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    async def run() -> None:
        async with backend:
            typer.echo("GPTLink Local\n")
            typer.echo(f"Workspace:\n{workspace.root}\n")
            typer.echo("Chat backend:\nChatGPT-Web2API\n")
            typer.echo(f"Endpoint:\n{backend.base_url}\n")
            if not await backend.health():
                typer.echo(
                    "Status:\nUNAVAILABLE\n\nChat backend unavailable.\n\n"
                    "Start ChatGPT-Web2API first:\n\nchatgpt-web2api --port 8081",
                    err=True,
                )
                raise typer.Exit(1)
            typer.echo("Status:\nCONNECTED\n")

            selected: Conversation
            if conversation is not None:
                try:
                    parsed = UUID(conversation)
                except ValueError as error:
                    raise typer.BadParameter("conversation must be a UUID") from error
                conversation_id = str(parsed)
                try:
                    history = await backend.get_conversation(conversation_id)
                except ChatBackendCapabilityError:
                    typer.echo(
                        "Note: this Web2API REST version cannot validate conversation history; "
                        "GPTLink will enforce that every response keeps the supplied ID.\n"
                    )
                    selected = Conversation(conversation_id, conversation_id)
                else:
                    selected = history.conversation
            else:
                try:
                    conversations = await backend.list_conversations()
                except ChatBackendCapabilityError as error:
                    typer.echo(f"Conversation listing unavailable: {error}\n", err=True)
                    entered = typer.prompt("Conversation UUID")
                    try:
                        selected_id = str(UUID(entered))
                    except ValueError as validation_error:
                        raise typer.BadParameter(
                            "conversation must be a UUID"
                        ) from validation_error
                    selected = Conversation(selected_id, selected_id)
                else:
                    if not conversations:
                        typer.echo("No recent conversations are available.", err=True)
                        raise typer.Exit(2)
                    typer.echo("Recent conversations:\n")
                    for index, item in enumerate(conversations, start=1):
                        typer.echo(f"[{index}] {item.title}")
                    choice = typer.prompt("\nChoose conversation", type=int)
                    if choice < 1 or choice > len(conversations):
                        raise typer.BadParameter("conversation selection is out of range")
                    selected = conversations[choice - 1]

            typer.echo(
                f"\nAttached:\n{selected.title}\n\nConversation ID:\n{selected.id}\n\n"
                f"Workspace:\n{workspace.root}\n\nPermission:\n{permission.value}\n"
            )
            runtime = build_local_runtime(
                workspace,
                permission=permission,
                approver=SessionApprover(interactive_prompt),
                settings=settings,
            )
            context = await collect_workspace_context(
                runtime,
                conversation_id=selected.id,
                conversation_title=selected.title,
            )
            orchestrator = ChatOrchestrator(
                backend,
                runtime,
                max_actions_per_turn=settings.local_max_actions_per_turn,
            )
            initial = build_session_prompt(workspace.root, permission, context)
            response = await orchestrator.run_turn(
                initial,
                conversation_id=selected.id,
                conversation_title=selected.title,
            )
            typer.echo(f"ChatGPT:\n{response.content}\n")
            typer.echo("Attached. Enter messages here; type /exit to stop.")
            while True:
                message = typer.prompt("You")
                if message.strip().casefold() in {"/exit", "/quit"}:
                    return
                response = await orchestrator.run_turn(
                    message,
                    conversation_id=selected.id,
                    conversation_title=selected.title,
                )
                typer.echo(f"ChatGPT:\n{response.content}\n")

    try:
        asyncio.run(run())
    except ChatBackendError as error:
        typer.echo(f"Chat backend error: {error}", err=True)
        raise typer.Exit(1) from error
    except KeyboardInterrupt:
        typer.echo("\nDetached.")


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
