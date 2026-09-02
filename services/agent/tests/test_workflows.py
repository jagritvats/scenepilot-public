"""End-to-end orchestrator tests with test doubles for Gemini and Parallel.

These doubles are explicit test fixtures (never used at runtime) so the orchestration,
evidence mapping, follow-up loop, plan assembly, approval and persistence paths are
exercised without network access.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from scenepilot.agents.schemas import (
    DisruptionVerificationOutput,
    EvidenceAssessmentOutput,
    ProductionPlanOutput,
    RescueExplanationOutput,
    RescueProposalOutput,
    ResearchPlanOutput,
    SceneBreakdownOutput,
)
from scenepilot.domain.enums import EvidenceStatus, RunKind, RunStatus
from scenepilot.domain.models import PlanningState, RescueState, SearchResultItem, WorkflowRun
from scenepilot.seed.nightfall import DAY4_ID, build_project, make_fixture_disruption
from scenepilot.store.db import make_engine
from scenepilot.store.repo import Repo
from scenepilot.workflows.context import RunContext
from scenepilot.workflows.planning import run_planning
from scenepilot.workflows.rescue import approve, run_rescue


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


class FakeParallel:
    """Stands in for ParallelSearchTool.search: returns canned results and records calls."""

    def __init__(self, tool):
        self.tool = tool
        self.n = 0

    def search(self, objective: str, queries: list[str], **kw: Any):
        from scenepilot.domain.models import SearchRun, utcnow

        self.n += 1
        sr = SearchRun(run_id=self.tool.run_id, project_id=self.tool.project_id, question_id=kw.get("question_id"), purpose=kw.get("purpose", "research"), round=kw.get("round", 1), objective=objective, queries=queries, mode=kw.get("mode") or "advanced", session_id=self.tool.session.session_id, client_model=self.tool.session.client_model)
        sr.results = [
            SearchResultItem(url="https://digitalsky.dgca.gov.in/airspace-map", title="DGCA Digital Sky airspace map", publish_date="2026-07-01", excerpts=["Red zones require prior permission; yellow zones need ATC clearance above 200 ft."]),
            SearchResultItem(url="https://www.reddit.com/r/drones/mumbai", title="Flying in Mumbai?", publish_date="2023-01-10", excerpts=["Someone said you can just fly at dawn."]),
        ]
        sr.status = "OK"
        sr.provider_search_id = f"search_fake_{self.n}"
        sr.finished_at = utcnow()
        self.tool.calls.append(sr)
        self.tool.session.calls.append(sr)
        self.tool.on_search_run(sr)
        self.tool.on_event("parallel", f"Parallel returned {len(sr.results)} source(s) (test double)", {"search_run_id": sr.id})
        return sr


class FakeExtract:
    """Stands in for ParallelExtractTool.extract."""

    def __init__(self, tool):
        self.tool = tool
        self.n = 0

    def extract(self, urls, objective, **kw):
        from scenepilot.domain.models import ExtractResultItem, ExtractRun, utcnow

        self.n += 1
        xr = ExtractRun(run_id=self.tool.run_id, project_id=self.tool.project_id, question_id=kw.get("question_id"), search_run_id=kw.get("search_run_id"), purpose=kw.get("purpose", "evidence_open_source"), objective=objective, urls=list(urls), session_id=self.tool.session.session_id, client_model=self.tool.session.client_model)
        xr.results = [ExtractResultItem(url=urls[0], title="Digital Sky policy", publish_date="2026-07-01", excerpts=["Red zones require prior permission from the DGCA."], full_content="# Airspace map\n\nRed zones require prior permission from the DGCA. Yellow zones need ATC clearance.")]
        xr.status = "OK"
        xr.provider_extract_id = f"extract_fake_{self.n}"
        xr.finished_at = utcnow()
        self.tool.calls.append(xr)
        self.tool.session.calls.append(xr)
        self.tool.on_extract_run(xr)
        return xr


class FakeGemini:
    """Stands in for GeminiRuntime.run_structured: returns canned structured outputs per role."""

    def __init__(self, outputs: dict[str, Any]):
        self.outputs = outputs
        self.calls: list[str] = []

    async def run_structured(self, role: str, user_text: str, schema, **kw):
        self.calls.append(role)
        out = self.outputs[role]
        if callable(out):
            out = out(user_text, len([c for c in self.calls if c == role]))
        return schema.model_validate(out)


def _repo() -> Repo:
    return Repo(make_engine("sqlite:///:memory:"))


def _analyst_output(user_text: str, nth: int) -> dict:
    # cite the first result of the first search run mentioned in the prompt
    import re

    ids = re.findall(r"### SearchRun (search_[0-9a-f]+)", user_text)
    ref = f"{ids[-1]}#1" if ids else "search_missing#1"
    xids = re.findall(r"### ExtractRun (extract_[0-9a-f]+)", user_text)
    if xids:
        return {"status": "SUPPORTED", "assessment": "The extracted policy page states the rule verbatim.", "evidence": [{"claim": "Red zones require prior permission from the DGCA", "source_ref": f"{xids[-1]}#1", "relevance": 0.95, "confidence": 0.95, "production_implication": "Apply before the shoot"}], "follow_up_objective": "", "follow_up_queries": []}
    if nth == 1:
        return {"status": "WEAK", "assessment": "Only indirect evidence so far.", "evidence": [{"claim": "Red zones need prior permission", "source_ref": ref, "relevance": 0.8, "confidence": 0.7, "production_implication": "Check the rooftop's zone"}, {"claim": "made up", "source_ref": "search_bogus#9", "relevance": 0.9, "confidence": 0.9}], "follow_up_objective": "Find the DGCA rule for red zones in Mumbai", "follow_up_queries": ["DGCA red zone permission Mumbai", "digital sky Mumbai airspace"]}
    return {"status": "SUPPORTED", "assessment": "Official map confirms permission process.", "evidence": [{"claim": "Red zones require prior permission", "source_ref": ref, "relevance": 0.95, "confidence": 0.9, "production_implication": "Apply 7+ days ahead"}], "follow_up_objective": "", "follow_up_queries": []}


PLANNING_OUTPUTS = {
    "scene_breakdown": {"scene": {"heading": "EXT. MUMBAI ROOFTOP — SUNSET", "int_ext": "EXT", "time_of_day": "SUNSET", "synopsis": "Rooftop chase", "estimated_minutes": 180, "cast_roles": ["Rider"], "equipment": ["drone"], "rain_tolerant": False}, "requirements": [
        {"ref": "R1", "category": "REGULATORY", "description": "Drone permission", "importance": "CRITICAL", "source_ref": "A drone follows", "depends_on": [], "weather_sensitive": False},
        {"ref": "R2", "category": "SAFETY", "description": "Dry rooftop", "importance": "CRITICAL", "source_ref": "Rain begins", "depends_on": ["R1"], "weather_sensitive": True},
    ]},
    "research_planner": {"questions": [
        {"ref": "Q1", "question": "What drone permissions apply over Lower Parel rooftops?", "rationale": "Drone shot is critical", "priority": "CRITICAL", "requirement_refs": ["req_sc_42_1", "req_bogus"], "objective": "Find current DGCA rules", "search_queries": ["DGCA drone Mumbai permission", "digital sky red zone"]},
        {"ref": "Q2", "question": "What is the September rain pattern in Mumbai?", "rationale": "Weather risk", "priority": "MEDIUM", "requirement_refs": ["req_sc_42_2"], "objective": "Find monsoon withdrawal timing", "search_queries": ["Mumbai September rainfall"]},
    ]},
    "evidence_analyst": _analyst_output,
    "production_planner": {
        "key_facts": [{"statement": "Red zones need permission", "evidence_ids": ["__EV__"]}, {"statement": "unsupported claim", "evidence_ids": []}],
        "inferences": ["Permission lead time affects the schedule"],
        "candidates": [{"title": "Practical rooftop with drone", "description": "Shoot for real", "pros": ["authentic"], "cons": ["permit risk"], "evidence_ids": ["__EV__"]}, {"title": "VFX fireworks", "description": "Composite", "pros": [], "cons": [], "evidence_ids": []}],
        "recommended_candidate_index": 0,
        "recommendation": "Go practical with a permit buffer.",
        "risks": [{"title": "Permit delay", "description": "DGCA turnaround", "severity": "HIGH", "likelihood": 0.4, "confidence": 0.7, "kind": "FACT", "mitigations": ["apply early"], "evidence_ids": ["__EV__"], "requirement_ids": ["req_sc_42_1", "req_nope"]}],
        "unresolved": [{"question": "Who is the rooftop owner's insurer?", "why_it_matters": "liability"}],
    },
}


# --------------------------------------------------------------------------- #
# Planning workflow
# --------------------------------------------------------------------------- #


def test_planning_workflow_end_to_end_with_doubles():
    repo = _repo()
    project = build_project()
    repo.save_project(project)
    run = WorkflowRun(project_id=project.id, kind=RunKind.PLANNING, planning=PlanningState(scene_id="sc_42"))
    repo.save_run(run)
    ctx = RunContext(repo, run, project)
    fake_parallel = FakeParallel(ctx.parallel)
    ctx.parallel.search = fake_parallel.search  # type: ignore[method-assign]
    fake_extract = FakeExtract(ctx.extract)
    ctx.extract.extract = fake_extract.extract  # type: ignore[method-assign]

    # the planner cites "__EV__" which we resolve to the first real evidence id once known
    def planner(user_text: str, nth: int):
        ev_ids = [e.id for e in run.planning.evidence]
        raw = PLANNING_OUTPUTS["production_planner"]
        import json

        return json.loads(json.dumps(raw).replace("__EV__", ev_ids[0] if ev_ids else "ev_none"))

    outputs = dict(PLANNING_OUTPUTS)
    outputs["production_planner"] = planner

    class GeminiWithToolReplay(FakeGemini):
        """On Q2's first analyst call, simulate the analyst having extracted a page (as a replayed tool call)."""

        async def run_structured(self, role, user_text, schema, **kw):
            if role == "evidence_analyst" and "rq_" in user_text and "_2:" in user_text.split("\n")[0] and kw.get("replay_tool_calls") and "ExtractRun" not in user_text:
                created = kw["replay_tool_calls"]([{"name": "parallel_extract", "args": {"url": "https://digitalsky.dgca.gov.in/airspace-map", "objective": "red zone rule", "question_id": "rq_@@@@@@_2"}}])
                assert created and created[0].startswith("extract_")
                # the orchestrator links tool-created runs to the question after this call returns (same as live)
                return schema.model_validate({"status": "SUPPORTED", "assessment": "Extracted page confirms it.", "evidence": [{"claim": "Red zones require prior permission from the DGCA", "source_ref": f"{created[0]}#1", "relevance": 0.95, "confidence": 0.95, "production_implication": "Apply early"}], "follow_up_objective": "", "follow_up_queries": []})
            return await super().run_structured(role, user_text, schema, **kw)

    ctx.gemini = GeminiWithToolReplay(outputs)  # type: ignore[assignment]

    asyncio.run(run_planning(ctx))

    assert run.status == RunStatus.COMPLETED, run.error
    # Q2's evidence came from a Parallel Extract of the policy page, linked to its origin and persisted
    q2 = next(q for q in run.planning.questions if q.id.endswith("_2"))
    ev2 = [e for e in run.planning.evidence if e.question_id == q2.id]
    assert ev2 and ev2[0].extract_run_id and ev2[0].extract_run_id == q2.extract_run_ids[0]
    assert repo.list_extract_runs(ids=q2.extract_run_ids)[0].question_id == q2.id  # real rq id, not a placeholder
    assert fake_extract.n == 1
    # every search run carries the shared session and client model
    assert all(s.session_id == ctx.parallel_session.session_id == f"scenepilot_planning_{run.id}" and s.client_model == ctx.parallel_session.client_model for s in repo.list_search_runs(run_id=run.id))
    scene = project.scene("sc_42")
    assert scene.estimated_minutes == 150 and scene.estimated_minutes_breakdown == 180  # scheduled scene keeps its committed duration
    assert scene.time_of_day.value == "SUNSET"
    assert any("Breakdown differs" in e.message for e in repo.list_activity(run_id=run.id))
    assert [r.id for r in scene.requirements] == ["req_sc_42_1", "req_sc_42_2"]
    assert scene.requirements[1].depends_on == ["req_sc_42_1"]  # refs resolved to ids

    st = run.planning
    assert len(st.questions) == 2
    q1 = st.questions[0]
    assert q1.requirement_ids == ["req_sc_42_1"]  # bogus requirement ref dropped
    # research → WEAK → follow-up → SUPPORTED: two search runs on Q1, one follow-up round
    assert q1.status == EvidenceStatus.SUPPORTED
    assert len(q1.search_run_ids) == 2
    assert st.follow_up_rounds >= 1
    # evidence with an invented source_ref was dropped; real one kept with deterministic authority
    assert all(e.source_url.startswith("https://digitalsky.dgca.gov.in") for e in st.evidence)
    assert all(e.authority.value == "OFFICIAL" for e in st.evidence)
    # persisted search runs are linked to questions
    saved = repo.list_search_runs(run_id=run.id)
    assert len(saved) == fake_parallel.n and all(s.question_id for s in saved)
    # plan assembled, facts without evidence demoted to inferences, readiness computed
    plan = project.plans["sc_42"]
    assert plan.readiness_score > 0 and plan.readiness is not None
    assert len(plan.key_facts) == 1 and "unsupported claim" in plan.inferences
    assert plan.recommended_candidate_id == plan.candidates[0].id
    assert plan.risks[0].kind.value == "FACT" and plan.risks[0].requirement_ids == ["req_sc_42_1"]
    assert any("insurer" in u.question for u in plan.unresolved)
    # persisted state round-trips
    assert repo.get_project(project.id).plans["sc_42"].readiness_score == plan.readiness_score
    kinds = {e.kind for e in repo.list_activity(run_id=run.id)}
    assert {"gemini", "parallel", "info"} <= kinds


