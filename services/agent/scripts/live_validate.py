"""Run both workflows LIVE against Gemini + Parallel and record responses for replay.

    cd services/agent
    uv run python scripts/live_validate.py            # planning (Sc 42) + rescue (Day 4, rain_pm)
    uv run python scripts/live_validate.py rescue     # only the rescue workflow
    uv run python scripts/live_validate.py planning   # only the planning workflow
    uv run python scripts/live_validate.py deep       # location dossier (Task) + substitutes (FindAll)

The `deep` scenarios cost real money (~$0.03 per dossier, ~$0.005 per entity search), so they run
only when asked for explicitly and only with their feature flags on.

Requires GOOGLE_API_KEY (or Vertex ADC) and PARALLEL_API_KEY in .env. Sets SCENEPILOT_RECORD=1
so every Gemini/Parallel response is saved under seed/fixtures/recordings for replay mode.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

os.environ.setdefault("SCENEPILOT_RECORD", "1")
os.environ.setdefault("SCENEPILOT_MODE", "live")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from scenepilot.config import settings  # noqa: E402
from scenepilot.domain.enums import RunKind, RunStatus  # noqa: E402
from scenepilot.domain.models import PlanningState, RescueState, WorkflowRun  # noqa: E402
from scenepilot.seed.nightfall import DAY4_ID, build_project, make_fixture_disruption  # noqa: E402
from scenepilot.services.parallel_usage import summarize  # noqa: E402
from scenepilot.store.db import make_engine  # noqa: E402
from scenepilot.store.repo import Repo  # noqa: E402
from scenepilot.workflows.context import RunContext  # noqa: E402
from scenepilot.workflows.planning import run_planning  # noqa: E402
from scenepilot.workflows.rescue import approve, run_rescue  # noqa: E402


def banner(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def dump_activity(repo: Repo, run_id: str) -> None:
    for e in repo.list_activity(run_id=run_id):
        print(f"  {e.ts.strftime('%H:%M:%S')} [{e.kind:13}] {e.message[:150]}")


def dump_usage(repo: Repo, run_id: str) -> None:
    u = summarize(repo.list_search_runs(run_id=run_id), repo.list_extract_runs(run_id=run_id))
    print(f"parallel usage: {u['searches']} searches {u['by_mode']} · {u['extracts']} extracts ({u['urls']} urls) · skus={u['usage']} · warnings={u['warnings']} · est ${u['est_cost_usd']} · session={u['session_ids']} · client_model={u['client_model']}")


def pre_extract(ctx: RunContext, url: str, objective: str, search_run_id: str | None, question_id: str | None) -> None:
    """Record the 'Open source' extract the demo will click, so replay mode has it."""
    xr = ctx.extract.extract([url], objective, question_id=question_id, search_run_id=search_run_id, purpose="evidence_open_source")
    print(f"  pre-extracted {url[:70]} → {xr.status} ({sum(len(r.full_content or '') for r in xr.results):,} chars)")


async def planning(repo: Repo) -> bool:
    banner("PLANNING · Scene 42 (live)")
    project = repo.get_project("proj_nightfall") or build_project()
    repo.save_project(project)
    run = WorkflowRun(project_id=project.id, kind=RunKind.PLANNING, mode=settings.mode, planning=PlanningState(scene_id="sc_42"))
    repo.save_run(run)
    ctx = RunContext(repo, run, project)
    t0 = time.time()
    await run_planning(ctx)
    dump_activity(repo, run.id)
    st = run.planning
    print(f"\nstatus={run.status.value} stage={run.stage} in {time.time() - t0:.0f}s · gemini calls={ctx.gemini.calls} · parallel calls={len(ctx.parallel.calls)}")
    if run.status != RunStatus.COMPLETED:
        print("ERROR:", run.error)
        return False
    print(f"requirements={len(st.requirements)} questions={len(st.questions)} search_runs={len(st.search_run_ids)} follow_up_rounds={st.follow_up_rounds} evidence={len(st.evidence)}")
    for q in st.questions:
        print(f"  {q.id} [{q.status.value if q.status else '?'}] {q.question[:90]}  (searches={len(q.search_run_ids)}, evidence={len(q.evidence_ids)})")
    plan = st.plan
    print(f"readiness={plan.readiness_score} candidates={len(plan.candidates)} risks={len(plan.risks)} unresolved={len(plan.unresolved)} facts={len(plan.key_facts)} inferences={len(plan.inferences)}")
    print("recommendation:", plan.recommendation[:300])
    dump_usage(repo, run.id)
    for q in st.questions:
        ev = next((e for e in st.evidence if e.question_id == q.id), None)
        if ev and q.status and q.status.value == "SUPPORTED":
            pre_extract(ctx, ev.source_url, q.question, ev.search_run_id, q.id)
    ctx.save()
    errors = [sr for sr in repo.list_search_runs(run_id=run.id) if sr.status == "ERROR"]
    if errors:
        print(f"WARNING: {len(errors)} Parallel search(es) errored: {errors[0].error}")
    return True


async def rescue(repo: Repo) -> bool:
    banner("RESCUE · Day 4 · rain_pm (live)")
    project = repo.get_project("proj_nightfall") or build_project()
    day = project.shoot_day(DAY4_ID)
    d = make_fixture_disruption(project.id, day.id, "rain_pm")
    project.disruptions.append(d)
    repo.save_project(project)
    run = WorkflowRun(project_id=project.id, kind=RunKind.RESCUE, mode=settings.mode, rescue=RescueState(shoot_day_id=day.id, disruption_id=d.id))
    repo.save_run(run)
    ctx = RunContext(repo, run, project)
    t0 = time.time()
    await run_rescue(ctx)
    dump_activity(repo, run.id)
    st = run.rescue
    print(f"\nstatus={run.status.value} stage={run.stage} in {time.time() - t0:.0f}s · gemini calls={ctx.gemini.calls} · parallel calls={len(ctx.parallel.calls)}")
    if run.status != RunStatus.AWAITING_APPROVAL:
        print("ERROR:", run.error)
        return False
    print(f"verification={d.verification_status.value if d.verification_status else None} conf={d.verification_confidence} evidence={len(st.evidence)}")
    print("summary:", (d.verification_summary or "")[:300])
    for o in st.options:
        print(f"  [{o.label}] {'feasible' if o.feasible else 'REJECTED'} total={o.score.total if o.score else '-'} origin={o.origin} :: {o.title[:80]}")
        if o.explanation:
            print(f"       {o.explanation[:160]}")
    print("rationale:", st.recommendation_rationale[:400])
    dump_usage(repo, run.id)
    if st.evidence:
        ev = st.evidence[0]
        pre_extract(ctx, ev.source_url, f"{d.title}: what does this page say about the disruption and its timing?", ev.search_run_id, None)
        ctx.save()
    approve(ctx, st.recommended_option_id)
    print(f"approved → {run.status.value}; changeset changes={len(st.changeset.changes)} actions={len(st.actions)}")
    return True


async def deep(repo: Repo) -> bool:
    """F1 + F2: one location dossier and one substitute search, both explicitly requested."""
    from scenepilot.services.dossier import map_facts, merge_facts
    from scenepilot.tools.parallel_findall import ParallelFindAllTool
    from scenepilot.tools.parallel_memory import ParallelMemoryTool, scope_key
    from scenepilot.tools.parallel_task import ParallelTaskTool

    if not (settings.parallel_task_enabled or settings.parallel_findall_enabled or settings.parallel_memory_enabled):
        print("\n=== deep === skipped: set SCENEPILOT_PARALLEL_TASK / _FINDALL / _MEMORY to 1 to run these")
        return True

    project = repo.get_project("proj_nightfall") or build_project()
    repo.save_project(project)
    scope = scope_key(project, settings) if settings.parallel_memory_enabled else None
    ok = True

    if settings.parallel_task_enabled:
        print(f"\n=== dossier === {settings.parallel_task_processor} on the hero rooftop (~$0.03)")
        t0 = time.time()
        tool = ParallelTaskTool(project, memory_scope_key=scope, on_event=lambda k, m, meta: print(f"  [{k}] {m[:110]}"))
        tr = tool.dossier(project.resource("loc_rooftop"))
        repo.save_task_run(tr)
        print(f"status={tr.status} in {time.time() - t0:.0f}s fields={len(tr.output)} basis={len(tr.basis)}")
        facts = map_facts(tr, project)
        merge_facts(project, "loc_rooftop", facts)
        repo.save_project(project)
        for f in facts:
            print(f"  [{f.binding.value:8}] {f.label}: {f.value[:60]} rule={f.rule.kind if f.rule else '-'}")
        ok = ok and tr.status in {"OK", "REPLAY"}

    if settings.parallel_findall_enabled:
        print(f"\n=== substitutes === {settings.parallel_findall_mode} for the crane (~$0.005 entity_search / ~$0.49 findall)")
        t0 = time.time()
        fa = ParallelFindAllTool(project, memory_scope_key=scope, on_event=lambda k, m, meta: print(f"  [{k}] {m[:110]}"))
        fr = fa.find_substitutes(project.resource("eq_crane"), shoot_day_id=DAY4_ID)
        repo.save_findall_run(fr)
        print(f"status={fr.status} in {time.time() - t0:.0f}s candidates={len(fr.candidates)}")
        for v in fr.candidates[:5]:
            print(f"  - {v.name[:44]:44} {v.url[:50]}")
        ok = ok and fr.status in {"OK", "REPLAY"} and bool(fr.candidates)

    if settings.parallel_memory_enabled:
        print("\n=== memory === reading back what this production has learned")
        read = ParallelMemoryTool(project, on_event=lambda k, m, meta: print(f"  [{k}] {m[:110]}")).retrieve(limit=10)
        repo.save_memory_read(read)
        print(f"status={read.status} entries={len(read.entries)} scope={read.scope_key}")
        for e in read.entries[:5]:
            print(f"  - {e.kind:8} {e.ref_id[:24]:24} {e.input_excerpt[:50]}")

    print("\nParallel usage (deep):", summarize([], [], repo.list_task_runs(project_id=project.id), repo.list_findall_runs(project_id=project.id)))
    return ok


async def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"mode={settings.mode} record={settings.record} model={settings.gemini_model} gemini={settings.gemini_configured} parallel={settings.parallel_configured} search_mode={settings.parallel_search_mode}")
    if not (settings.gemini_configured and settings.parallel_configured):
        print("Both GOOGLE_API_KEY (or Vertex) and PARALLEL_API_KEY are required for live validation.")
        return 2
    repo = Repo(make_engine(f"sqlite:///{(settings.data_dir / 'live_validate.db').as_posix()}"))
    ok = True
    # Rescue first: it is recorded against the seeded production state (the demo's reset → rescue path).
    if which in ("all", "rescue"):
        ok = await rescue(repo) and ok
    if which in ("all", "planning"):
        ok = await planning(repo) and ok
    if which in ("all", "deep"):
        ok = await deep(repo) and ok
    from scenepilot.tools.recorder import Recorder

    rec = Recorder(settings.recordings_dir, "replay", False)
    print(f"\nrecordings: gemini={len(rec.list_keys('gemini'))} parallel_search={len(rec.list_keys('parallel_search'))} parallel_extract={len(rec.list_keys('parallel_extract'))} parallel_task={len(rec.list_keys('parallel_task'))} → {settings.recordings_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
