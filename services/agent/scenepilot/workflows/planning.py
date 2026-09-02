"""Production Orchestrator — Workflow 1 (planning vertical slice).

scene → structured requirements → research questions → Parallel searches → evidence
grading → follow-up research (Search/Extract) → grounded production plan (+ deterministic readiness).

Executed as an ADK `Workflow` graph (see `graph.py`). The six stages are nodes; the
research → evaluate → research-again loop is a routed cycle rather than a `while`, which is what it
always was in behaviour. Each node calls the same step function with the same prompts, so the
graph changes who schedules the work and nothing about what it does.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from dataclasses import dataclass, field

from ..agents.schemas import EvidenceAssessmentOutput, ProductionPlanOutput, ResearchPlanOutput, SceneBreakdownOutput
from ..domain.enums import ClaimKind, EvidenceStatus, Importance, IntExt, RequirementCategory, RiskStatus, RunStatus, Severity, TimeOfDay
from ..domain.models import Candidate, Evidence, ExtractRun, PlanningState, ProductionPlan, Requirement, ResearchQuestion, Risk, Scene, SearchRun, UnresolvedQuestion
from ..services.evidence import authority_for, combined_confidence, freshness_for
from ..services.readiness import compute_readiness
from ..tools.parallel_extract import PARALLEL_EXTRACT_TOOL_DOC
from ..tools.parallel_search import PARALLEL_SEARCH_TOOL_DOC, format_extracts_for_prompt, format_results_for_prompt
from .context import RunContext
from .graph import Failure, node, run_workflow

log = logging.getLogger(__name__)

MAX_QUESTIONS = 5
MAX_FOLLOW_UP_ROUNDS = 2
ROUND1_MODE = "fast"  # cheap, sub-second fan-out; follow-ups use the deeper mode
FOLLOW_UP_MODE = None  # None -> settings.parallel_search_mode (advanced by default)
SEARCH_CONCURRENCY = 4  # round-1 questions are independent: fan out, bounded
ASSESS_CONCURRENCY = 2  # analyst loops per question run side by side (Gemini + Parallel calls interleave)
ANALYST_SEARCH_BUDGET = 2  # Parallel's guidance: bounded, deliberate follow-ups
ANALYST_EXTRACT_BUDGET = 1
MEMORY_RECALL_LIMIT = 8  # entries pulled into the planner prompt when the producer opts in
MEMORY_EXCERPT_CHARS = 400


async def run_planning(ctx: RunContext) -> None:
    run, project = ctx.run, ctx.project
    state: PlanningState = run.planning  # type: ignore[assignment]
    scene = project.scene(state.scene_id)
    run.status = RunStatus.RUNNING
    ctx.stage("breakdown", f"Planning run started for Scene {scene.number} — {scene.heading}")
    failure = Failure()
    try:
        await run_workflow(build_planning_workflow(ctx, failure), ctx, failure)
        plan = state.plan
        run.status = RunStatus.COMPLETED
        ctx.stage("completed", f"Production plan ready — readiness {plan.readiness_score}/100, {len(plan.risks)} risks, {len(plan.unresolved)} unresolved" if plan else "Production plan ready")
    except Exception as exc:  # noqa: BLE001
        log.error("planning run failed: %s\n%s", exc, traceback.format_exc())
        run.status = RunStatus.FAILED
        run.error = f"{type(exc).__name__}: {exc}"[:800]
        ctx.stage("failed", f"Planning run failed: {run.error}")


# --------------------------------------------------------------------------- #
# The graph
# --------------------------------------------------------------------------- #


@dataclass
class _Rounds:
    """Per-run scratch shared by the two nodes of the research loop.

    Not domain state — it is the loop's own bookkeeping, so it lives with the graph rather than on
    `PlanningState`, which is persisted and read by the UI.
    """

    number: int = 1  # which assessment round is running; the cycle is bounded by MAX_FOLLOW_UP_ROUNDS
    queue: list[str] | None = None  # question ids to (re)assess; None on the first pass = all of them
    latest: dict[str, EvidenceAssessmentOutput] = field(default_factory=dict)  # question id → last assessment
    tooled: set[str] = field(default_factory=set)  # questions whose analyst has already been given tools


def build_planning_workflow(ctx: RunContext | None, failure: Failure):
    """scene → questions → research ⇄ evaluate → grounded plan, as an ADK graph.

    The one cycle is the honest part: `evidence` routes back to `follow_up` while a question is
    still WEAK, CONFLICTING or MISSING *and* the analyst asked for specific follow-up queries —
    the orchestrator-guaranteed second look, drawn as an edge instead of buried in a `while`.
    """
    from google.adk.workflow import START, Workflow

    rounds = _Rounds()
    step = lambda fn, name, description: node(fn, name=name, run_ctx=ctx, failure=failure, description=description)  # noqa: E731

    breakdown = step(_step_breakdown, "breakdown", "Gemini reads the scene into typed production requirements.")
    research_plan = step(_step_research_plan, "research_plan", "Gemini turns the unknowns into a handful of answerable research questions.")
    research = step(_step_research, "research", "One Parallel Search per question, fanned out concurrently.")
    evidence = step(lambda c: _step_evidence(c, rounds), "evidence", "The analyst grades each question's evidence and may search or extract for itself.")
    follow_up = step(lambda c: _step_follow_up(c, rounds), "follow_up", "A second Parallel Search for every question the analyst could not yet support.")
    plan = step(_step_plan, "plan", "Gemini synthesises the plan; readiness is computed deterministically.")

    return Workflow(
        name="scenepilot_planning",
        description="Plan a scene against live web evidence.",
        edges=[
            (START, breakdown, research_plan, research, evidence),
            (evidence, {"follow_up": follow_up, "plan": plan}),
            (follow_up, evidence),
        ],
    )


# --------------------------------------------------------------------------- #
# The steps. Each is exactly the stage it was before being given a node to sit in.
# --------------------------------------------------------------------------- #


def _scene(ctx: RunContext):
    state: PlanningState = ctx.run.planning  # type: ignore[assignment]
    return ctx.project.scene(state.scene_id), state


async def _step_breakdown(ctx: RunContext) -> None:
    project = ctx.project
    scene, state = _scene(ctx)
    breakdown = await ctx.gemini.run_structured("scene_breakdown", _breakdown_prompt(project, scene), SceneBreakdownOutput)
    scheduled = any(i.scene_id == scene.id for d in project.shoot_days for i in d.items)
    _apply_breakdown(scene, breakdown, scheduled=scheduled, log=ctx.log)
    state.requirements = list(scene.requirements)
    ctx.log("gemini", f"{len(scene.requirements)} production requirements extracted across {len({r.category for r in scene.requirements})} categories", {"count": len(scene.requirements)})
    ctx.save_project()


async def _step_research_plan(ctx: RunContext) -> None:
    """Optionally starting from what this production already learned (Parallel Memory)."""
    scene, state = _scene(ctx)
    ctx.stage("research_plan")
    recalled = await _recall(ctx, scene) if state.used_memory else ""
    plan = await ctx.gemini.run_structured("research_planner", _research_plan_prompt(ctx.project, scene, recalled), ResearchPlanOutput)
    state.questions = _to_questions(ctx.run.id, scene, plan)
    ctx.log("gemini", f"{len(state.questions)} research questions planned", {"questions": [q.question for q in state.questions]})
    ctx.save()


async def _step_research(ctx: RunContext) -> None:
    _scene_, state = _scene(ctx)
    ctx.stage("research", f"Researching {len(state.questions)} unknowns via Parallel Search ({ROUND1_MODE} mode, session {ctx.parallel_session.session_id})")
    sem = asyncio.Semaphore(SEARCH_CONCURRENCY)

    async def round1(q: ResearchQuestion) -> SearchRun:
        async with sem:
            objective = q.rationale or q.question
            return await asyncio.to_thread(ctx.parallel.search, f"{q.question} — {objective}", q.search_queries, question_id=q.id, purpose="research", round=1, mode=ROUND1_MODE)

    results = await asyncio.gather(*(round1(q) for q in state.questions))
    for q, sr in zip(state.questions, results):
        q.search_run_ids.append(sr.id)
    ctx.save()
    if state.questions and all(sr.status == "ERROR" for sr in results):
        raise RuntimeError(f"Parallel Search unavailable for every research question ({results[0].error})")


async def _step_evidence(ctx: RunContext, rounds: _Rounds) -> str:
    """Grade this round's questions, then decide whether the graph loops or moves on.

    Questions are independent, so a round runs them side by side under a small semaphore. Only the
    *first* pass on a question gives the analyst its Parallel tools — after that it is re-reading
    results the orchestrator fetched, exactly as before.
    """
    scene, state = _scene(ctx)
    if rounds.number == 1:
        ctx.stage("evidence", "Evaluating evidence sufficiency")
    targets = [q for q in state.questions if rounds.queue is None or q.id in rounds.queue]
    sem = asyncio.Semaphore(ASSESS_CONCURRENCY)

    async def assess(q: ResearchQuestion) -> None:
        async with sem:
            await _assess_question(ctx, scene, q, state, rounds)
            ctx.save()

    await asyncio.gather(*(assess(q) for q in targets))

    unfinished = [q for q in targets if q.status != EvidenceStatus.SUPPORTED and rounds.latest.get(q.id) and rounds.latest[q.id].follow_up_queries]
    if unfinished and rounds.number <= MAX_FOLLOW_UP_ROUNDS:
        rounds.queue = [q.id for q in unfinished]
        return "follow_up"
    for q in targets:  # nothing more will be asked of these
        ctx.log("info", f"{q.id} graded {q.status.value if q.status else 'UNKNOWN'} with {len(q.evidence_ids)} evidence item(s)", {"question_id": q.id, "status": q.status.value if q.status else None})
    return "plan"


async def _step_follow_up(ctx: RunContext, rounds: _Rounds) -> None:
    """One more Parallel Search per still-unsupported question, using the analyst's own queries."""
    _scene_, state = _scene(ctx)
    for q in [q for q in state.questions if q.id in (rounds.queue or [])]:
        out = rounds.latest[q.id]
        state.follow_up_rounds += 1
        ctx.log("info", f"Evidence {q.status.value if q.status else '?'} for {q.id} → follow-up research round {rounds.number + 1}", {"question_id": q.id, "queries": out.follow_up_queries})
        sr = await asyncio.to_thread(ctx.parallel.search, out.follow_up_objective or q.question, out.follow_up_queries, question_id=q.id, purpose="follow_up", round=rounds.number + 1, mode=FOLLOW_UP_MODE)
        q.search_run_ids.append(sr.id)
    rounds.number += 1
    ctx.save()


