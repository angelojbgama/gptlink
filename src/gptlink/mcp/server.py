"""Official SDK server construction for the GPTLink MCP adapter."""

from mcp.server import MCPServer

from gptlink.gateway.operations import GatewayOperations
from gptlink.gateway.remote import RemoteAgentOperations

from . import tools_commands, tools_devices, tools_filesystem, tools_git, tools_processes


def create_mcp_server(
    operations: GatewayOperations, remote: RemoteAgentOperations, *, caller: str
) -> MCPServer:
    server = MCPServer(
        "GPTLink",
        version="0.1.0",
        instructions="Operate only explicitly authorized remote devices and paths.",
    )
    tools_devices.register(server, operations, caller)
    tools_filesystem.register(server, operations, remote, caller)
    tools_commands.register(server, operations, remote, caller)
    tools_git.register(server, operations, remote, caller)
    tools_processes.register(server, operations, remote, caller)
    return server
