"""Parallel Memory: the production's accumulated web knowledge, read only when asked.

Covers the invariant that matters most for this layer — a disabled feature makes **no SDK call at
all** — plus the flattening of Parallel's Task/Monitor/FindAll memory union, honest degradation in
replay mode and when the beta API misbehaves, and the producer's evict path.
"""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from scenepilot.config import settings as default_settings
from scenepilot.seed.nightfall import build_project
from scenepilot.tools.parallel_memory import ParallelMemoryTool, scope_key


# --------------------------------------------------------------------------- #
# A fake Parallel client shaped exactly like parallel.types.beta.*MemoryResult
# --------------------------------------------------------------------------- #


class _TaskResult:
    kind = "task"

    def __init__(self):
        self.id = "trun_abc123"
        self.input_excerpt = "Filming at Bandra rooftop, Mumbai"
        self.output_excerpt = "Noise curfew 22:00–06:00; BMC permit required"
        self.updated_at = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)


class _Event:
    def __init__(self, eid: str, excerpt: str):
        self.event_id = eid
        self.event_group_id = "mevtgrp_1"
        self.excerpt = excerpt
        self.detected_at = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)


class _MonitorResult:
    kind = "monitor"

    def __init__(self):
        self.id = "monitor_b007"
        self.input_excerpt = "IMD warnings for Mumbai"
        self.status = "active"
        self.updated_at = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
        self.matched_events = [_Event("mevt_1", "Orange alert issued"), _Event("mevt_2", "Nowcast: heavy rain 13:00–17:00")]


class _FindAllResult:
    kind = "findall"

    def __init__(self):
        self.id = "findall_x1"
        self.input_excerpt = "Camera crane rental houses in Mumbai"
        self.matched_count = 7
        self.updated_at = datetime(2026, 8, 29, 11, 0, tzinfo=timezone.utc)


class FakeMemory:
    def __init__(self, results=None, raises: Exception | None = None):
        self._results = results if results is not None else [_TaskResult(), _MonitorResult(), _FindAllResult()]
        self._raises = raises
        self.calls: list[dict] = []
        self.evicted: list[dict] = []
        self.cleared: list[str | None] = []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return type("Resp", (), {"results": list(self._results)})()

    def evict(self, **kwargs):
        self.evicted.append(kwargs)

    def clear(self, **kwargs):
        self.cleared.append(kwargs.get("memory_scope_key"))


class FakeClient:
    def __init__(self, memory: FakeMemory):
        self.beta = type("Beta", (), {"memory": memory})()


def _live_settings(**over):
    base = {"mode": "live", "record": False, "parallel_api_key": "test-key", "parallel_memory_enabled": True}
    return replace(default_settings, **{**base, **over})


def _tool(memory: FakeMemory, settings=None, project=None):
    return ParallelMemoryTool(project or build_project(), settings=settings or _live_settings(), client=FakeClient(memory))


# --------------------------------------------------------------------------- #


def test_scope_key_is_stable_and_uses_parallels_allowed_charset():
    p = build_project()
    assert scope_key(p, _live_settings()) == f"scenepilot_{p.id}"
    p.id = "proj nightfall/2026"  # anything Parallel would reject is sanitised, never raised
    assert scope_key(p, _live_settings()) == "scenepilot_proj_nightfall_2026"


def test_retrieve_flattens_the_task_monitor_findall_union():
    mem = FakeMemory()
    read = _tool(mem).retrieve(query="noise curfew", limit=5)

    assert read.status == "OK" and len(read.entries) == 3
    assert mem.calls == [{"query": "noise curfew", "limit": 5, "kind": None, "memory_scope_key": "scenepilot_proj_nightfall"}]

    task, monitor, findall = read.entries
    assert (task.kind, task.ref_id) == ("task", "trun_abc123")
    assert "Noise curfew" in task.output_excerpt
    # a monitor carries no output_excerpt of its own — its matched events become the preview
    assert monitor.kind == "monitor" and monitor.status == "active"
    assert monitor.event_ids == ["mevt_1", "mevt_2"]
    assert "Orange alert issued" in monitor.output_excerpt and "heavy rain" in monitor.output_excerpt
    assert (findall.kind, findall.matched_count) == ("findall", 7)