async def _step_plan(ctx: RunContext) -> None:
    scene, state = _scene(ctx)
    ctx.stage("plan", "Synthesising grounded production plan")
    pp = await ctx.gemini.run_structured("production_planner", _plan_prompt(ctx.project, scene, state), ProductionPlanOutput)
    production_plan = _to_plan(ctx.run.id, scene, state, pp, ctx.project.plans.get(scene.id))
    state.plan = production_plan
    ctx.project.plans[scene.id] = production_plan
    ctx.save_project()


# --------------------------------------------------------------------------- #
# Evidence loop
# --------------------------------------------------------------------------- #


def _question_runs(ctx: RunContext, q: ResearchQuestion) -> tuple[list[SearchRun], list[ExtractRun]]:
    return ctx.repo.list_search_runs(ids=q.search_run_ids), ctx.repo.list_extract_runs(ids=q.extract_run_ids)


async def _assess_question(ctx: RunContext, scene: Scene, q: ResearchQuestion, state: PlanningState, rounds: _Rounds) -> None:
    """One analyst pass over one question. The graph, not this function, decides whether to loop."""
    runs, xruns = _question_runs(ctx, q)
    total = sum(len(r.results) for r in runs)
    ctx.log("info", f"Assessing {q.id}: {total} source(s) from {len(runs)} search run(s)", {"question_id": q.id})

    if runs and all(r.status == "ERROR" for r in runs):
        # Parallel was unavailable for this question: grade honestly instead of asking Gemini to invent
        q.status = EvidenceStatus.MISSING
        q.assessment = "Search unavailable — no external evidence could be retrieved for this question."
        ctx.log("warning", f"{q.id} graded MISSING: Parallel Search unavailable", {"question_id": q.id})
        return

    if q.id in rounds.tooled:
        # A later round: the analyst is re-reading results the orchestrator fetched for it, so it
        # gets no tools and no budget of its own — the same second look as before.
        out = await ctx.gemini.run_structured("evidence_analyst", _analyst_prompt(scene, q, runs, xruns), EvidenceAssessmentOutput)
        rounds.latest[q.id] = out
        _apply_assessment(ctx, q, out, runs, xruns, state)
        return
    rounds.tooled.add(q.id)

    # Agentic path: the analyst may call Parallel Search (≤2) and Parallel Extract (≤1) itself.
    search_fn = ctx.parallel.make_adk_tool(default_question_id=q.id, purpose="agent_follow_up", round=2, max_calls=ANALYST_SEARCH_BUDGET)
    extract_fn = ctx.extract.make_adk_tool(default_question_id=q.id, purpose="agent_extract", max_calls=ANALYST_EXTRACT_BUDGET)
    tools = _make_async_tools(search_fn, extract_fn)
    before_s, before_x = len(ctx.parallel.calls), len(ctx.extract.calls)

    def replay_tool_calls(calls: list[dict]) -> list[str]:
        """Re-issue recorded tool calls through the same budgeted tools; return created run ids in call order."""
        created: list[str] = []
        for c in calls:
            a = c.get("args", {})
            if c.get("name") == "parallel_search":
                res = search_fn(a.get("objective", q.question), list(a.get("search_queries", [])), q.id)
                if res.get("search_run_id"):
                    created.append(res["search_run_id"])
            elif c.get("name") == "parallel_extract":
                res = extract_fn(a.get("url", ""), a.get("objective", q.question), q.id)
                if res.get("extract_run_id"):
                    created.append(res["extract_run_id"])
        return created

    out = await ctx.gemini.run_structured("evidence_analyst", _analyst_prompt(scene, q, runs, xruns), EvidenceAssessmentOutput, tools=tools, replay_tool_calls=replay_tool_calls)
    # questions are assessed concurrently, so attribute new tool calls by question id, never by position
    new_s = [sr for sr in ctx.parallel.calls[before_s:] if sr.question_id == q.id]
    new_x = [xr for xr in ctx.extract.calls[before_x:] if xr.question_id == q.id]
    for sr in new_s:
        if sr.id not in q.search_run_ids:
            q.search_run_ids.append(sr.id)
    for xr in new_x:
        if xr.id not in q.extract_run_ids:
            q.extract_run_ids.append(xr.id)
    if new_s or new_x:
        replayed = any(r.replayed for r in [*new_s, *new_x])
        ctx.log("parallel", f"Evidence Analyst used Parallel autonomously for {q.id}: {len(new_s)} search(es), {len(new_x)} extract(s){' (replayed)' if replayed else ''}", {"question_id": q.id})
        runs, xruns = _question_runs(ctx, q)
    rounds.latest[q.id] = out
    _apply_assessment(ctx, q, out, runs, xruns, state)


