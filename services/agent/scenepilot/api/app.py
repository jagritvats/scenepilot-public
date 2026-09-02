"""FastAPI service for ScenePilot."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..config import settings
from ..domain.enums import DisruptionType, FactBinding, IntExt, ResourceType, RiskStatus, RunKind, RunStatus, ShootDayStatus, TimeOfDay
from ..domain.models import ActivityEvent, Availability, Disruption, FactChange, MonitorRecord, PlanningState, ProductionBrief, RescueState, Scene, ScheduleItem, WorkflowRun, utcnow
from ..seed.migrate import migrate_seed_state
from ..seed.nightfall import DAY4_ID, DISRUPTION_FIXTURES, PROJECT_ID, build_project, make_fixture_disruption, reanchor_shoot_days
from ..seed.warm import warm_demo_state
from ..services.callsheet import build_call_sheet
from ..services.oneliner import build_one_liner, one_liner_moves
from ..services.export_mmsx import generate_mmsx_xml
from ..services.insurance_dossier import compile_insurance_dossier
from ..services.dossier import binding_facts, location_resources, map_facts, merge_facts
from ..services.weather import map_timeline
from ..ingestion.breakdown_agent import map_breakdown_to_elements, run_breakdown_agent
from ..ingestion.dood import (
    DOOD_CODES,
    UNMODELLED_CODES,
    build_dood_matrix,
    dood_delta,
    dood_totals,
    unlinked_characters,
)
from ..ingestion.parsers import parse_screenplay
from ..services.ephemeris import city_ephemeris
from ..services.completion import day_completion
from ..services.day_cost import day_cost, production_cost_strip
from ..services.dpr import build_dpr
from ..services.movement_order import build_movement_order
from ..services.sides import build_sides
from ..services.risk_register import build_risk_register, find_risk
from ..services.heatmap import build_heatmap
from ..services.recall import recall_view
from ..services.revert import RevertRefused, revert_changeset, stand_down
from ..services.commit_ripple import CommitRefused, commit_board, commit_placement, materialize_pickup_day, pending_clearance
from ..services.geo import day_geography
from ..services.labor_rules import DGA_SAG_PACK, FWICE_CINTAA_PACK, active_pack, active_preset, get_rule_pack
from ..services.impact import applicability
from ..services.wrap import WrapOutcome, WrapRefused, wrap_day
from ..services.schedule import ValidationContext, is_available, validate_schedule
from ..services.timeutil import overlaps, to_minutes
from ..services.multiday_solver import resolve_deferred_scenes_multiday
from ..dispatch.delivery import CHANNELS, SIMULATION_NOTE, acknowledge_dispatch, dispatch_roster, generate_crew_dispatches, get_dispatches_for_day, mark_dispatch_read, re_ping_unacknowledged
from ..services.fact_watch import SIMULATED_SNAPSHOT, adopt_change, changes_from_recheck, changes_from_snapshot, dismiss_change, pending_changes
from ..services.monitor_ingest import SIMULATED_EVENTS, draft_from_event
from ..tools.parallel_memory import ParallelMemoryTool, scope_key as memory_scope_key
from ..tools.parallel_findall import ParallelFindAllTool
from ..tools.parallel_task import ParallelTaskTool
from ..tools.parallel_monitor import ParallelMonitorTool, monitor_queries
from .deps import feature_state, require_budget, require_capability, require_feature, require_parallel_key
from ..services.budget import call_budget
from ..services.parallel_usage import summarize
from ..store.repo import Repo
from ..tools.recorder import Recorder, ReplayMiss
from ..workflows.context import RunContext
from ..workflows.graph import catalog as graph_catalog
from ..workflows.planning import run_planning
from ..workflows.rescue import approve, run_rescue

log = logging.getLogger("scenepilot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

repo = Repo()
_tasks: set[asyncio.Task] = set()


def _ensure_seed() -> None:
    existing = repo.get_project(PROJECT_ID)
    if existing is None:
        repo.save_project(build_project())
        log.info("Seeded %s", PROJECT_ID)
    else:
        _refresh(existing)
    _warm(repo.get_project(PROJECT_ID))


def _refresh(p) -> int:
    """Put the hero day back on today, and the stored project back in step with the seed.

    Two problems with the same root: with a persistent `DATABASE_URL` the seed is *found*, never
    rebuilt. Its dates therefore age with the deployment until "SHOOT DAY 4" is a week in the past
    and the monitor queries name a date that has been and gone; and every field the seed grew after
    that database was written stays null forever on the one deployment anybody looks at. Sliding the
    whole schedule keeps the day-to-day gaps the turnaround rule reads; `migrate_seed_state` fills in
    what a fresh build would have had. Neither rewrites anything a producer decided.

    Called on read as well as at startup, because startup is not enough: `--min-instances 1
    --no-cpu-throttling` plus a persistent database is a process that can live through a fortnight of
    judging without booting once, and the drift it was written to prevent happens at midnight, not at
    boot. Both checks are pure comparisons, so the read path costs no write until the request that
    first crosses a midnight in Mumbai — or the first request after a deploy that added a field.
    """
    shift = reanchor_shoot_days(p)
    notes = migrate_seed_state(p)
    if not shift and not notes:
        return 0
    repo.save_project(p)
    if shift:
        day = p.shoot_day(DAY4_ID)
        log.info("Re-anchored %s by %+d day(s); day 4 is %s", PROJECT_ID, shift, day.date)
        _log_project(p, "info", f"Re-anchored the shoot schedule by {shift:+d} day(s) so Shoot Day {day.day_number} is today ({day.date}) — dates only; nothing this production decided was touched", {"shift_days": shift, "day_4_date": day.date})
    for note in notes:
        log.info("Migrated %s: %s", PROJECT_ID, note)
        _log_project(p, "info", note, {"seed": "migration"})
    return shift


def _warm(p) -> list[str]:
    """Replay the bundled screenplay and dossier recordings into a cold project (idempotent).

    Announced in the activity feed rather than done silently: a fact that can reject a scene has to
    say where it came from, and "restored from a recording" is part of where it came from.
    """
    if p is None:
        return []
    notes = warm_demo_state(repo, p, settings)
    for note in notes:
        _log_project(p, "info", note, {"seed": "warm"})
    return notes


@asynccontextmanager
async def lifespan(app: FastAPI):
    call_budget.reset()
    _ensure_seed()
    yield


# The docs endpoints and a cross-origin allowance are both off unless this is a dev box. All browser
# traffic reaches the service same-origin through the Next proxy, which sends no Origin header, so a
# wildcard bought nothing and made every priced POST cross-site drivable from a public URL.
app = FastAPI(
    title="ScenePilot Agent Service", version="0.1.0", lifespan=lifespan,
    docs_url="/docs" if settings.dev_mode else None,
    redoc_url="/redoc" if settings.dev_mode else None,
    openapi_url="/openapi.json" if settings.dev_mode else None,
)
if settings.allowed_origins:
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_methods=["GET", "POST", "DELETE"], allow_headers=["content-type"])


def _project(project_id: str):
    p = repo.get_project(project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    _refresh(p)
    return p


def _run(run_id: str) -> WorkflowRun:
    r = repo.get_run(run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return r


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# --------------------------------------------------------------------------- #
# Health / meta
# --------------------------------------------------------------------------- #


@app.get("/api/health")
def health() -> dict[str, Any]:
    rec = Recorder(settings.recordings_dir, settings.mode, settings.record)
    return {
        "ok": True,
        "mode": settings.mode,
        "record": settings.record,
        "gemini_model": settings.gemini_model,
        "gemini_configured": settings.gemini_configured,
        "parallel_configured": settings.parallel_configured,
        "parallel_search_mode": settings.parallel_search_mode,
        "parallel_client_model": settings.gemini_model,
        # Search and Extract are always on; Task, FindAll and Memory are flagged; Monitor is gated on
        # having a reachable webhook rather than on a flag, so it is not in `parallel_features` and
        # used to be missing from this list entirely. It is called at runtime like the rest
        # (`tools/parallel_monitor.py`), and the README's headline claim is that all six are — so the
        # one payload a reader greps has to be able to say six, or the claim reads as inflated.
        "parallel_apis": ["search", "extract", *sorted(settings.parallel_features | ({"monitor"} if feature_state(settings)["monitors"]["enabled"] else set()))],
        # Parallel only. `feature_state` also carries the irreversible-write capabilities, and this
        # key is what a reader takes as the list of Parallel APIs this deployment speaks — so a
        # shoot-day wrap appearing here would be a false claim about the integration.
        # `GET /api/features` returns both, which is where the UI reads them from.
        "parallel_features": {k: v for k, v in feature_state(settings).items() if v.get("kind") != "write"},
        "database": settings.database_url.split("://")[0],
        "recordings": {"gemini": len(rec.list_keys("gemini")), "parallel_search": len(rec.list_keys("parallel_search")), "parallel_extract": len(rec.list_keys("parallel_extract"))},
        "adk": _adk_version(),
    }


def _adk_version() -> str:
    try:
        from importlib.metadata import version

        return f"google-adk {version('google-adk')} · parallel-web {version('parallel-web')}"
    except Exception:  # noqa: BLE001
        return "unknown"


@app.get("/api/agent-graph")
def agent_graph() -> dict[str, Any]:
    """The two orchestrators as ADK `Workflow` graphs — the same objects the engine runs.

    Served rather than drawn so the diagram cannot drift from the pipeline: rename a node in code
    and it is renamed on screen; delete a stage and it stops being drawn. Node names are the same
    strings a live run reports as its stage, which is what lets the UI highlight where a run is.
    """
    return graph_catalog()


# --------------------------------------------------------------------------- #
# Projects & scenes
# --------------------------------------------------------------------------- #


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    out = []
    for p in repo.list_projects():
        _refresh(p)
        plans = list(p.plans.values())
        out.append({
            "id": p.id, "title": p.title, "synthetic": p.synthetic, "logline": p.logline, "base_city": p.base_city,
            "scene_count": len(p.scenes), "shoot_day_count": len(p.shoot_days),
            "readiness": {sid: pl.readiness_score for sid, pl in p.plans.items()},
            "avg_readiness": round(sum(pl.readiness_score for pl in plans) / len(plans)) if plans else None,
            "shoot_days": [{"id": d.id, "day_number": d.day_number, "date": d.date, "status": d.status.value, "scene_count": len(d.items)} for d in p.shoot_days],
            "updated_at": p.updated_at,
        })
    return out


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    p = _project(project_id)
    runs = repo.list_runs(project_id)
    return {"project": p.model_dump(mode="json"), "runs": [{"id": r.id, "kind": r.kind.value, "status": r.status.value, "stage": r.stage, "created_at": r.created_at, "scene_id": r.planning.scene_id if r.planning else None, "shoot_day_id": r.rescue.shoot_day_id if r.rescue else None} for r in runs]}


# "Reset demo state" is the mid-demo safety button, so it may not become a spinner on somebody
# else's API: every cancel gets a short client timeout and no retry, and the whole sweep gets a wall
# clock. Worst case the button costs RESET_CANCEL_BUDGET_S + one call, not the 900 s Cloud Run
# ceiling — and a monitor the sweep never reached is reported by id like one that refused.
RESET_CANCEL_BUDGET_S = 8.0
RESET_CANCEL_CALL_TIMEOUT_S = 3.0


def _cancel_monitors(p, budget_s: float | None = None) -> tuple[list[str], list[str]]:
    """Cancel this project's live Parallel monitors. Best effort: returns (cancelled, failed) ids.

    A monitor is a server-side object at Parallel that keeps executing — and billing — long after
    the record of it here is thrown away, so dropping the list without cancelling is a charge nobody
    can see. Nothing here may raise and nothing here may hang: a monitor we could not reach in the
    time available is reported by id so it can be cancelled by hand (`POST /monitors/{id}/cancel`),
    and the reset still happens.
    """
    live = [m for m in p.monitors if m.status == "active"]
    if not live or not settings.parallel_configured:
        return [], []
    budget_s = RESET_CANCEL_BUDGET_S if budget_s is None else budget_s
    cancelled, failed = [], []
    try:
        tool = ParallelMonitorTool(settings=settings, timeout=RESET_CANCEL_CALL_TIMEOUT_S, max_retries=0)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not build a Parallel monitor client to cancel %d monitor(s): %s", len(live), exc)
        return [], [m.id for m in live]
    deadline = time.monotonic() + budget_s
    for i, m in enumerate(live):
        if time.monotonic() >= deadline:
            log.warning("reset: %.0fs cancel budget spent after %d monitor(s); %d not attempted", budget_s, len(cancelled), len(live) - i)
            failed.extend(rest.id for rest in live[i:])
            break
        try:
            tool.cancel(m.id)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not cancel Parallel monitor %s: %s", m.id, exc)
            failed.append(m.id)
            continue
        m.status = "cancelled"
        cancelled.append(m.id)
    return cancelled, failed


@app.post("/api/projects/{project_id}/reset")
def reset_project(project_id: str) -> dict[str, Any]:
    if project_id != PROJECT_ID:
        raise HTTPException(400, "only the seeded demo project can be reset")
    existing = repo.get_project(project_id)
    cancelled, failed = _cancel_monitors(existing) if existing else ([], [])
    repo.delete_project_data(project_id)
    repo.save_project(build_project())
    # Cooldowns, not the ledger. This route has no auth and is a button on a public page, so
    # zeroing what has been spent here would make SCENEPILOT_PAID_CALL_BUDGET unenforceable:
    # reset, spend it, reset again. Resetting the *demo* does not un-spend the money.
    call_budget.clear_cooldowns()
    fresh = repo.get_project(project_id)
    if cancelled:
        _log_project(fresh, "parallel", f"Cancelled {len(cancelled)} live Parallel monitor(s) before the reset — a discarded monitor keeps billing until it is cancelled at Parallel", {"monitor_ids": cancelled})
    if failed:
        _log_project(fresh, "warning", f"Could not cancel {len(failed)} Parallel monitor(s) before the reset — they may still be billing and can be cancelled by id: {', '.join(failed)}", {"monitor_ids": failed})
    return {"ok": True, "cancelled_monitors": cancelled, "uncancelled_monitors": failed, "warmed": _warm(fresh)}


# Free text that reaches a Gemini prompt, bounded before it gets there: roughly ten script pages for
# one scene, a 250-page feature for a whole draft.
MAX_SCENE_TEXT = 20_000
MAX_SCREENPLAY_TEXT = 600_000


class SceneInput(BaseModel):
    number: str = Field(max_length=16)
    text: str = Field(max_length=MAX_SCENE_TEXT, description="Pasted scene text or a manual brief")
    source_kind: str = "pasted_text"
    heading: str | None = Field(default=None, max_length=200)
    int_ext: str = "EXT"
    time_of_day: str = "DAY"
    estimated_minutes: int = 120


@app.post("/api/projects/{project_id}/scenes")
def create_scene(project_id: str, body: SceneInput) -> dict[str, Any]:
    p = _project(project_id)
    if any(s.number == body.number for s in p.scenes):
        raise HTTPException(409, f"scene {body.number} already exists")
    brief = ProductionBrief(project_id=p.id, source_kind=body.source_kind, raw_text=body.text)
    heading = body.heading or (body.text.strip().splitlines()[0][:80] if body.text.strip() else f"SCENE {body.number}")
    scene = Scene(id=f"sc_{body.number}", number=body.number, heading=heading, int_ext=IntExt(body.int_ext), time_of_day=TimeOfDay(body.time_of_day), script_text=body.text, estimated_minutes=body.estimated_minutes, brief_id=brief.id)
    p.briefs.append(brief)
    p.scenes.append(scene)
    repo.save_project(p)
    return {"scene": scene.model_dump(mode="json")}


@app.get("/api/projects/{project_id}/scenes/{scene_id}")
def get_scene(project_id: str, scene_id: str) -> dict[str, Any]:
    p = _project(project_id)
    try:
        scene = p.scene(scene_id)
    except KeyError:
        clean_num = scene_id.replace("sc_", "").replace("scene_", "")
        scene = next((s for s in p.scenes if str(s.number) == clean_num or s.id == f"sc_{clean_num}"), None)
        if not scene:
            raise HTTPException(404, "scene not found")
    runs = [r for r in repo.list_runs(project_id, RunKind.PLANNING.value) if r.planning and r.planning.scene_id == scene_id]
    latest = runs[0] if runs else None
    search_runs = repo.list_search_runs(run_id=latest.id) if latest else []
    extract_runs = repo.list_extract_runs(run_id=latest.id) if latest else []
    return {
        "scene": scene.model_dump(mode="json"),
        "plan": p.plans.get(scene_id).model_dump(mode="json") if scene_id in p.plans else None,
        "run": latest.model_dump(mode="json") if latest else None,
        "search_runs": [s.model_dump(mode="json") for s in search_runs],
        "extract_runs": [x.model_dump(mode="json") for x in extract_runs],
        # What this run reused from Parallel Memory, joined back to the run that first learned it.
        "recalled": recall_view(
            p,
            repo.list_memory_reads(project_id, run_id=latest.id) if latest else [],
            repo.list_task_runs(project_id=project_id),
            repo.list_findall_runs(project_id=project_id),
        ),
        "parallel_usage": summarize(search_runs, extract_runs),
        "activity": [e.model_dump(mode="json") for e in repo.list_activity(run_id=latest.id)] if latest else [],
        "brief": next((b.model_dump(mode="json") for b in p.briefs if b.id == scene.brief_id), None),
    }


class PlanRequest(BaseModel):
    text: str | None = Field(default=None, max_length=MAX_SCENE_TEXT, description="Optional replacement scene text before planning")
    use_memory: bool = Field(default=False, description="Start the research plan from this production's Parallel memory")


@app.post("/api/projects/{project_id}/scenes/{scene_id}/plan")
async def plan_scene(project_id: str, scene_id: str, body: PlanRequest | None = None) -> dict[str, Any]:
    p = _project(project_id)
    try:
        scene = p.scene(scene_id)
    except KeyError:
        clean_num = scene_id.replace("sc_", "").replace("scene_", "")
        scene = next((s for s in p.scenes if str(s.number) == clean_num or s.id == f"sc_{clean_num}"), None)
        if not scene:
            raise HTTPException(404, "scene not found")
    # `None` is "the box was not touched"; a blank string is "the producer emptied it". The two used
    # to arrive here identically, so clearing the box and pressing the button spent a real Gemini
    # breakdown and real Parallel searches on the text that had just been deleted — the one input the
    # producer had explicitly said not to use. This textarea already rewrites committed scene text on
    # every other edit, so honouring the empty case is the limiting case of a decision the page has
    # already made, not a new one.
    if body and body.text is not None:
        cleaned = body.text.strip()
        if cleaned:
            scene.script_text = cleaned
            brief = ProductionBrief(project_id=p.id, source_kind="pasted_text", raw_text=scene.script_text)
            p.briefs.append(brief)
            scene.brief_id = brief.id
        else:
            # The brief stays in the project's history — it is a record of something that was really
            # pasted. The scene stops citing it, because the text it holds is no longer the scene's.
            scene.script_text = ""
            scene.brief_id = None
    active = [r for r in repo.list_runs(project_id, RunKind.PLANNING.value) if r.planning and r.planning.scene_id == scene_id and r.status in (RunStatus.PENDING, RunStatus.RUNNING)]
    if active:
        return {"run_id": active[0].id, "already_running": True}
    require_budget("plan", scene.id, settings=settings)
    run = WorkflowRun(project_id=p.id, kind=RunKind.PLANNING, mode=settings.active_mode, planning=PlanningState(scene_id=scene_id, used_memory=bool(body and body.use_memory)))
    repo.save_project(p)
    repo.save_run(run)
    ctx = RunContext(repo, run, p)
    _spawn(run_planning(ctx))
    return {"run_id": run.id}


# --------------------------------------------------------------------------- #
# Screenplay ingestion, AI element breakdown & DOOD matrix
# --------------------------------------------------------------------------- #


class ScreenplayUploadInput(BaseModel):
    text: str = Field(max_length=MAX_SCREENPLAY_TEXT, description="Fountain or Final Draft XML content")
    format_hint: str = "auto"
    sync_scenes: bool = True


@app.post("/api/projects/{project_id}/screenplay/upload")
def upload_screenplay(project_id: str, body: ScreenplayUploadInput) -> dict[str, Any]:
    p = _project(project_id)
    if not body.text.strip():
        raise HTTPException(400, "screenplay text is empty")
    parsed_scenes = parse_screenplay(body.text, format_hint=body.format_hint)
    p.parsed_screenplay_scenes = parsed_scenes

    synced_count = 0
    if body.sync_scenes:
        for ps in parsed_scenes:
            existing = next((s for s in p.scenes if s.number == ps.scene_number), None)
            if existing:
                existing.heading = ps.heading
                existing.int_ext = ps.int_ext
                existing.time_of_day = ps.time_of_day
                existing.eighths = ps.eighths
                existing.script_text = ps.raw_text
            else:
                new_scene = Scene(
                    id=f"sc_{ps.scene_number}",
                    number=ps.scene_number,
                    heading=ps.heading,
                    int_ext=ps.int_ext,
                    time_of_day=ps.time_of_day,
                    script_text=ps.raw_text,
                    eighths=ps.eighths,
                )
                p.scenes.append(new_scene)
                synced_count += 1

    repo.save_project(p)
    _log_project(
        p,
        "info",
        f"Screenplay parsed: {len(parsed_scenes)} scene(s), {synced_count} new scene(s) synced to project",
        {"scenes_parsed": len(parsed_scenes), "synced": synced_count},
    )
    return {
        "scenes": [s.model_dump(mode="json") for s in parsed_scenes],
        "scene_count": len(parsed_scenes),
        "synced_count": synced_count,
    }


@app.get("/api/projects/{project_id}/screenplay/scenes")
def get_screenplay_scenes(project_id: str) -> dict[str, Any]:
    p = _project(project_id)
    for ps in p.parsed_screenplay_scenes:
        sc = next((s for s in p.scenes if str(s.number) == str(ps.scene_number)), None)
        if sc:
            if sc.breakdown_elements and not ps.elements:
                ps.elements = sc.breakdown_elements
            if sc.stop_conditions and not ps.stop_conditions:
                ps.stop_conditions = sc.stop_conditions
            if sc.continuity_notes and not ps.continuity_notes:
                ps.continuity_notes = sc.continuity_notes
    return {
        "scenes": [s.model_dump(mode="json") for s in p.parsed_screenplay_scenes],
        "count": len(p.parsed_screenplay_scenes),
    }


@app.post("/api/projects/{project_id}/scenes/{scene_id}/breakdown-elements")
async def breakdown_scene_elements(project_id: str, scene_id: str) -> dict[str, Any]:
    p = _project(project_id)
    try:
        scene = p.scene(scene_id)
    except KeyError:
        clean_num = scene_id.replace("sc_", "").replace("scene_", "")
        scene = next((s for s in p.scenes if str(s.number) == clean_num), None)
        if not scene:
            raise HTTPException(404, "scene not found")

    output = await run_breakdown_agent(scene.heading, scene.script_text)
    elements = map_breakdown_to_elements(output)
    scene.breakdown_elements = elements
    scene.stop_conditions = output.stop_conditions
    scene.continuity_notes = output.continuity_notes

    for ps in p.parsed_screenplay_scenes:
        if str(ps.scene_number) == str(scene.number):
            ps.elements = elements
            ps.stop_conditions = output.stop_conditions
            ps.continuity_notes = output.continuity_notes

    repo.save_project(p)

    _log_project(
        p,
        "gemini",
        f"CreativeBreakdownAgent analyzed Scene {scene.number}: {len(elements)} elements across categories",
        {"scene_id": scene.id, "element_count": len(elements), "stop_conditions": output.stop_conditions},
    )
    return {
        "scene_id": scene.id,
        "elements": [e.model_dump(mode="json") for e in elements],
        "stop_conditions": output.stop_conditions,
        "continuity_notes": output.continuity_notes,
    }


@app.get("/api/projects/{project_id}/one-liner")
def get_one_liner(project_id: str) -> dict[str, Any]:
    """The whole shoot on one page, and — where a recovery has been approved — what it moved.

    `baseline` is the same document built against the rescue's own pre-recovery schedule for the one
    day it touched, so the two versions differ only by that change and the moves listed between them
    are attributable to it and nothing else.
    """
    p = _project(project_id)
    current = build_one_liner(p)
    applied = [
        r for r in repo.list_runs(project_id, RunKind.RESCUE.value)
        if r.status == RunStatus.APPLIED and r.rescue and r.rescue.baseline
    ]
    baseline = None
    moves: list[dict[str, Any]] = []
    changed_day_id = None
    if applied:
        latest = applied[0]
        changed_day_id = latest.rescue.shoot_day_id
        baseline = build_one_liner(p, overrides={changed_day_id: latest.rescue.baseline})
        moves = one_liner_moves(baseline, current)

    # What each day of the *current* sheet costs in consequences. Annotated here rather than inside
    # `build_one_liner`, which is repo-free by design and cannot see which recovery was applied.
    # The baseline sheet deliberately gets no figure: it is a historical document, and a cost printed
    # on it would price a schedule the production no longer holds.
    deferred_by_day = {d.id: _applied_deferred(d, applied) for d in p.shoot_days}
    strip = production_cost_strip(p, deferred_by_day=deferred_by_day)
    by_day = {d["shoot_day_id"]: d for d in strip["days"]}
    for day_row in current["days"]:
        card = by_day.get(day_row["shoot_day_id"])
        if card:
            day_row["cost"] = {"total_inr": card["total_inr"], "basis": card["basis"]}

    return {
        "current": current,
        "baseline": baseline,
        "changed_day_id": changed_day_id,
        "moves": moves,
        "production_cost_inr": strip["total_inr"],
    }


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/dpr")
def get_dpr(project_id: str, day_id: str) -> dict[str, Any]:
    """The Daily Production Report for a wrapped day. Refuses, with the reason, for any other."""
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    report = build_dpr(p, day)
    if report is None:
        raise HTTPException(
            409,
            f"Day {day.day_number} has not wrapped, so it has delivered nothing to report. A daily "
            "production report for a day that has not happened is a forecast wearing a report's clothes — "
            "the call sheet is the document for a day still ahead.",
        )
    return {"dpr": report}


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/movement-order")
def get_movement_order(project_id: str, day_id: str) -> dict[str, Any]:
    """The day's transport sheet: legs, departures, arrivals and what the production cannot say."""
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    return {"movement_order": build_movement_order(p, day)}


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/sides")
def get_sides(project_id: str, day_id: str) -> dict[str, Any]:
    """The day's pages, in shooting order. Scenes the Studio has no text for print as named gaps."""
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    return {"sides": build_sides(p, day)}


