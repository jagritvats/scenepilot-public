"""The demo seed pre-loads from recordings — without inventing, accepting, or rewriting anything.

A cold Cloud Run instance used to open on empty panels, which hid the one beat the whole demo
rests on. `seed/warm.py` fixes that by replaying recordings of real Parallel Task runs. The tests
here are the guard rails on that shortcut: it must stay a *replay*, stay *labelled*, stay
*unaccepted*, and stay *out of committed production state*.
"""

from dataclasses import replace

from scenepilot.config import settings as default_settings
from scenepilot.domain.enums import FactBinding, TimeOfDay
from scenepilot.seed.nightfall import PROJECT_ID, build_project
from scenepilot.seed.warm import warm_demo_state
from scenepilot.store.db import make_engine
from scenepilot.store.repo import Repo

ROOFTOP = "loc_rooftop"


def _repo() -> Repo:
    return Repo(make_engine("sqlite:///:memory:"))


def _warm():
    repo, project = _repo(), build_project()
    repo.save_project(project)
    notes = warm_demo_state(repo, project, replace(default_settings, warm_demo=True))
    return repo, project, notes


def test_warm_seed_loads_the_screenplay_and_every_recorded_dossier():
    repo, project, notes = _warm()
    assert len(project.parsed_screenplay_scenes) == 5
    assert {s.scene_number for s in project.parsed_screenplay_scenes} == {"42", "27", "48", "31", "19"}
    # one dossier per location that has a recording, persisted as an observable TaskRun. Filtered by
    # purpose: the warm seed also replays weather timelines, which are Task runs against a *day*, so
    # counting every run here made the dossier assertion fail the moment a weather fixture landed.
    runs = [r for r in repo.list_task_runs(project_id=PROJECT_ID) if r.purpose == "location_dossier"]
    assert len(runs) == 4 and {r.resource_id for r in runs} == {"loc_rooftop", "loc_alley", "loc_street", "loc_apartment"}
    weather = [r for r in repo.list_task_runs(project_id=PROJECT_ID) if r.purpose == "weather_timeline"]
    assert {r.shoot_day_id for r in weather} == {"day_4", "day_6"}, "the committed weather fixtures warm too"
    assert notes and any("Screenplay Studio" in n for n in notes)


def test_seeded_runs_are_labelled_as_replays_not_live_research():
    repo, _project, _ = _warm()
    runs = repo.list_task_runs(project_id=PROJECT_ID)
    assert all(r.status == "REPLAY" and r.replayed for r in runs)
    # they are real recordings: every one carries the provider's run id from the live call
    assert all(r.provider_run_id for r in runs)


def test_the_hero_curfew_arrives_graded_hard_cited_and_unaccepted():
    """The Day 6 beat: a statute is proposed as a hard constraint, and waits for the producer."""
    _repo_, project, _ = _warm()
    curfew = next(f for f in project.location_facts if f.resource_id == ROOFTOP and f.key == "noise_curfew")
    assert curfew.value == "22:00-06:00"
    assert curfew.binding == FactBinding.HARD and curfew.confidence == "high"
    assert curfew.rule is not None and curfew.rule.kind == "TIME_WINDOW_BAN"
    assert any("indiacode.nic.in" in c.url for c in curfew.citations)
    # the acceptance click is the whole point of the beat — the seed must never make it
    assert not curfew.accepted and not curfew.binds


def test_seeding_accepts_nothing_so_no_seeded_fact_constrains_the_schedule():
    _repo_, project, _ = _warm()
    assert project.location_facts and not any(f.binds for f in project.location_facts)


def test_seeding_does_not_rewrite_committed_production_state():
    """The studio is filled from the draft; INT/EXT, time of day and durations stay the producer's."""
    _repo_, project, _ = _warm()
    fresh = build_project()
    for scene in project.scenes:
        before = fresh.scene(scene.id)
        assert (scene.int_ext, scene.time_of_day, scene.estimated_minutes) == (before.int_ext, before.time_of_day, before.estimated_minutes)
    # in particular the cover scene stays ANY — the draft calls it MORNING, which would add a
    # daylight constraint the producer never agreed to
    assert project.scene("sc_27").time_of_day == TimeOfDay.ANY


def test_every_scene_carries_a_page_count_after_warming():
    """No scene reaches the board unpaginated, and none of them is paginated by the draft.

    Both halves have been wrong here. First the seed left five scenes for the parser and warming
    never ran it, so a fresh deployment showed "—" against five of nine scenes. Then warming did run
    it — and the draft in `fixtures/` is a five-scene excerpt, so the parser measured six lines and
    the hero day read "4 sc · 4/8 pgs" against 600 scheduled minutes. The page count is production
    state now; the draft supplies the text and nothing else.
    """
    _repo_, project, notes = _warm()
    assert [s.number for s in project.scenes if s.eighths is None] == []
    assert project.scene("sc_48").eighths == build_project().scene("sc_48").eighths
    assert any("scene text from that draft" in n for n in notes)


def test_the_draft_never_overwrites_a_page_count_or_text_the_seed_stated():
    """Adoption fills blanks only. A value the seed states is the production's, not the draft's."""
    _repo_, project, _ = _warm()
    fresh = build_project()
    for scene in project.scenes:
        before = fresh.scene(scene.id)
        if before.eighths is not None:
            assert scene.eighths == before.eighths
        if before.script_text:
            assert scene.script_text == before.script_text
    # sc_42 is the case that matters: the seed gives it text *and* the draft carries scene 42
    assert project.scene("sc_42").script_text == fresh.scene("sc_42").script_text


def test_warming_never_creates_a_production_scene_from_the_draft():
    """The `sc_101`/`sc_102` failure mode: a synced draft putting a scene with no set on the board."""
    _repo_, project, _ = _warm()
    assert {s.id for s in project.scenes} == {s.id for s in build_project().scenes}
    assert all(s.location_id for s in project.scenes)


def test_warming_twice_changes_nothing():
    repo, project, _ = _warm()
    facts, runs = len(project.location_facts), len(repo.list_task_runs(project_id=PROJECT_ID))
    assert warm_demo_state(repo, project, replace(default_settings, warm_demo=True)) == []
    assert len(project.location_facts) == facts
    assert len(repo.list_task_runs(project_id=PROJECT_ID)) == runs


def test_warming_is_skipped_when_the_deployment_turns_it_off():
    repo, project = _repo(), build_project()
    repo.save_project(project)
    assert warm_demo_state(repo, project, replace(default_settings, warm_demo=False)) == []
    assert not project.location_facts and not project.parsed_screenplay_scenes


def test_a_location_researched_live_is_never_overwritten_by_the_seed():
    """A live dossier is the real thing; the seed must defer to it, not clobber it."""
    from scenepilot.domain.models import LocationFact, TaskRun

    repo, project = _repo(), build_project()
    live = TaskRun(project_id=project.id, resource_id=ROOFTOP, status="OK", output={"noise_curfew": "21:00-06:00"})
    repo.save_task_run(live)
    project.location_facts.append(
        LocationFact(project_id=project.id, resource_id=ROOFTOP, task_run_id=live.id, key="noise_curfew", label="Noise curfew", value="21:00-06:00")
    )
    repo.save_project(project)

    warm_demo_state(repo, project, replace(default_settings, warm_demo=True))
    rooftop_facts = [f for f in project.location_facts if f.resource_id == ROOFTOP]
    assert [f.value for f in rooftop_facts] == ["21:00-06:00"]
    assert len([r for r in repo.list_task_runs(project_id=PROJECT_ID) if r.resource_id == ROOFTOP]) == 1
