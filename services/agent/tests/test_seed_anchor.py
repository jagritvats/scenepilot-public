"""The hero day is always today — after a reset, and after a week of uptime.

Shoot Day 4 carries the whole demo: the rain the agent verifies through Parallel is *today's* rain,
and the monitor queries name that date on screen. It used to be bound at import, while its siblings
were computed per call, which broke in two directions. A reset re-dated days 3/5/6 around a Day 4
that never moved, so one midnight was enough to make the chronology run backwards (Day 3 after Day
5) — and `next_day_call` reads "the earliest day after this one" to price the turnaround-rest rule.
On a long-lived instance the opposite: nothing rebuilds, so the hero day silently ages into the past
— and "nothing rebuilds" outlives a startup hook, so the re-anchor has to happen on read.

Two clocks are wrong here and only one of them is the container's. `date.today()` on Cloud Run is a
UTC date, and until 05:30 IST that is yesterday in Mumbai: the hero day would anchor one day early
and every Parallel query would name a date the production is not shooting. Today means today *where
the production is*.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

from scenepilot.domain.enums import FactBinding
from scenepilot.domain.models import LocationFact, MonitorRecord
from scenepilot.seed import nightfall
from scenepilot.seed.nightfall import DAY4_ID, PROJECT_ID, SEED_CITY, build_project, reanchor_shoot_days
from scenepilot.services.ephemeris import city_today
from scenepilot.services.recovery import next_day_call


def _today() -> str:
    """Today in Mumbai — the production's date, which is the only one the hero day answers to."""
    return city_today(SEED_CITY).isoformat()


def _dates(project) -> dict[str, str]:
    return {d.id: d.date for d in project.shoot_days}


def _in_order(project) -> list[str]:
    return [d.date for d in sorted(project.shoot_days, key=lambda d: d.day_number)]


# --------------------------------------------------------------------------- #
# Built fresh
# --------------------------------------------------------------------------- #


def test_every_shoot_day_date_is_read_when_the_project_is_built(monkeypatch):
    monkeypatch.setattr(nightfall, "_today_offset", lambda days: (date(2027, 3, 1) + timedelta(days=days)).isoformat())
    assert _dates(build_project()) == {
        "day_3": "2027-02-28", DAY4_ID: "2027-03-01", "day_5": "2027-03-02", "day_6": "2027-03-03",
    }


def test_build_project_puts_day_4_on_today_and_the_others_around_it():
    project = build_project()
    assert project.shoot_day(DAY4_ID).date == _today()
    assert _in_order(project) == sorted(_in_order(project))
    assert nightfall.DAY4_DATE == _today()


def test_the_day_after_the_next_day_is_still_the_next_day():
    """The rebuild that a midnight used to break: every date moves together, or none does."""
    from scenepilot.services.timeutil import to_minutes

    project = build_project()
    day5 = project.shoot_day("day_5")
    assert next_day_call(project, project.shoot_day(DAY4_ID)) == 24 * 60 + to_minutes(day5.unit_call)
    assert date.fromisoformat(day5.date) - date.fromisoformat(project.shoot_day(DAY4_ID).date) == timedelta(days=1)


# --------------------------------------------------------------------------- #
# Re-anchored in place
# --------------------------------------------------------------------------- #


def _stale(days_ago: int):
    project = build_project()
    for day in project.shoot_days:
        day.date = (date.fromisoformat(day.date) - timedelta(days=days_ago)).isoformat()
    return project


def test_reanchoring_moves_a_stale_day_4_to_today_and_keeps_every_offset():
    project = _stale(9)
    before = _dates(project)

    assert reanchor_shoot_days(project) == 9

    after = _dates(project)
    assert after[DAY4_ID] == _today()
    gaps = lambda d: {k: (date.fromisoformat(v) - date.fromisoformat(d[DAY4_ID])).days for k, v in d.items()}
    assert gaps(after) == gaps(before) == {"day_3": -1, DAY4_ID: 0, "day_5": 1, "day_6": 2}


def test_reanchoring_a_project_that_is_already_on_today_changes_nothing():
    project = build_project()
    before = _dates(project)
    assert reanchor_shoot_days(project) == 0
    assert _dates(project) == before


