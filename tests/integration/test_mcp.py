"""Official MCP client over authenticated Streamable HTTP into an Agent dispatcher."""

import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from gptlink.agent.dispatcher import AgentDispatcher
from gptlink.agent.filesystem.operations import FilesystemOperations
from gptlink.agent.filesystem.paths import SandboxPaths
from gptlink.agent.jobs import JobManager
from gptlink.agent.read_operations import GitReadService, ProcessInfo
from gptlink.common.config import Settings
from gptlink.common.types import Capability, DeviceStatus, PermissionLevel
from gptlink.gateway.app import create_app
from gptlink.persistence.database import Database
from gptlink.persistence.models import Device
from gptlink.persistence.repositories import DeviceRepository
from gptlink.protocol.codec import decode_message

TOKEN = "mcp-test-token-that-is-longer-than-32-characters"
CAPABILITIES = [
    Capability.FILESYSTEM_READ,
    Capability.FILESYSTEM_WRITE,
    Capability.FILESYSTEM_SEARCH,
    Capability.COMMAND_START,
    Capability.GIT_READ,
    Capability.PROCESS_READ,
]


class TestProcessService:
    def list(self):
        return [ProcessInfo(pid=123, command="test-agent")]


class DispatchSocket:
    def __init__(self, dispatcher, registry):
        self.dispatcher = dispatcher
        self.registry = registry
        self.connection = None

    async def send_text(self, data: str):
        response = await self.dispatcher.dispatch(decode_message(data))
        assert self.connection is not None
        assert await self.registry.resolve(self.connection, response)

    async def close(self, code: int):
        return None


@asynccontextmanager
async def running_mcp_gateway(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.txt").write_text("hello remote world\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}",
        gateway_host="localhost",
        mcp_token=TOKEN,
    )
    database = Database(settings.database_url)
    app = create_app(settings, database=database)
    device = Device(
        device_id=uuid4(),
        display_name="MCP Test Agent",
        platform="test",
        hostname="test-agent",
        agent_version="0.1.0",
        protocol_version=1,
        capabilities=CAPABILITIES,
        status=DeviceStatus.OFFLINE,
        permission_level=PermissionLevel.READ_WRITE,
        device_token_hash="not-exposed",
    )
    filesystem = FilesystemOperations(
        SandboxPaths([root]),
        permission=PermissionLevel.READ_WRITE,
        capabilities=set(CAPABILITIES),
        max_read_bytes=1024 * 1024,
        max_write_bytes=1024 * 1024,
        max_search_results=100,
    )
    dispatcher = AgentDispatcher(
        filesystem,
        JobManager(),
        GitReadService(filesystem.paths, set(CAPABILITIES)),
        TestProcessService(),
    )

    async with app.router.lifespan_context(app):
        async with database.transaction() as session:
            await DeviceRepository(session).add(device)
        socket = DispatchSocket(dispatcher, app.state.registry)
        socket.connection = await app.state.registry.register(device.device_id, socket)
        try:
            yield app, device, root
        finally:
            await app.state.registry.disconnect(socket.connection)


@asynccontextmanager
async def official_client(app, token: str = TOKEN):
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        headers={"Authorization": f"Bearer {token}"},
        base_url="http://localhost:8000",
    )
    async with http:
        transport = streamable_http_client(
            "http://localhost:8000/mcp", http_client=http, terminate_on_close=False
        )
        async with Client(transport) as client:
            yield client


@pytest.mark.asyncio
async def test_missing_invalid_and_valid_mcp_bearer(tmp_path):
    async with running_mcp_gateway(tmp_path) as (app, _, _):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            missing = await client.post("/mcp", json={})
            invalid = await client.post(
                "/mcp", json={}, headers={"Authorization": "Bearer wrong-value"}
            )
            valid = await client.post("/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code != 401
    assert TOKEN not in missing.text + invalid.text + valid.text


@pytest.mark.asyncio
async def test_official_client_lists_stable_tools_and_safe_devices(tmp_path):
    async with running_mcp_gateway(tmp_path) as (app, device, _):
        async with official_client(app) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {
                "devices_list",
                "device_info",
                "filesystem_list",
                "filesystem_read",
                "filesystem_write",
                "filesystem_search",
                "command_start",
                "command_status",
                "command_output",
                "command_cancel",
                "git_status",
                "git_diff",
                "process_list",
                "lease_acquire",
                "lease_release",
            } == names
            listed = await client.call_tool("devices_list", {})
            info = await client.call_tool("device_info", {"device_id": str(device.device_id)})

    assert listed.is_error is False
    assert info.is_error is False
    assert "device_token_hash" not in repr(listed.structured_content)
    assert str(device.device_id) in repr(info.structured_content)


@pytest.mark.asyncio
async def test_mcp_flows_through_registry_and_agent_services(tmp_path):
    async with running_mcp_gateway(tmp_path) as (app, device, root):
        arguments = {"device_id": str(device.device_id), "path": str(root)}
        async with official_client(app) as client:
            listing = await client.call_tool("filesystem_list", arguments)
            read = await client.call_tool(
                "filesystem_read", {**arguments, "path": str(root / "note.txt")}
            )
            search = await client.call_tool("filesystem_search", {**arguments, "query": "remote"})
            git = await client.call_tool("git_status", arguments)
            processes = await client.call_tool("process_list", {"device_id": str(device.device_id)})
            lease = await client.call_tool(
                "lease_acquire", {"device_id": str(device.device_id), "ttl_seconds": 60}
            )
            lease_id = lease.structured_content["lease_id"]
            write = await client.call_tool(
                "filesystem_write",
                {
                    "device_id": str(device.device_id),
                    "request_id": str(uuid4()),
                    "lease_id": lease_id,
                    "path": str(root / "written.txt"),
                    "data": "written over MCP",
                },
            )

    assert all(
        result.is_error is False for result in (listing, read, search, git, processes, lease, write)
    )
    assert "note.txt" in repr(listing.structured_content)
    assert "hello remote world" in repr(read.structured_content)
    assert "note.txt" in repr(search.structured_content)
    assert "test-agent" in repr(processes.structured_content)
    assert (root / "written.txt").read_text(encoding="utf-8") == "written over MCP"
