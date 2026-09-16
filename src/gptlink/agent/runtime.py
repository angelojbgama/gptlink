"""Construct a Linux Agent runtime from explicit local policy settings."""

from gptlink.agent.client import AgentClient, CredentialStore
from gptlink.agent.dispatcher import AgentDispatcher
from gptlink.agent.filesystem.operations import FilesystemOperations
from gptlink.agent.filesystem.paths import SandboxPaths
from gptlink.agent.jobs import JobManager
from gptlink.agent.read_operations import GitReadService, ProcessReadService
from gptlink.common.config import Settings
from gptlink.common.types import Capability, PermissionLevel


def linux_capabilities(settings: Settings) -> set[Capability]:
    if not settings.agent_roots:
        return set()
    capabilities = {
        Capability.FILESYSTEM_READ,
        Capability.FILESYSTEM_SEARCH,
        Capability.COMMAND_START,
        Capability.GIT_READ,
        Capability.PROCESS_READ,
    }
    if settings.agent_permission_level in {
        PermissionLevel.READ_WRITE,
        PermissionLevel.FULL_ACCESS,
    }:
        capabilities.add(Capability.FILESYSTEM_WRITE)
    return capabilities


def build_linux_agent(settings: Settings, store: CredentialStore) -> AgentClient:
    capabilities = linux_capabilities(settings)
    dispatcher = None
    if settings.agent_roots:
        paths = SandboxPaths(settings.agent_roots)
        filesystem = FilesystemOperations(
            paths,
            permission=settings.agent_permission_level,
            capabilities=capabilities,
            max_read_bytes=settings.max_file_bytes,
            max_write_bytes=settings.max_file_bytes,
            max_search_results=settings.max_search_results,
        )
        dispatcher = AgentDispatcher(
            filesystem,
            JobManager(
                max_output_bytes=settings.max_job_output_bytes,
                max_concurrent_jobs=settings.max_concurrent_jobs,
            ),
            GitReadService(paths, capabilities),
            ProcessReadService(capabilities, max_results=settings.max_search_results),
        )
    return AgentClient(settings, store, dispatcher=dispatcher, capabilities=capabilities)
