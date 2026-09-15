"""JSON encoding and discriminated decoding for GPTLink messages."""

from pydantic import TypeAdapter

from gptlink.protocol.messages import ProtocolMessage

_MESSAGE_ADAPTER = TypeAdapter(ProtocolMessage)


def encode_message(message: ProtocolMessage) -> str:
    """Serialize a validated protocol envelope to compact JSON."""
    return message.model_dump_json()


def decode_message(data: str) -> ProtocolMessage:
    """Validate JSON and select its concrete envelope from the wire ``type``."""
    return _MESSAGE_ADAPTER.validate_json(data)