def test_empty_query_is_sent_as_none_so_parallel_returns_most_recent():
    mem = FakeMemory()
    read = _tool(mem).retrieve()
    assert mem.calls[0]["query"] is None and read.query == "" and read.status == "OK"


def test_replay_mode_reports_unavailable_instead_of_inventing_entries():
    mem = FakeMemory()
    read = _tool(mem, settings=_live_settings(mode="replay")).retrieve(query="anything")
    assert read.status == "UNAVAILABLE" and read.entries == []
    assert "replay" in (read.error or "")
    assert mem.calls == []  # server-side state is never faked from a recording


def test_missing_key_reports_unavailable_without_calling_out():
    mem = FakeMemory()
    read = _tool(mem, settings=_live_settings(parallel_api_key=None)).retrieve()
    assert read.status == "UNAVAILABLE" and "PARALLEL_API_KEY" in (read.error or "") and mem.calls == []


def test_a_beta_api_that_drifts_surfaces_as_an_errored_read_not_an_exception():
    mem = FakeMemory(raises=RuntimeError("unexpected payload"))
    events: list[tuple[str, str, dict]] = []
    tool = ParallelMemoryTool(build_project(), settings=_live_settings(), client=FakeClient(mem), on_event=lambda k, m, meta: events.append((k, m, meta)))
    read = tool.retrieve(query="x")
    assert read.status == "ERROR" and "unexpected payload" in read.error and read.finished_at is not None
    assert events and events[-1][0] == "warning"


def test_evict_and_clear_pass_the_project_scope():
    mem = FakeMemory()
    tool = _tool(mem)
    tool.evict("task", "trun_abc123")
    tool.clear()
    assert mem.evicted == [{"id": "trun_abc123", "kind": "task", "memory_scope_key": "scenepilot_proj_nightfall"}]
    assert mem.cleared == ["scenepilot_proj_nightfall"]
    with pytest.raises(ValueError):
        tool.evict("scene", "sc_42")
    with pytest.raises(ValueError):
        tool.retrieve(kind="scene")


# --------------------------------------------------------------------------- #
# The gate: disabled by default, and disabled means no SDK call at all.
# --------------------------------------------------------------------------- #


def test_memory_is_disabled_by_default_and_the_api_says_how_to_enable_it(monkeypatch):
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))

    with TestClient(app_module.app) as c:
        feats = c.get("/api/features").json()["features"]
        assert feats["memory"]["enabled"] is False
        assert feats["memory"]["env"] == "SCENEPILOT_PARALLEL_MEMORY=1"

        r = c.get("/api/projects/proj_nightfall/memory")
        assert r.status_code == 501
        detail = r.json()["detail"]
        assert detail["feature"] == "memory" and detail["env"] == "SCENEPILOT_PARALLEL_MEMORY=1"

        assert c.post("/api/projects/proj_nightfall/memory/evict", json={"kind": "task", "ref_id": "trun_1"}).status_code == 501
        assert c.delete("/api/projects/proj_nightfall/memory?confirm=true").status_code == 501


def test_enabled_memory_route_persists_the_read_and_stamps_the_scope_on_the_project(monkeypatch):
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    monkeypatch.setattr(app_module, "repo", repo)
    monkeypatch.setattr(app_module, "settings", _live_settings())
    mem = FakeMemory()
    monkeypatch.setattr(app_module.ParallelMemoryTool, "client", property(lambda self: FakeClient(mem)))

    with TestClient(app_module.app) as c:
        body = c.get("/api/projects/proj_nightfall/memory?query=curfew&limit=3").json()
        assert body["scope_key"] == "scenepilot_proj_nightfall"
        assert body["read"]["status"] == "OK" and len(body["read"]["entries"]) == 3
        assert len(body["recent"]) == 1  # the read is itself persisted and observable

        assert repo.get_project("proj_nightfall").memory_scope_key == "scenepilot_proj_nightfall"
        assert repo.list_memory_reads("proj_nightfall")[0].query == "curfew"

        # clearing a scope is destructive → refuse without explicit confirmation
        assert c.delete("/api/projects/proj_nightfall/memory").status_code == 400
        assert c.delete("/api/projects/proj_nightfall/memory?confirm=true").json()["ok"] is True
        assert mem.cleared == ["scenepilot_proj_nightfall"]