class RevertRequest(BaseModel):
    reason: str = "no reason given"
    reverted_by: str = "producer"


class StandDownRequest(BaseModel):
    reason: str = Field(default="no reason given", max_length=2_000)
    stood_down_by: str = "producer"


@app.post("/api/runs/{run_id}/stand-down")
def stand_down_run(run_id: str, body: StandDownRequest) -> dict[str, Any]:
    """End a rescue without approving any of it, and give the day back.

    The one state in this product a producer could reach and not leave. A disruption no legal
    schedule survives leaves every option infeasible, `approve` refuses all of them, and the day page
    hides the fixture picker and the manual entry form while a disruption is live — so the only way
    out was resetting the entire production. This is the way out.
    """
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    p = _project(run.project_id)
    try:
        result = stand_down(p, run, stood_down_by=body.stood_down_by, reason=body.reason.strip() or "no reason given")
    except RevertRefused as exc:
        raise HTTPException(409, str(exc))
    repo.save_project(p)
    repo.save_run(run)
    day = result["day"]
    repo.log(ActivityEvent(
        run_id=run.id, project_id=p.id, kind="decision",
        message=f"Producer stood down the recovery for Day {day.day_number} — {run.rescue.stood_down_reason}",
        meta={"shoot_day_id": day.id, "disruption_id": run.rescue.disruption_id, "options_offered": len(run.rescue.options)},
    ))
    return {"run": run.model_dump(mode="json"), "day": day.model_dump(mode="json")}


@app.post("/api/runs/{run_id}/revert")
def revert_recovery(run_id: str, body: RevertRequest) -> dict[str, Any]:
    """Roll an applied recovery back off the schedule, as its own audit-trailed event."""
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    p = _project(run.project_id)
    changeset = run.rescue.changeset if run.rescue else None
    if changeset is None:
        raise HTTPException(409, "This run applied no change set, so there is nothing to roll back.")
    try:
        result = revert_changeset(p, run, changeset, reverted_by=body.reverted_by, reason=body.reason)
    except RevertRefused as exc:
        raise HTTPException(409, str(exc))

    repo.save_changeset(result["changeset"])
    repo.save_project(p)
    repo.save_run(run)
    repo.log(
        ActivityEvent(
            run_id=run.id,
            project_id=p.id,
            kind="decision",
            message=(
                f"Producer reverted the approved recovery on Day {result['day'].day_number} — {body.reason}. "
                f"The schedule is back to its pre-recovery state ({result['restored_items']} scene(s))."
            ),
            meta={"reverted_changeset_id": result["reverted_changeset_id"], "changeset_id": result["changeset"].id},
        )
    )
    return {
        "changeset": result["changeset"].model_dump(mode="json"),
        "reverted_changeset_id": result["reverted_changeset_id"],
        "day": result["day"].model_dump(mode="json"),
        "hard_violations": result["hard_violations"],
        "note": result["note"],
    }


class PlacementCommit(BaseModel):
    scene_id: str
    committed_by: str = "producer"


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/commit-placement")
def commit_downstream_placement(project_id: str, day_id: str, body: PlacementCommit) -> dict[str, Any]:
    """Place a carried scene onto this downstream day, for real, with an audit trail."""
    p = _project(project_id)
    try:
        result = commit_placement(p, day_id, body.scene_id, committed_by=body.committed_by)
    except CommitRefused as exc:
        raise HTTPException(409, str(exc))
    repo.save_changeset(result["changeset"])
    repo.save_project(p)
    _log_project(
        p,
        "approval",
        f"Producer placed Scene {result['scene_number']} on Day {result['day'].day_number} at {result['start']}"
        + (f" — {result['added_overtime_cost_inr']:,} INR of overtime" if result["added_overtime_cost_inr"] else " — inside the standard day"),
        {"changeset_id": result["changeset"].id, "shoot_day_id": day_id, "scene_id": body.scene_id},
    )
    return {
        "changeset": result["changeset"].model_dump(mode="json"),
        "day": result["day"].model_dump(mode="json"),
        "added_overtime_cost_inr": result["added_overtime_cost_inr"],
        "notes": result["notes"],
        "soft_violations": result["soft_violations"],
    }