def _make_async_tools(search_fn, extract_fn):
    """Async wrappers for ADK (tool calls run in threads); docstrings carry Parallel's recommended schema wording."""

    async def parallel_search(objective: str, search_queries: list[str], question_id: str = "") -> dict:
        return await asyncio.to_thread(search_fn, objective, search_queries, question_id)

    async def parallel_extract(url: str, objective: str, question_id: str = "") -> dict:
        return await asyncio.to_thread(extract_fn, url, objective, question_id)

    parallel_search.__doc__ = PARALLEL_SEARCH_TOOL_DOC
    parallel_extract.__doc__ = PARALLEL_EXTRACT_TOOL_DOC
    return [parallel_search, parallel_extract]


def _apply_assessment(ctx: RunContext, q: ResearchQuestion, out: EvidenceAssessmentOutput, runs: list[SearchRun], xruns: list[ExtractRun], state: PlanningState) -> None:
    q.status = EvidenceStatus(out.status)
    q.assessment = out.assessment
    # replace evidence for this question (latest assessment wins)
    state.evidence = [e for e in state.evidence if e.question_id != q.id]
    q.evidence_ids = []
    lookup: dict[str, SearchRun | ExtractRun] = {r.id: r for r in [*runs, *xruns]}
    dropped = 0
    for ev in out.evidence:
        resolved = _resolve_source(ev.source_ref, lookup)
        if resolved is None:
            dropped += 1
            continue
        source_run, item = resolved
        authority = authority_for(item.url)
        freshness = freshness_for(item.publish_date)
        is_extract = isinstance(source_run, ExtractRun)
        e = Evidence(
            question_id=q.id,
            search_run_id=source_run.search_run_id if is_extract else source_run.id,
            extract_run_id=source_run.id if is_extract else None,
            claim=ev.claim, source_url=item.url, source_title=item.title,
            excerpt=(item.excerpts[0] if item.excerpts else (item.full_content or "" if is_extract else ""))[:600], publish_date=item.publish_date, freshness=freshness,
            relevance=ev.relevance, authority=authority, confidence=combined_confidence(ev.confidence, authority, freshness, ev.relevance),
            kind=ClaimKind.FACT, production_implication=ev.production_implication or None,
        )
        state.evidence.append(e)
        q.evidence_ids.append(e.id)
    if dropped:
        ctx.log("warning", f"Dropped {dropped} evidence item(s) whose source reference did not match a real search or extract result", {"question_id": q.id})


