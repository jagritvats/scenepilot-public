"""The call sheet is the document a judge reads hardest, so its gaps are pinned as tightly as its facts.

A call sheet is the one piece of paper everybody on a unit reads, which makes it the place a
plausible-looking invented value does the most damage: a hospital nobody checked, a forecast nobody
fetched, a page count that is a guess. The tests here are mostly about the *absences* — that a day
with no reported weather says so instead of printing one, that a set whose dossier has not run is
listed as a gap rather than quietly dropped, that a day total is withheld when a scene has no count.

The rest pin the two numbers a reader checks first: "Day 4 of 6", which is read off the production's
own day numbering and not off how many days happen to be stored, and the revision colour, which is a
true statement only while it counts approved ChangeSets rather than attempted recoveries.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from scenepilot.config import settings as default_settings
from scenepilot.domain.enums import DisruptionType, ResourceType
from scenepilot.seed.nightfall import DAY4_ID, build_project, make_fixture_disruption
from scenepilot.seed.warm import warm_demo_state
from scenepilot.services.callsheet import REVISION_LADDER, build_call_sheet, eighths_label, revision_of
from scenepilot.store.db import make_engine
from scenepilot.store.repo import Repo


@pytest.fixture()
def project():
    return build_project()


@pytest.fixture()
def warm():
    """The seed with its recorded location dossiers replayed onto it — where the hospital facts live."""
    repo, project = Repo(make_engine("sqlite:///:memory:")), build_project()
    repo.save_project(project)
    warm_demo_state(repo, project, replace(default_settings, warm_demo=True))
    return project


def _day(project, number: int):
    return next(d for d in project.shoot_days if d.day_number == number)


def _sheet(project, number: int = 4, **kwargs):
    return build_call_sheet(project, _day(project, number), **kwargs)


# --------------------------------------------------------------------------- #
# "Day 4 of 6" — the production's numbering, not the row count
# --------------------------------------------------------------------------- #


def test_the_day_count_is_the_productions_own_numbering_not_how_many_days_are_stored(project):
    """Counting the rows would print "Day 4 of 4" and tell the unit it was wrapping the picture.

    This project models Days 3–6 of a six-day schedule. Those are different numbers, and the one a
    crew reads off a call sheet is the schedule's, so the sheet has to say 6 while holding 4.
    """
    assert len(project.shoot_days) == 4
    sheet = _sheet(project, 4)
    assert sheet["day_number"] == 4
    assert sheet["day_of_total"] == 6
    assert sheet["days_held"] == [3, 4, 5, 6]


def test_every_day_reports_the_same_schedule_length(project):
    assert {_sheet(project, n)["day_of_total"] for n in (3, 4, 5, 6)} == {6}


# --------------------------------------------------------------------------- #
# F3 — the revision ladder
# --------------------------------------------------------------------------- #


def test_an_unrevised_sheet_is_white_and_does_not_call_itself_a_revision():
    original = revision_of(0)
    assert original["name"] == "WHITE" and original["is_original"]
    # A first-issue call sheet does not print "Rev.0"; it prints nothing, and so does this.
    assert original["label"] == "WHITE" and "Rev" not in original["label"]


def test_each_approved_change_moves_the_sheet_one_colour_down_the_trade_ladder():
    assert [revision_of(i)["name"] for i in range(6)] == ["WHITE", "BLUE", "PINK", "YELLOW", "GREEN", "GOLDENROD"]
    assert revision_of(1)["label"] == "Rev.1 — BLUE"


def test_the_ladder_wraps_onto_double_whites_rather_than_running_out():
    """Unreachable on a six-day schedule, and handled anyway so the field can never come back blank."""
    assert revision_of(len(REVISION_LADDER))["name"] == "DOUBLE WHITE"
    assert revision_of(2 * len(REVISION_LADDER))["name"] == "3× WHITE"
    assert revision_of(-5)["name"] == "WHITE"


def test_every_rung_of_the_ladder_carries_a_colour_the_page_can_be_tinted_with():
    for name, hex_colour in REVISION_LADDER:
        assert hex_colour.startswith("#") and len(hex_colour) == 7, name


# --------------------------------------------------------------------------- #
# Page counts
# --------------------------------------------------------------------------- #


def test_the_hero_day_totals_four_and_seven_eighths_pages(project):
    """The board and the sheet total the same day, so they have to reach the same figure."""
    pages = _sheet(project, 4)["pages"]
    assert pages["total_eighths"] == 39
    assert pages["total_label"] == "4 7/8"
    assert pages["scene_count"] == 4 and pages["unpriced_scenes"] == []


def test_a_day_with_an_uncounted_scene_states_no_total_and_names_the_scene(project):
    """A partial total read as the day's pages is a smaller number than the day is shooting."""
    day = _day(project, 4)
    project.scene(day.items[0].scene_id).eighths = None
    pages = _sheet(project, 4)["pages"]
    assert pages["total_eighths"] is None and pages["total_label"] is None
    assert pages["unpriced_scenes"] == [project.scene(day.items[0].scene_id).number]
    assert "understate" in pages["reason"]


