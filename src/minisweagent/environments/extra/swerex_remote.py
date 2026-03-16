import asyncio
from typing import Any

from pydantic import BaseModel
from swerex.deployment.remote import RemoteDeployment
from swerex.runtime.abstract import Command as RexCommand

from minisweagent.exceptions import Submitted
from minisweagent.utils.serialize import recursive_merge


class SwerexRemoteEnvironmentConfig(BaseModel):
    host: str = "http://127.0.0.1"
    """URL of the swerex server."""
    port: int = 8000
    """Port of the swerex server."""
    auth_token: str = ""
    """Authentication token for the swerex server."""
    cwd: str = "/"
    """Working directory for command execution."""
    timeout: int = 30
    """Default command execution timeout in seconds."""
    startup_timeout: float = 10.0
    """How long to wait for server to be alive on init."""


class SwerexRemoteEnvironment:
    def __init__(self, **kwargs):
        """Connects to an already-running swerex HTTP server."""
        self.config = SwerexRemoteEnvironmentConfig(**kwargs)
        self.deployment = RemoteDeployment(
            host=self.config.host,
            port=self.config.port,
            auth_token=self.config.auth_token,
        )
        asyncio.run(self.deployment.start())
        asyncio.run(self.deployment.runtime.wait_until_alive(timeout=self.config.startup_timeout))
        # Ensure cwd exists on the remote server
        if self.config.cwd != "/":
            asyncio.run(
                self.deployment.runtime.execute(
                    RexCommand(command=f"mkdir -p {self.config.cwd}", shell=True, check=True)
                )
            )

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """Execute a command via the remote swerex server."""
        command = action.get("command", "")
        try:
            result = asyncio.run(
                self.deployment.runtime.execute(
                    RexCommand(
                        command=command,
                        shell=True,
                        check=False,
                        cwd=cwd or self.config.cwd,
                        timeout=timeout or self.config.timeout,
                        merge_output_streams=True,
                    )
                )
            )
            output = {"output": result.stdout, "returncode": result.exit_code, "exception_info": ""}
        except Exception as e:
            output = {
                "output": str(e) if str(e) else "",
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        self._check_finished(output)
        return output

    def _check_finished(self, output: dict):
        """Raises Submitted if the output indicates task completion."""
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" and output["returncode"] == 0:
            submission = "".join(lines[1:])
            raise Submitted(
                {
                    "role": "exit",
                    "content": submission,
                    "extra": {"exit_status": "Submitted", "submission": submission},
                }
            )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return recursive_merge(self.config.model_dump(), kwargs)

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                }
            }
        }

    def stop(self):
        asyncio.run(self.deployment.stop())
