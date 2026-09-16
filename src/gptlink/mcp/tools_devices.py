"""Thin MCP tools for safe device metadata and explicit leases."""

from uuid import UUID

from mcp.server import MCPServer

from gptlink.gateway.operations import GatewayOperations

from .schemas import DeviceInfo, LeaseInfo, ReleaseResult


def register(server: MCPServer, operations: GatewayOperations, caller: str) -> None:
    @server.tool(name="devices_list", structured_output=True)
    async def devices_list() -> list[DeviceInfo]:
        """List authorized devices without credentials or pairing secrets."""
        return [
            DeviceInfo.model_validate(item) for item in await operations.list_devices(caller=caller)
        ]

    @server.tool(name="device_info", structured_output=True)
    async def device_info(device_id: UUID) -> DeviceInfo:
        """Return safe metadata for one non-revoked device."""
        return DeviceInfo.model_validate(
            await operations.device_info(device_id=device_id, caller=caller)
        )

    @server.tool(name="lease_acquire", structured_output=True)
    async def lease_acquire(device_id: UUID, ttl_seconds: float = 60) -> LeaseInfo:
        """Acquire the device's exclusive mutation lease."""
        return LeaseInfo.model_validate(
            await operations.acquire_lease(device_id=device_id, caller=caller, ttl=ttl_seconds)
        )

    @server.tool(name="lease_release", structured_output=True)
    async def lease_release(device_id: UUID, lease_id: UUID) -> ReleaseResult:
        """Release a mutation lease owned by this MCP caller."""
        return ReleaseResult.model_validate(
            await operations.release_lease(device_id=device_id, lease_id=lease_id, caller=caller)
        )