def test_reanchoring_moves_a_schedule_that_drifted_forwards_back_again():
    project = _stale(-4)
    assert reanchor_shoot_days(project) == -4
    assert project.shoot_day(DAY4_ID).date == _today()


def test_reanchoring_accepts_an_explicit_target_date():
    project = build_project()
    reanchor_shoot_days(project, today="2027-07-04")
    assert _dates(project) == {"day_3": "2027-07-03", DAY4_ID: "2027-07-04", "day_5": "2027-07-05", "day_6": "2027-07-06"}


def test_reanchoring_touches_the_dates_and_nothing_else():
    """It runs on every read, so it must be safe to run against a production somebody has worked in."""
    project = _stale(11)
    fact = LocationFact(project_id=project.id, resource_id="loc_rooftop", task_run_id="trun_seed", key="noise_curfew",
                        label="Noise curfew", value="22:00-06:00", binding=FactBinding.HARD, accepted=True, accepted_by="producer")
    project.location_facts.append(fact)
    project.monitors.append(MonitorRecord(id="mon_1", project_id=project.id, shoot_day_id=DAY4_ID, kind="WEATHER", status="active"))
    project.memory_scope_key = "scenepilot_proj_nightfall"
    items_before = [(i.id, i.scene_id, i.start, i.end) for i in project.shoot_day(DAY4_ID).items]

    reanchor_shoot_days(project)

    assert project.location_facts == [fact] and fact.accepted is True and fact.binding == FactBinding.HARD
    assert [m.id for m in project.monitors] == ["mon_1"]
    assert project.memory_scope_key == "scenepilot_proj_nightfall"
    assert [(i.id, i.scene_id, i.start, i.end) for i in project.shoot_day(DAY4_ID).items] == items_before


def test_reanchoring_a_project_without_the_hero_day_is_a_no_op():
    project = build_project()
    project.shoot_days = [d for d in project.shoot_days if d.id != DAY4_ID]
    before = _dates(project)
    assert reanchor_shoot_days(project) == 0
    assert _dates(project) == before


# --------------------------------------------------------------------------- #
# Through the service: startup and reset
# --------------------------------------------------------------------------- #


def _api(monkeypatch, project=None):
    from dataclasses import replace

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    if project is not None:
        repo.save_project(project)
    monkeypatch.setattr(app_module, "repo", repo)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, warm_demo=False))
    return app_module, repo


def test_startup_reanchors_a_project_that_persisted_across_a_week_of_uptime(monkeypatch):
    """The only path that matters with a real DATABASE_URL: the seed is found, never rebuilt."""
    app_module, repo = _api(monkeypatch, _stale(7))

    app_module._ensure_seed()

    project = repo.get_project(PROJECT_ID)
    assert project.shoot_day(DAY4_ID).date == _today()
    assert _in_order(project) == sorted(_in_order(project))
    assert any("Re-anchored" in e.message for e in repo.list_activity(project_id=PROJECT_ID))


def test_startup_reanchoring_keeps_the_work_already_done_in_the_project(monkeypatch):
    project = _stale(7)
    fact = LocationFact(project_id=project.id, resource_id="loc_rooftop", task_run_id="trun_seed", key="noise_curfew",
                        label="Noise curfew", value="22:00-06:00", binding=FactBinding.HARD, accepted=True, accepted_by="producer")
    project.location_facts.append(fact)
    app_module, repo = _api(monkeypatch, project)

    app_module._ensure_seed()

    kept = repo.get_project(PROJECT_ID).location_facts
    assert [f.id for f in kept] == [fact.id] and kept[0].accepted is True


def test_startup_leaves_a_project_that_is_already_on_today_alone(monkeypatch):
    app_module, repo = _api(monkeypatch, build_project())

    app_module._ensure_seed()

    assert [e for e in repo.list_activity(project_id=PROJECT_ID) if "Re-anchored" in e.message] == []


