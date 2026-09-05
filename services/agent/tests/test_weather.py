"""The hourly weather timeline: cited per hour, and never drawn where nothing was said.

Two families of property here, and the first is the expensive one:

  1. **Key stability.** The recording is bought once, in a supervised paid session, and replayed for
     the life of the demo. The seed re-anchors the shoot week to *today* on every boot, so a key that
     carries a raw date rots overnight; and every date normalises to the same placeholder, so a key
     that carries *only* the date collides across days and the second recording overwrites the first.
     Both are tested, because either one silently wastes the session.
  2. **Nothing is invented.** An hour nobody answered is absent, not zero; a condition without a
     figure keeps the condition and reports no percentage; a "no information found" answer is
     dropped rather than graded.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from scenepilot.config import settings as default_settings
from scenepilot.domain.models import TaskRun
from scenepilot.seed.nightfall import DAY4_ID, build_project
from scenepilot.services.budget import PRICED, costs_money
from scenepilot.services.dossier import map_facts
from scenepilot.services.weather import map_timeline, parse_precip_pct
from scenepilot.tools.parallel_task import (
    DOSSIER_SCHEMA,
    WEATHER_SCHEMA,
    build_task_input,
    build_task_request,
    build_weather_input,
    recording_key,
)

from .test_dossier import FakeClient, FakeTaskRuns, _Basis, _Citation, _live_settings


CONTENT = {
    "day_summary": "Monsoon showers through the afternoon, heaviest between 13:00 and 16:00.",
    "hour_06": "10% - overcast, dry",
    "hour_12": "45% - passing showers",
    "hour_13": "70% - moderate showers",
    "hour_14": "showers likely, heavy at times",
    "hour_15": "",
    "hour_16": "No information found for this hour.",
    "hour_17": "Hourly intensity not stated for this period.",
}
BASIS = [
    _Basis("day_summary", "high", [_Citation("https://mausam.imd.gov.in/mumbai", "IMD Mumbai")]),
    _Basis("hour_06", "high", [_Citation("https://mausam.imd.gov.in/mumbai/0600", "IMD nowcast 06:00", ["10% chance"])]),
    _Basis("hour_13", "high", [_Citation("https://mausam.imd.gov.in/mumbai/1300", "IMD nowcast 13:00", ["70% moderate showers"])]),
    _Basis("hour_14", "medium", []),
]


def _day(project, day_id=DAY4_ID):
    return project.shoot_day(day_id)


def _run_weather(settings=None, content=CONTENT, basis=BASIS, day_id=DAY4_ID):
    from scenepilot.tools.parallel_task import ParallelTaskTool

    p = build_project()
    fake = FakeTaskRuns(content, basis)
    tool = ParallelTaskTool(p, settings=settings or _live_settings(), client=FakeClient(fake))
    return p, tool.weather_timeline(_day(p, day_id)), fake


# --------------------------------------------------------------------------- #
# 1. The paid recording's key
# --------------------------------------------------------------------------- #


def _key_for(project, day):
    return recording_key(build_task_request(build_weather_input(project, day), "core-fast", WEATHER_SCHEMA))


def test_the_key_survives_the_seed_re_anchoring_the_shoot_week():
    """A recording bought today must still hit next week. The date normalises; the key does not move."""
    p = build_project()
    day = _day(p)
    before = _key_for(p, day)
    day.date = "2026-10-01"  # the seed re-dates the week on every boot
    assert _key_for(p, day) == before


def test_two_days_do_not_share_one_recording():
    """Every date collapses to the same placeholder, so the day number is what keeps the keys apart."""
    p = build_project()
    days = p.shoot_days
    keys = {_key_for(p, d) for d in days}
    assert len(keys) == len(days), "two shoot days hash to one fixture — the second recording would overwrite the first"


def test_a_reworded_prompt_is_a_different_recording():
    """The guard that says out loud: the input text is frozen once the fixture is bought."""
    p = build_project()
    day = _day(p)
    reworded = build_weather_input(p, day).replace("hour-by-hour", "hour by hour")
    assert recording_key(build_task_request(reworded, "core-fast", WEATHER_SCHEMA)) != _key_for(p, day)


def test_the_weather_request_carries_the_weather_schema_and_the_dossier_still_carries_its_own():
    p = build_project()
    weather = build_task_request(build_weather_input(p, _day(p)), "core-fast", WEATHER_SCHEMA)
    dossier = build_task_request(build_task_input(p, p.resource("loc_rooftop")), "core-fast")
    assert weather["output_schema"] is WEATHER_SCHEMA
    assert dossier["output_schema"] is DOSSIER_SCHEMA  # the generalisation must not move dossier keys


def test_the_prompt_prints_the_date_in_the_one_form_that_normalises():
    p = build_project()
    day = _day(p)
    text = build_weather_input(p, day)
    assert day.date in text and f"production day {day.day_number}" in text
    # No solar or schedule content: both are recomputed per date and would drift the key.
    for drifting in ("golden hour", "sunrise", "sunset", "unit call"):
        assert drifting not in text.lower()


# --------------------------------------------------------------------------- #
# 2. The request the tool actually sends
# --------------------------------------------------------------------------- #


def test_the_task_request_is_by_the_book():
    p, tr, fake = _run_weather()
    sent = fake.created[0]
    assert sent["processor"] == "core-fast"
    assert sent["task_spec"] == {"output_schema": {"type": "json", "json_schema": WEATHER_SCHEMA}}
    assert sent["metadata"] == {"project_id": p.id, "shoot_day_id": DAY4_ID, "kind": "weather_timeline"}
    assert "field-basis-2025-11-25" in sent["betas"]  # a citation per hour
    assert "session_id" not in sent and "client_model" not in sent
    # A forecast re-ask is a new question about a moved world, not a continued investigation.
    assert "previous_interaction_id" not in sent
    assert tr.status == "OK" and tr.purpose == "weather_timeline"


def test_the_run_is_day_scoped_and_can_never_become_a_location_fact():
    p, tr, _ = _run_weather()
    assert tr.shoot_day_id == DAY4_ID and tr.resource_id is None
    assert map_facts(tr, p) == []  # no resource → the dossier gate ignores it entirely


# --------------------------------------------------------------------------- #
# 3. Nothing is invented
# --------------------------------------------------------------------------- #


def test_only_the_hours_a_source_answered_are_drawn():
    _, tr, _ = _run_weather()
    view = map_timeline(tr)
    assert [h["hour"] for h in view["hours"]] == [6, 12, 13, 14]
    # 15 was empty, 16 said nothing was found, and 17 hedged with "not stated". None becomes a zero
    # bar: the same narrow filter that grades dossier fields decides, and it errs towards dropping.
    assert not any(h["hour"] in (15, 16, 17) for h in view["hours"])


def test_a_condition_without_a_figure_keeps_the_words_and_reports_no_percentage():
    _, tr, _ = _run_weather()
    hours = {h["hour"]: h for h in map_timeline(tr)["hours"]}
    assert hours[13]["precip_pct"] == 70
    assert hours[14]["precip_pct"] is None and "showers likely" in hours[14]["value"]


@pytest.mark.parametrize(
    "value,expected",
    [("70% - moderate showers", 70), ("chance of rain 5 %", 5), ("showers likely", None), ("120% certain", None), ("0% - dry", 0)],
)
def test_percentage_parsing_reads_only_what_is_written(value, expected):
    assert parse_precip_pct(value) == expected


def test_every_hour_carries_its_own_basis():
    _, tr, _ = _run_weather()
    hours = {h["hour"]: h for h in map_timeline(tr)["hours"]}
    assert hours[13]["confidence"] == "high" and hours[13]["citations"][0]["url"].endswith("/1300")
    assert hours[6]["citations"][0]["excerpts"] == ["10% chance"]
    assert hours[12]["citations"] == [] and hours[12]["confidence"] is None  # no basis reported for it
    assert map_timeline(tr)["cited_hours"] == 2


def test_a_run_that_answered_nothing_renders_as_nothing():
    _, tr, _ = _run_weather(content={"day_summary": "No information found."}, basis=[])
    assert map_timeline(tr) is None


def test_an_errored_or_missing_run_is_never_a_timeline():
    assert map_timeline(None) is None
    assert map_timeline(TaskRun(purpose="weather_timeline", status="ERROR", error="boom")) is None


# --------------------------------------------------------------------------- #
# 4. Gating, pricing and the API
# --------------------------------------------------------------------------- #


def test_the_timeline_is_priced_and_free_to_replay():
    assert "weather" in PRICED and PRICED["weather"].recorded
    assert costs_money("weather", _live_settings()) is True
    assert costs_money("weather", replace(default_settings, mode="replay")) is False


def test_the_weather_endpoint_gates_researches_and_reads_back(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))

    with TestClient(app_module.app) as c:
        # Off by default, and the refusal names the variable that turns it on.
        r = c.post(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}/weather-timeline")
        assert r.status_code == 501 and r.json()["detail"]["env"] == "SCENEPILOT_PARALLEL_TASK=1"

        # Day 4 now warms from a committed recording, so it reads back a real replayed run. What
        # came back is a *day* summary and not one usable hour — Mumbai answers at day resolution —
        # which is the honest shape this feature was built for: hours empty, summary cited, and the
        # UI keeps offering the hourly ask rather than drawing an axis of blanks.
        warmed = c.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}/weather-timeline").json()["timeline"]
        assert warmed is not None and warmed["replayed"] is True
        assert warmed["hours"] == [], "no source answered a single hour; drawing one would invent it"
        assert warmed["day_summary"] and warmed["day_summary"]["citations"], "a summary must carry its sources"

        # A day with no recording at all still says so, rather than drawing an empty axis.
        assert c.get("/api/projects/proj_nightfall/shoot-days/day_5/weather-timeline").json()["timeline"] is None
        assert c.get("/api/projects/proj_nightfall/shoot-days/day_nope/weather-timeline").status_code == 404

        monkeypatch.setattr(app_module, "settings", _live_settings(warm_demo=False))
        monkeypatch.setattr(app_module.ParallelTaskTool, "client", property(lambda self: FakeClient(FakeTaskRuns(CONTENT, BASIS))))

        body = c.post(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}/weather-timeline").json()
        assert body["task_run"]["status"] == "OK" and body["task_run"]["shoot_day_id"] == DAY4_ID
        assert [h["hour"] for h in body["timeline"]["hours"]] == [6, 12, 13, 14]

        # And it reads back from the GET without spending anything again.
        assert c.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}/weather-timeline").json()["timeline"]["cited_hours"] == 2

        # A weather run is a Task run against the project; the location panel has nothing to draw it
        # as, so it must not arrive in the project-wide dossier view.
        assert all(t["purpose"] == "location_dossier" for t in c.get("/api/projects/proj_nightfall/dossiers").json()["task_runs"])


def test_a_wrapped_day_is_never_forecast(monkeypatch):
    """Researching tomorrow's rain for a day already in the can spends money on nobody's question."""
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    monkeypatch.setattr(app_module, "settings", _live_settings(warm_demo=False))
    with TestClient(app_module.app) as c:
        wrapped = next(d for d in build_project().shoot_days if d.status.value == "WRAPPED")
        r = c.post(f"/api/projects/proj_nightfall/shoot-days/{wrapped.id}/weather-timeline")
        assert r.status_code == 409 and "wrapped" in r.json()["detail"].lower()
