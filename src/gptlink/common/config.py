"""Typed application configuration with security-preserving defaults."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gptlink.chat.web2api import validate_loopback_url
from gptlink.common.types import PermissionLevel


class Settings(BaseSettings):
    """Configuration shared by the Gateway and Agent.

    The empty root allow-list intentionally makes a newly configured Agent
    unable to access files until its operator opts into a specific directory.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="GPTLINK_",
        extra="ignore",
    )

    env: Literal["development", "production"] = "development"
    gateway_host: str = "127.0.0.1"
    gateway_port: int = Field(default=8000, ge=1, le=65535)
    gateway_url: str = "http://127.0.0.1:8000"
    database_url: str = "sqlite+aiosqlite:///./gptlink.db"
    agent_roots: list[Path] = Field(default_factory=list)
    agent_wsl_roots: list[str] = Field(default_factory=list)
    agent_permission_level: PermissionLevel = PermissionLevel.READ_ONLY
    max_concurrent_jobs: int = Field(default=2, ge=1)
    max_command_length: int = Field(default=4096, ge=1)
    max_file_bytes: int = Field(default=1_048_576, ge=1)
    max_search_results: int = Field(default=1_000, ge=1)
    max_job_output_bytes: int = Field(default=1_048_576, ge=1)
    chat_backend: Literal["web2api"] = "web2api"
    web2api_url: str = "http://127.0.0.1:8081"
    local_audit_path: Path = Path("~/.gptlink/local-audit.jsonl")
    local_max_actions_per_turn: int = Field(default=20, ge=1, le=100)
    heartbeat_interval: float = Field(default=10, gt=0, allow_inf_nan=False)
    offline_threshold: float = Field(default=30, gt=0, allow_inf_nan=False)
    handshake_timeout: float = Field(default=10, gt=0, allow_inf_nan=False)
    mcp_token: SecretStr | None = None
    mcp_caller: str = Field(default="mcp-single-user", min_length=1, max_length=200)
    mcp_allowed_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    )
    mcp_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ]
    )

    @field_validator("mcp_token")
    @classmethod
    def require_strong_mcp_token(cls, token: SecretStr | None) -> SecretStr | None:
        if token is not None and len(token.get_secret_value()) < 32:
            raise ValueError("mcp_token must contain at least 32 characters")
        return token

    @field_validator("web2api_url")
    @classmethod
    def require_loopback_chat_backend(cls, value: str) -> str:
        return validate_loopback_url(value)

    @field_validator("agent_roots")
    @classmethod
    def reject_filesystem_roots(cls, roots: list[Path]) -> list[Path]:
        """Forbid universal POSIX and Windows roots in every environment."""
        for root in roots:
            normalized_root = root.as_posix().replace("/", "\\").rstrip("\\").casefold()
            if root == Path("/") or normalized_root == "c:":
                msg = "agent_roots must not include / or C:\\\\"
                raise ValueError(msg)
        return roots

    @field_validator("agent_wsl_roots")
    @classmethod
    def reject_wsl_universal_root(cls, roots: list[str]) -> list[str]:
        if any(root == "/" or not root.startswith("/") or "\\" in root for root in roots):
            raise ValueError("agent_wsl_roots require specific Linux absolute paths")
        return roots

    @model_validator(mode="after")
    def require_tls_in_production(self) -> "Settings":
        """Reject cleartext Gateway endpoints in production."""
        if self.env == "production" and urlparse(self.gateway_url).scheme not in {"https", "wss"}:
            msg = "production gateway_url must use HTTPS or WSS"
            raise ValueError(msg)
        if self.env == "production" and self.mcp_token is None:
            raise ValueError("production requires mcp_token")
        return self