def _resolve_source(ref: str, lookup: dict[str, SearchRun | ExtractRun]):
    ref = (ref or "").strip().strip("[]")
    if "#" not in ref:
        return None
    run_id, _, n = ref.rpartition("#")
    source_run = lookup.get(run_id.strip())
    if source_run is None:
        return None
    try:
        idx = int(n) - 1
    except ValueError:
        return None
    if idx < 0 or idx >= len(source_run.results):
        return None
    return source_run, source_run.results[idx]


# --------------------------------------------------------------------------- #
# Prompt builders (data → text). Prompt *instructions* live in prompts/v1.
# --------------------------------------------------------------------------- #


def _breakdown_prompt(project, scene: Scene) -> str:
    src = scene.script_text or scene.synopsis
    return (
        f"PRODUCTION: {project.title} (fictional). Base city: {project.base_city}, {project.country_code}.\n"
        f"SCENE NUMBER: {scene.number}\n"
        f"KNOWN HEADING: {scene.heading}\n\n"
        f"SCENE INPUT:\n{src}\n"
    )


def _apply_breakdown(scene: Scene, out: SceneBreakdownOutput, scheduled: bool = False, log=None) -> None:
    """Apply Gemini's breakdown. For a scene already on a shoot day, production state stays
    authoritative (duration, INT/EXT, time of day); Gemini's estimate is kept as a signal."""
    facts = out.scene
    scene.heading = facts.heading or scene.heading
    scene.synopsis = facts.synopsis or scene.synopsis
    if facts.estimated_minutes and facts.estimated_minutes > 0:
        scene.estimated_minutes_breakdown = int(facts.estimated_minutes)
    if scheduled:
        diffs = []
        if facts.int_ext != scene.int_ext.value:
            diffs.append(f"{facts.int_ext} vs scheduled {scene.int_ext.value}")
        if facts.time_of_day != scene.time_of_day.value:
            diffs.append(f"{facts.time_of_day} vs scheduled {scene.time_of_day.value}")
        if scene.estimated_minutes_breakdown and abs(scene.estimated_minutes_breakdown - scene.estimated_minutes) >= 30:
            diffs.append(f"≈{scene.estimated_minutes_breakdown} min vs {scene.estimated_minutes} min scheduled")
        if diffs and log:
            log("warning", "Breakdown differs from the committed schedule (kept schedule): " + "; ".join(diffs), {"scene_id": scene.id})
    else:
        scene.int_ext = IntExt(facts.int_ext)
        scene.time_of_day = TimeOfDay(facts.time_of_day)
        if scene.estimated_minutes_breakdown:
            scene.estimated_minutes = scene.estimated_minutes_breakdown
        scene.rain_tolerant = bool(facts.rain_tolerant)
    ref_to_id = {r.ref: f"req_{scene.id}_{i + 1}" for i, r in enumerate(out.requirements)}
    scene.requirements = [
        Requirement(
            id=ref_to_id[r.ref], scene_id=scene.id, category=RequirementCategory(r.category), description=r.description,
            importance=Importance(r.importance), source_ref=r.source_ref or None,
            depends_on=[ref_to_id[d] for d in r.depends_on if d in ref_to_id], weather_sensitive=r.weather_sensitive,
        )
        for r in out.requirements
    ]