def test_planning_workflow_marks_failure_cleanly():
    repo = _repo()
    project = build_project()
    repo.save_project(project)
    run = WorkflowRun(project_id=project.id, kind=RunKind.PLANNING, planning=PlanningState(scene_id="sc_42"))
    repo.save_run(run)
    ctx = RunContext(repo, run, project)

    class Boom:
        async def run_structured(self, *a, **k):
            raise RuntimeError("model unavailable")

    ctx.gemini = Boom()  # type: ignore[assignment]
    asyncio.run(run_planning(ctx))
    assert run.status == RunStatus.FAILED and "model unavailable" in (run.error or "")
    assert repo.get_run(run.id).status == RunStatus.FAILED


# --------------------------------------------------------------------------- #
# Rescue workflow
# --------------------------------------------------------------------------- #


def _rescue_outputs(project):
    def verifier(user_text: str, nth: int):
        import re

        ids = re.findall(r"### SearchRun (search_[0-9a-f]+)", user_text)
        return {"status": "PARTIALLY_CORROBORATED", "summary": "Showers likely in the afternoon.", "confidence": 0.7, "evidence": [{"claim": "Afternoon showers expected", "source_ref": f"{ids[0]}#1", "relevance": 0.9, "confidence": 0.8, "production_implication": "Keep exteriors before 13:00"}, {"claim": "fake", "source_ref": "nope#1", "relevance": 1, "confidence": 1}], "notes_for_planning": ["Gusts 40 km/h"]}

    return {
        "disruption_verifier": verifier,
        "rescue_planner": {"proposals": [
            {"title": "Market first", "strategy": "front-load the street", "order_scene_numbers": ["48", "31", "19", "27", "42"], "deferred_scene_numbers": []},
            {"title": "Protect the hero, drop the market", "strategy": "keep 42, defer 48", "order_scene_numbers": ["31", "19", "27", "42"], "deferred_scene_numbers": ["48"]},
        ], "reasoning": "protect golden hour"},
        "rescue_explainer": lambda user_text, nth: {"headline": "Push the rooftop past the rain, cover with Sc 27", "options": [{"label": l, "explanation": f"Explanation {l}", "trade_offs": ["some trade-off"]} for l in "ABCDEF"], "recommendation_rationale": "A wins on preservation."},
    }


