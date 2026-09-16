"""Static safety contracts for container and CI deployment artifacts."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_dockerfile_runs_gateway_as_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER gptlink" in dockerfile
    assert "gptlink gateway run" not in dockerfile
    assert '["gptlink", "gateway", "run"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile


def test_compose_has_only_scoped_data_and_local_port():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    gateway = compose["services"]["gateway"]
    assert gateway["container_name"] == "gptlink-gateway"
    assert gateway["ports"] == ["127.0.0.1:8000:8000"]
    assert gateway["volumes"] == ["gptlink-data:/data"]
    assert gateway.get("privileged") is not True
    assert gateway["read_only"] is True
    assert gateway["cap_drop"] == ["ALL"]
    serialized = repr(gateway).casefold()
    assert "docker.sock" not in serialized
    assert "/root" not in serialized
    assert "/home" not in serialized
    assert "healthcheck" in gateway
    assert "GPTLINK_MCP_ALLOWED_HOSTS" in gateway["environment"]


def test_ci_runs_all_gates_on_linux_and_windows():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]["test"]
    assert set(jobs["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    commands = "\n".join(step.get("run", "") for step in jobs["steps"])
    for gate in ("ruff check", "ruff format --check", "mypy src", "pytest -q"):
        assert gate in commands
