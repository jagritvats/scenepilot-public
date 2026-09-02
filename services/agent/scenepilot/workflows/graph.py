"""The ADK plumbing that turns ScenePilot's orchestrators into real `google.adk.workflow` graphs.

The orchestrators were always graphs — a fixed sequence of stages with one loop in it — but they
were written as Python control flow, which meant the shape existed only in a reader's head. ADK
2.7 ships `Workflow`: a node/edge graph with routed edges, cycles, per-node timeouts and retries,
resumability and replay. Expressing the pipeline in it costs nothing at runtime and buys three
things worth having:

* the structure becomes **data** — `GET /api/agent-graph` serves the same object the engine runs,
  so the diagram in the UI cannot drift from the pipeline;
* the follow-up loop becomes a **routed cycle** rather than a `while`, which is what it always was;
* the stages become **nodes with names**, and those names are the same strings the run reports as
  its stage — so the graph highlights where a live run actually is.

Two deliberate choices:

**Leaf nodes wrap the existing steps.** Each node calls the same function the orchestrator always
called, with the same prompt text, so every recording keyed on that text still replays. ADK
schedules; the deterministic engine still decides. This is the same division of labour the product
argues for everywhere else, applied to its own control flow.

**Errors keep their identity.** A node that raises is recorded here and re-raised after the run, so
the orchestrator's own `except` still sees the original exception rather than an ADK wrapper — the
run is marked FAILED with the message a human needs.

`ParallelAgent` / `SequentialAgent` / `LoopAgent` are *not* used: ADK 2.7 deprecates all three in
favour of `Workflow`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.workflow import Workflow

    from .context import RunContext

log = logging.getLogger(__name__)

APP_NAME = "scenepilot"
USER_ID = "producer"

# A step is an async function of the ScenePilot run context. It may return a route (a string) to
# steer a conditional edge; anything else is ignored.
Step = Callable[["RunContext"], Awaitable[Any]]


class Failure:
    """Holds the first exception a node raised, so it can be re-raised outside the ADK run.

    ADK turns a node exception into a shut-down of the graph and a `ctx.error`; the orchestrators
    are written against real exceptions. This carries the original across that boundary, and makes
    every later node a no-op so a failed run does not keep spending on Gemini and Parallel.
    """

    def __init__(self) -> None:
        self.error: BaseException | None = None

    def raise_if_failed(self) -> None:
        if self.error is not None:
            raise self.error


def node(
    step: Step,
    *,
    name: str,
    run_ctx: RunContext | None,
    failure: Failure,
    description: str,
    timeout: float | None = None,
):
    """Wrap one orchestrator step as a `FunctionNode`.

    A step returning a string sets the node's route, which is how the graph's conditional edges are
    chosen. `run_ctx` is None when the graph is built only to be *described* (the API and the UI
    diagram), in which case no node is ever executed.
    """
    from google.adk.workflow import FunctionNode

    # The parameter is named `ctx` on purpose: ADK binds a node's context by looking for a
    # Context-annotated parameter and falls back to that name.
    async def run(ctx):
        if failure.error is not None:  # a previous node failed; do not keep spending
            return None
        if run_ctx is None:
            raise RuntimeError(f"node {name} was built for description only and cannot run")
        try:
            route = await step(run_ctx)
        except BaseException as exc:  # noqa: BLE001 — recorded, then re-raised to stop the graph
            failure.error = exc
            raise
        if isinstance(route, str):
            ctx.route = route
        return None

    run.__name__ = name
    run.__doc__ = description
    return FunctionNode(func=run, name=name, timeout=timeout)


def describe(wf: Workflow) -> dict[str, Any]:
    """The graph as data: what the engine will run, in the shape the UI draws.

    Serving this rather than a hand-drawn diagram is the point — a node renamed in code is renamed
    on screen, and a stage that stops existing stops being drawn.
    """
    from google.adk.workflow import START

    graph = wf.graph
    nodes = [
        {"name": n.name, "description": (n.description or "").strip().split("\n")[0]}
        for n in (graph.nodes if graph else [])
        if n.name != START.name
    ]
    edges = [
        {"from": e.from_node.name, "to": e.to_node.name, "route": e.route}
        for e in (graph.edges if graph else [])
    ]
    return {
        "name": wf.name,
        "description": (wf.description or "").strip(),
        "start": [e["to"] for e in edges if e["from"] == START.name],
        "terminal": sorted(graph._terminal_node_names) if graph else [],
        "nodes": nodes,
        "edges": edges,
    }


def catalog() -> dict[str, Any]:
    """Both orchestrators, described. Built with no run context — nothing here executes."""
    from .planning import build_planning_workflow
    from .rescue import build_rescue_workflow

    return {
        "runtime": _adk_runtime(),
        "graphs": [describe(build_planning_workflow(None, Failure())), describe(build_rescue_workflow(None, Failure()))],
    }


def _adk_runtime() -> str:
    try:
        from importlib.metadata import version

        return f"google.adk.workflow · google-adk {version('google-adk')}"
    except Exception:  # noqa: BLE001
        return "google.adk.workflow"


async def run_workflow(wf: Workflow, ctx: RunContext, failure: Failure) -> None:
    """Execute a graph through ADK's `Runner`, then re-raise whatever a node raised.

    The events ADK emits are not the product's observability surface — `ctx.log` already writes an
    activity feed the producer reads — so they are drained rather than translated. What ADK is here
    for is the scheduling, the routed cycle and the graph being real.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    sessions = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, node=wf, session_service=sessions)
    session = await sessions.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=f"{wf.name}-{ctx.run.id}")
    message = types.Content(role="user", parts=[types.Part(text=f"{wf.name} for {ctx.project.id} (run {ctx.run.id})")])
    async for _event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
        pass
    failure.raise_if_failed()