def test_rescue_workflow_end_to_end_with_doubles_and_approval():
    repo = _repo()
    project = build_project()
    day = project.shoot_day(DAY4_ID)
    d = make_fixture_disruption(project.id, day.id, "rain_pm")
    project.disruptions.append(d)
    repo.save_project(project)
    run = WorkflowRun(project_id=project.id, kind=RunKind.RESCUE, rescue=RescueState(shoot_day_id=day.id, disruption_id=d.id))
    repo.save_run(run)
    ctx = RunContext(repo, run, project)
    fake_parallel = FakeParallel(ctx.parallel)
    ctx.parallel.search = fake_parallel.search  # type: ignore[method-assign]
    ctx.gemini = FakeGemini(_rescue_outputs(project))  # type: ignore[assignment]

    asyncio.run(run_rescue(ctx))

    assert run.status == RunStatus.AWAITING_APPROVAL, run.error
    st = run.rescue
    # verification: two Parallel searches, evidence with a bogus ref dropped, confidence flows into scoring
    assert fake_parallel.n == 2
    assert d.verification_status is not None and d.verification_status.value == "PARTIALLY_CORROBORATED"
    assert len(st.evidence) == 1 and d.evidence_ids == [st.evidence[0].id]
    assert st.options[0].score.confidence == 70
    # Gemini proposals validated deterministically: 'Market first' rejected by permit; duplicate of A deduped
    gemini_opts = [o for o in st.options if o.origin == "gemini"]
    assert gemini_opts and not gemini_opts[0].feasible and "Bhuleshwar" in (gemini_opts[0].rejected_reason or "")
    assert any("gemini" in o.origin and o.feasible for o in st.options)  # A carries origin deterministic+gemini
    # explanations applied by label; headline became the recommended option's title
    rec = next(o for o in st.options if o.id == st.recommended_option_id)
    assert rec.title.startswith("Push the rooftop") and rec.explanation == f"Explanation {rec.label}"
    assert st.recommendation_rationale == "A wins on preservation."
    assert day.status.value == "RECOVERY_PROPOSED"

    # approval → changeset → applied → actions → persisted
    with pytest.raises(ValueError):
        approve(ctx, next(o.id for o in st.options if not o.feasible))
    approve(ctx, st.recommended_option_id)
    assert run.status == RunStatus.APPLIED
    assert st.changeset is not None and st.changeset.applied_at is not None
    assert len(st.actions) > 10
    persisted = repo.get_project(project.id)
    assert persisted.shoot_day(DAY4_ID).status.value == "RECOVERED"
    assert repo.list_changesets(project.id)[0].id == st.changeset.id
    with pytest.raises(ValueError):
        approve(ctx, st.recommended_option_id)  # already applied