def test_reset_does_not_leak_memory_reads_across_projects():
    from scenepilot.domain.models import MemoryRead
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    repo.save_project(build_project())
    repo.save_memory_read(MemoryRead(project_id="proj_nightfall", scope_key="scenepilot_proj_nightfall", query="q"))
    assert len(repo.list_memory_reads("proj_nightfall")) == 1
    repo.delete_project_data("proj_nightfall")
    assert repo.list_memory_reads("proj_nightfall") == []


# --------------------------------------------------------------------------- #
# F3b — the Research Planner may start from what the production already learned
# --------------------------------------------------------------------------- #


def test_the_planner_prompt_is_unchanged_unless_the_producer_opts_in():
    """Opt-out must be byte-identical: every Gemini recording is keyed on this prompt."""
    from scenepilot.workflows.planning import _research_plan_prompt

    p = build_project()
    scene = p.scene("sc_42")
    assert _research_plan_prompt(p, scene) == _research_plan_prompt(p, scene, "")

    with_memory = _research_plan_prompt(p, scene, "- [task] Bandra rooftop\n  Noise curfew 22:00-06:00\n")
    assert with_memory.startswith(_research_plan_prompt(p, scene))  # purely additive
    assert "ALREADY RESEARCHED FOR THIS PRODUCTION" in with_memory and "Noise curfew" in with_memory


def test_recall_feeds_remembered_runs_into_the_plan_and_counts_them(monkeypatch):
    import asyncio

    from scenepilot.domain.enums import RunKind
    from scenepilot.domain.models import PlanningState, WorkflowRun
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo
    from scenepilot.workflows import planning as planning_mod
    from scenepilot.workflows.context import RunContext

    p = build_project()
    repo = Repo(make_engine("sqlite:///:memory:"))
    repo.save_project(p)
    run = WorkflowRun(project_id=p.id, kind=RunKind.PLANNING, planning=PlanningState(scene_id="sc_42", used_memory=True))
    repo.save_run(run)
    ctx = RunContext(repo, run, p, settings=_live_settings())

    memory = FakeMemory()
    monkeypatch.setattr(
        "scenepilot.tools.parallel_memory.ParallelMemoryTool",
        lambda project, **kw: ParallelMemoryTool(project, settings=kw.get("settings"), client=FakeClient(memory), on_event=kw.get("on_event"), run_id=kw.get("run_id")),
    )

    recalled = asyncio.run(planning_mod._recall(ctx, p.scene("sc_42")))
    assert "[task]" in recalled and "Noise curfew" in recalled
    assert run.planning.memory_entries_used == 3
    assert repo.list_memory_reads(p.id)[0].entries  # the read is persisted like any other Parallel call
    assert memory.calls[0]["memory_scope_key"] == scope_key(p, _live_settings())


def test_recall_is_a_no_op_when_the_deployment_has_memory_off(monkeypatch):
    import asyncio

    from scenepilot.domain.enums import RunKind
    from scenepilot.domain.models import PlanningState, WorkflowRun
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo
    from scenepilot.workflows import planning as planning_mod
    from scenepilot.workflows.context import RunContext

    p = build_project()
    repo = Repo(make_engine("sqlite:///:memory:"))
    repo.save_project(p)
    run = WorkflowRun(project_id=p.id, kind=RunKind.PLANNING, planning=PlanningState(scene_id="sc_42", used_memory=True))
    repo.save_run(run)
    ctx = RunContext(repo, run, p, settings=_live_settings(parallel_memory_enabled=False))

    assert asyncio.run(planning_mod._recall(ctx, p.scene("sc_42"))) == ""
    assert any("disabled" in e.message for e in repo.list_activity(run_id=run.id))
