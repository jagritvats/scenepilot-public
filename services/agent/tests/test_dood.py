"""Tests for Day-Out-Of-Days (DOOD) cast matrix generation."""

from __future__ import annotations

import pytest
from scenepilot.domain.breakdown_models import CastDOODEntry
from scenepilot.domain.enums import ResourceType
from scenepilot.domain.models import Project, Resource, Scene, ScheduleItem, ShootDay
from scenepilot.ingestion.dood import build_dood_matrix


def test_dood_matrix_calculation():
    # Setup test project with 4 shoot days
    d1 = ShootDay(id="d1", project_id="p1", day_number=1, date="2026-09-01", items=[ScheduleItem(id="i1", scene_id="s1", start="08:00", end="12:00")])
    d2 = ShootDay(id="d2", project_id="p1", day_number=2, date="2026-09-02", items=[ScheduleItem(id="i2", scene_id="s2", start="08:00", end="12:00")])
    d3 = ShootDay(id="d3", project_id="p1", day_number=3, date="2026-09-03", items=[ScheduleItem(id="i3", scene_id="s3", start="08:00", end="12:00")])
    d4 = ShootDay(id="d4", project_id="p1", day_number=4, date="2026-09-04", items=[ScheduleItem(id="i4", scene_id="s4", start="08:00", end="12:00")])

    # Cast 1: Works day 1 and day 4 -> Days 2 & 3 are Hold (H)
    cast1 = Resource(id="c1", type=ResourceType.CAST, name="Lead Actor", day_rate_inr=25000)
    # Cast 2: Works day 2 only -> Single day (SWF)
    cast2 = Resource(id="c2", type=ResourceType.CAST, name="Guest Star")

    s1 = Scene(id="s1", number="1", heading="EXT. PARK - DAY", int_ext="EXT", time_of_day="DAY", cast_ids=["c1"])
    s2 = Scene(id="s2", number="2", heading="INT. CAFE - DAY", int_ext="INT", time_of_day="DAY", cast_ids=["c2"])
    s3 = Scene(id="s3", number="3", heading="EXT. STREET - DAY", int_ext="EXT", time_of_day="DAY", cast_ids=[])
    s4 = Scene(id="s4", number="4", heading="INT. OFFICE - DAY", int_ext="INT", time_of_day="DAY", cast_ids=["c1"])

    project = Project(
        id="p1",
        title="Test Project",
        scenes=[s1, s2, s3, s4],
        resources=[cast1, cast2],
        shoot_days=[d1, d2, d3, d4],
    )

    dood = build_dood_matrix(project)
    assert len(dood) == 2

    # Verify Cast 1
    c1_dood = next(e for e in dood if e.cast_id == "c1")
    assert c1_dood.day_status["d1"] == "SW"
    assert c1_dood.day_status["d2"] == "H"
    assert c1_dood.day_status["d3"] == "H"
    assert c1_dood.day_status["d4"] == "WF"
    assert c1_dood.total_work_days == 2
    assert c1_dood.total_hold_days == 2
    # First call to last, inclusive — what the production is engaged for, work + hold.
    assert c1_dood.total_engaged_days == 4
    assert c1_dood.hold_day_cost_warning is True
    # Priced at the performer's own contracted rate, never at a default.
    assert c1_dood.day_rate_inr == 25000
    assert c1_dood.estimated_hold_cost_inr == 2 * 25000

    # Verify Cast 2
    c2_dood = next(e for e in dood if e.cast_id == "c2")
    assert c2_dood.day_status["d1"] == ""
    assert c2_dood.day_status["d2"] == "SWF"
    assert c2_dood.day_status["d3"] == ""
    assert c2_dood.day_status["d4"] == ""
    assert c2_dood.total_work_days == 1
    assert c2_dood.total_hold_days == 0
    assert c2_dood.total_engaged_days == 1
    assert c2_dood.hold_day_cost_warning is False


def test_a_performer_with_no_day_rate_is_counted_but_not_priced():
    """The bug this replaces: every performer defaulted to ₹25,000, a rate read from nothing.

    A lead's idle day and a stunt double's cost the production very different money, and a matrix
    that quotes one figure for both is quoting a number no contract contains. Counting the hold days
    is a fact; pricing them without a rate is not.
    """
    days = [
        ShootDay(id=f"d{i}", project_id="p1", day_number=i, date=f"2026-09-0{i}", items=[ScheduleItem(id=f"i{i}", scene_id=f"s{i}", start="08:00", end="12:00")])
        for i in range(1, 5)
    ]
    unpriced = Resource(id="c1", type=ResourceType.CAST, name="Unpriced Actor")
    scenes = [
        Scene(id=f"s{i}", number=str(i), heading="INT. ROOM - DAY", int_ext="INT", time_of_day="DAY", cast_ids=["c1"] if i in (1, 4) else [])
        for i in range(1, 5)
    ]
    project = Project(id="p1", title="Test Project", scenes=scenes, resources=[unpriced], shoot_days=days)

    entry = build_dood_matrix(project)[0]

    assert entry.total_hold_days == 2
    assert entry.day_rate_inr is None
    # `None`, not 0: zero is a cost, and this is the absence of one.
    assert entry.estimated_hold_cost_inr is None
    assert entry.hold_day_cost_warning is True
    assert "no day rate on file" in entry.warning_message