def test_internal_disruption_skips_external_verification():
    repo = _repo()
    project = build_project()
    day = project.shoot_day(DAY4_ID)
    # `vikram_late`, not `crane_failure`: both are internal and skip verification, but the crane swap
    # completes before Sc 42 opens and so reaches `nothing_to_recover` instead of an option list.
    # What is under test here is the verifier being skipped, which needs the run to get past impact.
    d = make_fixture_disruption(project.id, day.id, "vikram_late")
    project.disruptions.append(d)
    repo.save_project(project)
    run = WorkflowRun(project_id=project.id, kind=RunKind.RESCUE, rescue=RescueState(shoot_day_id=day.id, disruption_id=d.id))
    repo.save_run(run)
    ctx = RunContext(repo, run, project)
    fake_parallel = FakeParallel(ctx.parallel)
    ctx.parallel.search = fake_parallel.search  # type: ignore[method-assign]
    ctx.gemini = FakeGemini(_rescue_outputs(project))  # type: ignore[assignment]
    asyncio.run(run_rescue(ctx))
    assert run.status == RunStatus.AWAITING_APPROVAL, run.error
    assert fake_parallel.n == 0 and d.verification_status is None
    assert run.rescue.options and run.rescue.options[0].score.confidence == 50  # unverified → neutral confidence