class PickupCommit(BaseModel):
    deferred_scene_ids: list[str]
    option_id: str = "opt_recovery"
    committed_by: str = "producer"


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/commit-pickup-day")
def commit_pickup_day(project_id: str, day_id: str, body: PickupCommit) -> dict[str, Any]:
    """Materialize the synthesized pickup day as a real shoot day — uncleared, and saying so."""
    p = _project(project_id)
    try:
        p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    plan = resolve_deferred_scenes_multiday(
        p, day_id, body.deferred_scene_ids, body.option_id,
        location_facts=[f for f in p.location_facts if f.binds],
    )
    if plan.synthesized_pickup_day is None:
        raise HTTPException(409, "This plan needs no pickup day — every carried scene is absorbed by a day already on the schedule.")
    try:
        result = materialize_pickup_day(p, plan.synthesized_pickup_day, committed_by=body.committed_by)
    except CommitRefused as exc:
        raise HTTPException(409, str(exc))
    repo.save_changeset(result["changeset"])
    repo.save_project(p)
    day = result["day"]
    _log_project(
        p,
        "approval",
        f"Producer committed Day {day.day_number} as a pickup unit on {day.date}, {len(day.items)} scene(s)"
        + (f" — {len(result['pending_clearance'])} resource(s) still to clear" if result["pending_clearance"] else ""),
        {"changeset_id": result["changeset"].id, "shoot_day_id": day.id},
    )
    return {
        "changeset": result["changeset"].model_dump(mode="json"),
        "day": day.model_dump(mode="json"),
        "pending_clearance": result["pending_clearance"],
        "clearance_note": result["clearance_note"],
        "hard_violations": result["hard_violations"],
    }


@app.get("/api/projects/{project_id}/conflict-heatmap")
def get_conflict_heatmap(project_id: str) -> dict[str, Any]:
    """Resource × day booking tightness — why this production is fragile where it is."""
    return build_heatmap(_project(project_id))


@app.get("/api/projects/{project_id}/risk-register")
def get_risk_register(project_id: str) -> dict[str, Any]:
    """Every risk the planning runs have put on record, ordered by exposure. Reads state only."""
    return build_risk_register(_project(project_id))


@app.get("/api/projects/{project_id}/parallel-spend")
def get_parallel_spend(project_id: str) -> dict[str, Any]:
    """Every Parallel call this production has made, priced. Reads state only.

    Deliberately production-wide rather than per-run: "what did this demo cost" is a question about
    the whole session, and the per-run strips answer a different one. The split between spent and
    replayed is the point — a deployment running on committed recordings has spent nothing, and
    saying so is the same statement the budget ledger makes when it declines to book those calls.
    """
    p = _project(project_id)
    usage = summarize(
        repo.list_search_runs(project_id=p.id),
        repo.list_extract_runs(project_id=p.id),
        repo.list_task_runs(project_id=p.id),
        repo.list_findall_runs(project_id=p.id),
    )
    return {"usage": usage, "mode": settings.mode, "budget": call_budget.state(settings)}


@app.get("/api/projects/{project_id}/cost-strip")
def get_cost_strip(project_id: str) -> dict[str, Any]:
    """Per-day consequence cost and the production total. Deterministic; reads state only."""
    p = _project(project_id)
    applied = [
        r for r in repo.list_runs(project_id, RunKind.RESCUE.value)
        if r.status == RunStatus.APPLIED and r.rescue and r.rescue.baseline
    ]
    return production_cost_strip(p, deferred_by_day={d.id: _applied_deferred(d, applied) for d in p.shoot_days})


@app.get("/api/projects/{project_id}/dood")
def get_dood_matrix(project_id: str) -> dict[str, Any]:
    p = _project(project_id)
    entries = build_dood_matrix(p)
    # What an approved recovery did to the cast schedule, if one has been approved. Built against
    # the rescue's own baseline, so every changed cell is attributable to that recovery — this is
    # the "who is now being paid to sit still, and what does that cost" half of the cost delta.
    delta = None
    applied = [
        r for r in repo.list_runs(project_id, RunKind.RESCUE.value)
        if r.status == RunStatus.APPLIED and r.rescue and r.rescue.baseline
    ]
    if applied:
        latest = applied[0]
        delta = dood_delta(p, latest.rescue.shoot_day_id, latest.rescue.baseline)
    return {
        "project_id": p.id,
        "entries": [e.model_dump(mode="json") for e in entries],
        "shoot_days": [{"id": d.id, "day_number": d.day_number, "date": d.date} for d in sorted(p.shoot_days, key=lambda x: (x.day_number, x.date))],
        # The codes this matrix emits, and the ones a real DOOD carries that this production has no
        # state behind. Sent rather than hardcoded in the UI so the legend cannot claim a code the
        # engine never produces.
        "codes": DOOD_CODES,
        "unmodelled_codes": UNMODELLED_CODES,
        # The bottom line, and the ratio between its two halves, which is what the document is for.
        "totals": dood_totals(p, entries),
        # Characters the breakdown found that nobody is cast for. A casting gap, reported as one —
        # never folded into the matrix as a work day nobody scheduled.
        "unlinked_characters": unlinked_characters(p),
        "delta": delta,
    }


# --------------------------------------------------------------------------- #
# Ephemeris, Labor Rules & Interactive Stripboard Simulation
# --------------------------------------------------------------------------- #


class SimulateStripMoveInput(BaseModel):
    items: list[ScheduleItem]
    # Unset = the production's own agreement, the same one the recovery validator and the "Rules in
    # force" card read. A named preset is a deliberate "what would this cost a DGA/SAG unit?".
    labor_preset: str | None = None


SimulateStripMoveInput.model_rebuild()


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/ephemeris")
def get_shoot_day_ephemeris(project_id: str, day_id: str) -> dict[str, Any]:
    p = _project(project_id)
    day = p.shoot_day(day_id)
    profile = city_ephemeris(p.base_city, day.date)
    return {
        "day_id": day.id,
        "date": day.date,
        "profile": profile.model_dump(mode="json"),
    }


@app.get("/api/projects/{project_id}/labor-rules")
def get_labor_rules(project_id: str) -> dict[str, Any]:
    """The pack in force, plus the ones a producer can price against.

    `active_preset` is derived from where the production shoots, and it is the same value the
    recovery validator enforces — the card that prints "at least N h rest" and the engine that
    rejects a schedule for breaching it cannot disagree.
    """
    p = _project(project_id)
    return {
        "active_preset": active_preset(p).value,
        "presets": {
            "DGA_SAG": DGA_SAG_PACK.model_dump(mode="json"),
            "FWICE_CINTAA": FWICE_CINTAA_PACK.model_dump(mode="json"),
        },
    }


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/simulate-strip-move")
def simulate_strip_move(
    project_id: str, day_id: str, body: SimulateStripMoveInput
) -> dict[str, Any]:
    p = _project(project_id)
    day = p.shoot_day(day_id)
    pack = get_rule_pack(body.labor_preset) if body.labor_preset else active_pack(p)

    # The day's live disruption, if any. A *draft* (a monitor detection the producer has not
    # confirmed) must not price the board — and dismissing a draft deletes it, so there is no
    # dismissed flag to test.
    disruption = next((d for d in p.disruptions if d.id == day.active_disruption_id), None) or next(
        (d for d in reversed(p.disruptions) if d.shoot_day_id == day.id and not d.draft), None
    )
    ctx = ValidationContext(
        project=p,
        day=day,
        disruption=disruption,
        labor_pack=pack,
        baseline_items=day.items,
        # The same accepted facts the recovery engine validates against (`services/recovery.py`).
        # Without them this endpoint scored a drag against every rule *except* the ones a producer
        # had gone to the trouble of accepting: a strip pushed past an accepted 22:00 noise curfew
        # came back valid here and rejected there, on the same project, seconds apart. The board
        # captions its red outlines with this endpoint's answer, so a curfew that binds the day has
        # to bind the drag.
        location_facts=[f for f in p.location_facts if f.binds],
    )
    violations = validate_schedule(ctx, body.items)
    hard_violations = [v for v in violations if v.hard]
    soft_violations = [v for v in violations if not v.hard]
    total_cost = sum(v.cost_inr for v in violations)

    return {
        "valid": len(hard_violations) == 0,
        "hard_violations": [v.model_dump(mode="json") for v in hard_violations],
        "soft_violations": [v.model_dump(mode="json") for v in soft_violations],
        "total_penalty_cost_inr": total_cost,
        "labor_pack_used": pack.name,
    }


# --------------------------------------------------------------------------- #
# Multi-Day Ripple Plan & Field Dispatch
# --------------------------------------------------------------------------- #


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/multiday-plan")
def get_multiday_ripple_plan(
    project_id: str,
    day_id: str,
    deferred_scene_ids: str = "sc_42",
    option_id: str = "opt_recovery",
) -> dict[str, Any]:
    p = _project(project_id)
    scene_ids = [s.strip() for s in deferred_scene_ids.split(",") if s.strip()]
    plan = resolve_deferred_scenes_multiday(
        project=p,
        source_day_id=day_id,
        deferred_scene_ids=scene_ids,
        option_id=option_id,
        # The same accepted facts the board is validated against, so a downstream placement cannot
        # quietly ignore a curfew the producer has already accepted on this day's schedule.
        location_facts=[f for f in p.location_facts if f.binds],
    )
    return plan.model_dump(mode="json")


class DispatchRequest(BaseModel):
    channels: list[str] = list(CHANNELS)


def _dispatch_day(project_id: str, day_id: str):
    p = _project(project_id)
    try:
        return p, p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")


