"""Thin MCP filesystem tools; all work occurs on the Agent."""

from uuid import UUID

from mcp.server import MCPServer

from gptlink.common.types import Capability
from gptlink.gateway.operations import GatewayOperations
from gptlink.gateway.remote import RemoteAgentOperations

from .schemas import FileContent, FilesystemEntry, WriteResult


def register(
    server: MCPServer,
    operations: GatewayOperations,
    remote: RemoteAgentOperations,
    caller: str,
) -> None:
    @server.tool(name="filesystem_list", structured_output=True)
    async def filesystem_list(device_id: UUID, path: str) -> list[FilesystemEntry]:
        """List a bounded directory inside an Agent-authorized root."""
        return await operations.read(
            device_id=device_id,
            caller=caller,
            capability=Capability.FILESYSTEM_READ,
            action="filesystem.list",
            execute=lambda: remote.filesystem_list(device_id, path),
            details={"path": path},
        )

    @server.tool(name="filesystem_read", structured_output=True)
    async def filesystem_read(device_id: UUID, path: str) -> FileContent:
        """Read one bounded UTF-8 file inside an Agent-authorized root."""
        return await operations.read(
            device_id=device_id,
            caller=caller,
            capability=Capability.FILESYSTEM_READ,
            action="filesystem.read",
            execute=lambda: remote.filesystem_read(device_id, path),
            details={"path": path},
        )

    @server.tool(name="filesystem_write", structured_output=True)
    async def filesystem_write(
        device_id: UUID, request_id: UUID, lease_id: UUID, path: str, data: str
    ) -> WriteResult:
        """Atomically write one file using a lease and unique request ID."""
        return await operations.mutate(
            device_id=device_id,
            caller=caller,
            request_id=request_id,
            lease_id=lease_id,
            capability=Capability.FILESYSTEM_WRITE,
            action="filesystem.write",
            execute=lambda: remote.filesystem_write(device_id, request_id, path, data),
            details={"path": path, "bytes": len(data.encode("utf-8"))},
        )

    @server.tool(name="filesystem_search", structured_output=True)
    async def filesystem_search(device_id: UUID, path: str, query: str) -> list[FilesystemEntry]:
        """Search bounded UTF-8 files inside an Agent-authorized root."""
        return await operations.read(
            device_id=device_id,
            caller=caller,
            capability=Capability.FILESYSTEM_SEARCH,
            action="filesystem.search",
            execute=lambda: remote.filesystem_search(device_id, path, query),
            details={"path": path, "query_length": len(query)},
        )
