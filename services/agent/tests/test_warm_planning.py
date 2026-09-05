"""The planning half of the warm seed, and the two things that make it safe to ship.

`warm_planning` exists because a cold instance used to show only half the product: research was
restored from recordings, planning was not, so a scene page opened with an empty plan and the only
way to fill it was a live run that costs money and takes a minute and a half.

Two properties have to hold, and neither is obvious from reading the function:

1. **The recording still matches.** The run is replayed, so it is only ever one prompt edit away
   from missing. If that happens the demo silently loses the plan again — so the suite fails here
   instead, naming the scene.
2. **It does not move the hero beat.** Planning writes requirements onto the scene, and the
   scheduler reads the day. If warming the plan changed what Day 4 accepts, the rescue that the
   whole submission is built around would propose a different set of options than the one the
   video, the gallery and the README all describe.
"""

import asyncio
from dataclasses import replace

import pytest

from scenepilot.config import settings as default_settings
from scenepilot.domain.models import RescueState, RunKind, RunStatus, WorkflowRun
from scenepilot.seed.nightfall import DAY4_ID, build_project, make_fixture_disruption
from scenepilot.seed.warm import WARM_PLAN_SCENE_ID, warm_demo_state, warm_planning
from scenepilot.store.db import make_engine
from scenepilot.store.repo import Repo
from scenepilot.workflows.context import RunContext
from scenepilot.workflows.rescue import run_rescue

WARM = replace(default_settings, warm_demo=True)


def _seeded() -> tuple[Repo, object]:
    """A cold project with the *research* half of the warm seed already replayed.

    That order is not incidental: the evidence analyst is prompted with the dossier facts the
    location research produced, so planning replays against a warmed project and misses against a
    bare one. `app.lifespan` and the reset route both warm in this order for the same reason.
    """
    repo, project = Repo(make_engine("sqlite:///:memory:")), build_project()
    repo.save_project(project)
    warm_demo_state(repo, project, WARM)
    return repo, project


def _warm(repo, project, settings=WARM) -> list[str]:
    return asyncio.run(warm_planning(repo, project, settings))


def _rescue_options(repo, project) -> list[tuple[str, bool]]:
    """Run the hero Day 4 rain rescue and report each option's label and feasibility."""
    d = make_fixture_disruption(project.id, DAY4_ID, "rain_pm")
    project.disruptions.append(d)
    repo.save_project(project)
    run = WorkflowRun(
        project_id=project.id, kind=RunKind.RESCUE, mode="replay",
        rescue=RescueState(shoot_day_id=DAY4_ID, disruption_id=d.id),
    )
    repo.save_run(run)
    asyncio.run(run_rescue(RunContext(repo, run, project)))
    saved = repo.get_run(run.id)
    return [(o.label, o.feasible) for o in saved.rescue.options]


def test_the_recorded_planning_run_still_replays():
    """If this fails, the recording no longer matches the prompt — re-record before deploying."""
    repo, project = _seeded()
    notes = _warm(repo, project)
    assert notes, f"warm planning produced nothing for {WARM_PLAN_SCENE_ID}: the recording no longer matches"
    runs = [r for r in repo.list_runs(project.id, RunKind.PLANNING.value) if r.planning]
    assert [r.status for r in runs] == [RunStatus.COMPLETED]
    plan = runs[0].planning.plan
    assert plan is not None and plan.readiness_score is not None
    assert runs[0].planning.questions and runs[0].planning.evidence


def test_it_is_stored_as_a_replay_and_accepts_nothing():
    """Same rule as the dossiers: warmed state is labelled replayed, and decides nothing."""
    repo, project = _seeded()
    _warm(repo, project)
    run = next(r for r in repo.list_runs(project.id, RunKind.PLANNING.value) if r.planning)
    assert run.mode == "replay"
    stored = repo.get_project(project.id)
    assert not [f for f in stored.location_facts if f.accepted], "planning must accept no fact"
    assert not stored.changeset_ids, "planning must apply nothing"


def test_it_is_idempotent():
    repo, project = _seeded()
    assert _warm(repo, project)
    assert _warm(repo, repo.get_project(project.id)) == [], "a planned scene must be left alone"


def test_it_is_off_when_the_warm_seed_is_off():
    repo, project = _seeded()
    assert _warm(repo, project, replace(default_settings, warm_demo=False)) == []


def test_warming_the_plan_does_not_change_what_day_4_accepts():
    """The hero beat: three feasible options and two rejected, with and without the warmed plan."""
    cold_repo, cold_project = _seeded()
    before = _rescue_options(cold_repo, cold_project)

    warm_repo, warm_project = _seeded()
    _warm(warm_repo, warm_project)
    after = _rescue_options(warm_repo, warm_repo.get_project(warm_project.id))

    assert before == after, f"warming the plan moved the rescue: {before} -> {after}"
    assert sum(1 for _, feasible in after if feasible) == 3
    assert sum(1 for _, feasible in after if not feasible) == 2


def test_the_planning_replay_is_deterministic():
    """The same recorded run must replay the same way every time, not most of the time.

    It did not. The evidence analyst grades questions concurrently, so two searches could share a
    `started_at`; `list_search_runs` ordered on that column alone and SQLite returns equal keys in
    arbitrary order. The analyst's sources therefore arrived in a different order run to run, which
    reordered its prompt, which changed the recording key, which turned a perfectly good recording
    into a `ReplayMiss`. Measured before the fix: **17 of 25**. So roughly one cold start in three
    got no warm plan at all, and the scene page a judge is pointed at opened empty.

    Adding an id tiebreak was not enough — run ids are generated per run, so ordering by them still
    does not reproduce creation order. `list_search_runs(ids=...)` now returns the caller's order.

    Ten iterations, because the failure was probabilistic: a single pass proved nothing, which is
    exactly why this survived until someone ran the suite in a loop.
    """
    for i in range(10):
        repo, project = _seeded()
        assert _warm(repo, project), f"warm planning produced nothing on iteration {i + 1} of 10"