def _dispatch_body(day, records) -> dict[str, Any]:
    return {
        "day_id": day.id,
        "dispatches": [d.model_dump(mode="json") for d in records],
        "count": len(records),
        # Said in the payload as well as on the panel: an API consumer reading this log is owed the
        # same warning a judge reading the screen gets.
        "simulated": True,
        "note": SIMULATION_NOTE,
    }


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/dispatch")
def dispatch_call_sheet_api(
    project_id: str, day_id: str, body: DispatchRequest | None = None
) -> dict[str, Any]:
    """Broadcast — the only thing in this app that creates a delivery record.

    Nothing is transmitted; this opens a queued row per recipient per channel so the tracking view
    has something to track. Deliberately a POST behind a button: reading a page must not manufacture
    a delivery log, which is exactly what the GET used to do.
    """
    p, day = _dispatch_day(project_id, day_id)
    requested = body.channels if body and body.channels else list(CHANNELS)
    channels = [c for c in dict.fromkeys(requested) if c in CHANNELS]
    if not channels:
        raise HTTPException(400, f"no known channel requested — pick from {', '.join(CHANNELS)}")
    dispatches = generate_crew_dispatches(p, day, channels=channels)  # type: ignore[arg-type]
    recipients = len({d.recipient_id for d in dispatches})
    _log_project(
        p,
        "dispatch",
        f"Call sheet for Day {day.day_number} queued to {recipients} cast and crew across "
        f"{', '.join(channels)} — {len(dispatches)} simulated deliveries; nothing was transmitted",
        {"recipient_count": recipients, "record_count": len(dispatches), "channels": channels, "simulated": True},
    )
    return _dispatch_body(day, dispatches)


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/dispatch")
def get_day_dispatches(project_id: str, day_id: str) -> dict[str, Any]:
    """The delivery log as it stands, plus the distribution list a broadcast would address.

    An empty `dispatches` means nothing has been broadcast, and it stays empty: this used to
    generate a log when it found none, so merely opening the call sheet stamped a delivery nobody
    asked for. The roster is derived on the fly from the call sheet and the production's own cast
    and `CREW` resources, so the panel can name every recipient before the button is pressed
    without writing anything down.
    """
    p, day = _dispatch_day(project_id, day_id)
    body = _dispatch_body(day, get_dispatches_for_day(p.id, day.id))
    body["roster"] = [r.model_dump(mode="json") for r in dispatch_roster(p, day)]
    return body


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/dispatch/{dispatch_id}/read")
def read_crew_dispatch(project_id: str, day_id: str, dispatch_id: str) -> dict[str, Any]:
    """Mark one row read. Simulated, and driven by a person clicking — never stamped on generation."""
    p, day = _dispatch_day(project_id, day_id)
    updated = mark_dispatch_read(p.id, day.id, dispatch_id)
    if not updated:
        raise HTTPException(404, "dispatch record not found")
    return updated.model_dump(mode="json")


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/dispatch/{dispatch_id}/ack")
def ack_crew_dispatch(
    project_id: str, day_id: str, dispatch_id: str
) -> dict[str, Any]:
    p, day = _dispatch_day(project_id, day_id)
    updated = acknowledge_dispatch(p.id, day.id, dispatch_id)
    if not updated:
        raise HTTPException(404, "dispatch record not found")
    return updated.model_dump(mode="json")


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/dispatch/re-ping")
def reping_unacknowledged_crew(project_id: str, day_id: str) -> dict[str, Any]:
    """Re-queue the rows nobody has acknowledged. Still simulated, still transmits nothing."""
    p, day = _dispatch_day(project_id, day_id)
    repinged = re_ping_unacknowledged(p.id, day.id)
    repo.log(
        ActivityEvent(
            project_id=project_id,
            run_id="dispatch",
            kind="dispatch_reping",
            message=f"Re-queued {len(repinged)} unacknowledged simulated deliveries for Day {day.day_number} — nothing was transmitted",
            meta={"repinged_count": len(repinged), "simulated": True},
        )
    )
    return {
        "status": "success",
        "repinged_count": len(repinged),
        "dispatches": [d.model_dump(mode="json") for d in repinged],
        "simulated": True,
        "note": SIMULATION_NOTE,
    }


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/export/mmsx")
@app.get("/api/projects/{project_id}/days/{day_id}/export/mmsx")
def export_day_mmsx(project_id: str, day_id: str) -> Response:
    """Export the shoot day's stripboard and breakdown sheets as XML.

    ScenePilot's own schema, shaped after how scheduling tools exchange a stripboard — unofficial,
    and not written or validated by Movie Magic Scheduling, which is what the download button says
    too. Served as `.xml`: `.mmsx` and `.sex` are MMS's real exchange extensions and handing one of
    them to a producer would promise an import this file has never been tested against.
    """
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    xml_content = generate_mmsx_xml(p, day)
    filename = f"{project_id}_{day_id}_stripboard.xml"
    return Response(
        content=xml_content,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/insurance-dossier")
@app.get("/api/projects/{project_id}/days/{day_id}/insurance-dossier")
def get_insurance_dossier_api(project_id: str, day_id: str) -> dict[str, Any]:
    """A Force Majeure claim packet for this shoot day, compiled from persisted state only.

    The whole rescue run is handed to the compiler rather than one option: the packet's argument is
    the options the engine *rejected* and the ChangeSet a producer actually signed, and neither is
    reachable from the recommendation alone. The verification searches come from the disruption's own
    `search_run_ids`, so the certified-source section is the calls that were really made.
    """
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    runs = [r for r in repo.list_runs(project_id, RunKind.RESCUE.value) if r.rescue and r.rescue.shoot_day_id == day_id]
    latest = runs[0] if runs else None
    disruption = next((d for d in p.disruptions if d.id == (latest.rescue.disruption_id if latest else day.active_disruption_id)), None)
    search_runs = repo.list_search_runs(ids=disruption.search_run_ids) if disruption else []
    return compile_insurance_dossier(p, day, disruption, latest, search_runs)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = _run(run_id)
    search_runs = repo.list_search_runs(run_id=run_id)
    extract_runs = repo.list_extract_runs(run_id=run_id)
    return {"run": run.model_dump(mode="json"), "activity": [e.model_dump(mode="json") for e in repo.list_activity(run_id=run_id)], "search_runs": [s.model_dump(mode="json") for s in search_runs], "extract_runs": [x.model_dump(mode="json") for x in extract_runs], "parallel_usage": summarize(search_runs, extract_runs)}


@app.get("/api/search-runs/{search_run_id}")
def get_search_run(search_run_id: str) -> dict[str, Any]:
    sr = repo.get_search_run(search_run_id)
    if sr is None:
        raise HTTPException(404, "search run not found")
    return sr.model_dump(mode="json")


@app.get("/api/extract-runs/{extract_run_id}")
def get_extract_run(extract_run_id: str) -> dict[str, Any]:
    xr = repo.get_extract_run(extract_run_id)
    if xr is None:
        raise HTTPException(404, "extract run not found")
    return xr.model_dump(mode="json")


class ExtractRequest(BaseModel):
    url: str
    search_run_id: str | None = None
    evidence_id: str | None = None


def _extract_objective(run: WorkflowRun, p, body: ExtractRequest) -> str:
    """Deterministic objective so the same click yields the same recorder key in replay."""
    if run.planning is not None:
        if body.evidence_id:
            ev = next((e for e in run.planning.evidence if e.id == body.evidence_id), None)
            if ev and ev.question_id:
                q = next((q for q in run.planning.questions if q.id == ev.question_id), None)
                if q:
                    return q.question
        if body.search_run_id:
            sr = repo.get_search_run(body.search_run_id)
            if sr and sr.question_id:
                q = next((q for q in run.planning.questions if q.id == sr.question_id), None)
                if q:
                    return q.question
    if run.rescue is not None:
        try:
            d = p.disruption(run.rescue.disruption_id)
            return f"{d.title}: what does this page say about the disruption and its timing?"
        except KeyError:
            pass
    if body.search_run_id:
        sr = repo.get_search_run(body.search_run_id)
        if sr:
            return sr.objective
    return "Extract the content relevant to the production research question."


@app.post("/api/runs/{run_id}/extract")
async def extract_source(run_id: str, body: ExtractRequest) -> dict[str, Any]:
    """'Open source' — fetch the full page through the Parallel Extract API (cached per run + URL)."""
    run = _run(run_id)
    p = _project(run.project_id)
    cached = repo.find_extract_run(run.id, body.url)
    if cached is not None:
        return {"extract_run": cached.model_dump(mode="json"), "cached": True}
    # Charged after the cache check, like every other priced route: re-opening a source already
    # fetched for this run costs nothing and must not cost a slot. Booked at all because the URL
    # comes from the caller, so on a public deployment this is the one paid Parallel endpoint an
    # anonymous visitor could otherwise drive without limit.
    require_budget("extract", body.url, settings=settings)
    ctx = RunContext(repo, run, p)
    objective = _extract_objective(run, p, body)
    question_id = None
    if run.planning is not None and body.search_run_id:
        sr = repo.get_search_run(body.search_run_id)
        question_id = sr.question_id if sr else None
    xr = await asyncio.to_thread(ctx.extract.extract, [body.url], objective, question_id=question_id, search_run_id=body.search_run_id, purpose="evidence_open_source")
    repo.save_run(run)
    return {"extract_run": xr.model_dump(mode="json"), "cached": False}


# --------------------------------------------------------------------------- #
# Shoot days & rescue
# --------------------------------------------------------------------------- #


def _applied_deferred(day, runs) -> list[str]:
    """Scenes an approved recovery carried off this day, so the card can price the carry-over.

    Read off the newest *applied* run only: a proposal a producer has not approved has not carried
    anything anywhere, and pricing its deferrals would charge the day for a decision nobody made.
    """
    applied = next((r for r in runs if r.status == RunStatus.APPLIED and r.rescue and r.rescue.baseline), None)
    if applied is None:
        return []
    scheduled = {i.scene_id for i in day.items}
    return [i.scene_id for i in applied.rescue.baseline if i.scene_id not in scheduled]


def _fixture_cards(p, day) -> list[dict[str, Any]]:
    """The three seeded fixtures, each carrying whether it can touch *this* day.

    They used to be offered whole on every unwrapped day. Days 5 and 6 are night units calling no
    crane and no Vikram, and all three fixtures produced identical option tuples there — Day 6
    answered a crane hydraulic fault with "move Sc 62 17:00->16:30; pull cover Sc 27 into 19:00", on
    a unit with no crane. `applicability` derives the verdict from the same predicates the solver
    uses; the card stays in the list so a producer can read why it is disabled rather than wonder
    where it went.
    """
    cards = []
    for key, spec in DISRUPTION_FIXTURES.items():
        applicable, reason = applicability(p, day, make_fixture_disruption(p.id, day.id, key))
        cards.append({
            "id": key, "type": spec["type"].value, "title": spec["title"], "description": spec["description"],
            "applicable": applicable, "not_applicable_reason": reason,
        })
    return cards


def _changeset_view(p, cs) -> dict[str, Any]:
    """A change set plus whether the production still stands behind it.

    `repo.list_changesets` returns every change set ever written for the day, and a revert removes
    the original's id from `project.changeset_ids` while leaving the row exactly as approved — so a
    rolled-back recovery kept rendering as "applied · producer", beside the inverted change set that
    rolled it back, which also carries `approved_by` and `applied_at`. Neither is filtered out: the
    audit trail has to read *approved, then reverted*, and the revert record is never in
    `changeset_ids` either (see `ChangeSet.is_revert_record` for how the two are told apart).
    """
    return {**cs.model_dump(mode="json"), "rescinded": cs.id not in p.changeset_ids and not cs.is_revert_record}


@app.get("/api/projects/{project_id}/shoot-days/{day_id}")
def get_shoot_day(project_id: str, day_id: str) -> dict[str, Any]:
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    runs = [r for r in repo.list_runs(project_id, RunKind.RESCUE.value) if r.rescue and r.rescue.shoot_day_id == day_id]
    latest = runs[0] if runs else None
    disruption = next((d for d in p.disruptions if d.id == (latest.rescue.disruption_id if latest else day.active_disruption_id)), None)
    return {
        "day": day.model_dump(mode="json"),
        "scenes": {s.id: s.model_dump(mode="json") for s in p.scenes},
        "resources": {r.id: r.model_dump(mode="json") for r in p.resources},
        "disruption": disruption.model_dump(mode="json") if disruption else None,
        "run": latest.model_dump(mode="json") if latest else None,
        "activity": [e.model_dump(mode="json") for e in repo.list_activity(run_id=latest.id)] if latest else [],
        "search_runs": [s.model_dump(mode="json") for s in repo.list_search_runs(run_id=latest.id)] if latest else [],
        "extract_runs": [x.model_dump(mode="json") for x in repo.list_extract_runs(run_id=latest.id)] if latest else [],
        "parallel_usage": summarize(repo.list_search_runs(run_id=latest.id), repo.list_extract_runs(run_id=latest.id), repo.list_task_runs(project_id=project_id), repo.list_findall_runs(project_id=project_id)) if latest else None,
        # No fixtures on a wrapped day: the endpoint refuses them (`_refuse_if_wrapped`), so offering
        # them here would be the payload inviting a click the API is going to reject.
        "fixtures": [] if day.status == ShootDayStatus.WRAPPED else _fixture_cards(p, day),
        "changesets": [_changeset_view(p, c) for c in repo.list_changesets(project_id) if c.shoot_day_id == day_id],
        # Which day each scene shoots on, so the board can draw a continuity chain that leaves this
        # day without a second fetch. Cross-day chains are this production's normal state.
        "scene_days": {i.scene_id: d.day_number for d in p.shoot_days for i in d.items},
        # Facts for this day's locations, so a rejection can show its provenance without a second fetch.
        "location_facts": [f.model_dump(mode="json") for f in p.location_facts if f.resource_id in {i.location_id for i in day.items if i.location_id}],
        # Where the day physically is: real coordinates, straight-line distances computed from them,
        # and the production's own travel minutes. See services/geo.py for what each number is not.
        "geography": day_geography(p, day),
        # What a day that has already been shot delivered. `None` on every day still ahead, which is
        # also the signal that the rescue controls below belong on this day and not on that one.
        "completion": day_completion(p, day),
        # Who this day calls that nobody has booked onto it. Computed the same way the validator
        # reads availability, so the blank the page prints and the reason a schedule is rejected
        # cannot disagree — and, now that the write exists, every row here is actionable.
        "pending_clearance": pending_clearance(p, day),
        # What this day costs in consequences, composed from terms that are each already priced
        # somewhere — see services/day_cost.py for the one-source-per-term rule.
        "day_cost": day_cost(p, day, deferred_scene_ids=_applied_deferred(day, runs)),
    }


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/call-sheet")
def get_call_sheet(project_id: str, day_id: str) -> dict[str, Any]:
    """Call sheet regenerated from production state; `baseline` is the pre-recovery sheet when a rescue has run."""
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    runs = [r for r in repo.list_runs(project_id, RunKind.RESCUE.value) if r.rescue and r.rescue.shoot_day_id == day_id]
    latest = runs[0] if runs else None
    disruption = next((d for d in p.disruptions if d.id == (latest.rescue.disruption_id if latest else day.active_disruption_id)), None)
    # Which colour paper this sheet goes out on. A production reissues on the next colour every time
    # a change is *approved*, so the count is applied rescue runs — not runs attempted, not options
    # generated. An unapproved recovery leaves the sheet white, which is the truth: nothing has been
    # signed, so nothing has been reissued.
    applied = [r for r in runs if r.status == RunStatus.APPLIED and r.rescue and r.rescue.changeset]
    revision = len(applied)
    approved = applied[0].rescue.changeset if applied else None
    evidence = latest.rescue.evidence if latest and latest.rescue else []
    current = build_call_sheet(
        p, day, None, disruption, label="current", revision=revision, evidence=evidence,
        approved_by=approved.approved_by if approved else None,
        approved_at=approved.applied_at.isoformat() if approved and approved.applied_at else None,
    )
    baseline = None
    changeset = None
    if latest and latest.rescue and latest.rescue.baseline and latest.status == RunStatus.APPLIED:
        # The sheet the unit was working to before this change was approved — one colour back on the
        # ladder, which is what makes the before/after read as two issues of one document.
        baseline = build_call_sheet(p, day, latest.rescue.baseline, None, label="before recovery", revision=max(0, revision - 1))
        changeset = latest.rescue.changeset.model_dump(mode="json") if latest.rescue.changeset else None
    return {"current": current, "baseline": baseline, "changeset": changeset, "run_id": latest.id if latest else None, "completion": day_completion(p, day)}


class DisruptionInput(BaseModel):
    fixture_id: str | None = Field(default=None, max_length=64)
    type: DisruptionType | None = None
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    window_start: str | None = None
    window_end: str | None = None
    affects_exteriors: bool = True
    affects_resource_ids: list[str] = Field(default_factory=list)
    affects_location_ids: list[str] = Field(default_factory=list)
    dry_out_minutes: int = 0


def _minutes_or_400(label: str, value: str, day) -> int:
    """`to_minutes` is the parser, so the API and the engine cannot disagree about what a time is.

    Deliberately not a second regex tightened to 00-23: `services/timeutil.py` allows HH>23 because
    this production's night units encode past-midnight as "28:00" (Day 5 and Day 6 both hard-wrap
    there), and a stricter pattern at the edge would reject the days it exists for.
    """
    try:
        return to_minutes(value)
    except ValueError:
        raise HTTPException(
            400,
            f"{label} {value!r} is not a time. Use HH:MM on the day's own clock — hours past midnight "
            f'count on ("28:00" is 04:00 the next morning), which is how Day {day.day_number} wraps at {day.hard_wrap}.',
        )


def _validate_disruption_window(day, window_start: str | None, window_end: str | None, dry_out_minutes: int) -> None:
    """Reject a window that cannot be true, before anything is spawned to choke on it.

    All of this used to be accepted and answered. `window_start='banana'` validated cleanly, and the
    first parse happened inside the background task: `to_minutes` raised, the run went FAILED, and
    the day was left AT_RISK under a disruption with no options and no retry. A reversed window was
    worse, because nothing crashed — `overlaps` clamps at zero, so 17:00-13:00 matched every scene
    zero times and the impact panel printed "0 scheduled scene(s) directly affected during
    17:00-13:00", which reads as a finding rather than as rejected input. A negative dry-out did the
    same thing arithmetically: -100000 min turned the window into (780, -98980).
    """
    if dry_out_minutes < 0:
        raise HTTPException(400, f"dry_out_minutes {dry_out_minutes} is negative; a dry-out extends the window past its end, it cannot pull it backwards.")
    if window_start is None and window_end is None:
        return
    if not (window_start and window_end):
        raise HTTPException(400, "a disruption window needs both a start and an end, or neither.")
    start = _minutes_or_400("window_start", window_start, day)
    end = _minutes_or_400("window_end", window_end, day)
    if end <= start:
        raise HTTPException(400, f"the window {window_start}–{window_end} ends before it starts; on a night unit a time past midnight is written 24:00–29:59, so 01:00 the next morning is 25:00.")
    call, wrap = to_minutes(day.unit_call), to_minutes(day.hard_wrap)
    if not overlaps(start, end, call, wrap):
        raise HTTPException(
            400,
            f"the window {window_start}–{window_end} does not overlap Day {day.day_number}'s operating window "
            f"{day.unit_call}–{day.hard_wrap}, so it cannot reach anything the day shoots.",
        )


def _refuse_if_unreachable(day, d: Disruption) -> None:
    """A disruption that names nothing it affects can never touch a scene, so it is not a report yet.

    `scene_exposed` has exactly four True branches and every one of them needs `affects_exteriors`,
    an entry in `affects_resource_ids` or one in `affects_location_ids`. The web form sends neither
    of the latter two and actively overrides the server-side `affects_exteriors=True` default to
    False for every non-weather type, so six of the seven disruption types were guaranteed no-ops:
    the impact panel reported nothing affected and the engine offered a repack anyway, and for
    TRANSPORT and REGULATORY it spent a real Parallel search verifying it first.
    """
    if d.affects_exteriors or d.affects_resource_ids or d.affects_location_ids:
        return
    raise HTTPException(
        400,
        f"this disruption affects no exteriors, no resource and no location, so nothing on Day {day.day_number} "
        "can be exposed to it. Say what it affects: exteriors, the cast or equipment it takes out, or the "
        "location it closes.",
    )


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/disruptions")
async def report_disruption(project_id: str, day_id: str, body: DisruptionInput) -> dict[str, Any]:
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    _refuse_if_wrapped(day)
    active = [r for r in repo.list_runs(project_id, RunKind.RESCUE.value) if r.rescue and r.rescue.shoot_day_id == day_id and r.status in (RunStatus.PENDING, RunStatus.RUNNING)]
    if active:
        return {"run_id": active[0].id, "disruption_id": active[0].rescue.disruption_id, "already_running": True}
    if body.fixture_id and body.fixture_id not in DISRUPTION_FIXTURES:
        raise HTTPException(400, "unknown fixture")
    if not body.fixture_id and not (body.type and body.title):
        raise HTTPException(400, "type and title are required for a manual disruption")
    # The window is checked before the budget is charged and before the Disruption is built: every
    # one of these used to be a 200 followed by a background crash or a confident wrong answer.
    if body.fixture_id:
        spec = DISRUPTION_FIXTURES[body.fixture_id]
        _validate_disruption_window(day, spec["window_start"], spec["window_end"], spec.get("dry_out_minutes", 0))
    else:
        _validate_disruption_window(day, body.window_start, body.window_end, body.dry_out_minutes)
    require_budget("disruption", day.id, settings=settings)
    if body.fixture_id:
        d = make_fixture_disruption(p.id, day.id, body.fixture_id)
    else:
        d = Disruption(project_id=p.id, shoot_day_id=day.id, type=body.type, title=body.title, description=body.description or body.title, window_start=body.window_start, window_end=body.window_end, affects_exteriors=body.affects_exteriors, affects_resource_ids=body.affects_resource_ids, affects_location_ids=body.affects_location_ids, dry_out_minutes=body.dry_out_minutes, source="manual", synthetic=False)
    _refuse_if_unreachable(day, d)
    p.disruptions.append(d)
    run = _start_rescue_for(p, day, d)
    return {"run_id": run.id, "disruption_id": d.id}


# --------------------------------------------------------------------------- #
# Parallel Monitor: the outside world pushes disruptions
# --------------------------------------------------------------------------- #


def _refuse_if_wrapped(day) -> None:
    """A day that has already been shot cannot be rescued.

    Every recovery option the engine can offer for a wrapped day is an instruction about a shoot
    that is over: it prices deferring a scene that is in the can, and rejects the others against
    bookings that were used and released. On Day 3 the impact panel correctly reported "0 scheduled
    scene(s) directly affected" and then offered ₹60,000 of recovery anyway. The refusal is the
    answer, and it names the day's own record so the caller has somewhere to go.

    This guard only ever knew about wrapping. Whether a fixture can touch an *unwrapped* day is
    `services/impact.py`'s `applicability`, which the day payload reports per fixture — the pathology
    named above was not confined to Day 3.
    """
    if day.status == ShootDayStatus.WRAPPED:
        raise HTTPException(
            409,
            f"Day {day.day_number} wrapped on {day.date}; a day that has already been shot cannot be "
            "rescued. Its call sheet is the record of what it delivered.",
        )


def _refuse_if_rescue_in_flight(project_id: str, day) -> None:
    """Refuse to start a second rescue for a day whose first one has not been decided."""
    live = next(
        (r for r in repo.list_runs(project_id, RunKind.RESCUE.value)
         if r.rescue and r.rescue.shoot_day_id == day.id
         and r.status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL)),
        None,
    )
    if live is not None:
        raise HTTPException(
            409,
            f"Rescue run {live.id} is already {live.status.value.lower().replace('_', ' ')} for Day {day.day_number}. "
            "Approve or revert that recommendation before reporting another disruption on the same day.",
        )