def test_the_hero_cast_are_priced_from_the_production_and_not_from_a_default():
    from scenepilot.seed.nightfall import build_project

    project = build_project()
    rates = {e.name: e.day_rate_inr for e in build_dood_matrix(project)}
    assert rates["Aarav Mehta (Rider / lead)"] == 180000
    assert rates["Stunt double (Rider)"] == 45000
    # The defect this pins: a single default made these two the same money.
    assert len(set(rates.values())) == len(rates)


def test_the_before_and_after_matrix_names_who_gained_a_paid_hold_day():
    """An aggregate cost delta is an abstraction; a person and a rupee figure is what a UPM reacts to."""
    from scenepilot.ingestion.dood import dood_delta

    days = [
        ShootDay(id=f"d{i}", project_id="p1", day_number=i, date=f"2026-09-0{i}", items=[])
        for i in range(1, 5)
    ]
    # Committed state, after the recovery deferred Sc 2 off day 2: the performer works days 1 and 4,
    # so day 2 — the day they were dropped from — and day 3 are both paid holds.
    days[0].items = [ScheduleItem(id="i1", scene_id="s1", start="08:00", end="12:00")]
    days[3].items = [ScheduleItem(id="i4", scene_id="s4", start="08:00", end="12:00")]
    cast = Resource(id="c1", type=ResourceType.CAST, cast_number=1, name="Lead Actor", day_rate_inr=95000)
    scenes = [
        Scene(id=f"s{i}", number=str(i), heading="INT. ROOM - DAY", int_ext="INT", time_of_day="DAY", cast_ids=["c1"])
        for i in (1, 2, 4)
    ]
    project = Project(id="p1", title="Test Project", scenes=scenes, resources=[cast], shoot_days=days)

    # The rescue's baseline is that one day's pre-recovery schedule: day 2 still had Sc 2 on it, so
    # the performer worked it and only day 3 was a hold.
    baseline = [ScheduleItem(id="i2", scene_id="s2", start="08:00", end="12:00")]
    delta = dood_delta(project, "d2", baseline)

    change = delta["changes"][0]
    assert change["name"] == "Lead Actor" and change["cast_number"] == 1
    assert change["hold_days_before"] == 1 and change["hold_days_after"] == 2
    assert change["hold_days_gained"] == 1
    assert change["work_days_before"] == 3 and change["work_days_after"] == 2
    # The cell that moved is the day the recovery took them off.
    assert {"shoot_day_id": "d2", "before": "W", "after": "H"} in change["cells"]
    assert change["added_hold_cost_inr"] == 95000
    assert "Lead Actor gains 1 paid hold day" in delta["headline"]
    assert "₹95,000" in delta["headline"]


def test_a_recovery_that_moved_nobody_reports_no_change_rather_than_a_highlight():
    from scenepilot.ingestion.dood import dood_delta

    days = [
        ShootDay(id=f"d{i}", project_id="p1", day_number=i, date=f"2026-09-0{i}", items=[ScheduleItem(id=f"i{i}", scene_id=f"s{i}", start="08:00", end="12:00")])
        for i in range(1, 3)
    ]
    cast = Resource(id="c1", type=ResourceType.CAST, cast_number=1, name="Lead Actor", day_rate_inr=95000)
    scenes = [Scene(id=f"s{i}", number=str(i), heading="INT. ROOM - DAY", int_ext="INT", time_of_day="DAY", cast_ids=["c1"]) for i in (1, 2)]
    project = Project(id="p1", title="Test Project", scenes=scenes, resources=[cast], shoot_days=days)

    delta = dood_delta(project, "d2", list(days[1].items))

    assert delta["changes"] == []
    assert delta["headline"] is None
    assert delta["total_added_hold_cost_inr"] is None


# --------------------------------------------------------------------------- #
# Drop and pickup — the only lever a production has against hold-day cost
# --------------------------------------------------------------------------- #


def _engagement_with_a_gap(hold_days: int, day_rate: int = 95000):
    """A performer who works the first and last day of an (hold_days + 2)-day schedule."""
    total = hold_days + 2
    days = [
        ShootDay(id=f"d{i}", project_id="p1", day_number=i, date=f"2026-09-{i:02d}", items=[])
        for i in range(1, total + 1)
    ]
    days[0].items = [ScheduleItem(id="i1", scene_id="s1", start="08:00", end="12:00")]
    days[-1].items = [ScheduleItem(id="i2", scene_id="s2", start="08:00", end="12:00")]
    cast = Resource(id="c1", type=ResourceType.CAST, cast_number=1, name="Lead Actor", day_rate_inr=day_rate)
    scenes = [
        Scene(id=f"s{i}", number=str(i), heading="INT. ROOM - DAY", int_ext="INT", time_of_day="DAY", cast_ids=["c1"])
        for i in (1, 2)
    ]
    return Project(id="p1", title="Gap", scenes=scenes, resources=[cast], shoot_days=days)