def test_reset_re_dates_the_whole_schedule_not_just_its_siblings(monkeypatch):
    app_module, _ = _api(monkeypatch, _stale(6))

    with TestClient(app_module.app) as c:
        c.post(f"/api/projects/{PROJECT_ID}/reset")
        days = c.get(f"/api/projects/{PROJECT_ID}").json()["project"]["shoot_days"]

    by_number = [d["date"] for d in sorted(days, key=lambda d: d["day_number"])]
    assert by_number == sorted(by_number) and len(set(by_number)) == len(by_number)
    assert next(d for d in days if d["id"] == DAY4_ID)["date"] == _today()


# --------------------------------------------------------------------------- #
# ...and on read, because startup is not often enough
#
# `--min-instances 1 --no-cpu-throttling` plus a persistent DATABASE_URL is a process that can run
# for the whole judging period without booting again. The drift it was written to prevent happens at
# midnight, not at boot.
# --------------------------------------------------------------------------- #


def _drift(repo, days_ago: int) -> None:
    """Age the stored project the way a week of uptime would, with the process still running."""
    project = repo.get_project(PROJECT_ID)
    for day in project.shoot_days:
        day.date = (date.fromisoformat(day.date) - timedelta(days=days_ago)).isoformat()
    repo.save_project(project)


def test_a_shoot_day_read_re_anchors_a_process_that_has_been_up_since_last_week(monkeypatch):
    app_module, repo = _api(monkeypatch, build_project())

    with TestClient(app_module.app) as c:
        _drift(repo, 8)  # the process never restarts; only the calendar moves
        body = c.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}").json()

    assert body["day"]["date"] == _today()
    assert repo.get_project(PROJECT_ID).shoot_day(DAY4_ID).date == _today()
    assert any("Re-anchored" in e.message for e in repo.list_activity(project_id=PROJECT_ID))


def test_the_project_list_re_anchors_too_so_no_page_opens_on_a_stale_date(monkeypatch):
    app_module, repo = _api(monkeypatch, build_project())

    with TestClient(app_module.app) as c:
        _drift(repo, 3)
        listed = c.get("/api/projects").json()[0]

    assert next(d for d in listed["shoot_days"] if d["id"] == DAY4_ID)["date"] == _today()


def test_a_read_of_a_project_already_on_today_writes_nothing(monkeypatch):
    """The guard is a date comparison per request, not a database write per request."""
    app_module, repo = _api(monkeypatch, build_project())
    saves: list[str] = []
    saved = repo.save_project
    monkeypatch.setattr(repo, "save_project", lambda p: (saves.append(p.id), saved(p))[1])

    with TestClient(app_module.app) as c:
        for _ in range(3):
            assert c.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}").status_code == 200
        c.get("/api/projects")

    assert saves == []


# --------------------------------------------------------------------------- #
# Whose "today"
# --------------------------------------------------------------------------- #


def test_today_is_the_productions_day_not_the_servers(monkeypatch):
    """00:30 IST on the 2nd is 19:00 UTC on the 1st. The Mumbai shoot day is the 2nd."""
    late = datetime(2027, 3, 1, 19, 0, tzinfo=timezone.utc)
    assert late.date() == date(2027, 3, 1)
    assert city_today("Mumbai", late) == date(2027, 3, 2)
    assert city_today("Mumbai", datetime(2027, 3, 1, 18, 29, tzinfo=timezone.utc)) == date(2027, 3, 1)


def test_the_seed_dates_the_hero_day_by_the_productions_own_clock(monkeypatch):
    asked: list[str] = []

    def _city_today(city, now=None):
        asked.append(city)
        return date(2027, 3, 2)

    monkeypatch.setattr(nightfall, "city_today", _city_today)

    project = build_project()
    assert _dates(project) == {"day_3": "2027-03-01", DAY4_ID: "2027-03-02", "day_5": "2027-03-03", "day_6": "2027-03-04"}
    assert set(asked) == {SEED_CITY}

    stale = _stale(5)
    stale_shift = reanchor_shoot_days(stale)
    assert stale.shoot_day(DAY4_ID).date == "2027-03-02" and stale_shift != 0
    assert asked[-1] == project.base_city  # the re-anchor asks the project's own city, not a constant
