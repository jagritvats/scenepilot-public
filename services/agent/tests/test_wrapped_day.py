"""A day that has already been shot is a record, not a plan.

Day 3 wrapped at 07:15 with one aerial plate in the can. The product asked it forward-looking
questions anyway: it offered the three rain/cast/crane fixtures on it, reported "0 scheduled
scene(s) directly affected" and then recommended deferring the already-shot scene for ₹60,000; its
call sheet printed a dusk golden hour for a unit that never saw one, a lunch at 11:15 four hours
after wrap, and an advisory to watch the sky. None of that is a rendering bug — the API was
genuinely offering it. These tests hold the API to the other answer: refuse the rescue, and hand
back what the day delivered instead.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from scenepilot.domain.enums import ScheduleItemStatus
from scenepilot.seed.nightfall import DAY4_ID, PROJECT_ID, build_project
from scenepilot.services.callsheet import build_call_sheet
from scenepilot.services.completion import day_completion
from scenepilot.services.timeutil import to_minutes


@pytest.fixture()
def project():
    return build_project()


@pytest.fixture()
def client(monkeypatch, project):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    repo.save_project(project)
    monkeypatch.setattr(app_module, "repo", repo)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, warm_demo=False))
    with TestClient(app_module.app) as c:
        yield c


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #


def test_a_wrapped_day_reports_what_it_shot_what_it_cost_and_what_carried(project):
    record = day_completion(project, project.shoot_day("day_3"))

    assert record["wrapped"] is True
    assert record["unit_call"] == "05:15" and record["first_shot"] == "05:45" and record["wrap"] == "07:15"
    assert record["elapsed_minutes"] == 120 and record["standard_minutes"] == 480
    assert [r["scene_number"] for r in record["scenes_completed"]] == ["12"]
    assert record["scenes_carried"] == [] and record["minutes_shot"] == 90
    assert record["eighths_shot"] == project.scene("sc_12").eighths
    assert record["locations"] == ["Sea link approach — Worli"] and record["units"] == ["SPLINTER"]
    # It came in under an 8 h call with nothing outstanding, so it cost nothing beyond the day.
    assert record["overtime_minutes"] == 0 and record["cost_inr"] == 0
    assert "wrapped 07:15" in record["summary"] and "Nothing carried" in record["summary"]


def test_a_day_still_ahead_of_the_production_has_no_delivery_to_report(project):
    """`None` is the signal that the rescue controls belong on this day, not the record panel."""
    assert day_completion(project, project.shoot_day(DAY4_ID)) is None
    assert day_completion(project, project.shoot_day("day_5")) is None


def test_a_scene_nobody_marked_completed_is_reported_as_outstanding_not_quietly_counted(project):
    day = project.shoot_day("day_3")
    day.items[0].status = ScheduleItemStatus.DEFERRED

    record = day_completion(project, day)

    assert record["scenes_completed"] == [] and [r["scene_number"] for r in record["scenes_carried"]] == ["12"]
    assert record["carry_over_cost_inr"] == day.carry_over_cost == record["cost_inr"]
    assert "1 scene(s) outstanding" in record["summary"]


def test_the_day_payload_and_the_call_sheet_both_carry_the_record(client):
    day = client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_3").json()
    sheet = client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_3/call-sheet").json()

    assert day["completion"]["wrap"] == "07:15" and sheet["completion"]["wrap"] == "07:15"
    assert client.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}").json()["completion"] is None


# --------------------------------------------------------------------------- #
# The refusal
# --------------------------------------------------------------------------- #


def test_a_wrapped_day_refuses_a_disruption_instead_of_costing_a_recovery_for_it(client):
    r = client.post(f"/api/projects/{PROJECT_ID}/shoot-days/day_3/disruptions", json={"fixture_id": "rain_pm"})

    assert r.status_code == 409
    assert "wrapped" in r.json()["detail"] and "cannot be rescued" in r.json()["detail"]
    assert client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_3").json()["run"] is None


def test_a_wrapped_day_is_not_offered_the_fixtures_it_would_refuse(client):
    assert client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_3").json()["fixtures"] == []
    assert len(client.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}").json()["fixtures"]) == 4


def test_a_manual_disruption_on_a_wrapped_day_is_refused_too(client):
    r = client.post(
        f"/api/projects/{PROJECT_ID}/shoot-days/day_3/disruptions",
        json={"type": "WEATHER", "title": "Rain", "window_start": "05:00", "window_end": "07:00"},
    )
    assert r.status_code == 409


def test_the_hero_day_still_accepts_the_rain_fixture(client):
    r = client.post(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "rain_pm"})
    assert r.status_code == 200 and r.json()["run_id"]


# --------------------------------------------------------------------------- #
# The call sheet of a day that is over
# --------------------------------------------------------------------------- #


def test_the_dawn_unit_gets_the_golden_hour_it_actually_worked(project):
    day3, day4 = project.shoot_day("day_3"), project.shoot_day(DAY4_ID)

    sheet3, sheet4 = build_call_sheet(project, day3), build_call_sheet(project, day4)

    assert sheet3["sun"] == "Golden hour (dawn) " + "–".join(day3.golden_hour_dawn)
    assert to_minutes(day3.golden_hour_dawn[1]) < to_minutes("07:15")  # the window it shot inside
    # The hero day's sunset scene is unchanged: it is the dusk window the validator enforces.
    assert sheet4["sun"] == "Golden hour (dusk) " + "–".join(day4.golden_hour_dusk)


def test_no_meal_penalty_is_printed_for_a_break_that_falls_after_wrap(project):
    """`evaluate_meal_penalties` owes nothing when the unit wraps first; the sheet now agrees."""
    lunch = build_call_sheet(project, project.shoot_day("day_3"))["meals"]["lunch"]

    assert lunch["due"] is False and lunch["count"] == 0
    assert "none due" in lunch["time"] and "07:15" in lunch["time"]
    assert "meal penalty exposure" not in lunch["time"]


def test_the_hero_days_real_lunch_gap_is_untouched(project):
    lunch = build_call_sheet(project, project.shoot_day(DAY4_ID))["meals"]["lunch"]
    assert lunch["due"] is True and lunch["scheduled_gap"] is True and lunch["time"] == "12:30–13:30"


def test_the_night_unit_still_owes_the_meal_penalty_the_stripboard_charges(project):
    """Day 6's break falls at 22:00 with no gap and the unit wraps at 23:30 — a real exposure."""
    lunch = build_call_sheet(project, project.shoot_day("day_6"))["meals"]["lunch"]
    assert lunch["due"] is True and lunch["scheduled_gap"] is False and "meal penalty exposure" in lunch["time"]


def test_a_wrapped_sheet_states_what_it_delivered_instead_of_advising_on_the_weather(project):
    advisories = build_call_sheet(project, project.shoot_day("day_3"))["advisories"]

    assert any("wrapped at 07:15" in a and "record of what was shot" in a for a in advisories)
    assert not any("Weather-sensitive equipment" in a for a in advisories)
    # ...and the hero day, which has not happened yet, still gets the warning.
    assert any("Weather-sensitive equipment" in a for a in build_call_sheet(project, project.shoot_day(DAY4_ID))["advisories"])


def test_the_wrapped_row_reads_completed_and_names_its_set(project):
    row = build_call_sheet(project, project.shoot_day("day_3"))["schedule"][0]
    assert row["status"] == ScheduleItemStatus.COMPLETED.value
    assert row["location"] == "Sea link approach — Worli" and row["cast"] == []
