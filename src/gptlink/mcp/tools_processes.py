"""Thin read-only MCP process tool."""

from uuid import UUID

from mcp.server import MCPServer

from gptlink.common.types import Capability
from gptlink.gateway.operations import GatewayOperations
from gptlink.gateway.remote import RemoteAgentOperations

from .schemas import ProcessInfo


def register(
    server: MCPServer,
    operations: GatewayOperations,
    remote: RemoteAgentOperations,
    caller: str,
) -> None:
    @server.tool(name="process_list", structured_output=True)
    async def process_list(device_id: UUID) -> list[ProcessInfo]:
        """Return a bounded process list; process termination is not exposed."""
        return await operations.read(
            device_id=device_id,
            caller=caller,
            capability=Capability.PROCESS_READ,
            action="process.list",
            execute=lambda: remote.process_list(device_id),
        )
