"""Strict, immutable GPTLink version-one wire messages."""

from datetime import datetime
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from gptlink.common.types import Capability, JobStatus, ShellKind, StreamKind
from gptlink.protocol.version import PROTOCOL_VERSION


def require_protocol_version(value: object) -> int:
    """Accept only the integer value for the current protocol revision."""
    if type(value) is not int or value != PROTOCOL_VERSION:
        msg = f"protocol_version must be the integer {PROTOCOL_VERSION}"
        raise ValueError(msg)
    return value


def require_timezone_aware(value: datetime) -> datetime:
    """Reject naive datetimes before they become distributed event timestamps."""
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "timestamp must be timezone-aware"
        raise ValueError(msg)
    return value


class ProtocolModel(BaseModel):
    """Base for every payload and envelope accepted from the wire."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AgentHelloPayload(ProtocolModel):
    """Agent credentials plus the only protocol version it supports in v1."""

    token: str = Field(min_length=1)
    versions: tuple[int, ...] = Field(min_length=1)
    capabilities: tuple[Capability, ...]

    @field_validator("versions")
    @classmethod
    def require_current_version_only(cls, versions: tuple[int, ...]) -> tuple[int, ...]:
        """Keep the handshake advertisement coherent with its v1 envelope."""
        if versions != (PROTOCOL_VERSION,):
            msg = f"versions must be exactly ({PROTOCOL_VERSION},)"
            raise ValueError(msg)
        return versions


class AgentWelcomePayload(ProtocolModel):
    """Protocol version selected by the Gateway after a successful hello."""

    selected_version: int

    @field_validator("selected_version", mode="before")
    @classmethod
    def require_selected_current_version(cls, value: object) -> int:
        """Ensure the welcome cannot negotiate an unsupported version."""
        return require_protocol_version(value)


class HeartbeatPingPayload(ProtocolModel):
    """Agent liveness probe with an unambiguous event time."""

    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        """Keep clock values safe to compare across hosts."""
        return require_timezone_aware(value)


class HeartbeatPongPayload(HeartbeatPingPayload):
    """Gateway reply to an Agent heartbeat probe."""


class CommandStartPayload(ProtocolModel):
    """A requested command with its explicitly selected interpreter and cwd."""

    command: str = Field(min_length=1)
    shell: ShellKind
    cwd: str = Field(min_length=1)
    timeout: float = Field(default=30, gt=0, le=86_400, allow_inf_nan=False)


class CommandStartedPayload(ProtocolModel):
    """Agent acknowledgement that a command job was created."""

    pid: int = Field(ge=1)


class CommandOutputPayload(ProtocolModel):
    """One ordered, timestamped fragment of command output."""

    sequence_number: int = Field(ge=0)
    stream: StreamKind
    timestamp: datetime
    data: str

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        """Keep output chronology unambiguous across Agent and Gateway hosts."""
        return require_timezone_aware(value)


class CommandFinishedPayload(ProtocolModel):
    """Terminal status and optional process exit result for a command job."""

    status: Literal[
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.TIMED_OUT,
    ]
    exit_code: int | None = None


class CommandCancelPayload(ProtocolModel):
    """A caller-provided explanation for cancelling an allocated command job."""

    reason: str = Field(min_length=1)


class CommandStatusPayload(ProtocolModel):
    """An intentionally argument-free command status request."""


class CommandOutputRequestPayload(ProtocolModel):
    """Request command output after an optional sequence cursor."""

    after_sequence: int = Field(default=-1, ge=-1)


class FilesystemListPayload(ProtocolModel):
    """A bounded directory listing request."""

    path: str = Field(min_length=1)


class FilesystemReadPayload(ProtocolModel):
    """A bounded file read request."""

    path: str = Field(min_length=1)


class FilesystemWritePayload(ProtocolModel):
    """A file write request whose data is carried directly in the envelope."""

    path: str = Field(min_length=1)
    data: str


class FilesystemSearchPayload(ProtocolModel):
    """A textual filesystem search request."""

    path: str = Field(min_length=1)
    query: str = Field(min_length=1)


class GitStatusPayload(ProtocolModel):
    """A Git status request rooted at a sandboxed repository path."""

    path: str = Field(min_length=1)


class GitDiffPayload(ProtocolModel):
    """A Git diff request rooted at a sandboxed repository path."""

    path: str = Field(min_length=1)
    revision: str | None = Field(default=None, min_length=1)


class ProcessListPayload(ProtocolModel):
    """An intentionally argument-free process listing request."""


class OperationResultPayload(ProtocolModel):
    """A JSON-safe result correlated to one Gateway request."""

    result: JsonValue


class ErrorPayload(ProtocolModel):
    """A transport-safe failure code, message, and retry hint."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool


class Envelope(ProtocolModel):
    """Fields present and explicitly required on every GPTLink message."""

    protocol_version: int
    request_id: UUID
    device_id: UUID

    @field_validator("protocol_version", mode="before")
    @classmethod
    def require_current_protocol_version(cls, value: object) -> int:
        """Prevent missing or coercible versions from entering the v1 codec."""
        return require_protocol_version(value)


class JobEnvelope(Envelope):
    """Envelope used after the Agent has allocated a command job."""

    job_id: UUID


class AgentHello(Envelope):
    """First Agent-to-Gateway handshake message."""

    type: Literal["agent.hello"]
    payload: AgentHelloPayload


class AgentWelcome(Envelope):
    """Gateway handshake response confirming the selected protocol version."""

    type: Literal["agent.welcome"]
    payload: AgentWelcomePayload


class HeartbeatPing(Envelope):
    """Agent liveness event."""

    type: Literal["heartbeat.ping"]
    payload: HeartbeatPingPayload


class HeartbeatPong(Envelope):
    """Gateway liveness response."""

    type: Literal["heartbeat.pong"]
    payload: HeartbeatPongPayload


class CommandStart(Envelope):
    """Gateway request for the Agent to create a command job."""

    type: Literal["command.start"]
    payload: CommandStartPayload


class CommandStarted(JobEnvelope):
    """Agent acknowledgement that a command job was created."""

    type: Literal["command.started"]
    payload: CommandStartedPayload


class CommandOutput(JobEnvelope):
    """Agent command-output event."""

    type: Literal["command.output"]
    payload: CommandOutputPayload


class CommandFinished(JobEnvelope):
    """Agent terminal command-job event."""

    type: Literal["command.finished"]
    payload: CommandFinishedPayload


class CommandCancel(JobEnvelope):
    """Gateway request to cancel an allocated command job."""

    type: Literal["command.cancel"]
    payload: CommandCancelPayload


class CommandStatus(JobEnvelope):
    """Gateway request for current in-memory command job status."""

    type: Literal["command.status"]
    payload: CommandStatusPayload


class CommandOutputRequest(JobEnvelope):
    """Gateway request for bounded command job output."""

    type: Literal["command.output.request"]
    payload: CommandOutputRequestPayload


class FilesystemList(Envelope):
    """Gateway request for a sandboxed directory listing."""

    type: Literal["filesystem.list"]
    payload: FilesystemListPayload


class FilesystemRead(Envelope):
    """Gateway request for a sandboxed file read."""

    type: Literal["filesystem.read"]
    payload: FilesystemReadPayload


class FilesystemWrite(Envelope):
    """Gateway request for an atomic sandboxed file write."""

    type: Literal["filesystem.write"]
    payload: FilesystemWritePayload


class FilesystemSearch(Envelope):
    """Gateway request for a bounded sandboxed text search."""

    type: Literal["filesystem.search"]
    payload: FilesystemSearchPayload


class GitStatus(Envelope):
    """Gateway request for a bounded Git status."""

    type: Literal["git.status"]
    payload: GitStatusPayload


class GitDiff(Envelope):
    """Gateway request for a bounded Git diff."""

    type: Literal["git.diff"]
    payload: GitDiffPayload


class ProcessList(Envelope):
    """Gateway request for a bounded process list."""

    type: Literal["process.list"]
    payload: ProcessListPayload


class OperationResult(Envelope):
    """Agent response to a correlated operation request."""

    type: Literal["operation.result"]
    payload: OperationResultPayload


class Error(Envelope):
    """A response or event reporting a protocol-level failure."""

    type: Literal["error"]
    payload: ErrorPayload


type ProtocolMessage = Annotated[
    AgentHello
    | AgentWelcome
    | HeartbeatPing
    | HeartbeatPong
    | CommandStart
    | CommandStarted
    | CommandOutput
    | CommandFinished
    | CommandCancel
    | CommandStatus
    | CommandOutputRequest
    | FilesystemList
    | FilesystemRead
    | FilesystemWrite
    | FilesystemSearch
    | GitStatus
    | GitDiff
    | ProcessList
    | OperationResult
    | Error,
    Field(discriminator="type"),
]

MESSAGE_TYPES: Final = {
    "agent.hello": AgentHello,
    "agent.welcome": AgentWelcome,
    "heartbeat.ping": HeartbeatPing,
    "heartbeat.pong": HeartbeatPong,
    "command.start": CommandStart,
    "command.started": CommandStarted,
    "command.output": CommandOutput,
    "command.finished": CommandFinished,
    "command.cancel": CommandCancel,
    "command.status": CommandStatus,
    "command.output.request": CommandOutputRequest,
    "filesystem.list": FilesystemList,
    "filesystem.read": FilesystemRead,
    "filesystem.write": FilesystemWrite,
    "filesystem.search": FilesystemSearch,
    "git.status": GitStatus,
    "git.diff": GitDiff,
    "process.list": ProcessList,
    "operation.result": OperationResult,
    "error": Error,
}