def _start_rescue_for(p, day, d: Disruption) -> WorkflowRun:
    """Every rescue starts here, which is why the guards live here and not in one caller.

    `services/monitor_ingest.py` builds disruptions too, and a REGULATORY or OTHER draft used to
    reach this function carrying nothing it could affect.
    """
    _refuse_if_wrapped(day)
    _refuse_if_unreachable(day, d)
    run = WorkflowRun(project_id=p.id, kind=RunKind.RESCUE, mode=settings.active_mode, rescue=RescueState(shoot_day_id=day.id, disruption_id=d.id))
    repo.save_project(p)
    repo.save_run(run)
    ctx = RunContext(repo, run, p)
    _spawn(run_rescue(ctx))
    return run


def _log_project(p, kind: str, message: str, meta: dict[str, Any] | None = None) -> None:
    from ..domain.models import ActivityEvent

    repo.log(ActivityEvent(run_id=None, project_id=p.id, kind=kind, message=message, meta=meta or {}))


def _revalidate_open_rescues(p, *, cause: str, meta_extra: dict[str, Any]) -> None:
    """Re-verdict every option list still awaiting approval, because what binds just changed.

    A producer accepting a cited statute is not a note-to-self: it changes which schedules are legal,
    and the options already on screen were validated against the facts as they stood before the
    click. So every rescue still *awaiting a decision* is re-validated here, and the option that
    breaks the newly-accepted rule turns red where it sits.

    Two runs are deliberately left alone. An **applied** run is the record of what was approved and
    on what grounds — rewriting its verdicts would edit history to match a later opinion. A
    **running** one validates against live facts at its own candidates step anyway, and the poll
    picks that up.

    Scope is every awaiting run rather than only the fact's own location: `external_rule_check`
    already filters facts to the locations a day actually books, so a day the fact cannot touch
    simply reports no change.
    """
    from ..domain.models import ActivityEvent
    from ..services.recovery import revalidate_options

    for run in repo.list_runs(p.id, RunKind.RESCUE.value):
        if run.status != RunStatus.AWAITING_APPROVAL or not run.rescue or not run.rescue.options:
            continue
        try:
            day = p.shoot_day(run.rescue.shoot_day_id)
        except KeyError:
            continue
        disruption = next((d for d in p.disruptions if d.id == run.rescue.disruption_id), None)
        flips = revalidate_options(p, day, disruption, run.rescue)
        repo.save_run(run)

        changed = ", ".join(f"option {f['label']} → {'feasible' if f['now_feasible'] else 'infeasible'}" for f in flips)
        repo.log(
            ActivityEvent(
                run_id=run.id,
                project_id=p.id,
                kind="deterministic",
                message=(
                    f"Re-validated {len(run.rescue.options)} recovery option(s) for Day {day.day_number} after {cause}"
                    + (f" — {changed}" if flips else " — no verdict changed")
                ),
                meta={"run_id": run.id, "flips": flips, **meta_extra},
            )
        )
        # The recommendation is not re-picked here (ranking belongs to the run), but a recommendation
        # that just became illegal has to say so: `approve()` will refuse it and the button is disabled.
        if any(f["option_id"] == run.rescue.recommended_option_id and not f["now_feasible"] for f in flips):
            repo.log(
                ActivityEvent(
                    run_id=run.id,
                    project_id=p.id,
                    kind="warning",
                    message=(
                        f"The recommended recovery for Day {day.day_number} is no longer feasible under the "
                        "accepted rules — it cannot be approved as it stands"
                    ),
                    meta={"run_id": run.id, "option_id": run.rescue.recommended_option_id, **meta_extra},
                )
            )


@app.get("/api/features")
def features() -> dict[str, Any]:
    """Which deep-Parallel integrations this deployment has enabled, and how to enable the rest.

    The web app renders a disabled button with the reason rather than hiding it, so the capability
    stays visible even when it is off. `budget` is the other half of that honesty: what this
    deployment has left to spend before the same buttons start refusing for a different reason.
    """
    return {"features": feature_state(settings), "budget": call_budget.state(settings)}


# --------------------------------------------------------------------------- #
# Parallel Task — location dossiers whose cited facts become production constraints.
# Slow (1–5 min) and paid, so always an explicit producer action, never implicit.
# --------------------------------------------------------------------------- #


def _dossier_view(p, resource_id: str | None = None) -> dict[str, Any]:
    facts = [f for f in p.location_facts if resource_id is None or f.resource_id == resource_id]
    # Location dossiers only. Weather timelines are Task runs against the same project and would
    # otherwise arrive unfiltered in the project-wide view, where the panel has nothing to render
    # them as — they are day-scoped and carry no resource.
    runs = [t for t in repo.list_task_runs(project_id=p.id, resource_id=resource_id) if t.purpose == "location_dossier"]
    # Which locations are showing a *replayed* dossier (seeded or served after a live failure) rather
    # than research this deployment ran itself. The panel says so; "Re-research" is the live path.
    latest: dict[str | None, bool] = {}
    for t in repo.list_task_runs(project_id=p.id):
        if t.status in {"OK", "REPLAY"} and t.purpose == "location_dossier":
            latest[t.resource_id] = t.replayed
    watches = [m for m in p.monitors if m.monitor_type == "snapshot" and (resource_id is None or m.resource_id == resource_id)]
    changes = [c for c in p.fact_changes if resource_id is None or c.resource_id == resource_id]
    return {
        "facts": [f.model_dump(mode="json") for f in facts],
        "task_runs": [t.model_dump(mode="json") for t in runs],
        "watches": [m.model_dump(mode="json") for m in watches],
        "fact_changes": [c.model_dump(mode="json") for c in sorted(changes, key=lambda c: (not c.pending, c.detected_at))],
        "locations": [
            {
                "id": r.id,
                "name": r.name,
                "fact_count": len([f for f in p.location_facts if f.resource_id == r.id]),
                "binding_count": len(binding_facts(p, r.id)),
                "watched": any(m.resource_id == r.id and m.monitor_type == "snapshot" and m.status != "cancelled" for m in p.monitors),
                "pending_changes": len(pending_changes(p, r.id)),
                "replayed": latest.get(r.id, False),
            }
            for r in location_resources(p)
        ],
        "processor": settings.parallel_task_processor,
        "live_watch_possible": bool(settings.public_base_url and settings.parallel_configured),
    }


@app.get("/api/projects/{project_id}/dossiers")
def list_dossiers(project_id: str, resource_id: str | None = None) -> dict[str, Any]:
    """What Parallel has discovered about this production's locations. Reads state only."""
    return _dossier_view(_project(project_id), resource_id)


@app.post("/api/projects/{project_id}/resources/{resource_id}/dossier")
async def research_location(project_id: str, resource_id: str, date: str | None = None) -> dict[str, Any]:
    """Run one Parallel Task dossier for a location and grade the result into LocationFacts."""
    require_feature("task", settings)
    p = _project(project_id)
    try:
        resource = p.resource(resource_id)
    except KeyError:
        raise HTTPException(404, "resource not found")
    if resource.type != ResourceType.LOCATION:
        raise HTTPException(400, "dossiers are only meaningful for locations")
    require_budget("dossier", resource_id, settings=settings)

    scope = memory_scope_key(p, settings) if settings.parallel_memory_enabled else None
    tool = ParallelTaskTool(p, settings=settings, memory_scope_key=scope, on_event=lambda kind, msg, meta: _log_project(p, kind, msg, meta))
    # Re-researching a location continues the earlier investigation rather than starting cold.
    prior = next((t.interaction_id for t in reversed(repo.list_task_runs(project_id=p.id, resource_id=resource_id))
                  if t.interaction_id and t.status in {"OK", "REPLAY"}), None)
    try:
        task_run = await asyncio.to_thread(tool.dossier, resource, date, prior)
    except ReplayMiss as exc:
        raise HTTPException(503, f"replay mode has no recording for this dossier: {exc}")
    repo.save_task_run(task_run)

    facts = map_facts(task_run, p)
    # Diffed before the merge, never after: `changes_from_recheck` compares the new dossier against
    # what the production currently believes, and after a merge there is nothing left to compare to.
    prior_output = next(
        (t.output for t in reversed(repo.list_task_runs(project_id=p.id, resource_id=resource_id))
         if t.id != task_run.id and t.status in {"OK", "REPLAY"} and t.purpose == "location_dossier"),
        None,
    )
    withheld = merge_facts(p, resource_id, facts)
    if withheld and prior_output is not None:
        # A rule the producer accepted is still holding this schedule up, so the newcomer arrives as a
        # decision rather than as a replacement — the same route a monitor's finding takes, and the
        # same card renders it. Without this the accepted rule was dropped on the floor and every
        # option rejected for breaching it went quietly feasible again.
        keys = {f.key for f in withheld}
        for change in changes_from_recheck(p, resource_id, task_run, prior_output):
            if change.key in keys and not any(c.key == change.key and c.pending and c.resource_id == resource_id for c in p.fact_changes):
                p.fact_changes.append(change)
    if scope and p.memory_scope_key != scope:
        p.memory_scope_key = scope
    repo.save_project(p)

    hard = [f for f in facts if f.binding == FactBinding.HARD]
    if task_run.status in {"OK", "REPLAY"}:
        held = f"; {len(withheld)} change(s) to rules already in force are waiting on a decision" if withheld else ""
        _log_project(
            p,
            "parallel",
            f"Dossier for {resource.name}: {len(facts)} fact(s), {len(hard)} proposed as hard constraints awaiting producer acceptance{held}",
            {"task_run_id": task_run.id, "resource_id": resource_id, "withheld_keys": [f.key for f in withheld]},
        )
    # Any fact-set mutation can change which schedules are legal, and this endpoint was the one door
    # that never said so — a re-research could move what binds and leave a green option list beside it.
    _revalidate_open_rescues(p, cause=f"a re-research of {resource.name} changed what this production knows", meta_extra={"resource_id": resource_id, "task_run_id": task_run.id})
    return {"task_run": task_run.model_dump(mode="json"), **_dossier_view(p, resource_id)}


def _latest_dossier(project_id: str, resource_id: str):
    """The newest dossier that actually completed — the only thing a snapshot monitor can watch."""
    runs = repo.list_task_runs(project_id=project_id, resource_id=resource_id)
    return next((t for t in reversed(runs) if t.status in {"OK", "REPLAY"} and t.purpose == "location_dossier"), None)


# --------------------------------------------------------------------------- #
# Parallel Task — the hourly weather timeline behind the disruption scrubber.
# Day-scoped rather than location-scoped, and priced the same as a dossier.
# --------------------------------------------------------------------------- #


def _latest_weather(project_id: str, day_id: str):
    """The newest completed weather timeline for a day, or None if nobody has researched it."""
    runs = repo.list_task_runs(project_id=project_id)
    return next(
        (t for t in reversed(runs) if t.purpose == "weather_timeline" and t.shoot_day_id == day_id and t.status in {"OK", "REPLAY"}),
        None,
    )


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/weather-timeline")
def get_weather_timeline(project_id: str, day_id: str) -> dict[str, Any]:
    """The hourly precipitation timeline researched for this day. Reads state only.

    `{"timeline": null}` is the honest answer for a day nobody has researched — the UI renders the
    named, priced button rather than an axis of empty bars.
    """
    p = _project(project_id)
    try:
        p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    return {"timeline": map_timeline(_latest_weather(p.id, day_id))}


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/weather-timeline")
async def research_weather_timeline(project_id: str, day_id: str) -> dict[str, Any]:
    """Run one Parallel Task weather timeline for a shoot day: every hour cited separately."""
    require_feature("task", settings)
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    if day.status == ShootDayStatus.WRAPPED:
        raise HTTPException(
            409,
            f"Day {day.day_number} wrapped on {day.date}; forecasting a day that has already been shot "
            "would spend money answering a question nobody has.",
        )
    require_budget("weather", day.id, settings=settings)

    scope = memory_scope_key(p, settings) if settings.parallel_memory_enabled else None
    tool = ParallelTaskTool(p, settings=settings, memory_scope_key=scope, on_event=lambda kind, msg, meta: _log_project(p, kind, msg, meta))
    try:
        task_run = await asyncio.to_thread(tool.weather_timeline, day)
    except ReplayMiss as exc:
        raise HTTPException(503, f"replay mode has no recording for this weather timeline: {exc}")
    repo.save_task_run(task_run)
    if scope and p.memory_scope_key != scope:
        p.memory_scope_key = scope
        repo.save_project(p)

    timeline = map_timeline(task_run)
    if timeline is not None:
        _log_project(
            p,
            "parallel",
            f"Hourly weather timeline for Day {day.day_number}: {len(timeline['hours'])} hour(s) answered, "
            f"{timeline['cited_hours']} with their own citations",
            {"task_run_id": task_run.id, "shoot_day_id": day.id},
        )
    return {"task_run": task_run.model_dump(mode="json"), "timeline": timeline}