def _shoot_window(project) -> str:
    dates = sorted(d.date for d in project.shoot_days)
    return f"{dates[0]} to {dates[-1]}" if dates else "TBD"


def _research_plan_prompt(project, scene: Scene, recalled: str = "") -> str:
    reqs = "\n".join(f"- {r.id} [{r.category.value}/{r.importance.value}] {r.description}" for r in scene.requirements)
    prompt = (
        f"PRODUCTION: {project.title} (fictional). Base city: {project.base_city}, {project.country_code}. Planned shoot window: {_shoot_window(project)}.\n"
        f"SCENE {scene.number}: {scene.heading}\nSYNOPSIS: {scene.synopsis}\n\nREQUIREMENTS:\n{reqs}\n"
    )
    # Appended only when the producer opted in, so the default prompt — and every recording keyed on
    # it — is unchanged.
    if recalled:
        prompt += (
            "\nALREADY RESEARCHED FOR THIS PRODUCTION (Parallel memory — earlier dossiers, monitors and "
            "vendor searches). Do not spend a question re-asking what these already answer; ask what they "
            "leave open, and say in the objective what is already known.\n" + recalled
        )
    return prompt


async def _recall(ctx: RunContext, scene: Scene) -> str:
    """One explicit Parallel Memory read, rendered for the planner. Never fatal."""
    from ..tools.parallel_memory import ParallelMemoryTool

    if not ctx.settings.parallel_memory_enabled:
        ctx.log("warning", "Memory recall was requested but this deployment has it disabled (SCENEPILOT_PARALLEL_MEMORY=1)", {})
        return ""
    tool = ParallelMemoryTool(ctx.project, settings=ctx.settings, on_event=ctx.log, run_id=ctx.run.id)
    read = await asyncio.to_thread(tool.retrieve, f"{scene.heading} {scene.synopsis}".strip(), MEMORY_RECALL_LIMIT)
    ctx.repo.save_memory_read(read)
    if ctx.run.planning is not None:
        ctx.run.planning.memory_entries_used = len(read.entries)
    if read.status != "OK" or not read.entries:
        ctx.log("info", f"No prior research to reuse ({read.status.lower()}) — planning from scratch", {"memory_read_id": read.id})
        return ""
    ctx.log("parallel", f"Research planner is starting from {len(read.entries)} remembered run(s) in scope {read.scope_key}", {"memory_read_id": read.id, "count": len(read.entries)})
    lines = []
    for e in read.entries:
        summary = (e.output_excerpt or e.input_excerpt or "").replace("\n", " ")[:MEMORY_EXCERPT_CHARS]
        lines.append(f"- [{e.kind}] {e.input_excerpt[:120]}\n  {summary}")
    return "\n".join(lines) + "\n"


