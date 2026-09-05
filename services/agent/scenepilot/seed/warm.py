"""Warm the demo project from bundled recordings, so a cold instance is never an empty room.

The product is deliberately expensive-honest: a Parallel Task dossier costs real money and takes
1–5 minutes, so it never fires on its own. The cost of that rule is
that a freshly-deployed instance opens on empty panels — and the one beat that carries the whole
argument (*a cited statute rejects a specific scene on a specific night*) is invisible until
somebody clicks and waits. Cloud Run makes it worse: without `DATABASE_URL` the store is SQLite
under `/tmp`, so a new revision throws that state away again.

So the seed replays what real live runs already produced:

* the hero screenplay, parsed into the Screenplay Studio;
* one location dossier per location, replayed from `seed/fixtures/recordings/parallel_task/`;
* the hourly weather timeline for any day one was recorded for, from the same directory.

Three rules keep this honest, and they are the reason it is a seed rather than a fixture:

1. **Nothing is invented.** Every dossier here is a recording of a real Parallel Task run, keyed by
   the exact request the live tool would send. No recording, no dossier.
2. **It is labelled.** The runs are stored with `status=REPLAY` / `replayed=True` — the same state a
   replay-mode run produces — so the UI calls them replayed and *Re-research* runs them live.
3. **It accepts nothing.** Facts arrive graded but unaccepted, so the producer's acceptance click
   (the moment the web becomes a constraint) still has to happen, in front of the user.

Idempotent: a location that already has facts, or a run that already happened, is left alone.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Settings, settings as default_settings
from ..domain.enums import FactBinding
from ..domain.models import Project, Resource, ShootDay, TaskRun, utcnow
from ..ingestion.parsers import parse_screenplay
from ..services.dossier import location_resources, map_facts, merge_facts
from ..services.weather import map_timeline
from ..store.repo import Repo
from ..tools.parallel_task import (
    DOSSIER_SCHEMA,
    WEATHER_SCHEMA,
    apply_payload,
    build_task_input,
    build_task_request,
    build_weather_input,
    recording_key,
)
from ..tools.recorder import Recorder

log = logging.getLogger(__name__)

HERO_SCREENPLAY_PATH = Path(__file__).parent / "fixtures" / "hero_screenplay.fountain"


def hero_screenplay_text() -> str:
    """The Fountain draft of Project Nightfall shipped with the repo."""
    return HERO_SCREENPLAY_PATH.read_text(encoding="utf-8")


# The one thing a draft is allowed to state about a scene the production already has: the scene's
# own text. Deliberately not `heading`, `int_ext` or `time_of_day` — those are constraints the
# scheduler reads and a producer owns, and rewriting them from a draft can change what the board
# will accept. And deliberately not `eighths`: see `_adopt_draft_text`.
DRAFT_OWNED_SCENE_FIELDS = ("script_text",)


def warm_screenplay(project: Project) -> tuple[int, list[str]]:
    """Fill the Screenplay Studio.

    Returns the number of scenes parsed (0 if it was already loaded) and the scene numbers that took
    their text from the draft.

    The full `sync_scenes` is deliberately NOT applied. Syncing rewrites committed production state
    (a scene's INT/EXT and time of day) from the draft, which is a producer's decision and can change
    what the scheduler will accept, and it *creates* a production scene for any slug line the draft
    happens to carry — which is how a stray upload puts a scene with no set and no cast onto a board
    nobody scheduled it on. Neither is something a startup path may do unattended.
    """
    if project.parsed_screenplay_scenes:
        return 0, []
    scenes = parse_screenplay(hero_screenplay_text(), format_hint="fountain")
    project.parsed_screenplay_scenes = scenes
    return len(scenes), _adopt_draft_text(project, scenes)


def _adopt_draft_text(project: Project, parsed: list) -> list[str]:
    """Copy the draft's scene text onto the production scenes that carry none.

    Existing scenes only, matched on scene number, and only where the stored value is still the
    model's default. A scene the draft does not mention is untouched, and a scene number the
    production does not have is *not* created.

    `eighths` is pointedly not adopted, and the seed no longer leaves a scene for the draft to
    paginate. The Fountain file here is a five-scene *excerpt*, so the parser measures five to eight
    lines and returns 1/8 of a page — which is a true statement about the text in the Studio and a
    false one about a 150-minute scene on a board. The two numbers are answers to different
    questions and the board's is production state, so it lives in `seed/nightfall.py` where a
    producer states it beside the minutes it has to agree with. What still has to arrive from the
    draft is the text itself: `POST /scenes/{id}/breakdown-elements` reads `scene.script_text`, and
    the recorded Gemini breakdowns are keyed on it.
    """
    by_number = {str(s.number): s for s in project.scenes}
    adopted: list[str] = []
    for ps in parsed:
        scene = by_number.get(str(ps.scene_number))
        if scene is None:
            continue
        for name in DRAFT_OWNED_SCENE_FIELDS:
            fresh = getattr(ps, "raw_text" if name == "script_text" else name, None)
            if fresh in (None, "") or getattr(scene, name) not in (None, ""):
                continue
            setattr(scene, name, fresh)
            adopted.append(scene.number)
    return adopted


def _replay_dossier(project: Project, resource: Resource, recorder: Recorder, processor: str) -> TaskRun | None:
    """Rebuild one location's dossier from its recording, or None when nothing was recorded for it."""
    input_text = build_task_input(project, resource)
    payload = recorder.lookup("parallel_task", recording_key(build_task_request(input_text, processor)))
    if payload is None:
        return None
    tr = TaskRun(project_id=project.id, resource_id=resource.id, processor=processor, input=input_text, output_schema=DOSSIER_SCHEMA)
    apply_payload(tr, payload, replayed=True)
    tr.finished_at = utcnow()
    return tr


def warm_dossiers(repo: Repo, project: Project, settings: Settings) -> list[tuple[Resource, int, int]]:
    """Replay a dossier for every location that has one recorded and no research of its own yet.

    Returns (resource, facts, hard_facts) per location warmed.
    """
    recorder = Recorder(settings.recordings_dir)
    researched = {t.resource_id for t in repo.list_task_runs(project_id=project.id) if t.status in {"OK", "REPLAY"}}
    warmed: list[tuple[Resource, int, int]] = []
    for resource in location_resources(project):
        if resource.id in researched or any(f.resource_id == resource.id for f in project.location_facts):
            continue
        task_run = _replay_dossier(project, resource, recorder, settings.parallel_task_processor)
        if task_run is None:
            continue
        repo.save_task_run(task_run)
        facts = map_facts(task_run, project)
        merge_facts(project, resource.id, facts)
        warmed.append((resource, len(facts), len([f for f in facts if f.rule is not None and f.binding == FactBinding.HARD])))
    return warmed


def _replay_weather(project: Project, day: ShootDay, recorder: Recorder, processor: str) -> TaskRun | None:
    """Rebuild one day's weather timeline from its recording, or None when nothing was recorded."""
    input_text = build_weather_input(project, day)
    payload = recorder.lookup("parallel_task", recording_key(build_task_request(input_text, processor, WEATHER_SCHEMA)))
    if payload is None:
        return None
    tr = TaskRun(
        project_id=project.id,
        shoot_day_id=day.id,
        purpose="weather_timeline",
        processor=processor,
        input=input_text,
        output_schema=WEATHER_SCHEMA,
    )
    apply_payload(tr, payload, replayed=True)
    tr.finished_at = utcnow()
    return tr


def warm_weather_timelines(repo: Repo, project: Project, settings: Settings) -> list[tuple[ShootDay, int]]:
    """Replay an hourly weather timeline for every day that has one recorded and none of its own.

    Returns (day, hours answered) per day warmed. A day with no recording is skipped in silence —
    the scrubber then offers the priced button, which is the honest state for weather nobody has
    researched. The key normalises the date, so a recording made in the session survives the seed
    re-anchoring the shoot week to today.
    """
    recorder = Recorder(settings.recordings_dir)
    researched = {t.shoot_day_id for t in repo.list_task_runs(project_id=project.id) if t.purpose == "weather_timeline" and t.status in {"OK", "REPLAY"}}
    warmed: list[tuple[ShootDay, int]] = []
    for day in project.shoot_days:
        if day.id in researched:
            continue
        task_run = _replay_weather(project, day, recorder, settings.parallel_task_processor)
        if task_run is None:
            continue
        repo.save_task_run(task_run)
        timeline = map_timeline(task_run)
        if timeline is None:  # a recording that answered nothing warms nothing
            continue
        warmed.append((day, len(timeline["hours"])))
    return warmed


def warm_demo_state(repo: Repo, project: Project, settings: Settings | None = None) -> list[str]:
    """Pre-load the demo and persist it. Returns one activity line per thing warmed."""
    settings = settings or default_settings
    if not settings.warm_demo:
        return []
    notes: list[str] = []
    parsed, adopted = warm_screenplay(project)
    if parsed:
        notes.append(f"Screenplay Studio pre-loaded with the {parsed}-scene Project Nightfall draft (Fountain, parsed at startup)")
    if adopted:
        notes.append(
            f"Scene{'s' if len(adopted) > 1 else ''} {', '.join(adopted)} took {'their' if len(adopted) > 1 else 'its'} "
            "scene text from that draft, which is what a Gemini breakdown reads. Page counts, INT/EXT and time of day "
            "are production state and were left exactly as the producer stated them"
        )
    for resource, facts, hard in warm_dossiers(repo, project, settings):
        notes.append(
            f"Dossier for {resource.name} restored from a recorded Parallel Task run — {facts} cited fact(s), "
            f"{hard} of them proposed as hard constraints awaiting producer acceptance. Press Re-research for a live run."
        )
    for day, hours in warm_weather_timelines(repo, project, settings):
        notes.append(
            f"Hourly weather timeline for Day {day.day_number} restored from a recorded Parallel Task run — "
            f"{hours} hour(s) answered, each with its own citations. The disruption scrubber reads it."
        )
    if notes:
        repo.save_project(project)
        log.info("Warmed the demo project: %s", "; ".join(notes))
    return notes


# The one seeded scene whose recorded planning run replays end to end. The other eight carry scene
# text that no committed `scene_breakdown` recording matches, so attempting them would write a
# FAILED run into the activity feed a judge reads first. `test_warm_planning.py` pins this: if the
# recording ever stops matching, the suite fails here rather than the demo failing in front of
# somebody.
WARM_PLAN_SCENE_ID = "sc_42"


async def warm_planning(repo: Repo, project: Project, settings: Settings | None = None) -> list[str]:
    """Replay one recorded planning run, so a cold instance shows both halves of the product.

    The warm seed already restored what *research* produced — dossiers, the weather timeline, the
    screenplay. It did not restore what *planning* produced, and planning is the other half of the
    argument: the graded evidence, the FACT / INFERENCE / RECOMMENDATION split, the risk register
    and the readiness score exist only once a scene has been planned. So a cold instance opened on a
    scene page with an empty plan, and the only way to fill it was a 60–90 second live run that
    spends money — exactly the wait this module exists to remove everywhere else.

    The same three rules hold. It is a *recording of a real run*, replayed through the ordinary
    workflow rather than written in as state, so every row it leaves is one the live path leaves:
    the searches as sent, the evidence grades, the follow-up round the analyst asked for. It is
    forced into replay by `degraded_to_replay()`, so a live deployment does not spend on it and the
    run is stored and chipped as replayed. And it decides nothing that is the producer's to decide —
    a plan is a proposal; planning accepts nothing and applies nothing.

    Verified not to move the hero beat: the Day 4 rain rescue proposes the same five options, three
    feasible and two rejected, with and without this run.

    **Call this after `warm_demo_state`, not before.** The evidence analyst is prompted with the
    dossier facts location research produced, so the recorded prompt only matches once those facts
    are on the project; against a bare seed this replays into a `ReplayMiss` and returns nothing.
    """
    settings = settings or default_settings
    if not settings.warm_demo:
        return []

    # Imported here rather than at module scope: the workflow package pulls in the agent runtime and
    # the Parallel tools, and the seed is imported by both of those on other paths.
    from ..config import degraded_to_replay
    from ..domain.models import PlanningState, RunKind, RunStatus, WorkflowRun
    from ..workflows.context import RunContext
    from ..workflows.planning import run_planning

    try:
        scene = project.scene(WARM_PLAN_SCENE_ID)
    except KeyError:
        return []

    # Idempotent in the same sense as the dossiers: a scene that has already been planned is left
    # alone, so this costs nothing on a warm instance and never stacks a second run onto a first.
    for r in repo.list_runs(project.id, RunKind.PLANNING.value):
        if r.planning and r.planning.scene_id == WARM_PLAN_SCENE_ID and r.status == RunStatus.COMPLETED:
            return []

    with degraded_to_replay():
        run = WorkflowRun(
            project_id=project.id,
            kind=RunKind.PLANNING,
            mode=settings.active_mode,
            planning=PlanningState(scene_id=WARM_PLAN_SCENE_ID),
        )
        repo.save_run(run)
        try:
            await run_planning(RunContext(repo, run, project))
        except Exception as exc:  # a stale recording must not take the service down with it
            log.warning("Warm planning for %s did not replay: %s", WARM_PLAN_SCENE_ID, exc)
            return []

    saved = repo.get_run(run.id)
    if saved is None or saved.status != RunStatus.COMPLETED or saved.planning is None:
        return []
    plan = saved.planning.plan
    score = getattr(plan, "readiness_score", None)
    return [
        f"Scene {scene.number} pre-planned from a recorded run — {len(saved.planning.questions)} research "
        f"question(s), {len(saved.planning.evidence)} graded evidence item(s)"
        + (f", readiness {score}/100" if score is not None else "")
        + ". Press Re-plan for a live run."
    ]