def test_each_scheduled_row_carries_its_own_page_count_in_board_notation(project):
    rows = _sheet(project, 4)["schedule"]
    assert [r["pages"] for r in rows] == ["1 2/8", "1 4/8", "1 1/8", "1"]
    assert sum(r["eighths"] for r in rows) == 39


def test_eighths_are_written_the_way_a_board_writes_them():
    assert eighths_label(39) == "4 7/8"
    assert eighths_label(8) == "1"
    assert eighths_label(3) == "3/8"
    # A scene with no count reads as absent, never as a scene that takes no pages.
    assert eighths_label(None) is None
    assert eighths_label(0) == "0/8"


# --------------------------------------------------------------------------- #
# Sun and weather
# --------------------------------------------------------------------------- #


def test_the_sheets_sun_is_the_same_sun_the_validator_holds_scenes_to(project):
    """Two golden hours on one production is the bug that lived inside the engine; not again here."""
    day = _day(project, 4)
    solar = _sheet(project, 4)["solar"]
    assert solar["golden_hour_dusk"] == list(day.golden_hour_dusk)
    assert solar["golden_hour_dawn"] == list(day.golden_hour_dawn)
    assert solar["sunrise"] < solar["solar_noon"] < solar["sunset"]
    assert solar["civil_twilight_dawn"] < solar["sunrise"]
    assert solar["civil_twilight_dusk"] > solar["sunset"]


def test_a_day_nobody_reported_weather_for_prints_no_forecast_and_says_why(project):
    weather = _sheet(project, 4)["weather"]
    assert weather["reported"] is False
    assert weather["headline"] is None and weather["sources"] == []
    assert "no forecast has been fetched" in weather["reason"]


def test_a_reported_peril_prints_its_window_dry_out_and_external_check(project):
    day = _day(project, 4)
    disruption = make_fixture_disruption(project.id, DAY4_ID, "rain_pm")
    assert disruption.type == DisruptionType.WEATHER
    weather = build_call_sheet(project, day, disruption=disruption)["weather"]
    assert weather["reported"] is True
    assert weather["headline"] == disruption.title
    assert weather["window"]["start"] == disruption.window_start
    # The unit cannot shoot the moment the rain stops; the sheet carries the surface's dry-out too.
    assert weather["window"]["dry_out_minutes"] == disruption.dry_out_minutes
    assert weather["window"]["clear_at"] > weather["window"]["end"]


def test_a_non_weather_disruption_does_not_become_a_weather_block(project):
    """A permit withdrawal is a real disruption and not a forecast; the weather block stays empty."""
    day = _day(project, 4)
    disruption = make_fixture_disruption(project.id, DAY4_ID, "rain_pm")
    disruption.type = DisruptionType.REGULATORY
    weather = build_call_sheet(project, day, disruption=disruption)["weather"]
    assert weather["reported"] is False


# --------------------------------------------------------------------------- #
# The hospital — a Parallel dossier field reaching a document a unit uses
# --------------------------------------------------------------------------- #


def test_the_nearest_hospital_is_listed_per_set_with_the_page_parallel_cited(warm):
    """A company move across Mumbai moves the nearest emergency department with it."""
    hospitals = _sheet(warm, 4)["safety"]["hospitals"]
    assert len(hospitals["entries"]) >= 2
    for entry in hospitals["entries"]:
        assert entry["value"].strip()
        assert entry["location"]
        assert entry["source_url"] and entry["source_url"].startswith("http")
    # Every set listed is a set this day actually works.
    day_sets = {warm.resource(i.location_id or warm.scene(i.scene_id).location_id).name for i in _day(warm, 4).items}
    assert {e["location"] for e in hospitals["entries"]} <= day_sets


def test_a_set_whose_dossier_returned_no_hospital_is_reported_as_a_gap_not_dropped(warm):
    """The alley's recorded dossier answers "" for this field, and a missing hospital must be visible."""
    hospitals = _sheet(warm, 4)["safety"]["hospitals"]
    assert hospitals["sets_without_one"]
    named = {n for n in hospitals["sets_without_one"]}
    assert not (named & {e["location"] for e in hospitals["entries"]})


def test_a_production_with_no_dossiers_run_says_so_rather_than_leaving_the_row_blank(project):
    hospitals = _sheet(project, 4)["safety"]["hospitals"]
    assert hospitals["entries"] == []
    assert "Run the location dossier" in hospitals["reason"]


# --------------------------------------------------------------------------- #
# Departments, radio and safety
# --------------------------------------------------------------------------- #


def test_the_radio_plan_comes_off_the_production_not_out_of_the_renderer(project):
    """A channel computed at print time is an operating instruction nobody agreed to."""
    heads = {r.attributes.get("department"): r.walkie_channel for r in project.resources if r.type == ResourceType.CREW}
    assert heads["1st AD"] == 1
    # Two departments share a channel because this unit put them on one; that is allowed and stated.
    assert heads["Stunt & rigging"] == heads["SFX / pyrotechnics"] == 5
    assert all(1 <= c <= 8 for c in heads.values())
    # Nothing that is not crew carries one — a location does not hold a radio.
    assert all(r.walkie_channel is None for r in project.resources if r.type != ResourceType.CREW)