# --------------------------------------------------------------------------- #
# ADK plumbing sanity (no network): agents and tool declarations build cleanly
# --------------------------------------------------------------------------- #


def test_adk_agents_and_tool_declarations_build():
    from google.adk.agents import LlmAgent
    from google.adk.tools import FunctionTool

    from scenepilot.agents import prompts
    from scenepilot.tools.parallel_search import ParallelSearchTool

    tool = ParallelSearchTool(run_id="r", project_id="p")
    fn = tool.make_adk_tool(default_question_id="q1")
    decl = FunctionTool(fn)._get_declaration()
    assert decl is not None and decl.name == "parallel_search"
    props = decl.parameters.properties if decl.parameters else (decl.parameters_json_schema or {}).get("properties", {})
    assert {"objective", "search_queries", "question_id"} <= set(props)

    for role, schema in [("scene_breakdown", SceneBreakdownOutput), ("research_planner", ResearchPlanOutput), ("evidence_analyst", EvidenceAssessmentOutput), ("production_planner", ProductionPlanOutput), ("disruption_verifier", DisruptionVerificationOutput), ("rescue_planner", RescueProposalOutput), ("rescue_explainer", RescueExplanationOutput)]:
        agent = LlmAgent(name=f"t_{role}", model="gemini-3.5-flash", instruction=prompts.load(role), output_schema=schema, output_key="result", tools=[fn] if role == "evidence_analyst" else [])
        assert agent.output_schema is schema


