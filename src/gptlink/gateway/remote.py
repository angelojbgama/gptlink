"""Typed Gateway requests over GPTLink Protocol v1."""

from uuid import UUID, uuid4

from gptlink.common.types import ShellKind
from gptlink.gateway.registry import ConnectionRegistry
from gptlink.protocol.messages import (
    CommandCancel,
    CommandCancelPayload,
    CommandOutputRequest,
    CommandOutputRequestPayload,
    CommandStart,
    CommandStartPayload,
    CommandStatus,
    CommandStatusPayload,
    FilesystemList,
    FilesystemListPayload,
    FilesystemRead,
    FilesystemReadPayload,
    FilesystemSearch,
    FilesystemSearchPayload,
    FilesystemWrite,
    FilesystemWritePayload,
    GitDiff,
    GitDiffPayload,
    GitStatus,
    GitStatusPayload,
    ProcessList,
    ProcessListPayload,
)


class RemoteAgentOperations:
    def __init__(self, registry: ConnectionRegistry, *, timeout: float = 30) -> None:
        self.registry = registry
        self.timeout = timeout

    async def filesystem_list(self, device_id: UUID, path: str):
        return await self._request(
            device_id,
            FilesystemList(
                protocol_version=1,
                type="filesystem.list",
                request_id=uuid4(),
                device_id=device_id,
                payload=FilesystemListPayload(path=path),
            ),
        )

    async def filesystem_read(self, device_id: UUID, path: str):
        return await self._request(
            device_id,
            FilesystemRead(
                protocol_version=1,
                type="filesystem.read",
                request_id=uuid4(),
                device_id=device_id,
                payload=FilesystemReadPayload(path=path),
            ),
        )

    async def filesystem_write(self, device_id: UUID, request_id: UUID, path: str, data: str):
        return await self._request(
            device_id,
            FilesystemWrite(
                protocol_version=1,
                type="filesystem.write",
                request_id=request_id,
                device_id=device_id,
                payload=FilesystemWritePayload(path=path, data=data),
            ),
        )

    async def filesystem_search(self, device_id: UUID, path: str, query: str):
        return await self._request(
            device_id,
            FilesystemSearch(
                protocol_version=1,
                type="filesystem.search",
                request_id=uuid4(),
                device_id=device_id,
                payload=FilesystemSearchPayload(path=path, query=query),
            ),
        )

    async def command_start(
        self,
        device_id: UUID,
        request_id: UUID,
        command: str,
        shell: ShellKind,
        cwd: str,
        timeout: float,
    ):
        return await self._request(
            device_id,
            CommandStart(
                protocol_version=1,
                type="command.start",
                request_id=request_id,
                device_id=device_id,
                payload=CommandStartPayload(command=command, shell=shell, cwd=cwd, timeout=timeout),
            ),
        )

    async def command_status(self, device_id: UUID, job_id: UUID):
        return await self._request(
            device_id,
            CommandStatus(
                protocol_version=1,
                type="command.status",
                request_id=uuid4(),
                device_id=device_id,
                job_id=job_id,
                payload=CommandStatusPayload(),
            ),
        )

    async def command_output(self, device_id: UUID, job_id: UUID, after_sequence: int):
        return await self._request(
            device_id,
            CommandOutputRequest(
                protocol_version=1,
                type="command.output.request",
                request_id=uuid4(),
                device_id=device_id,
                job_id=job_id,
                payload=CommandOutputRequestPayload(after_sequence=after_sequence),
            ),
        )

    async def command_cancel(self, device_id: UUID, request_id: UUID, job_id: UUID, reason: str):
        return await self._request(
            device_id,
            CommandCancel(
                protocol_version=1,
                type="command.cancel",
                request_id=request_id,
                device_id=device_id,
                job_id=job_id,
                payload=CommandCancelPayload(reason=reason),
            ),
        )

    async def git_status(self, device_id: UUID, path: str):
        return await self._request(
            device_id,
            GitStatus(
                protocol_version=1,
                type="git.status",
                request_id=uuid4(),
                device_id=device_id,
                payload=GitStatusPayload(path=path),
            ),
        )

    async def git_diff(self, device_id: UUID, path: str, revision: str | None):
        return await self._request(
            device_id,
            GitDiff(
                protocol_version=1,
                type="git.diff",
                request_id=uuid4(),
                device_id=device_id,
                payload=GitDiffPayload(path=path, revision=revision),
            ),
        )

    async def process_list(self, device_id: UUID):
        return await self._request(
            device_id,
            ProcessList(
                protocol_version=1,
                type="process.list",
                request_id=uuid4(),
                device_id=device_id,
                payload=ProcessListPayload(),
            ),
        )

    async def _request(self, device_id: UUID, message):
        return await self.registry.request(device_id, message, timeout=self.timeout)
