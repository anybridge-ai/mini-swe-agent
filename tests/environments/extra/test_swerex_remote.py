from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minisweagent.environments.extra.swerex_remote import (
    SwerexRemoteEnvironment,
    SwerexRemoteEnvironmentConfig,
)
from minisweagent.exceptions import Submitted


def _make_env(**kwargs):
    """Create a SwerexRemoteEnvironment with mocked __init__ (no server required)."""
    with patch.object(SwerexRemoteEnvironment, "__init__", lambda self, **kw: None):
        env = SwerexRemoteEnvironment()
        env.config = SwerexRemoteEnvironmentConfig(**kwargs)
        return env


def test_swerex_remote_config_defaults():
    config = SwerexRemoteEnvironmentConfig()
    assert config.host == "http://127.0.0.1"
    assert config.port == 8000
    assert config.auth_token == ""
    assert config.cwd == "/"
    assert config.timeout == 30
    assert config.startup_timeout == 10.0


def test_swerex_remote_serialize():
    env = _make_env(port=9000, auth_token="secret")
    result = env.serialize()

    assert "info" in result
    assert "config" in result["info"]
    assert "environment" in result["info"]["config"]
    assert "environment_type" in result["info"]["config"]
    assert result["info"]["config"]["environment"]["port"] == 9000
    assert result["info"]["config"]["environment"]["auth_token"] == "secret"
    assert "SwerexRemoteEnvironment" in result["info"]["config"]["environment_type"]


def test_swerex_remote_execute():
    env = _make_env()

    mock_output = MagicMock()
    mock_output.stdout = "hello world\n"
    mock_output.exit_code = 0

    mock_runtime = MagicMock()
    mock_runtime.execute = AsyncMock(return_value=mock_output)

    mock_deployment = MagicMock()
    mock_deployment.runtime = mock_runtime
    env.deployment = mock_deployment

    result = env.execute({"command": "echo hello world"})

    assert result["output"] == "hello world\n"
    assert result["returncode"] == 0
    assert result["exception_info"] == ""


def test_swerex_remote_execute_raises_submitted():
    env = _make_env()

    mock_output = MagicMock()
    mock_output.stdout = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\ndiff --git a/file.py b/file.py\n"
    mock_output.exit_code = 0

    mock_runtime = MagicMock()
    mock_runtime.execute = AsyncMock(return_value=mock_output)

    mock_deployment = MagicMock()
    mock_deployment.runtime = mock_runtime
    env.deployment = mock_deployment

    with pytest.raises(Submitted) as exc_info:
        env.execute({"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git diff"})

    msg = exc_info.value.messages[0]
    assert msg["extra"]["exit_status"] == "Submitted"
    assert "diff --git" in msg["extra"]["submission"]
