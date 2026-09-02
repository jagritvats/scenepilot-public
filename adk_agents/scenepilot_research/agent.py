"""`adk web` entry point: an interactive ScenePilot research agent.

Run from services/agent:  uv run adk web ../../adk_agents
It exposes the same Parallel Search and Extract tools the Evidence Analyst uses. Tools are
built per invocation (fresh Parallel session + budgets) and every call is persisted as a
SearchRun/ExtractRun under run_id "adk-web", so the dev UI is as observable as the app.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SERVICE = Path(__file__).resolve().parents[2] / "services" / "agent"
if str(_SERVICE) not in sys.path:
    sys.path.insert(0, str(_SERVICE))

from google.adk.agents import LlmAgent  # noqa: E402
from google.adk.agents.callback_context import CallbackContext  # noqa: E402

from scenepilot.config import settings  # noqa: E402
from scenepilot.domain.models import ActivityEvent  # noqa: E402
from scenepilot.store.repo import Repo  # noqa: E402
from scenepilot.tools.parallel_extract import PARALLEL_EXTRACT_TOOL_DOC, ParallelExtractTool  # noqa: E402
from scenepilot.tools.parallel_search import PARALLEL_SEARCH_TOOL_DOC, ParallelSearchTool  # noqa: E402
from scenepilot.tools.parallel_session import ParallelSession, new_session_id  # noqa: E402

RUN_ID = "adk-web"
_repo = Repo()


def _log(kind: str, message: str, meta: dict) -> None:
    _repo.log(ActivityEvent(run_id=RUN_ID, project_id=None, kind=kind, message=message, meta=meta))


def _fresh_tools(callback_context: CallbackContext) -> None:
    """before_agent_callback: a new Parallel session and fresh tool budgets for every invocation."""
    session = ParallelSession(settings, session_id=new_session_id("adk"), client_model=settings.gemini_model)
    search = ParallelSearchTool(run_id=RUN_ID, project_id=None, session=session, on_search_run=_repo.save_search_run, on_event=_log, settings=settings)
    extract = ParallelExtractTool(run_id=RUN_ID, project_id=None, session=session, on_extract_run=_repo.save_extract_run, on_event=_log, settings=settings)
    search_fn = search.make_adk_tool(purpose="research", round=1, max_calls=3)
    extract_fn = extract.make_adk_tool(purpose="agent_extract", max_calls=1)
    search_fn.__doc__ = PARALLEL_SEARCH_TOOL_DOC
    extract_fn.__doc__ = PARALLEL_EXTRACT_TOOL_DOC
    callback_context._invocation_context.agent.tools = [search_fn, extract_fn]  # noqa: SLF001 — ADK exposes no public setter


root_agent = LlmAgent(
    name="scenepilot_research",
    model=settings.gemini_model,
    description="Film-production research agent grounded in live web evidence from the Parallel Search and Extract APIs.",
    instruction=(
        "You are ScenePilot's production research agent. A producer asks about the real-world feasibility of a scene "
        "(permits, drone/pyro rules, weather windows, location constraints, safety practice).\n\n"
        "TOOL RULES (Parallel Search API):\n"
        "- ALWAYS call `parallel_search` before answering.\n"
        "- `objective`: one concise, self-contained sentence naming the key entity/topic and the city, plus a source preference in words.\n"
        "- `search_queries`: EXACTLY 3 items. Each is a keyword phrase of 3 to 6 words. Vary entity names, synonyms and angles. "
        "NEVER a sentence, a question, an instruction, a quoted phrase, 'OR', or a site: operator.\n"
        "  Good: [\"Mumbai drone permission filming\", \"DGCA Digital Sky red zone\", \"Mumbai Police aerial photography NOC\"]\n"
        "  Bad: [\"What permissions are needed to fly a drone in Mumbai?\", \"site:dgca.gov.in drone\", \"\\\"Digital Sky\\\" OR \\\"UIN\\\"\"]\n"
        "- Call `parallel_search` again (max 3 calls in total) only with genuinely different angles. Use `parallel_extract` at most once "
        "when a policy page or PDF's exact wording matters.\n\n"
        "ANSWER FORMAT: four headed sections — FACTS, INFERENCES, RECOMMENDATION, UNKNOWNS. Every FACT ends with the full URL of the "
        "search result it comes from, e.g. (https://digitalsky.dgca.gov.in/...). Facts without a returned URL are not facts — move them "
        "to INFERENCES. Never invent a source."
    ),
    tools=[],
    before_agent_callback=_fresh_tools,
)
