"""Versioned GPTLink protocol envelopes and JSON codec."""

from gptlink.protocol.codec import decode_message, encode_message
from gptlink.protocol.messages import ProtocolMessage

__all__ = ["ProtocolMessage", "decode_message", "encode_message"]
