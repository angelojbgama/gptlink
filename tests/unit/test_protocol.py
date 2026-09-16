"""Contracts for the canonical versioned GPTLink wire protocol."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from gptlink.common.types import Capability, JobStatus, ShellKind, StreamKind
from gptlink.protocol.codec import decode_message, encode_message
from gptlink.protocol.messages import (
    MESSAGE_TYPES,
    AgentHello,
    AgentHelloPayload,
    AgentWelcome,
    AgentWelcomePayload,
    CommandCancel,
    CommandCancelPayload,
    CommandFinished,
    CommandFinishedPayload,
    CommandOutput,
    CommandOutputPayload,
    CommandOutputRequest,
    CommandOutputRequestPayload,
    CommandStart,
    CommandStarted,
    CommandStartedPayload,
    CommandStartPayload,
    CommandStatus,
    CommandStatusPayload,
    Error,
    ErrorPayload,
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
    HeartbeatPing,
    HeartbeatPingPayload,
    HeartbeatPong,
    HeartbeatPongPayload,
    OperationResult,
    OperationResultPayload,
    ProcessList,
    ProcessListPayload,
    ProtocolMessage,
)
from gptlink.protocol.version import PROTOCOL_VERSION

DEVICE_ID = UUID("69b03f7a-e10c-4ce8-956d-7b6ecb53fa49")
REQUEST_ID = UUID("bd437f02-9e68-4dc4-9742-e5e248ea0486")
JOB_ID = UUID("c862f130-0cb1-4ff5-8956-2071ee4a07b2")
TIMESTAMP = datetime(2026, 9, 15, 16, 30, tzinfo=UTC)


def envelope_fields() -> dict[str, object]:
    """Return the required hand-authored base envelope values."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": REQUEST_ID,
        "device_id": DEVICE_ID,
    }


def protocol_messages() -> list[ProtocolMessage]:
    """Return one real wire example for each registered message type."""
    return [
        AgentHello(
            **envelope_fields(),
            type="agent.hello",
            payload=AgentHelloPayload(
                token="credential",
                versions=(PROTOCOL_VERSION,),
                capabilities=(Capability.COMMAND_START,),
            ),
        ),
        AgentWelcome(
            **envelope_fields(),
            type="agent.welcome",
            payload=AgentWelcomePayload(selected_version=PROTOCOL_VERSION),
        ),
        HeartbeatPing(
            **envelope_fields(),
            type="heartbeat.ping",
            payload=HeartbeatPingPayload(timestamp=TIMESTAMP),
        ),
        HeartbeatPong(
            **envelope_fields(),
            type="heartbeat.pong",
            payload=HeartbeatPongPayload(timestamp=TIMESTAMP),
        ),
        CommandStart(
            **envelope_fields(),
            type="command.start",
            payload=CommandStartPayload(command="printf hello", shell=ShellKind.BASH, cwd="/work"),
        ),
        CommandStarted(
            **envelope_fields(),
            type="command.started",
            job_id=JOB_ID,
            payload=CommandStartedPayload(pid=42),
        ),
        CommandOutput(
            **envelope_fields(),
            type="command.output",
            job_id=JOB_ID,
            payload=CommandOutputPayload(
                sequence_number=0,
                stream=StreamKind.STDOUT,
                timestamp=TIMESTAMP,
                data="hello",
            ),
        ),
        CommandFinished(
            **envelope_fields(),
            type="command.finished",
            job_id=JOB_ID,
            payload=CommandFinishedPayload(status=JobStatus.SUCCEEDED, exit_code=0),
        ),
        CommandCancel(
            **envelope_fields(),
            type="command.cancel",
            job_id=JOB_ID,
            payload=CommandCancelPayload(reason="user requested cancellation"),
        ),
        CommandStatus(
            **envelope_fields(),
            type="command.status",
            job_id=JOB_ID,
            payload=CommandStatusPayload(),
        ),
        CommandOutputRequest(
            **envelope_fields(),
            type="command.output.request",
            job_id=JOB_ID,
            payload=CommandOutputRequestPayload(after_sequence=3),
        ),
        FilesystemList(
            **envelope_fields(),
            type="filesystem.list",
            payload=FilesystemListPayload(path="src"),
        ),
        FilesystemRead(
            **envelope_fields(),
            type="filesystem.read",
            payload=FilesystemReadPayload(path="README.md"),
        ),
        FilesystemWrite(
            **envelope_fields(),
            type="filesystem.write",
            payload=FilesystemWritePayload(path="notes.txt", data="contents"),
        ),
        FilesystemSearch(
            **envelope_fields(),
            type="filesystem.search",
            payload=FilesystemSearchPayload(path="src", query="TODO"),
        ),
        GitStatus(
            **envelope_fields(),
            type="git.status",
            payload=GitStatusPayload(path="."),
        ),
        GitDiff(
            **envelope_fields(),
            type="git.diff",
            payload=GitDiffPayload(path=".", revision="HEAD"),
        ),
        ProcessList(
            **envelope_fields(),
            type="process.list",
            payload=ProcessListPayload(),
        ),
        OperationResult(
            **envelope_fields(),
            type="operation.result",
            payload=OperationResultPayload(result={"ok": True}),
        ),
        Error(
            **envelope_fields(),
            type="error",
            payload=ErrorPayload(
                code="permission_denied", message="operation denied", retryable=False
            ),
        ),
    ]


