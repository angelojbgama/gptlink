"""GPTLink's shared exception hierarchy."""


class GPTLinkError(Exception):
    """Base exception for expected GPTLink domain failures."""


class ConfigurationError(GPTLinkError):
    """Raised when configuration cannot satisfy a GPTLink safety boundary."""
