"""The orchestrators are ADK `Workflow` graphs, and the graph the UI draws is the one that runs.

`test_workflows.py` already proves the pipelines *behave*; these tests are about the structure that
now carries them — that it is real ADK, that the loop is a routed cycle rather than a comment, that
the rescue graph ends at a human, and that `/api/agent-graph` serves the running object rather than
a picture of one.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from scenepilot.api.app import app
from scenepilot.workflows.graph import Failure, catalog, describe
from scenepilot.workflows.planning import build_planning_workflow
from scenepilot.workflows.rescue import build_rescue_workflow


def _planning():
    return build_planning_workflow(None, Failure())


def _rescue():
    return build_rescue_workflow(None, Failure())


def test_both_orchestrators_are_adk_workflow_graphs():
    from google.adk.workflow import Workflow

    for wf in (_planning(), _rescue()):
        assert isinstance(wf, Workflow)
        assert wf.graph is not None and wf.graph.edges


def test_planning_stages_are_nodes_in_pipeline_order():
    d = describe(_planning())
    assert d["name"] == "scenepilot_planning"
    assert d["start"] == ["breakdown"]
    assert {n["name"] for n in d["nodes"]} == {"breakdown", "research_plan", "research", "evidence", "follow_up", "plan"}
    assert d["terminal"] == ["plan"]
    assert all(n["description"] for n in d["nodes"])


def test_the_follow_up_loop_is_a_routed_cycle_not_a_while():
    """research → evaluate → research again, expressed the way ADK expresses it."""
    edges = {(e["from"], e["to"]): e["route"] for e in describe(_planning())["edges"]}
    assert edges[("evidence", "follow_up")] == "follow_up"  # only when a question is still unsupported
    assert edges[("evidence", "plan")] == "plan"
    assert edges[("follow_up", "evidence")] is None  # …and back round, unconditionally
    # ADK rejects a cycle with no conditional edge in it; that this graph validated is the proof
    assert _planning().graph is not None


def test_the_rescue_graph_ends_at_a_producer():
    d = describe(_rescue())
    assert d["name"] == "scenepilot_rescue"
    assert [n["name"] for n in d["nodes"]] == ["disruption", "verify", "impact", "candidates", "nothing_to_recover", "proposals", "explain", "awaiting_approval"]
    # nothing is downstream of approval: applying a ChangeSet is not a step the pipeline may take
    assert "awaiting_approval" in d["terminal"]


def test_impact_can_end_the_run_instead_of_recovering():
    """The graph's only branch, and it is upstream of every schedule the solver would enumerate.

    A disruption that touches nothing is an answer. Without this edge the pipeline reported "0
    scheduled scene(s) directly affected" and then recommended moving two scenes anyway — on Day 4 a
    repack outscores the untouched baseline 94 to 93, because `pack_day` restarts the cursor at unit
    call, and "Hold the existing schedule" packs to the same strategic key so the distinctness dedup
    dropped it from the list a producer would have compared against.
    """
    d = describe(_rescue())
    edges = {(e["from"], e["to"]): e["route"] for e in d["edges"]}
    assert edges[("impact", "candidates")] == "candidates"
    assert edges[("impact", "nothing_to_recover")] == "nothing_to_recover"
    # both ends are terminal and neither of them acts: one waits for a producer, the other tells
    # them there is nothing to wait for
    assert sorted(d["terminal"]) == ["awaiting_approval", "nothing_to_recover"]


def test_node_names_match_the_stages_a_run_reports():
    """The UI highlights the live node by matching it against `run.stage`, so the two must agree.

    `follow_up` is the one node that reports no stage of its own: it is a second lap of `evidence`,
    and a run that flickered between two stage names while looping would read as progress it is not
    making.
    """
    import re

    from scenepilot.workflows import planning, rescue

    for module, wf in ((planning, _planning()), (rescue, _rescue())):
        stages = set(re.findall(r'ctx\.stage\("(\w+)"', open(module.__file__, encoding="utf-8").read()))
        names = {n["name"] for n in describe(wf)["nodes"]}
        assert names - stages <= {"follow_up"}, f"{module.__name__}: nodes that report no stage"


def test_a_description_only_graph_refuses_to_run():
    """Built with no run context (for the API), a node must fail loudly rather than half-execute."""
    import asyncio

    import pytest

    from google.adk.workflow import FunctionNode

    node = next(n for n in _planning().graph.nodes if isinstance(n, FunctionNode) and n.name == "breakdown")
    with pytest.raises(RuntimeError, match="description only"):
        asyncio.run(node._func(ctx=None))


def test_agent_graph_route_serves_the_catalog():
    with TestClient(app) as c:
        body = c.get("/api/agent-graph").json()
    assert body == catalog()
    assert "google-adk" in body["runtime"]
    assert [g["name"] for g in body["graphs"]] == ["scenepilot_planning", "scenepilot_rescue"]


def test_a_failed_node_stops_the_graph_and_keeps_the_original_exception():
    """ADK turns a raise into a shut-down; the orchestrators are written against real exceptions."""
    import asyncio

    import pytest

    from google.adk.workflow import START, Workflow

    from scenepilot.workflows.graph import node, run_workflow

    failure = Failure()
    ran: list[str] = []

    async def boom(_ctx):
        ran.append("boom")
        raise RuntimeError("model unavailable")

    async def after(_ctx):
        ran.append("after")

    class _Ctx:  # the two attributes run_workflow needs
        class _Run:
            id = "run_test"

        class _Project:
            id = "proj_test"

        run, project = _Run(), _Project()

    ctx = _Ctx()
    wf = Workflow(
        name="failing_graph",
        edges=[(START, node(boom, name="boom", run_ctx=ctx, failure=failure, description="raises"), node(after, name="after", run_ctx=ctx, failure=failure, description="must not run"))],
    )
    with pytest.raises(RuntimeError, match="model unavailable"):
        asyncio.run(run_workflow(wf, ctx, failure))
    assert ran == ["boom"]