def test_registry_contains_only_the_canonical_wire_types() -> None:
    """An invented wire label cannot become part of the public protocol."""
    assert set(MESSAGE_TYPES) == {
        "agent.hello",
        "agent.welcome",
        "heartbeat.ping",
        "heartbeat.pong",
        "command.start",
        "command.started",
        "command.output",
        "command.finished",
        "command.cancel",
        "command.status",
        "command.output.request",
        "filesystem.list",
        "filesystem.read",
        "filesystem.write",
        "filesystem.search",
        "git.status",
        "git.diff",
        "process.list",
        "operation.result",
        "error",
    }


def test_every_registered_message_round_trips_through_json_codec() -> None:
    """Each request, response, and event survives JSON with its concrete type."""
    messages = protocol_messages()

    assert {message.type for message in messages} == set(MESSAGE_TYPES)
    assert [decode_message(encode_message(message)) for message in messages] == messages


def test_decode_rejects_unknown_message_type() -> None:
    """A Gateway cannot accept an unregistered operation over the wire."""
    data = json.dumps({**envelope_fields(), "type": "command.delete", "payload": {}}, default=str)

    with pytest.raises(ValidationError):
        decode_message(data)


@pytest.mark.parametrize("message", protocol_messages())
def test_decode_requires_protocol_version_on_every_envelope(message: ProtocolMessage) -> None:
    """No decoded envelope may inherit a missing protocol version."""
    data = message.model_dump(mode="json")
    del data["protocol_version"]

    with pytest.raises(ValidationError):
        decode_message(json.dumps(data))


@pytest.mark.parametrize("invalid_version", [True, 1.0, 2, "1"])
def test_decode_accepts_only_a_real_integer_protocol_version_one(
    invalid_version: object,
) -> None:
    """Boolean, float, string, and incompatible protocol versions are not v1."""
    data = protocol_messages()[0].model_dump(mode="json")
    data["protocol_version"] = invalid_version

    with pytest.raises(ValidationError):
        decode_message(json.dumps(data))


@pytest.mark.parametrize("versions", [(True,), (1.0,), (2,), (1, 2)])
def test_agent_hello_versions_are_strict_and_coherent(versions: tuple[object, ...]) -> None:
    """A v1 hello advertises exactly the real integer version carried by its envelope."""
    with pytest.raises(ValidationError):
        AgentHelloPayload(
            token="credential",
            versions=versions,
            capabilities=(Capability.COMMAND_START,),
        )


