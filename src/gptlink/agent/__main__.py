"""Linux development Agent CLI."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import typer

from gptlink.agent.client import CredentialStore, PairMetadata
from gptlink.agent.runtime import build_linux_agent
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
    client = build_linux_agent(settings, store)
    metadata = PairMetadata(
        display_name=display_name,
        hostname=socket.gethostname(),
        capabilities=sorted(client.capabilities, key=lambda item: item.value),
    )
    credential = asyncio.run(client.pair(gateway, code, metadata))
    typer.echo(f"Paired device {credential.device_id}")


@app.command("run")
def run(path: Path | None = typer.Option(None, help="Credential file path")) -> None:
    configured = Settings()
    store = CredentialStore(path or credential_path())
    try:
        asyncio.run(build_linux_agent(configured, store).run())
    except KeyboardInterrupt:
        return
