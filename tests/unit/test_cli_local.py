from typer.testing import CliRunner

from gptlink.cli.main import app


def test_local_help_documents_safe_chat_options() -> None:
    result = CliRunner().invoke(app, ["local", "--help"])

    assert result.exit_code == 0
    assert "--chat" in result.stdout
    assert "--conversation" in result.stdout
    assert "--read-only" in result.stdout
    assert "--write" in result.stdout
    assert "--chat-url" in result.stdout
    assert "current directory" in result.stdout


def test_local_requires_explicit_chat_mode() -> None:
    result = CliRunner().invoke(app, ["local"])

    assert result.exit_code != 0
    assert "requires --chat" in (result.stdout + result.stderr)