@pytest.mark.parametrize("missing_field", ["sequence_number", "stream", "timestamp", "data"])
def test_command_output_requires_all_sequence_fields(missing_field: str) -> None:
    """Persistence cannot receive an unordered or timestamp-free output event."""
    payload = CommandOutputPayload(
        sequence_number=0,
        stream=StreamKind.STDOUT,
        timestamp=TIMESTAMP,
        data="hello",
    ).model_dump()
    del payload[missing_field]

    with pytest.raises(ValidationError):
        CommandOutputPayload(**payload)


def test_command_output_requires_job_id_and_rejects_extra_payload_data() -> None:
    """Output events retain their job correlation and cannot carry undocumented data."""
    with pytest.raises(ValidationError):
        CommandOutput(
            **envelope_fields(),
            type="command.output",
            payload=CommandOutputPayload(
                sequence_number=0,
                stream=StreamKind.STDOUT,
                timestamp=TIMESTAMP,
                data="hello",
            ),
        )

    with pytest.raises(ValidationError):
        CommandOutputPayload(
            sequence_number=0,
            stream=StreamKind.STDOUT,
            timestamp=TIMESTAMP,
            data="hello",
            extra="not allowed",
        )


def test_command_output_requires_a_timezone_aware_timestamp() -> None:
    """Chunk chronology is unambiguous across Agent and Gateway hosts."""
    with pytest.raises(ValidationError):
        CommandOutputPayload(
            sequence_number=0,
            stream=StreamKind.STDOUT,
            timestamp=datetime(2026, 9, 15, 16, 30),
            data="hello",
        )


@pytest.mark.parametrize("identifier", ["request_id", "device_id"])
def test_envelopes_require_valid_request_and_device_uuids(identifier: str) -> None:
    """A malformed or absent correlation UUID is rejected before transport."""
    missing_identifier_fields = envelope_fields()
    del missing_identifier_fields[identifier]

    with pytest.raises(ValidationError):
        AgentWelcome(
            **missing_identifier_fields,
            type="agent.welcome",
            payload=AgentWelcomePayload(selected_version=PROTOCOL_VERSION),
        )

    invalid_identifier_fields = envelope_fields()
    invalid_identifier_fields[identifier] = "not-a-uuid"

    with pytest.raises(ValidationError):
        AgentWelcome(
            **invalid_identifier_fields,
            type="agent.welcome",
            payload=AgentWelcomePayload(selected_version=PROTOCOL_VERSION),
        )


def test_job_envelopes_require_a_valid_job_uuid() -> None:
    """A command event cannot be associated with a non-UUID job identifier."""
    with pytest.raises(ValidationError):
        CommandOutput(
            **envelope_fields(),
            type="command.output",
            job_id="not-a-uuid",
            payload=CommandOutputPayload(
                sequence_number=0,
                stream=StreamKind.STDOUT,
                timestamp=TIMESTAMP,
                data="hello",
            ),
        )


def test_command_output_rejects_a_negative_sequence_number() -> None:
    """Output ordering cannot begin below the first valid sequence number."""
    with pytest.raises(ValidationError):
        CommandOutputPayload(
            sequence_number=-1,
            stream=StreamKind.STDOUT,
            timestamp=TIMESTAMP,
            data="hello",
        )


def test_command_finished_rejects_a_non_terminal_status() -> None:
    """A finished event cannot claim a job remains pending or running."""
    with pytest.raises(ValidationError):
        CommandFinishedPayload(status=JobStatus.RUNNING)


def test_models_are_immutable_and_envelopes_reject_unknown_fields() -> None:
    """Protocol data cannot mutate after validation or grow undocumented envelope fields."""
    message = protocol_messages()[1]

    with pytest.raises(ValidationError):
        message.request_id = DEVICE_ID

    with pytest.raises(ValidationError):
        AgentWelcome(
            **envelope_fields(),
            type="agent.welcome",
            payload=AgentWelcomePayload(selected_version=PROTOCOL_VERSION),
            unexpected=True,
        )