@app.post("/api/projects/{project_id}/resources/{resource_id}/watch")
def watch_location(project_id: str, resource_id: str) -> dict[str, Any]:
    """Watch this location's dossier for change with a Parallel snapshot monitor."""
    require_feature("task", settings)
    p = _project(project_id)
    try:
        resource = p.resource(resource_id)
    except KeyError:
        raise HTTPException(404, "resource not found")
    existing = next((m for m in p.monitors if m.resource_id == resource_id and m.monitor_type == "snapshot" and m.status != "cancelled"), None)
    if existing is not None:
        return {"monitor": existing.model_dump(mode="json"), **_dossier_view(p, resource_id)}
    task_run = _latest_dossier(p.id, resource_id)
    if task_run is None:
        raise HTTPException(400, "research this location first — a snapshot monitor watches a dossier's output")
    if not settings.public_base_url:
        raise HTTPException(400, "PUBLIC_BASE_URL is not set — Parallel needs a reachable webhook URL. Use 'simulate' locally.")
    require_budget("monitors", resource_id, settings=settings)
    tool = ParallelMonitorTool()
    try:
        record = tool.watch_dossier(p, resource, task_run, f"{settings.public_base_url}/api/webhooks/parallel")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Parallel snapshot monitor create failed: {exc}")
    p.monitors.append(record)
    repo.save_project(p)
    _log_project(p, "parallel", f"Watching {resource.name} for rule changes — Parallel re-runs its dossier every {record.frequency} and reports only what moved", {"monitor_id": record.id, "resource_id": resource_id, "task_run_id": task_run.id})
    return {"monitor": record.model_dump(mode="json"), **_dossier_view(p, resource_id)}


@app.post("/api/projects/{project_id}/resources/{resource_id}/watch/simulate")
def simulate_snapshot_event(project_id: str, resource_id: str) -> dict[str, Any]:
    """Demo path: a fabricated snapshot diff goes through the same ingestion as a real webhook."""
    p = _project(project_id)
    try:
        resource = p.resource(resource_id)
    except KeyError:
        raise HTTPException(404, "resource not found")
    monitor = next((m for m in p.monitors if m.resource_id == resource_id and m.monitor_type == "snapshot"), None)
    if monitor is None:
        task_run = _latest_dossier(p.id, resource_id)
        monitor = MonitorRecord(
            id=f"monitor_simulated_snapshot_{resource_id}", project_id=p.id, kind="DOSSIER", monitor_type="snapshot",
            task_run_id=task_run.id if task_run else None, resource_id=resource_id,
            query=f"Changes to the filming rules for {resource.name}", frequency="1d", status="simulated",
        )
        p.monitors.append(monitor)
    event = {"event_id": f"mevt_sim_snap_{utcnow().strftime('%H%M%S')}", "event_group_id": "mevtgrp_simulated", "event_type": "snapshot", "event_date": utcnow().date().isoformat(), **SIMULATED_SNAPSHOT}
    created = _ingest_snapshot(p, monitor, event, simulated=True)
    return {"changes": [c.model_dump(mode="json") for c in created], **_dossier_view(p, resource_id)}


class ChangeDecision(BaseModel):
    decided_by: str = "producer"


@app.post("/api/projects/{project_id}/fact-changes/{change_id}/{decision}")
def decide_fact_change(project_id: str, change_id: str, decision: str, body: ChangeDecision | None = None) -> dict[str, Any]:
    """Adopt the changed value or keep the one the production already accepted."""
    if decision not in {"adopt", "dismiss"}:
        raise HTTPException(404, "unknown decision")
    p = _project(project_id)
    change = next((c for c in p.fact_changes if c.id == change_id), None)
    if change is None:
        raise HTTPException(404, "change not found")
    if not change.pending:
        raise HTTPException(409, f"this change was already {change.status.lower()}")
    who = body.decided_by if body else "producer"
    if decision == "adopt":
        fact = adopt_change(p, change, who)
        note = " — it must be accepted again before it constrains the schedule" if change.binding == FactBinding.HARD else ""
        msg = f"Producer adopted a change Parallel detected — {change.label}: {change.old_value or '(nothing)'} → {change.new_value}{note}"
        meta = {"change_id": change.id, "fact_id": fact.id, "resource_id": change.resource_id, "binding": change.binding.value}
    else:
        dismiss_change(change, who)
        msg = f"Producer kept the existing value for {change.label} — {change.old_value or '(nothing)'} (Parallel reported: {change.new_value[:80]})"
        meta = {"change_id": change.id, "resource_id": change.resource_id}
    repo.save_project(p)
    _log_project(p, "decision", msg, meta)
    # Adopting a change rewrites what the fact says (and clears its acceptance when it was binding),
    # so the schedule's verdicts have to answer to the new value. Dismissing changes nothing and is
    # re-validated anyway, which costs nothing and keeps the two paths honest about saying so.
    _revalidate_open_rescues(p, cause=f"the producer {decision}ed the change to '{change.label}'", meta_extra={"change_id": change.id, "resource_id": change.resource_id})
    return {"change": change.model_dump(mode="json"), **_dossier_view(p, change.resource_id)}


async def _recheck_one(p, resource_id: str, prior_output: dict[str, Any]) -> dict[str, Any]:
    """One location's re-check: a fresh dossier, compared against what we already believed."""
    resource = p.resource(resource_id)
    scope = memory_scope_key(p, settings) if settings.parallel_memory_enabled else None
    tool = ParallelTaskTool(p, settings=settings, memory_scope_key=scope, on_event=lambda kind, msg, meta: _log_project(p, kind, msg, meta))
    prior = next((t.interaction_id for t in reversed(repo.list_task_runs(project_id=p.id, resource_id=resource_id))
                  if t.interaction_id and t.status in {"OK", "REPLAY"}), None)
    try:
        # Deliberately the *same* question the Research button asked — a dossier is about standing
        # rules, not a forecast — so the comparison is like-for-like and replay keys still line up.
        task_run = await asyncio.to_thread(tool.dossier, resource, None, prior)
    except ReplayMiss as exc:
        return {"resource_id": resource_id, "name": resource.name, "status": "NO_RECORDING", "changes": 0, "detail": str(exc)}
    repo.save_task_run(task_run)
    if task_run.status not in {"OK", "REPLAY"}:
        return {"resource_id": resource_id, "name": resource.name, "status": task_run.status, "changes": 0, "task_run_id": task_run.id}
    # Note what we do *not* do: merge_facts. A re-check never rewrites what the producer accepted —
    # that is the whole difference between this and "Re-research". It only reports.
    found = changes_from_recheck(p, resource_id, task_run, prior_output)
    return {"resource_id": resource_id, "name": resource.name, "status": task_run.status, "changes": len(found), "task_run_id": task_run.id, "_found": found}


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/preflight")
async def preflight_day(project_id: str, day_id: str) -> dict[str, Any]:
    """Re-verify every researched location on a day before it locks, and report what moved.

    The honest counterpart to the snapshot monitors: those watch on Parallel's schedule, this is the
    producer asking on purpose at the one moment it matters. Costs a Task run per location, so it is
    an explicit button that prices itself first, like every other paid call in the product.
    """
    require_feature("task", settings)
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")

    location_ids = list(dict.fromkeys(i.location_id for i in day.items if i.location_id))
    latest = {rid: _latest_dossier(p.id, rid) for rid in location_ids}
    researched = [rid for rid in location_ids if latest[rid] is not None]
    unresearched = [{"id": rid, "name": p.resource(rid).name} for rid in location_ids if latest[rid] is None]
    if researched:
        require_budget("preflight", day.id, units=len(researched), settings=settings)

    # Concurrent: two locations take as long as one, and a producer is waiting.
    results = await asyncio.gather(*[_recheck_one(p, rid, latest[rid].output or {}) for rid in researched])

    created: list[Any] = []
    for r in results:
        created.extend(r.pop("_found", []))
    if created:
        p.fact_changes.extend(created)
    repo.save_project(p)

    urgent = [c for c in created if c.old_accepted and (c.old_binds or c.affects_schedule)]
    if created:
        _log_project(p, "warning" if urgent else "parallel",
                     f"Pre-flight for Day {day.day_number}: {len(created)} rule(s) changed since this production last looked"
                     + (f", {len(urgent)} of which the schedule is currently being enforced against" if urgent else ""),
                     {"shoot_day_id": day.id, "change_ids": [c.id for c in created]})
    else:
        _log_project(p, "parallel",
                     f"Pre-flight for Day {day.day_number}: re-verified {len(researched)} location(s) against Parallel — nothing has changed",
                     {"shoot_day_id": day.id, "checked": researched})

    return {
        "checked": results,
        "unresearched": unresearched,
        "changes": [c.model_dump(mode="json") for c in created],
        "urgent": len(urgent),
        **_dossier_view(p),
    }


@app.get("/api/task-runs/{task_run_id}")
def get_task_run(task_run_id: str) -> dict[str, Any]:
    tr = repo.get_task_run(task_run_id)
    if tr is None:
        raise HTTPException(404, "task run not found")
    return tr.model_dump(mode="json")


class FactDecision(BaseModel):
    accepted_by: str = "producer"


@app.post("/api/projects/{project_id}/facts/{fact_id}/{decision}")
def decide_fact(project_id: str, fact_id: str, decision: str, body: FactDecision | None = None) -> dict[str, Any]:
    """Accept or reject a discovered fact. Only an accepted HARD fact ever constrains a schedule."""
    if decision not in {"accept", "reject"}:
        raise HTTPException(404, "unknown decision")
    p = _project(project_id)
    fact = next((f for f in p.location_facts if f.id == fact_id), None)
    if fact is None:
        raise HTTPException(404, "fact not found")
    if decision == "accept":
        fact.accepted, fact.rejected = True, False
        fact.accepted_at, fact.accepted_by = utcnow(), (body.accepted_by if body else "producer")
        verb = "accepted" if fact.binds else "acknowledged"
        note = " — it now rejects any option that breaks it" if fact.binds else " (advisory: it informs, but does not constrain the schedule)"
    else:
        fact.accepted, fact.rejected = False, True
        verb, note = "rejected", ""
    repo.save_project(p)
    _log_project(p, "decision", f"Producer {verb} a fact Parallel found — {fact.label}: {fact.value}{note}", {"fact_id": fact.id, "resource_id": fact.resource_id, "binding": fact.binding.value})
    _revalidate_open_rescues(p, cause=f"the producer {verb} '{fact.label}'", meta_extra={"fact_id": fact.id, "resource_id": fact.resource_id})
    return {"fact": fact.model_dump(mode="json"), **_dossier_view(p, fact.resource_id)}


# --------------------------------------------------------------------------- #
# Parallel FindAll — real substitute suppliers when the production loses a resource.
# Costs money per run, so always an explicit producer action.
# --------------------------------------------------------------------------- #


def _substitutes_view(p, resource_id: str | None = None) -> dict[str, Any]:
    runs = repo.list_findall_runs(project_id=p.id, resource_id=resource_id)
    return {
        "findall_runs": [r.model_dump(mode="json") for r in runs],
        "mode": settings.parallel_findall_mode,
        "match_limit": settings.parallel_findall_match_limit,
    }


@app.get("/api/projects/{project_id}/substitutes")
def list_substitutes(project_id: str, resource_id: str | None = None) -> dict[str, Any]:
    return _substitutes_view(_project(project_id), resource_id)


@app.post("/api/projects/{project_id}/resources/{resource_id}/substitutes")
async def find_substitutes(project_id: str, resource_id: str, shoot_day_id: str | None = None, note: str | None = None, mode: str | None = None) -> dict[str, Any]:
    """Ask Parallel for real suppliers who could replace a resource the production has lost."""
    require_feature("findall", settings)
    p = _project(project_id)
    try:
        resource = p.resource(resource_id)
    except KeyError:
        raise HTTPException(404, "resource not found")
    require_budget("substitutes", resource_id, settings=settings)

    scope = memory_scope_key(p, settings) if settings.parallel_memory_enabled else None
    tool = ParallelFindAllTool(p, settings=settings, memory_scope_key=scope, on_event=lambda kind, msg, meta: _log_project(p, kind, msg, meta))
    findall_run = await asyncio.to_thread(tool.find_substitutes, resource, shoot_day_id=shoot_day_id, note=note, mode=mode)
    repo.save_findall_run(findall_run)
    return {"findall_run": findall_run.model_dump(mode="json"), **_substitutes_view(p, resource_id)}


def _resource_is_called_by(project, resource, item) -> bool:
    """Does this day's strip actually need this resource? Locations count as the item's own location."""
    try:
        scene = project.scene(item.scene_id)
    except KeyError:
        return False
    if resource.id == (item.location_id or scene.location_id):
        return True
    return resource.id in scene.cast_ids or resource.id in scene.equipment_ids


class ClearanceInput(BaseModel):
    shoot_day_id: str
    start: str = "00:00"
    end: str = "23:59"
    note: str | None = Field(default=None, max_length=500)
    cleared_by: str = "producer"