def test_agent_tool_enforces_follow_up_budget():
    from scenepilot.tools.parallel_search import ParallelSearchTool

    tool = ParallelSearchTool(run_id="r", project_id="p")
    tool.search = FakeParallel(tool).search  # type: ignore[method-assign]
    fn = tool.make_adk_tool(default_question_id="q1", max_calls=2)
    assert fn("a", ["x"])["status"] == "OK"
    assert fn("b", ["y"])["status"] == "OK"
    third = fn("c", ["z"])
    assert third["status"] == "LIMIT" and third["results"] == []
    assert len(tool.calls) == 2  # the third request never reached Parallel


def test_normalize_roundtrip_keeps_keys_stable_across_runs():
    from scenepilot.tools.normalize import denormalize, id_order, normalize, rq_run_id

    a = "### SearchRun search_0123456789 — [search_0123456789#1] rq_abc123_2 on 2026-08-24 ev_aaaaaaaaaa extract_1111111111"
    b = "### SearchRun search_ffffffffff — [search_ffffffffff#1] rq_def456_2 on 2026-09-01 ev_bbbbbbbbbb extract_2222222222"
    assert normalize(a) == normalize(b)
    out = '{"source_ref": "search_@0#1", "ids": ["ev_@1"], "q": "rq_@@@@@@_2"}'
    back = denormalize(out, id_order(b), rq_run_id(b))
    assert back == '{"source_ref": "search_ffffffffff#1", "ids": ["ev_bbbbbbbbbb"], "q": "rq_def456_2"}'


# --------------------------------------------------------------------------- #
# A recording key has to outlive the calendar
# --------------------------------------------------------------------------- #


def _rescue_prompts(anchor: str | None = None):
    """Day 4's two Gemini rescue prompts, built the way `workflows/rescue` builds them."""
    from scenepilot.seed.nightfall import reanchor_shoot_days
    from scenepilot.services.impact import analyze_impact
    from scenepilot.services.recovery import generate_candidates
    from scenepilot.workflows.rescue import _explain_prompt, _proposal_prompt

    project = build_project()
    if anchor:
        reanchor_shoot_days(project, today=anchor)
    day = project.shoot_day(DAY4_ID)
    disruption = make_fixture_disruption(project.id, day.id, "rain_pm")
    impact = analyze_impact(project, day, disruption)
    options = generate_candidates(project, day, disruption, impact, verification_confidence=0.8)
    return day, _proposal_prompt(project, day, disruption, impact, options), _explain_prompt(project, day, disruption, options)


def _gemini_key(role: str, user_text: str, schema) -> str:
    """The key `GeminiRuntime.run_structured` computes for this exact invocation."""
    from scenepilot.agents import prompts
    from scenepilot.tools.normalize import id_order, normalize
    from scenepilot.tools.recorder import Recorder

    return Recorder.key(
        "gemini",
        {
            "role": role,
            "prompt_version": prompts.DEFAULT_VERSION,
            "instruction": prompts.load(role, prompts.DEFAULT_VERSION),
            "user_text": normalize(user_text, id_order(user_text)),
            "schema": schema.__name__,
        },
    )