def _to_questions(run_id: str, scene: Scene, plan: ResearchPlanOutput) -> list[ResearchQuestion]:
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    qs = sorted(plan.questions, key=lambda q: order.get(q.priority, 9))[:MAX_QUESTIONS]
    valid_req = {r.id for r in scene.requirements}
    return [
        ResearchQuestion(
            id=f"rq_{run_id[-6:]}_{i + 1}", scene_id=scene.id, question=q.question, rationale=q.objective or q.rationale,
            priority=Importance(q.priority), requirement_ids=[r for r in q.requirement_refs if r in valid_req], search_queries=q.search_queries[:3] or [q.question],
        )
        for i, q in enumerate(qs)
    ]


def _analyst_prompt(scene: Scene, q: ResearchQuestion, runs: list[SearchRun], xruns: list[ExtractRun] | None = None) -> str:
    reqs = "\n".join(f"- {r.id}: {r.description}" for r in scene.requirements if r.id in q.requirement_ids) or "- (general)"
    text = (
        f"RESEARCH QUESTION {q.id}: {q.question}\nWHY IT MATTERS: {q.rationale}\nSCENE: {scene.heading}\nREQUIREMENTS SERVED:\n{reqs}\n\n"
        f"SEARCH RESULTS (Parallel Search API):\n{format_results_for_prompt(runs)}\n"
    )
    if xruns:
        text += f"\nEXTRACTED SOURCES (Parallel Extract API — full page content):\n{format_extracts_for_prompt(xruns)}\n"
    return text


