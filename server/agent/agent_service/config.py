"""Settings for the agent service, from AGENT_* environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _secret(name: str) -> str:
    path = os.environ.get(f"{name}_FILE")
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get(name, "").strip()


@dataclass(frozen=True)
class AgentSettings:
    listen_host: str = "0.0.0.0"
    listen_port: int = 8800
    token: str = field(default="", repr=False)
    workspace: Path = Path("/workspace")
    mcp_config: Path = Path("/config/mcp.json")  # optional extra MCP servers (connectors)
    assistant_name: str = "Claude"
    max_background_jobs: int = 3
    # Hard spending caps per job (USD); a runaway job stops instead of burning money.
    budget_question_usd: float = 1.0
    budget_task_usd: float = 5.0
    budget_build_usd: float = 15.0
    max_turns_question: int = 40
    max_turns_task: int = 300
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "AgentSettings":
        e = os.environ.get
        return cls(
            listen_host=e("AGENT_LISTEN_HOST", cls.listen_host),
            listen_port=int(e("AGENT_LISTEN_PORT", cls.listen_port)),
            token=_secret("INTEL_AGENT_TOKEN"),
            workspace=Path(e("AGENT_WORKSPACE", str(cls.workspace))),
            mcp_config=Path(e("AGENT_MCP_CONFIG", str(cls.mcp_config))),
            assistant_name=e("INTEL_ASSISTANT_NAME", cls.assistant_name),
            max_background_jobs=int(e("AGENT_MAX_BACKGROUND_JOBS", cls.max_background_jobs)),
            budget_question_usd=float(e("AGENT_BUDGET_QUESTION_USD", cls.budget_question_usd)),
            budget_task_usd=float(e("AGENT_BUDGET_TASK_USD", cls.budget_task_usd)),
            budget_build_usd=float(e("AGENT_BUDGET_BUILD_USD", cls.budget_build_usd)),
            max_turns_question=int(e("AGENT_MAX_TURNS_QUESTION", cls.max_turns_question)),
            max_turns_task=int(e("AGENT_MAX_TURNS_TASK", cls.max_turns_task)),
            log_level=e("INTEL_LOG_LEVEL", cls.log_level).upper(),
        )