@app.post("/api/projects/{project_id}/resources/{resource_id}/availability")
def clear_resource(project_id: str, resource_id: str, body: ClearanceInput) -> dict[str, Any]:
    """Book a resource onto a day — the write that was missing behind every "not cleared" blank.

    `Resource.availability` is read by the validator, the heatmap, the ripple panel and the call
    sheet, and until now it was written only by the seed and `seed/migrate.py`. So a committed pickup
    day named the three people nobody had booked onto it and offered no way to book them, and the day
    page's constraints panel called that "a gap in the production data" with no remedy.

    Replaces rather than appends. `Availability` has no id, so a second row for the same day could
    never be named by the release below — and `is_available` passes if *any* window covers the item,
    so two rows quietly widen the booking instead of correcting it.
    """
    p = _project(project_id)
    try:
        resource = p.resource(resource_id)
    except KeyError:
        raise HTTPException(404, "resource not found")
    try:
        day = p.shoot_day(body.shoot_day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    _refuse_if_wrapped(day)
    start = _minutes_or_400("start", body.start, day)
    end = _minutes_or_400("end", body.end, day)
    if end <= start:
        raise HTTPException(400, f"the window {body.start}–{body.end} ends before it starts.")

    resource.availability = [a for a in resource.availability if a.shoot_day_id != day.id]
    resource.availability.append(Availability(shoot_day_id=day.id, start=body.start, end=body.end, note=body.note))
    # Booking them back on retracts the release, so the seed migration may fill this day in again if
    # the window is ever removed by something other than a producer.
    resource.released_day_ids = [d for d in resource.released_day_ids if d != day.id]
    repo.save_project(p)

    # What a clearance does *not* fix belongs on the record with it: a window that does not cover the
    # scene the day schedules is a booking that still leaves the day invalid, and it looks like a fix.
    short = [
        i for i in day.items
        if _resource_is_called_by(p, resource, i)
        and not is_available(resource, day, to_minutes(i.start), to_minutes(i.end))
    ]
    detail = ""
    if short:
        scenes = ", ".join(f"Sc {p.scene(i.scene_id).number} {i.start}–{i.end}" for i in short)
        detail = f" — but it does not cover {scenes}, which the day still schedules them for"
    _log_project(
        p, "decision",
        f"Producer cleared {resource.name} for Day {day.day_number} {body.start}–{body.end}{detail}",
        {"resource_id": resource.id, "shoot_day_id": day.id, "start": body.start, "end": body.end, "uncovered_item_ids": [i.id for i in short]},
    )
    return {"resource": resource.model_dump(mode="json"), "day": day.model_dump(mode="json")}


@app.delete("/api/projects/{project_id}/resources/{resource_id}/availability")
def release_resource(project_id: str, resource_id: str, shoot_day_id: str) -> dict[str, Any]:
    """Give a day's booking back. Only ever removes a window that names this day."""
    p = _project(project_id)
    try:
        resource = p.resource(resource_id)
    except KeyError:
        raise HTTPException(404, "resource not found")
    try:
        day = p.shoot_day(shoot_day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    _refuse_if_wrapped(day)
    remaining = [a for a in resource.availability if a.shoot_day_id != day.id]
    if len(remaining) == len(resource.availability):
        raise HTTPException(
            404,
            f"{resource.name} has no window naming Day {day.day_number}. "
            + ("They are booked with no day stated, which the validator reads as every day, so there is nothing here to release."
               if any(a.shoot_day_id is None for a in resource.availability)
               else "There is nothing here to release."),
        )
    resource.availability = remaining
    # Recorded as a decision, not left as an absence: `seed/migrate.py` puts a seeded booking back on
    # any day a resource has no window for, which would undo this on the next read of the project.
    if day.id not in resource.released_day_ids:
        resource.released_day_ids.append(day.id)
    repo.save_project(p)
    _log_project(
        p, "decision", f"Producer released {resource.name} from Day {day.day_number}",
        {"resource_id": resource.id, "shoot_day_id": day.id},
    )
    return {"resource": resource.model_dump(mode="json"), "day": day.model_dump(mode="json")}


class RiskDecision(BaseModel):
    status: RiskStatus
    owner: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2_000)
    decided_by: str = "producer"


@app.post("/api/projects/{project_id}/risks/{risk_id}/decide")
def decide_risk(project_id: str, risk_id: str, body: RiskDecision) -> dict[str, Any]:
    """Give a risk an owner and a verdict. The register is a decision log or it is a printout.

    Returns the whole register rather than the one row: severity buckets, counts and the ordering are
    all derived from the set, so a caller handed a single row would have to re-derive them or fetch
    again. `decide_fact` already answers with the whole dossier view for the same reason.
    """
    p = _project(project_id)
    found = find_risk(p, risk_id)
    if found is None:
        raise HTTPException(404, "risk not found — it belongs to a scene that has not been planned, or the plan has been replaced")
    scene_id, risk = found
    risk.status, risk.owner, risk.decision_note = body.status, body.owner, body.note
    risk.decided_by, risk.decided_at = body.decided_by, utcnow()
    repo.save_project(p)
    scene = p.scene(scene_id)
    owner = f" — {risk.owner}" if risk.owner else ""
    _log_project(
        p, "decision",
        f"Producer marked the risk '{risk.title[:80]}' on Scene {scene.number} as {risk.status.value.lower()}{owner}",
        {"risk_id": risk.id, "scene_id": scene_id, "status": risk.status.value, "owner": risk.owner},
    )
    return build_risk_register(p)


class BoardCommit(BaseModel):
    items: list[dict[str, str]]
    reason: str | None = Field(default=None, max_length=500)
    committed_by: str = "producer"


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/commit-schedule")
def commit_schedule(project_id: str, day_id: str, body: BoardCommit) -> dict[str, Any]:
    """Keep the times a producer nudged on the board, re-validated under the pack in force.

    `/simulate-strip-move` is the what-if and takes a labour preset; this is the commit and takes
    none. A board previewed against DGA/SAG is a real question, but the production is held to its own
    agreement whatever the selector reads, so the preview and the commit cannot be one endpoint.
    """
    require_capability("commit_board", settings)
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    # After a hand edit the rescue's own `baseline` no longer describes the day, so a later revert
    # would restore the pre-edit board and throw the producer's work away without saying so.
    _refuse_if_rescue_in_flight(p.id, day)
    try:
        result = commit_board(p, day_id, body.items, reason=body.reason, committed_by=body.committed_by)
    except CommitRefused as exc:
        raise HTTPException(409, str(exc))

    changeset = result["changeset"]
    repo.save_changeset(changeset)
    repo.save_project(p)
    _log_project(
        p, "approval",
        f"Producer committed the board for Day {day.day_number} — {len(changeset.changes)} time(s) changed",
        {"shoot_day_id": day.id, "changeset_id": changeset.id},
    )
    return {"changeset": changeset.model_dump(mode="json"), "day": result["day"].model_dump(mode="json"), "notes": result["notes"]}


class WrapRequest(BaseModel):
    items: list[WrapOutcome]
    camera_wrap: str | None = None
    wrapped_by: str = "producer"


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/wrap")
def wrap_shoot_day(project_id: str, day_id: str, body: WrapRequest) -> dict[str, Any]:
    """Close a day out: every strip shot or carried, and the day becomes a record.

    The write that made three shipped features reachable. `day_completion` has computed a per-scene
    record on every day payload since it was written and nothing could ever produce a day for it to
    describe; `day_cost` has had a record branch it could never take; and the DPR refused every day
    but the one the seed ships wrapped.
    """
    require_capability("wrap", settings)
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    # A recovery approved after the wrap would apply a change set to a day that has already been
    # shot, so the decision has to be settled first — approve it, revert it, or stand it down.
    _refuse_if_rescue_in_flight(p.id, day)
    try:
        result = wrap_day(p, day_id, body.items, camera_wrap=body.camera_wrap, wrapped_by=body.wrapped_by)
    except WrapRefused as exc:
        raise HTTPException(409, str(exc))

    changeset = result["changeset"]
    repo.save_changeset(changeset)
    repo.save_project(p)
    carried = result["carried_scene_ids"]
    tail = f"; {len(carried)} scene(s) carried" if carried else "; nothing carried"
    _log_project(
        p, "approval",
        f"Producer wrapped Day {day.day_number} at {day.camera_wrap or 'an unrecorded time'}{tail}",
        {"shoot_day_id": day.id, "changeset_id": changeset.id, "carried_scene_ids": carried},
    )
    return {
        "day": result["day"].model_dump(mode="json"),
        "completion": result["completion"],
        "changeset": changeset.model_dump(mode="json"),
    }


@app.get("/api/projects/{project_id}/draft-disruptions")
def list_draft_disruptions(project_id: str) -> dict[str, Any]:
    """Every monitor-detected draft on the production, so one firing on Day 6 is visible from Day 4.

    Drafts were reachable only through the day page's monitor panel, which renders on one day at a
    time — so a monitor that fired while the producer was reading another day announced itself to
    nobody. `detected_at` is `received_at`: when we ingested the event, not `monitor_event.event_date`,
    which is the date the event is *about* and is date-only.
    """
    p = _project(project_id)
    days = {d.id: d for d in p.shoot_days}
    drafts = []
    for d in p.disruptions:
        if not d.draft:
            continue
        day = days.get(d.shoot_day_id)
        drafts.append({
            "disruption": d.model_dump(mode="json"),
            "shoot_day_id": d.shoot_day_id,
            "day_number": day.day_number if day else None,
            "date": day.date if day else None,
            "monitor_id": d.monitor_id,
            "detected_at": d.received_at.isoformat() if d.received_at else None,
        })
    drafts.sort(key=lambda x: (x["detected_at"] or ""), reverse=True)
    return {"drafts": drafts}


@app.delete("/api/findall-runs/{findall_run_id}/select")
def unselect_vendor(findall_run_id: str) -> dict[str, Any]:
    """Take the choice back. A shortlist a producer is still thinking about is not a decision.

    `select_vendor` sets one and clears the rest, so there was no way to return to *none chosen* — the
    UI disabled the button once a vendor was picked and the chip stayed on the card forever. Nothing
    downstream needs undoing: a selection only reaches production state through `derive_actions`
    inside `approve()`, so until a recovery is approved this is the whole of the record.
    """
    fr = repo.get_findall_run(findall_run_id)
    if fr is None:
        raise HTTPException(404, "findall run not found")
    chosen = next((v for v in fr.candidates if v.selected), None)
    if chosen is None:
        raise HTTPException(409, "no replacement is selected on this search, so there is nothing to take back.")
    for v in fr.candidates:
        v.selected = False
    repo.save_findall_run(fr)
    p = _project(fr.project_id) if fr.project_id else None
    if p is not None:
        resource = p.resource(fr.resource_id) if fr.resource_id else None
        _log_project(
            p,
            "decision",
            f"Producer withdrew {chosen.name} as the replacement for {resource.name if resource else fr.resource_id}",
            {"findall_run_id": fr.id, "vendor_id": chosen.id},
        )
    return {"findall_run": fr.model_dump(mode="json")}


@app.post("/api/findall-runs/{findall_run_id}/select/{vendor_id}")
def select_vendor(findall_run_id: str, vendor_id: str) -> dict[str, Any]:
    """The producer picks a replacement. This records the choice; applying it stays a ChangeSet."""
    fr = repo.get_findall_run(findall_run_id)
    if fr is None:
        raise HTTPException(404, "findall run not found")
    vendor = next((v for v in fr.candidates if v.id == vendor_id), None)
    if vendor is None:
        raise HTTPException(404, "vendor not found")
    for v in fr.candidates:
        v.selected = v.id == vendor_id
    repo.save_findall_run(fr)
    p = _project(fr.project_id) if fr.project_id else None
    if p is not None:
        resource = p.resource(fr.resource_id) if fr.resource_id else None
        _log_project(
            p,
            "decision",
            f"Producer selected {vendor.name} as the replacement for {resource.name if resource else fr.resource_id} — it will appear on the call sheet when the recovery is approved",
            {"findall_run_id": fr.id, "vendor_id": vendor.id, "vendor_url": vendor.url},
        )
    return {"findall_run": fr.model_dump(mode="json")}


# --------------------------------------------------------------------------- #
# Parallel Memory — the production's accumulated web knowledge.
# Every route here is an explicit producer action; nothing reads memory on its own.
# --------------------------------------------------------------------------- #


def _memory_tool(p) -> ParallelMemoryTool:
    return ParallelMemoryTool(p, settings=settings, on_event=lambda kind, msg, meta: _log_project(p, kind, msg, meta))


@app.get("/api/projects/{project_id}/memory")
async def read_memory(project_id: str, query: str = "", limit: int = 10, kind: str | None = None) -> dict[str, Any]:
    """Recall what Parallel has learned for this production (Task + Monitor + FindAll runs)."""
    require_feature("memory", settings)
    p = _project(project_id)
    tool = _memory_tool(p)
    try:
        read = await asyncio.to_thread(tool.retrieve, query, limit, kind)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    repo.save_memory_read(read)
    if p.memory_scope_key != tool.scope:
        p.memory_scope_key = tool.scope
        repo.save_project(p)
    return {
        "read": read.model_dump(mode="json"),
        "scope_key": tool.scope,
        "writes_memory": {"monitors": bool(p.monitors), "task": settings.parallel_task_enabled, "findall": settings.parallel_findall_enabled},
        "recent": [r.model_dump(mode="json") for r in repo.list_memory_reads(project_id, limit=8)],
    }


class MemoryEvictRequest(BaseModel):
    kind: str
    ref_id: str


@app.post("/api/projects/{project_id}/memory/evict")
async def evict_memory(project_id: str, body: MemoryEvictRequest) -> dict[str, Any]:
    """Producer marks one remembered run stale. The underlying run is untouched."""
    require_feature("memory", settings)
    require_parallel_key(settings)
    p = _project(project_id)
    tool = _memory_tool(p)
    try:
        await asyncio.to_thread(tool.evict, body.kind, body.ref_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Parallel memory evict failed: {exc}")
    return {"ok": True, "evicted": {"kind": body.kind, "ref_id": body.ref_id}, "scope_key": tool.scope}


@app.delete("/api/projects/{project_id}/memory")
async def clear_memory(project_id: str, confirm: bool = False) -> dict[str, Any]:
    """Forget this production's whole scope. Requires ?confirm=true — it cannot be undone."""
    require_feature("memory", settings)
    require_parallel_key(settings)
    if not confirm:
        raise HTTPException(400, "pass ?confirm=true — clearing a memory scope cannot be undone")
    p = _project(project_id)
    tool = _memory_tool(p)
    try:
        await asyncio.to_thread(tool.clear)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Parallel memory clear failed: {exc}")
    return {"ok": True, "scope_key": tool.scope}


@app.get("/api/projects/{project_id}/shoot-days/{day_id}/monitors")
def list_monitors(project_id: str, day_id: str) -> dict[str, Any]:
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    return {
        "monitors": [m.model_dump(mode="json") for m in p.monitors if m.shoot_day_id == day_id],
        "proposed": monitor_queries(p, day),
        "live_possible": bool(settings.public_base_url and settings.parallel_configured),
        "webhook_url": f"{settings.public_base_url}/api/webhooks/parallel" if settings.public_base_url else None,
        "drafts": [d.model_dump(mode="json") for d in p.disruptions if d.shoot_day_id == day_id and d.draft],
    }


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/monitors")
def create_monitors(project_id: str, day_id: str) -> dict[str, Any]:
    """Create live Parallel monitors for this shoot day (needs a public webhook URL)."""
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    if not settings.public_base_url:
        raise HTTPException(400, "PUBLIC_BASE_URL is not set — Parallel needs a reachable webhook URL. Use 'simulate' locally.")
    if any(m.shoot_day_id == day_id and m.status == "active" for m in p.monitors):
        return {"monitors": [m.model_dump(mode="json") for m in p.monitors if m.shoot_day_id == day_id]}
    require_budget("monitors", day.id, units=len(monitor_queries(p, day)), settings=settings)
    tool = ParallelMonitorTool()
    try:
        records = tool.create_for_day(p, day, f"{settings.public_base_url}/api/webhooks/parallel")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Parallel Monitor create failed: {exc}")
    p.monitors.extend(records)
    repo.save_project(p)
    _log_project(p, "parallel", f"Created {len(records)} Parallel monitor(s) for Day {day.day_number}: " + "; ".join(r.kind.lower() for r in records), {"monitor_ids": [r.id for r in records]})
    return {"monitors": [m.model_dump(mode="json") for m in records]}


@app.post("/api/projects/{project_id}/monitors/{monitor_id}/cancel")
def cancel_monitor(project_id: str, monitor_id: str) -> dict[str, Any]:
    """Stop a monitor at Parallel. The only way its daily charge ever ends."""
    p = _project(project_id)
    monitor = next((m for m in p.monitors if m.id == monitor_id), None)
    if monitor is None:
        raise HTTPException(404, "monitor not known")
    if monitor.status == "active":
        try:
            ParallelMonitorTool(settings=settings).cancel(monitor.id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Parallel monitor cancel failed: {exc}")
    monitor.status = "cancelled"
    repo.save_project(p)
    _log_project(p, "parallel", f"Cancelled Parallel monitor {monitor.id} — it stops executing and stops billing", {"monitor_id": monitor.id})
    return {"monitor": monitor.model_dump(mode="json")}


def _ingest_event(p, day, monitor: MonitorRecord, event: dict[str, Any], simulated: bool) -> Disruption | None:
    if any((d.monitor_event or {}).get("event_id") == event.get("event_id") for d in p.disruptions if event.get("event_id")):
        return None
    d = draft_from_event(p, day, monitor, event, simulated=simulated)
    p.disruptions.append(d)
    monitor.last_event_at = utcnow()
    monitor.event_count += 1
    repo.save_project(p)
    _log_project(p, "parallel", f"Parallel Monitor detected a change{' (simulated event)' if simulated else ''}: {d.title[:90]} — draft disruption awaiting producer confirmation", {"disruption_id": d.id, "monitor_id": monitor.id, "simulated": simulated})
    return d


def _ingest_snapshot(p, monitor: MonitorRecord, event: dict[str, Any], simulated: bool) -> list[FactChange]:
    """A dossier field moved. Record it as pending — the accepted value still rules until a human acts."""
    if event.get("event_id") and any(c.event_id == event["event_id"] for c in p.fact_changes):
        return []
    changes = changes_from_snapshot(p, monitor, event, simulated=simulated)
    if not changes:
        return []
    p.fact_changes.extend(changes)
    monitor.last_event_at = utcnow()
    monitor.event_count += 1
    repo.save_project(p)
    for c in changes:
        # An accepted, binding rule that changed is the one a producer must see today: the schedule
        # is being enforced against a value the source no longer says.
        urgent = c.old_accepted and (c.old_binds or c.affects_schedule)
        _log_project(
            p,
            "warning" if urgent else "parallel",
            f"Parallel Monitor detected a change{' (simulated event)' if simulated else ''} to {c.label} — "
            f"{c.old_value or '(nothing)'} → {c.new_value[:90]}"
            + (" — this rule is currently constraining the schedule" if urgent else ""),
            {"change_id": c.id, "monitor_id": monitor.id, "resource_id": c.resource_id, "fact_id": c.fact_id, "simulated": simulated},
        )
    return changes


@app.post("/api/projects/{project_id}/shoot-days/{day_id}/monitors/simulate")
def simulate_monitor_event(project_id: str, day_id: str, kind: str = "WEATHER") -> dict[str, Any]:
    """Demo path: a fabricated monitor event (clearly labelled) goes through the same ingestion as a real webhook."""
    p = _project(project_id)
    try:
        day = p.shoot_day(day_id)
    except KeyError:
        raise HTTPException(404, "shoot day not found")
    kind = kind.upper()
    if kind not in SIMULATED_EVENTS:
        raise HTTPException(400, f"unknown simulated event kind {kind}")
    monitor = next((m for m in p.monitors if m.shoot_day_id == day_id and m.kind == kind), None)
    if monitor is None:
        spec = next(q for q in monitor_queries(p, day) if q["kind"] == kind)
        monitor = MonitorRecord(id=f"monitor_simulated_{day_id}_{kind.lower()}", project_id=p.id, shoot_day_id=day_id, kind=kind, query=spec["query"], status="simulated")
        p.monitors.append(monitor)
    event = {"event_id": f"mevt_sim_{kind.lower()}_{utcnow().strftime('%H%M%S')}", "event_group_id": "mevtgrp_simulated", "event_date": day.date, "text": SIMULATED_EVENTS[kind], "basis": []}
    d = _ingest_event(p, day, monitor, event, simulated=True)
    return {"disruption": d.model_dump(mode="json") if d else None, "monitor": monitor.model_dump(mode="json")}


def _project_from_metadata(meta: dict[str, Any]):
    pid = meta.get("project_id")
    return repo.get_project(pid) if pid else None


def _monitor_execution_event(payload: dict[str, Any], event_type: str) -> dict[str, Any]:
    """A monitor ran (or failed to). Nothing was detected, so nothing is drafted — just observable."""
    data = payload.get("data") or {}
    monitor_id = data.get("monitor_id")
    p = _project_from_metadata(data.get("metadata") or {})
    if p is None:
        p = next((c for c in repo.list_projects() if any(m.id == monitor_id for m in c.monitors)), None)
    if p is None:
        return {"ok": True, "ignored": "unknown monitor"}
    failed = event_type.endswith("failed")
    _log_project(p, "warning" if failed else "parallel", f"Parallel Monitor {monitor_id} execution {'failed' if failed else 'completed'} with nothing to report", {"monitor_id": monitor_id, "event_type": event_type})
    return {"ok": True, "monitor_id": monitor_id}


def _task_status_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Task and FindAll share `task_run.status`. We poll for results, so this is a progress note."""
    data = payload.get("data") or {}
    meta = data.get("metadata") or {}
    provider_id = data.get("run_id") or data.get("findall_id")
    p = _project_from_metadata(meta)
    if p is None:
        return {"ok": True, "ignored": "unknown run"}
    status = data.get("status") or (data.get("run") or {}).get("status")
    _log_project(p, "parallel", f"Parallel reported {meta.get('kind') or 'run'} {provider_id} is {status}", {"provider_run_id": provider_id, "status": status, "event_type": "task_run.status"})
    return {"ok": True, "run_id": provider_id, "status": status}


@app.post("/api/webhooks/parallel")
async def parallel_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """One webhook, several Parallel products.

    Monitors send `monitor.event.detected` (plus execution completed/failed); Task and FindAll share
    `task_run.status`. Task dossiers block on `result()` and FindAll polls with a bound, so the
    status events are informational — we record them rather than depend on them, which keeps the
    feature working locally where `PUBLIC_BASE_URL` is unset and no webhook can arrive at all.
    """
    event_type = payload.get("type")
    if event_type in {"monitor.execution.completed", "monitor.execution.failed"}:
        return _monitor_execution_event(payload, event_type)
    if event_type == "task_run.status":
        return _task_status_event(payload)
    if event_type != "monitor.event.detected":
        return {"ok": True, "ignored": event_type}
    data = payload.get("data") or {}
    monitor_id = data.get("monitor_id")
    group = (data.get("event") or {}).get("event_group_id")
    meta = data.get("metadata") or {}
    p = repo.get_project(meta.get("project_id", "")) if meta.get("project_id") else None
    if p is None:
        for cand in repo.list_projects():
            if any(m.id == monitor_id for m in cand.monitors):
                p = cand
                break
    if p is None:
        raise HTTPException(404, "monitor not known")
    monitor = next((m for m in p.monitors if m.id == monitor_id), None)
    if monitor is None:
        raise HTTPException(404, "monitor not known")
    try:
        events = await asyncio.to_thread(ParallelMonitorTool().events, monitor_id, group)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"could not fetch monitor events: {exc}")
    if monitor.monitor_type == "snapshot":
        # A dossier diff, not a same-day disruption: it changes what the production knows, and only
        # a producer decides whether it changes what the schedule enforces.
        changes = [c for ev in events if ev.get("event_type") == "snapshot" for c in _ingest_snapshot(p, monitor, ev, simulated=False)]
        return {"ok": True, "fact_changes": [c.id for c in changes]}
    day = p.shoot_day(monitor.shoot_day_id) if monitor.shoot_day_id else None
    if day is None:
        return {"ok": True, "ignored": "monitor is not attached to a shoot day"}
    created = [d for d in (_ingest_event(p, day, monitor, ev, simulated=False) for ev in events if ev.get("event_type", "event_stream") == "event_stream") if d]
    return {"ok": True, "drafts": [d.id for d in created]}


class ConfirmRequest(BaseModel):
    window_start: str | None = None
    window_end: str | None = None
    dry_out_minutes: int | None = None
    type: DisruptionType | None = None


@app.post("/api/projects/{project_id}/disruptions/{disruption_id}/confirm")
async def confirm_disruption(project_id: str, disruption_id: str, body: ConfirmRequest) -> dict[str, Any]:
    """Producer confirms a monitor-detected draft → the rescue workflow starts (never automatically)."""
    p = _project(project_id)
    try:
        d = p.disruption(disruption_id)
    except KeyError:
        raise HTTPException(404, "disruption not found")
    if not d.draft:
        raise HTTPException(409, "disruption is not a draft")
    day = p.shoot_day(d.shoot_day_id)
    _refuse_if_wrapped(day)
    # A draft confirmed mid-decision starts a second run, and the day page reads `runs[0]` — the
    # option list a producer is looking at would be swapped out from under them for a fresh impact
    # panel, with no way back to the recommendation they were weighing. `report_disruption` has had
    # this guard since it shipped; this endpoint became reachable during a decision only once the
    # monitor panel stopped unmounting while a disruption was live.
    _refuse_if_rescue_in_flight(p.id, day)
    if body.type:
        d.type = body.type
    ws = body.window_start if body.window_start and body.window_end else d.window_start
    we = body.window_end if body.window_start and body.window_end else d.window_end
    dry_out = body.dry_out_minutes if body.dry_out_minutes is not None else d.dry_out_minutes
    if not (ws and we):
        raise HTTPException(400, "a time window is required to confirm")
    _validate_disruption_window(day, ws, we, dry_out)
    d.window_start, d.window_end, d.dry_out_minutes = ws, we, dry_out
    # Before the confirmation is written to the log: a refusal here must not leave an "approval"
    # event behind for a draft that was never confirmed.
    _refuse_if_unreachable(day, d)
    d.draft = False
    _log_project(p, "approval", f"Producer confirmed the monitor-detected disruption: {d.title[:80]} ({d.window_start}–{d.window_end})", {"disruption_id": d.id})
    run = _start_rescue_for(p, day, d)
    return {"run_id": run.id, "disruption_id": d.id}


@app.post("/api/projects/{project_id}/disruptions/{disruption_id}/dismiss")
def dismiss_disruption(project_id: str, disruption_id: str) -> dict[str, Any]:
    p = _project(project_id)
    before = len(p.disruptions)
    p.disruptions = [d for d in p.disruptions if not (d.id == disruption_id and d.draft)]
    if len(p.disruptions) == before:
        raise HTTPException(404, "draft not found")
    repo.save_project(p)
    _log_project(p, "info", "Producer dismissed a monitor-detected draft disruption", {"disruption_id": disruption_id})
    return {"ok": True}


class ApproveRequest(BaseModel):
    option_id: str
    approved_by: str = "producer"


@app.post("/api/runs/{run_id}/approve")
def approve_run(run_id: str, body: ApproveRequest) -> dict[str, Any]:
    run = _run(run_id)
    if run.kind != RunKind.RESCUE or run.rescue is None:
        raise HTTPException(400, "not a rescue run")
    p = _project(run.project_id)
    ctx = RunContext(repo, run, p)
    try:
        approve(ctx, body.option_id, approved_by=body.approved_by)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"run": run.model_dump(mode="json"), "changeset": run.rescue.changeset.model_dump(mode="json") if run.rescue.changeset else None, "actions": [a.model_dump(mode="json") for a in run.rescue.actions], "day": p.shoot_day(run.rescue.shoot_day_id).model_dump(mode="json")}


# What each activity kind *is*, so the log and the run feed cannot describe the same event two
# different ways. Sent to the client rather than mirrored there: the set is defined by whatever the
# writers actually emit, and a kind that reaches the UI without an entry here reads as a generic
# "orchestration" line — which is how a producer's own accountable decisions came to render in the
# same grey as a seed-migration note.
ACTIVITY_KINDS: dict[str, dict[str, str]] = {
    "decision": {"label": "PRODUCER", "category": "decision", "description": "A producer accepted or rejected something. The accountable acts."},
    "approval": {"label": "APPROVED", "category": "decision", "description": "A producer approved a recovery and a ChangeSet was applied."},
    "parallel": {"label": "PARALLEL", "category": "evidence", "description": "A call to the outside world through the Parallel APIs."},
    "gemini": {"label": "GEMINI", "category": "reasoning", "description": "A Gemini step — a proposal, a breakdown or a rationale."},
    "deterministic": {"label": "ENGINE", "category": "engine", "description": "The constraint engine validating, pricing or refusing a schedule."},
    "action": {"label": "ACTION", "category": "engine", "description": "A coordination action derived from an applied change."},
    "dispatch": {"label": "DISPATCH", "category": "engine", "description": "A call sheet composed into messages and queued. Simulated — nothing is transmitted."},
    "dispatch_reping": {"label": "DISPATCH", "category": "engine", "description": "Simulated crew delivery rows re-queued. Nothing is transmitted."},
    "warning": {"label": "WARN", "category": "attention", "description": "Something needs a human to look at it."},
    "error": {"label": "ERROR", "category": "attention", "description": "A step failed."},
    "info": {"label": "ORCH", "category": "orchestration", "description": "Bookkeeping: seeding, re-anchoring, migrations."},
}

CATEGORY_ORDER = ("decision", "evidence", "reasoning", "engine", "attention", "orchestration")


@app.get("/api/projects/{project_id}/activity")
def project_activity(project_id: str, limit: int = 400) -> dict[str, Any]:
    """The production log: every recorded act on this show, in the order it happened.

    This is the producer's audit trail and it spans both scopes — the project-level events written by
    the API (a fact accepted, a monitor cancelled, the seed re-anchored) and the run-level events
    written inside a workflow (a search issued, an option rejected, a rationale generated). They are
    one story and are returned as one list; `run_id` is what says which run a line belongs to, and the
    run index below is what lets the client name it.

    The whole point of the endpoint is that nothing here is reconstructed for display. Each line was
    written at the moment the thing happened, by the code that did it.
    """
    p = _project(project_id)
    events = repo.list_activity(project_id=p.id, limit=limit)
    counts: dict[str, int] = {}
    for event in events:
        category = ACTIVITY_KINDS.get(event.kind, ACTIVITY_KINDS["info"])["category"]
        counts[category] = counts.get(category, 0) + 1
    runs = {r.id: r for r in repo.list_runs(p.id)}
    return {
        "events": [e.model_dump(mode="json") for e in events],
        "kinds": ACTIVITY_KINDS,
        "categories": list(CATEGORY_ORDER),
        "counts_by_category": counts,
        # Enough to name the run a line came from, and to link to it. Runs with no activity are
        # included — a run that produced no log line is itself worth being able to see.
        "runs": [
            {"id": r.id, "kind": r.kind.value, "status": r.status.value,
             "scene_id": r.planning.scene_id if r.planning else None,
             "shoot_day_id": r.rescue.shoot_day_id if r.rescue else None,
             "created_at": r.created_at.isoformat()}
            for r in runs.values()
        ],
        "total": len(events),
        "truncated": len(events) >= limit,
    }