def _plan_prompt(project, scene: Scene, state: PlanningState) -> str:
    reqs = "\n".join(f"- {r.id} [{r.category.value}/{r.importance.value}] {r.description}" for r in scene.requirements)
    qs = "\n".join(f"- {q.id} [{q.status.value if q.status else 'UNKNOWN'}] {q.question}\n  assessment: {q.assessment or ''}" for q in state.questions)
    evs = "\n".join(
        f"- {e.id} ({e.authority.value}, {e.freshness.value}, conf {e.confidence:.2f}) for {e.question_id}: {e.claim} — {e.source_title or e.source_url}"
        + (f"\n  implication: {e.production_implication}" if e.production_implication else "")
        for e in state.evidence
    ) or "- (no evidence collected)"
    return (
        f"PRODUCTION: {project.title} (fictional). Base city: {project.base_city}, {project.country_code}. Planned shoot window: {_shoot_window(project)}.\n"
        f"SCENE {scene.number}: {scene.heading} ({scene.int_ext.value}, {scene.time_of_day.value}, ≈{scene.estimated_minutes} min)\nSYNOPSIS: {scene.synopsis}\n\n"
        f"REQUIREMENTS:\n{reqs}\n\nRESEARCH QUESTIONS (graded):\n{qs}\n\nEVIDENCE:\n{evs}\n"
    )


def _carry_decisions(risks: list[Risk], previous: ProductionPlan | None) -> None:
    """Keep what a producer decided about a risk the new plan raised again.

    A re-plan rebuilds `risks` from Gemini's output, so an owner and a status assigned on the
    register were dropped on the floor the next time anybody researched the scene — silently, which
    is the part that matters: the register is a decision log, and a decision log that forgets is
    worse than none.

    Matched on the title, and only the title, for the same reason `dossier.merge_facts` carries an
    acceptance only onto an identical value: a risk whose wording changed is a different claim, and
    inheriting a decision onto it would be putting words in the producer's mouth.
    """
    if previous is None:
        return
    decided = {r.title.strip().lower(): r for r in previous.risks if r.status != RiskStatus.OPEN or r.owner}
    for risk in risks:
        old = decided.get(risk.title.strip().lower())
        if old is None:
            continue
        risk.status, risk.owner = old.status, old.owner
        risk.decision_note, risk.decided_by, risk.decided_at = old.decision_note, old.decided_by, old.decided_at


def _to_plan(run_id: str, scene: Scene, state: PlanningState, out: ProductionPlanOutput, previous: ProductionPlan | None = None) -> ProductionPlan:
    valid_ev = {e.id for e in state.evidence}
    valid_req = {r.id for r in scene.requirements}

    def evs(ids: list[str]) -> list[str]:
        return [i for i in ids if i in valid_ev]

    candidates = [Candidate(scene_id=scene.id, title=c.title, description=c.description, pros=c.pros, cons=c.cons, evidence_ids=evs(c.evidence_ids)) for c in out.candidates]
    rec_idx = out.recommended_candidate_index if 0 <= out.recommended_candidate_index < len(candidates) else 0
    risks = [
        Risk(scene_id=scene.id, title=r.title, description=r.description, severity=Severity(r.severity), likelihood=r.likelihood, confidence=r.confidence,
             kind=ClaimKind.FACT if (r.kind == "FACT" and evs(r.evidence_ids)) else ClaimKind.INFERENCE, mitigations=r.mitigations,
             evidence_ids=evs(r.evidence_ids), requirement_ids=[x for x in r.requirement_ids if x in valid_req])
        for r in out.risks
    ]
    _carry_decisions(risks, previous)
    unresolved = [UnresolvedQuestion(question=u.question, why_it_matters=u.why_it_matters) for u in out.unresolved]
    for q in state.questions:
        if q.status in (EvidenceStatus.MISSING, EvidenceStatus.CONFLICTING) and not any(q.question.lower()[:40] in u.question.lower() for u in unresolved):
            unresolved.append(UnresolvedQuestion(question=q.question, why_it_matters=f"Research graded {q.status.value}", question_id=q.id))
    key_facts = [f"{f.statement} [{', '.join(evs(f.evidence_ids))}]" for f in out.key_facts if evs(f.evidence_ids)]
    demoted = [f.statement for f in out.key_facts if not evs(f.evidence_ids)]
    inferences = list(out.inferences) + demoted
    score, breakdown = compute_readiness(state.questions, state.evidence, risks, unresolved)
    return ProductionPlan(
        scene_id=scene.id, run_id=run_id, readiness_score=score, readiness=breakdown, candidates=candidates,
        recommended_candidate_id=candidates[rec_idx].id if candidates else None, recommendation=out.recommendation,
        risks=risks, unresolved=unresolved, key_facts=key_facts, inferences=inferences, evidence_ids=[e.id for e in state.evidence],
    )