def test_the_ad_and_the_production_office_are_on_every_day_whatever_is_scheduled(project):
    for number in (3, 4, 5, 6):
        departments = {d["department"] for d in _sheet(project, number)["departments"]}
        assert {"1st AD", "Production office"} <= departments


def test_a_department_reaches_the_sheet_because_the_day_carries_its_equipment(project):
    """Derived by the same mapping the coordination engine notifies against, so the two cannot drift."""
    day4 = {d["department"] for d in _sheet(project, 4)["departments"]}
    day5 = {d["department"] for d in _sheet(project, 5)["departments"]}
    # Day 4 flies the drone and rigs pyrotechnics; Day 5 is a stage interior that does neither.
    assert "Aerial / drone unit" in day4 and "SFX / pyrotechnics" in day4
    assert "Aerial / drone unit" not in day5


def test_every_listed_department_prints_a_channel_and_a_head(project):
    for head in _sheet(project, 4)["departments"]:
        assert head["channel"] is not None and head["name"]
    channels = [h["channel"] for h in _sheet(project, 4)["departments"]]
    assert channels == sorted(channels)


def test_the_safety_meeting_is_the_days_own_unit_call(project):
    day = _day(project, 4)
    safety = _sheet(project, 4)["safety"]
    assert safety["meeting"] == day.unit_call
    assert day.unit_call in safety["meeting_note"]


def test_hazards_are_read_off_what_the_day_actually_books(project):
    hazards = {h["item"] for h in _sheet(project, 4)["safety"]["hazards"]}
    assert "Stunt & rigging" in hazards and "SFX / pyrotechnics" in hazards
    # The rooftop's own recorded surface, not a generic caution.
    assert any("slippery when wet" in h["why"] for h in _sheet(project, 4)["safety"]["hazards"])


def test_weather_sensitive_kit_is_only_a_hazard_when_a_peril_is_actually_reported(project):
    day = _day(project, 4)
    clean = {h["item"] for h in _sheet(project, 4)["safety"]["hazards"]}
    assert "Weather-sensitive equipment" not in clean
    wet = build_call_sheet(project, day, disruption=make_fixture_disruption(project.id, DAY4_ID, "rain_pm"))
    assert "Weather-sensitive equipment" in {h["item"] for h in wet["safety"]["hazards"]}


# --------------------------------------------------------------------------- #
# Advance schedule and signatures
# --------------------------------------------------------------------------- #


def test_the_advance_block_is_the_next_day_by_number(project):
    advance = _sheet(project, 4)["advance"]
    assert advance["day_number"] == 5
    assert advance["unit_call"] == _day(project, 5).unit_call
    assert advance["sets"]


def test_the_last_day_held_advances_to_nothing(project):
    assert _sheet(project, 6)["advance"] is None


def test_the_sheet_is_prepared_by_the_productions_own_first_ad(project):
    signatures = _sheet(project, 4)["signatures"]
    ad = next(r for r in project.resources if r.attributes.get("department") == "1st AD")
    assert signatures["prepared_by"]["name"] == ad.name
    assert signatures["prepared_by_reason"] is None


def test_an_unapproved_sheet_carries_no_signature_and_says_why(project):
    signatures = _sheet(project, 4)["signatures"]
    assert signatures["approved_by"] is None
    assert "No recovery has been approved" in signatures["approved_reason"]


def test_an_approved_sheet_names_the_producer_who_signed_it(project):
    signatures = _sheet(project, 4, approved_by="producer", approved_at="2026-09-01T10:00:00+00:00")["signatures"]
    assert signatures["approved_by"] == "producer"
    assert signatures["approved_reason"] is None


# --------------------------------------------------------------------------- #
# Over the wire: the colour is a claim about what a producer approved
# --------------------------------------------------------------------------- #


def test_a_day_with_nothing_approved_is_served_white():
    """Reporting a disruption does not reissue a call sheet. Approving a recovery does."""
    from fastapi.testclient import TestClient

    from scenepilot.api.app import app
    from scenepilot.seed.nightfall import PROJECT_ID

    # As a context manager, so the app's lifespan seeds the (throwaway) database first.
    with TestClient(app) as client:
        body = client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_4/call-sheet").json()
    assert body["current"]["revision"]["is_original"]
    assert body["current"]["revision"]["name"] == "WHITE"
    # Nothing has been signed, so the sheet says so rather than signing itself.
    assert body["current"]["signatures"]["approved_by"] is None
    assert body["baseline"] is None


def test_the_schedule_rows_name_their_unit():
    """A call sheet that cannot say which unit shoots a scene cannot report a unit clash either."""
    from scenepilot.seed.nightfall import build_project
    from scenepilot.services.callsheet import build_call_sheet

    p = build_project()
    day3 = build_call_sheet(p, p.shoot_day("day_3"))
    assert [r["unit"] for r in day3["schedule"]] == ["SPLINTER"]
    day4 = build_call_sheet(p, p.shoot_day("day_4"))
    assert set(r["unit"] for r in day4["schedule"]) == {"MAIN"}
