"""Run the ADK eval suite for the ScenePilot research agent (live Gemini + Parallel).

    cd services/agent && uv run python scripts/adk_eval.py

Uses adk_agents/evals/research.evalset.json with adk_agents/evals/eval_config.json:
a deterministic `parallel_tool_use` metric plus an ADK rubric-based LLM judge.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parent.parent
AGENT_INIT = REPO / "adk_agents" / "scenepilot_research"  # adk 2.7 takes the agent directory
EVALSET = REPO / "adk_agents" / "evals" / "research.evalset.json"
CONFIG = REPO / "adk_agents" / "evals" / "eval_config.json"

if __name__ == "__main__":
    env = {**os.environ, "PYTHONPATH": str(SERVICE) + os.pathsep + os.environ.get("PYTHONPATH", ""), "PYTHONIOENCODING": "utf-8"}
    subprocess.call([sys.executable, "-m", "google.adk.cli", "telemetry", "disable"], env=env, cwd=str(SERVICE), stdin=subprocess.DEVNULL)
    cmd = [sys.executable, "-m", "google.adk.cli", "eval", str(AGENT_INIT), str(EVALSET), "--config_file_path", str(CONFIG), "--print_detailed_results"]
    print("$", " ".join(cmd[1:]))
    raise SystemExit(subprocess.call(cmd, env=env, cwd=str(SERVICE)))
