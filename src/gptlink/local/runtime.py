"""Direct adapter from strict chat actions to the existing Agent dispatcher."""

from __future__ import annotations

import os
from dataclasses import dataclass
from time import monotonic
from typing import Protocol
from uuid import UUID, uuid4

from gptlink.agent.dispatcher import AgentDispatcher, ProcessReader
from gptlink.agent.filesystem.operations import FilesystemOperations
from gptlink.agent.jobs import JobManager
from gptlink.agent.read_operations import GitReadService, ProcessReadService
from gptlink.chat.protocol import (
    ChatAction,
    CommandCancelArguments,
    CommandOutputArguments,
    CommandStartArguments,
    EmptyArguments,
    GitDiffArguments,
    JobArguments,
    PathArguments,
    SearchArguments,
    WriteArguments,
)
from gptlink.common.config import Settings
from gptlink.common.types import Capability, PermissionLevel, ShellKind
from gptlink.local.approvals import ApprovalRequest
from gptlink.local.audit import LocalAuditLog
from gptlink.local.workspace import LocalWorkspace
from gptlink.protocol.messages import (
    CommandCancel,
    CommandCancelPayload,
    CommandOutputRequest,
    CommandOutputRequestPayload,
    CommandStart,
    CommandStartPayload,
    CommandStatus,
    CommandStatusPayload,
    Error,
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
    OperationResult,
    ProcessList,
    ProcessListPayload,
    ProtocolMessage,
)


class Approver(Protocol):
    async def approve(self, request: ApprovalRequest) -> bool: ...


class LocalActionError(RuntimeError):
    """A local action was rejected or failed safely."""


@dataclass(frozen=True)
class LocalRuntime:
    workspace: LocalWorkspace
    permission: PermissionLevel
    dispatcher: AgentDispatcher
    approver: Approver
    audit: LocalAuditLog
    max_command_length: int
    max_results: int
    device_id: UUID

    async def execute(
        self,
        action: ChatAction,
        *,
        conversation_id: str,
        conversation_title: str,
    ) -> object:
        started = monotonic()
        status = "error"
        try:
            message = self._message(action)
            await self._authorize(action, conversation_title)
            response = await self.dispatcher.dispatch(message)
            if isinstance(response, Error):
                raise LocalActionError(response.payload.message)
            if not isinstance(response, OperationResult):
                raise LocalActionError("unexpected local runtime response")
            status = "ok"
            return self._bounded_result(action, response.payload.result)
        except PermissionError as error:
            status = "denied"
            raise LocalActionError(str(error)) from error
        except LocalActionError:
            raise
        except (KeyError, OSError, ValueError) as error:
            raise LocalActionError("local action rejected") from error
        finally:
            await self.audit.record(
                conversation_id=conversation_id,
                workspace=self.workspace.root,
                action=action.tool,
                result=status,
                duration_ms=max(0, round((monotonic() - started) * 1000)),
                request_id=action.id,
            )

    async def _authorize(self, action: ChatAction, conversation_title: str) -> None:
        if action.tool not in {"filesystem_write", "command_start"}:
            return
        if self.permission is PermissionLevel.READ_ONLY:
            raise PermissionError(f"READ_ONLY policy forbids {action.tool}")
        request = ApprovalRequest(conversation_title, self.workspace.root, action)
        if not await self.approver.approve(request):
            raise PermissionError("local operator denied the action")

    def _message(self, action: ChatAction) -> ProtocolMessage:
        request_id = uuid4()
        args = action.arguments
        if action.tool == "filesystem_list" and isinstance(args, PathArguments):
            return FilesystemList(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="filesystem.list",
                payload=FilesystemListPayload(
                    path=str(self.workspace.resolve(args.path, existing=True))
                ),
            )
        if action.tool == "filesystem_read" and isinstance(args, PathArguments):
            return FilesystemRead(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="filesystem.read",
                payload=FilesystemReadPayload(
                    path=str(self.workspace.resolve(args.path, existing=True))
                ),
            )
        if action.tool == "filesystem_search" and isinstance(args, SearchArguments):
            return FilesystemSearch(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="filesystem.search",
                payload=FilesystemSearchPayload(
                    path=str(self.workspace.resolve(args.path, existing=True)), query=args.query
                ),
            )
        if action.tool == "filesystem_write" and isinstance(args, WriteArguments):
            return FilesystemWrite(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="filesystem.write",
                payload=FilesystemWritePayload(
                    path=str(self.workspace.resolve(args.path)), data=args.data
                ),
            )
        if action.tool == "git_status" and isinstance(args, PathArguments):
            return GitStatus(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="git.status",
                payload=GitStatusPayload(
                    path=str(self.workspace.resolve(args.path, existing=True))
                ),
            )
        if action.tool == "git_diff" and isinstance(args, GitDiffArguments):
            return GitDiff(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="git.diff",
                payload=GitDiffPayload(
                    path=str(self.workspace.resolve(args.path, existing=True)),
                    revision=args.revision,
                ),
            )
        if action.tool == "process_list" and isinstance(args, EmptyArguments):
            return ProcessList(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="process.list",
                payload=ProcessListPayload(),
            )
        if action.tool == "command_start" and isinstance(args, CommandStartArguments):
            if len(args.command) > self.max_command_length:
                raise LocalActionError("command length limit exceeded")
            shell = ShellKind(args.shell)
            return CommandStart(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="command.start",
                payload=CommandStartPayload(
                    command=args.command,
                    shell=shell,
                    cwd=str(self.workspace.resolve(args.cwd, existing=True)),
                    timeout=args.timeout,
                ),
            )
        if action.tool == "command_status" and isinstance(args, JobArguments):
            return CommandStatus(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="command.status",
                job_id=_job_id(args.job_id),
                payload=CommandStatusPayload(),
            )
        if action.tool == "command_output" and isinstance(args, CommandOutputArguments):
            return CommandOutputRequest(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="command.output.request",
                job_id=_job_id(args.job_id),
                payload=CommandOutputRequestPayload(after_sequence=args.after_sequence),
            )
        if action.tool == "command_cancel" and isinstance(args, CommandCancelArguments):
            return CommandCancel(
                protocol_version=1,
                request_id=request_id,
                device_id=self.device_id,
                type="command.cancel",
                job_id=_job_id(args.job_id),
                payload=CommandCancelPayload(reason=args.reason),
            )
        raise LocalActionError("action arguments do not match the requested tool")

    def _bounded_result(self, action: ChatAction, result: object) -> object:
        if action.tool in {"filesystem_list", "filesystem_search", "process_list"} and isinstance(
            result, list
        ):
            return {"items": result, "truncated": len(result) >= self.max_results}
        return result