def test_an_agreement_with_no_drop_and_pickup_says_every_hold_day_is_paid():
    """Project Nightfall shoots under FWICE/CINTAA, which this pack models as having no provision.

    `None` is not "zero days" — "the mechanism does not exist here" and "the mechanism needs a very
    long gap" are different answers to a UPM asking whether a hold run can be released.
    """
    from scenepilot.seed.nightfall import build_project

    project = build_project()
    entry = next(e for e in build_dood_matrix(project) if e.total_hold_days > 0)

    assert entry.drop_pickup["minimum_days"] is None
    assert entry.drop_pickup["available"] is False
    assert entry.drop_pickup["releasable_days"] == 0
    assert "no drop-and-pickup provision" in entry.drop_pickup["note"]


def test_a_hold_run_shorter_than_the_minimum_cannot_be_released():
    project = _engagement_with_a_gap(hold_days=3)
    project.country_code = "US"  # DGA/SAG, which this pack models with a 10-day drop and pickup

    entry = build_dood_matrix(project)[0]

    assert entry.total_hold_days == 3
    assert entry.drop_pickup["minimum_days"] == 10
    assert entry.drop_pickup["longest_hold_run"] == 3
    assert entry.drop_pickup["available"] is False
    assert "only from 10 days" in entry.drop_pickup["note"]


def test_a_long_enough_hold_run_is_reported_as_releasable_and_priced():
    project = _engagement_with_a_gap(hold_days=12, day_rate=95000)
    project.country_code = "US"

    entry = build_dood_matrix(project)[0]

    assert entry.drop_pickup["available"] is True
    assert entry.drop_pickup["releasable_days"] == 12
    assert entry.drop_pickup["saving_inr"] == 12 * 95000
    # Advisory only: the matrix still charges the holds, because releasing is a producer's decision.
    assert entry.estimated_hold_cost_inr == 12 * 95000


def test_a_performer_with_no_holds_has_nothing_to_release():
    from scenepilot.seed.nightfall import build_project

    entry = next(e for e in build_dood_matrix(build_project()) if e.total_hold_days == 0)
    assert entry.drop_pickup["available"] is None
    assert "nothing to release" in entry.drop_pickup["note"]


# --------------------------------------------------------------------------- #
# Totals, and the casting gap that used to be silently counted as work
# --------------------------------------------------------------------------- #


def test_the_totals_row_reports_the_ratio_the_document_exists_for():
    from scenepilot.ingestion.dood import dood_totals
    from scenepilot.seed.nightfall import build_project

    project = build_project()
    entries = build_dood_matrix(project)
    totals = dood_totals(project, entries)

    assert totals["work_days"] == sum(e.total_work_days for e in entries)
    assert totals["hold_days"] == sum(e.total_hold_days for e in entries)
    assert totals["engaged_days"] == totals["work_days"] + totals["hold_days"]
    assert totals["hold_cost_inr"] == sum(e.estimated_hold_cost_inr or 0 for e in entries)
    assert totals["labor_pack"] == "FWICE / CINTAA Standard (India)"
    assert totals["unpriced_performers"] == []


def test_a_performer_with_no_rate_is_named_beside_the_total_so_it_reads_as_a_floor():
    from scenepilot.ingestion.dood import dood_totals

    project = _engagement_with_a_gap(hold_days=2)
    project.resource("c1").day_rate_inr = 0
    entries = build_dood_matrix(project)

    totals = dood_totals(project, entries)
    assert totals["hold_days"] == 2
    assert totals["hold_cost_inr"] is None
    assert totals["unpriced_performers"] == ["Lead Actor"]


def test_a_character_the_breakdown_found_but_nobody_is_cast_for_is_a_casting_gap_not_a_work_day():
    """The branch this replaces compared `AARAV` to `Aarav Mehta (Rider / lead)` and never fired.

    It should not have: a work day asserted from a language model's read of a draft is a day on a
    UPM's budget that nobody cast and nobody scheduled.
    """
    from scenepilot.domain.breakdown_models import BreakdownElement, ParsedSceneData
    from scenepilot.ingestion.dood import unlinked_characters

    project = _engagement_with_a_gap(hold_days=1)
    project.parsed_screenplay_scenes = [
        ParsedSceneData(
            scene_number="1", heading="INT. ROOM - DAY", int_ext="INT", time_of_day="DAY", setting="ROOM",
            page_start=1, page_end=1, eighths=8, action_text="", raw_text="",
            elements=[
                BreakdownElement(id="e1", category="CAST", name="LEAD", description="", count=1),
                BreakdownElement(id="e2", category="CAST", name="INSPECTOR KHAN", description="", count=1),
            ],
        )
    ]

    # "LEAD" tokenises onto the attached performer's own name; "INSPECTOR KHAN" matches nobody.
    gaps = unlinked_characters(project)
    assert [g["character"] for g in gaps] == ["INSPECTOR KHAN"]
    assert gaps[0]["scenes"] == ["1"] and gaps[0]["scheduled"] is True

    # And the matrix is unchanged by any of it: only `cast_ids` ever adds a day.
    assert build_dood_matrix(project)[0].total_work_days == 2