def test_the_key_masks_the_sun_and_leaves_every_chosen_time_alone():
    """The whole rule, on one prompt: labelled ephemeris readings go, production decisions stay."""
    from scenepilot.tools.normalize import normalize

    monday = "\n".join([
        "SHOOT DAY 4 (2026-09-01): unit call 06:30, hard wrap 22:00, golden hour 18:25–19:08",
        "DISRUPTION: Rain expected 13:00–17:00 — window 13:00–17:00 (+30 min dry-out)",
        "  16:30–18:30 Sc 42 EXT. ROOFTOP — SUNSET [EXT/SUNSET, 120 min]",
        "  LOCATION Market street — Bhuleshwar: 13:00–18:00 — traffic police permit window only",
        "  violations: soft: SUNSET scene 42 gets 35 min of golden hour instead of 43;"
        " HARD: DAY scene 31 has 12 min outside usable daylight (06:23–18:54);"
        " HARD: NIGHT scene 55 has 20 min before darkness (19:17)",
    ])
    # the same day of production, a week later: only the ephemeris moved
    monday_after = (
        monday.replace("2026-09-01", "2026-09-08").replace("18:25–19:08", "18:19–19:02")
        .replace("gets 35 min", "gets 41 min").replace("(06:23–18:54)", "(06:25–18:48)")
        .replace("has 20 min before darkness (19:17)", "has 14 min before darkness (19:10)")
    )
    assert monday != monday_after
    assert normalize(monday) == normalize(monday_after)

    out = normalize(monday)
    assert "golden hour @SOLAR@–@SOLAR@" in out
    for gone in ("18:25", "19:08", "06:23", "18:54", "19:17", "gets 35 min", "instead of 43"):
        assert gone not in out, f"{gone!r} is derived from the date and must not reach the key"
    # …and every time a person decided still keys the recording, or it would replay for another question
    for kept in ("unit call 06:30", "hard wrap 22:00", "Rain expected 13:00–17:00", "16:30–18:30 Sc 42",
                 "13:00–18:00 — traffic police permit window only", "SUNSET scene 42", "NIGHT scene 55"):
        assert kept in out, f"{kept!r} is a production decision and must keep affecting the key"


def test_a_rescue_recording_key_survives_the_shoot_day_moving():
    """A key recorded on one date still resolves a week later — the point of the whole exercise.

    The dates are pinned rather than taken from the clock so this asserts the mechanism and not the
    season: on both of them the deterministic engine reaches identical verdicts, so the sun is the
    only thing that moved. (When the sun moves far enough to *change* a verdict — a sunset scene's
    golden-hour compromise hardening into a rejection — the prompts genuinely differ and the key is
    supposed to move with them.)
    """
    day_a, proposal_a, explain_a = _rescue_prompts("2026-09-01")
    day_b, proposal_b, explain_b = _rescue_prompts("2026-09-08")

    assert day_a.date != day_b.date
    assert day_a.golden_hour_dusk != day_b.golden_hour_dusk, "the ephemeris did not move; the test proves nothing"
    assert proposal_a != proposal_b and explain_a != explain_b, "the drift never reached the prompt"

    assert _gemini_key("rescue_planner", proposal_a, RescueProposalOutput) == _gemini_key("rescue_planner", proposal_b, RescueProposalOutput)
    assert _gemini_key("rescue_explainer", explain_a, RescueExplanationOutput) == _gemini_key("rescue_explainer", explain_b, RescueExplanationOutput)


def test_todays_rescue_prompt_prints_the_sun_without_keying_on_it():
    """The same proof against whatever date the demo is actually running on."""
    from datetime import date, timedelta

    from scenepilot.tools.normalize import normalize

    day, proposal, _explain = _rescue_prompts()
    header = proposal.splitlines()[0]
    assert f"golden hour {day.golden_hour_dusk[0]}–{day.golden_hour_dusk[1]}" in header, "the sun stopped reaching the prompt"

    _day_b, proposal_b, _ = _rescue_prompts((date.fromisoformat(day.date) + timedelta(days=7)).isoformat())
    header_b = proposal_b.splitlines()[0]
    assert header != header_b
    assert normalize(header) == normalize(header_b)
    assert day.golden_hour_dusk[0] not in normalize(header)
    assert f"unit call {day.unit_call}" in normalize(header)
