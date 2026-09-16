"""Linux development Agent CLI."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import typer

from gptlink.agent.client import AgentClient, CredentialStore, PairMetadata
from gptlink.common.config import Settings

app = typer.Typer(help="GPTLink outbound Agent")


def credential_path() -> Path:
    return Path.home() / ".config" / "gptlink" / "agent.json"


@app.command("pair")
def pair(
    gateway: str = typer.Option(...),
    code: str = typer.Option(...),
    display_name: str = typer.Option("Linux Dev Agent"),
    path: Path | None = typer.Option(None, help="Credential file path"),
) -> None:
    settings = Settings(gateway_url=gateway)
    store = CredentialStore(path or credential_path())
    metadata = PairMetadata(display_name=display_name, hostname=socket.gethostname())
    credential = asyncio.run(AgentClient(settings, store).pair(gateway, code, metadata))
    typer.echo(f"Paired device {credential.device_id}")


@app.command("run")
def run(path: Path | None = typer.Option(None, help="Credential file path")) -> None:
    configured = Settings()
    store = CredentialStore(path or credential_path())
    try:
        asyncio.run(AgentClient(configured, store).run())
    except KeyboardInterrupt:
        return
