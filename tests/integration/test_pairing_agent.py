"""Pairing HTTP boundary and credential hand-off tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from gptlink.common.config import Settings
from gptlink.gateway.app import create_app
from gptlink.gateway.pairing import PairingService
from gptlink.persistence.database import Database


def test_pairing_endpoint_returns_credential_once(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    settings = Settings(
        _env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}"
    )
    app = create_app(settings, database=database)
    with TestClient(app) as client:
        offer = client.portal.call(PairingService(database).create_code)
        payload = {
            "code": offer.code,
            "metadata": {
                "display_name": "vps-dev-agent",
                "platform": "linux",
                "hostname": "vps",
                "agent_version": "0.1.0",
                "protocol_version": 1,
                "capabilities": [],
            },
        }
        response = client.post("/api/v1/pair", json=payload)
        assert response.status_code == 201
        credential = response.json()
        assert set(credential) == {"device_id", "token", "display_name"}
        assert len(credential["token"]) >= 32
        assert client.post("/api/v1/pair", json=payload).status_code == 400
