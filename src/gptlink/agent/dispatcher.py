"""Dispatch validated GPTLink requests to local policy-enforcing services."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from gptlink.agent.filesystem.operations import FilesystemOperations
from gptlink.agent.jobs import JobManager
from gptlink.agent.read_operations import GitReadService, ProcessReadService
from gptlink.protocol.messages import (
    CommandCancel,
    CommandOutputRequest,
    CommandStart,
    CommandStatus,
    Error,
    ErrorPayload,
    FilesystemList,
    FilesystemRead,
    FilesystemSearch,
    FilesystemWrite,
    GitDiff,
    GitStatus,
    OperationResult,
    OperationResultPayload,
    ProcessList,
    ProtocolMessage,
)


class AgentDispatcher:
    """The Agent-side policy boundary; it has no knowledge of MCP."""

    def __init__(
        self,
        filesystem: FilesystemOperations,
        jobs: JobManager,
        git: GitReadService,
        processes: ProcessReadService,
    ) -> None:
        self.filesystem = filesystem
        self.jobs = jobs
        self.git = git
        self.processes = processes

    async def dispatch(self, message: ProtocolMessage) -> OperationResult | Error:
        try:
            result = await self._execute(message)
        except Exception:
            return Error(
                protocol_version=1,
                type="error",
                request_id=message.request_id,
                device_id=message.device_id,
                payload=ErrorPayload(
                    code="operation_failed", message="Agent operation failed", retryable=False
                ),
            )
        return OperationResult(
            protocol_version=1,
            type="operation.result",
            request_id=message.request_id,
            device_id=message.device_id,
            payload=OperationResultPayload(result=result),
        )

    async def _execute(self, message: ProtocolMessage):
        if isinstance(message, FilesystemList):
            entries = await asyncio.to_thread(self.filesystem.list, message.payload.path)
            return [asdict(entry) for entry in entries]
        if isinstance(message, FilesystemRead):
            return {"content": await asyncio.to_thread(self.filesystem.read, message.payload.path)}
        if isinstance(message, FilesystemWrite):
            await asyncio.to_thread(
                self.filesystem.write, message.payload.path, message.payload.data
            )
            return {"written": True}
        if isinstance(message, FilesystemSearch):
            search_entries = await asyncio.to_thread(
                self.filesystem.search, message.payload.path, message.payload.query
            )
            return [asdict(entry) for entry in search_entries]
        if isinstance(message, GitStatus):
            return {"output": await self.git.status(message.payload.path)}
        if isinstance(message, GitDiff):
            return {"output": await self.git.diff(message.payload.path, message.payload.revision)}
        if isinstance(message, ProcessList):
            processes = await asyncio.to_thread(self.processes.list)
            return [asdict(process) for process in processes]
        if isinstance(message, CommandStart):
            cwd = self.filesystem.paths.resolve_existing(message.payload.cwd)
            job_id = await self.jobs.start(
                device_id=message.device_id,
                request_id=message.request_id,
                command=message.payload.command,
                shell=message.payload.shell,
                cwd=cwd,
                timeout=message.payload.timeout,
            )
            return {"job_id": str(job_id)}
        if isinstance(message, CommandStatus):
            result = await self.jobs.status(message.job_id)
            return _job_result(result)
        if isinstance(message, CommandOutputRequest):
            chunks = await self.jobs.output(
                message.job_id, after_sequence=message.payload.after_sequence
            )
            return [
                {
                    "job_id": str(chunk.job_id),
                    "sequence_number": chunk.sequence_number,
                    "stream": chunk.stream.value,
                    "timestamp": chunk.timestamp.isoformat(),
                    "data": chunk.data,
                }
                for chunk in chunks
            ]
        if isinstance(message, CommandCancel):
            return {"cancelled": await self.jobs.cancel(message.job_id)}
        raise ValueError("unsupported Agent operation")


def _job_result(result) -> dict:
    return {
        "job_id": str(result.job_id),
        "status": result.status.value,
        "exit_code": result.exit_code,
        "output_truncated": result.output_truncated,
    }
