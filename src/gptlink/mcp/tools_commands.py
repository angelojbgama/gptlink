"""Thin MCP command-job tools with explicit shell selection."""

from uuid import UUID

from mcp.server import MCPServer

from gptlink.common.types import Capability, ShellKind
from gptlink.gateway.operations import GatewayOperations
from gptlink.gateway.remote import RemoteAgentOperations

from .schemas import CancelResult, JobOutput, JobStarted, JobState


def register(
    server: MCPServer,
    operations: GatewayOperations,
    remote: RemoteAgentOperations,
    caller: str,
) -> None:
    @server.tool(name="command_start", structured_output=True)
    async def command_start(
        device_id: UUID,
        request_id: UUID,
        lease_id: UUID,
        command: str,
        shell: ShellKind,
        cwd: str,
        timeout_seconds: float = 30,
    ) -> JobStarted:
        """Start a bounded job with an explicit shell, lease, and request ID."""
        return await operations.mutate(
            device_id=device_id,
            caller=caller,
            request_id=request_id,
            lease_id=lease_id,
            capability=Capability.COMMAND_START,
            action="command.start",
            execute=lambda: remote.command_start(
                device_id, request_id, command, shell, cwd, timeout_seconds
            ),
            details={"shell": shell.value, "cwd": cwd, "command_length": len(command)},
            risk_level="HIGH",
        )

    @server.tool(name="command_status", structured_output=True)
    async def command_status(device_id: UUID, job_id: UUID) -> JobState:
        """Return the current status of a command job."""
        return await operations.read(
            device_id=device_id,
            caller=caller,
            capability=Capability.COMMAND_START,
            action="command.status",
            execute=lambda: remote.command_status(device_id, job_id),
        )

    @server.tool(name="command_output", structured_output=True)
    async def command_output(
        device_id: UUID, job_id: UUID, after_sequence: int = -1
    ) -> list[JobOutput]:
        """Read bounded command output after a sequence cursor."""
        return await operations.read(
            device_id=device_id,
            caller=caller,
            capability=Capability.COMMAND_START,
            action="command.output",
            execute=lambda: remote.command_output(device_id, job_id, after_sequence),
        )

    @server.tool(name="command_cancel", structured_output=True)
    async def command_cancel(
        device_id: UUID,
        request_id: UUID,
        lease_id: UUID,
        job_id: UUID,
        reason: str = "caller requested cancellation",
    ) -> CancelResult:
        """Cancel a command process tree using a lease and request ID."""
        return await operations.mutate(
            device_id=device_id,
            caller=caller,
            request_id=request_id,
            lease_id=lease_id,
            capability=Capability.COMMAND_START,
            action="command.cancel",
            execute=lambda: remote.command_cancel(device_id, request_id, job_id, reason),
            risk_level="HIGH",
        )
