"""Tests for the outbound Linux Agent primitives."""

import os
from pathlib import Path

import pytest

from gptlink.agent.client import AgentCredential, CredentialStore
from gptlink.agent.reconnect import Backoff


def test_backoff_is_capped_and_resets_after_success():
    backoff = Backoff(base=1.0, cap=30.0, jitter=0.0)
    assert [backoff.delay(i) for i in range(7)] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    backoff.reset()
    assert backoff.delay(0) == 1.0


def test_credential_store_writes_token_with_restricted_permissions(tmp_path: Path):
    path = tmp_path / "agent.json"
    store = CredentialStore(path)
    credential = AgentCredential(
        gateway="http://127.0.0.1:8000",
        device_id="desktop-dev",
        token="secret-token",
        display_name="Dev Agent",
    )
    store.save(credential)
    assert store.load() == credential
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_credential_store_rejects_invalid_shape(tmp_path: Path):
    path = tmp_path / "agent.json"
    path.write_text('{"token":"secret"}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid agent credential file"):
        CredentialStore(path).load()