def _job_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise LocalActionError("job_id must be a UUID") from error


def build_local_runtime(
    workspace: LocalWorkspace,
    *,
    permission: PermissionLevel,
    approver: Approver,
    settings: Settings,
    audit: LocalAuditLog | None = None,
) -> LocalRuntime:
    """Assemble existing Agent operations without a Device or network transport."""
    capabilities = {
        Capability.FILESYSTEM_READ,
        Capability.FILESYSTEM_SEARCH,
        Capability.GIT_READ,
        Capability.PROCESS_READ,
        Capability.COMMAND_START,
    }
    if permission is PermissionLevel.READ_WRITE:
        capabilities.add(Capability.FILESYSTEM_WRITE)
    filesystem = FilesystemOperations(
        workspace.paths,
        permission=permission,
        capabilities=capabilities,
        max_read_bytes=settings.max_file_bytes,
        max_write_bytes=settings.max_file_bytes,
        max_search_results=settings.max_search_results,
    )
    executor = None
    if os.name == "nt":
        from gptlink.agent.executors.windows_process import (
            WindowsExecutor,
            WindowsProcessReadService,
        )

        executor = WindowsExecutor()
        processes: ProcessReader = WindowsProcessReadService(
            capabilities, max_results=settings.max_search_results
        )
    else:
        processes = ProcessReadService(capabilities, max_results=settings.max_search_results)
    dispatcher = AgentDispatcher(
        filesystem,
        JobManager(
            max_output_bytes=settings.max_job_output_bytes,
            max_concurrent_jobs=settings.max_concurrent_jobs,
            executor=executor,
        ),
        GitReadService(workspace.paths, capabilities),
        processes,
    )
    return LocalRuntime(
        workspace=workspace,
        permission=permission,
        dispatcher=dispatcher,
        approver=approver,
        audit=audit or LocalAuditLog(settings.local_audit_path),
        max_command_length=settings.max_command_length,
        max_results=settings.max_search_results,
        device_id=uuid4(),
    )
