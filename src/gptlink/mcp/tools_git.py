"""Thin read-only MCP Git tools."""

from uuid import UUID

from mcp.server import MCPServer

from gptlink.common.types import Capability
from gptlink.gateway.operations import GatewayOperations
from gptlink.gateway.remote import RemoteAgentOperations

from .schemas import GitOutput


def register(
    server: MCPServer,
    operations: GatewayOperations,
    remote: RemoteAgentOperations,
    caller: str,
) -> None:
    @server.tool(name="git_status", structured_output=True)
    async def git_status(device_id: UUID, path: str) -> GitOutput:
        """Return bounded porcelain Git status without mutation."""
        return await operations.read(
            device_id=device_id,
            caller=caller,
            capability=Capability.GIT_READ,
            action="git.status",
            execute=lambda: remote.git_status(device_id, path),
            details={"path": path},
        )

    @server.tool(name="git_diff", structured_output=True)
    async def git_diff(device_id: UUID, path: str, revision: str | None = None) -> GitOutput:
        """Return a bounded Git diff without mutation."""
        return await operations.read(
            device_id=device_id,
            caller=caller,
            capability=Capability.GIT_READ,
            action="git.diff",
            execute=lambda: remote.git_diff(device_id, path, revision),
            details={"path": path, "revision": revision},
        )
