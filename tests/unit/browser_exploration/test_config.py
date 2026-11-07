from pathlib import Path

import pytest
from pydantic import ValidationError

from browser_exploration.config import Settings


def test_environment_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify environment configuration.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for environment configuration.
    """
    monkeypatch.setenv("PLAYWRIGHT_MCP_URL", "http://127.0.0.1:9999/mcp")
    monkeypatch.setenv("OPENAI_MODEL", "available-model")
    monkeypatch.setenv("BROWSER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("BROWSER_MAX_STEPS", "12")
    settings = Settings()
    assert settings.mcp_url == "http://127.0.0.1:9999/mcp"
    assert settings.model == "available-model"
    assert settings.output_dir == tmp_path
    assert settings.max_steps == 12
    assert settings.retain_raw_evidence is False
    assert settings.network_restricted is False
    assert settings.langsmith_enabled is False


@pytest.mark.parametrize(
    "endpoint",
    [
        "stdio://mcp",
        "http://private.example/mcp",
        "https://user:pass@example.com/mcp",
        "http://127.0.0.1:8931/mcp?token=secret",
        "http://127.0.0.1:bad/mcp",
        "http://127.0.0.1:8931/mcp#secret",
        "http://127.0.0.1:8931/\nmcp",
    ],
)
def test_invalid_mcp_configuration(endpoint: str) -> None:
    """Verify invalid mcp configuration.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for invalid mcp configuration.
    """
    with pytest.raises(ValidationError):
        Settings(mcp_url=endpoint)


def test_remote_mcp_requires_encrypted_transport() -> None:
    """Verify remote mcp requires encrypted transport.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for remote mcp requires encrypted transport.
    """
    assert Settings(mcp_url="https://private.example/mcp").mcp_url.startswith("https")


def test_execution_budget_configuration() -> None:
    """Verify execution budget configuration.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for execution budget configuration.
    """
    with pytest.raises(ValidationError):
        Settings(max_steps=0)
    with pytest.raises(ValidationError):
        Settings(tool_timeout_seconds=-1)
