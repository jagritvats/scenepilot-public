"""The one-liner: the whole shoot on one page, and what a change did to it.

The reason this document exists at all is that the entire before and after fits side by side, so
the tests that matter are about the diff being *attributable*: both versions are built over the same
days from the same project with exactly one day's items swapped, so a scene that appears in the move
list moved because of the recovery and for no other reason.

The rest pin the same withholding rule the call sheet follows — a page total is stated only when
every scene in scope carries a count, because a partial total read as a day's pages is a smaller
number than the day is actually shooting.
"""

from __future__ import annotations

import pytest

from scenepilot.seed.nightfall import DAY4_ID, build_project, make_fixture_disruption
from scenepilot.services.impact import analyze_impact
from scenepilot.services.oneliner import build_one_liner, one_liner_moves
from scenepilot.services.recovery import generate_candidates


@pytest.fixture()
def project():
    return build_project()


def _day(one_liner, number: int):
    return next(d for d in one_liner["days"] if d["day_number"] == number)


def test_every_scheduled_scene_gets_exactly_one_line(project):
    one_liner = build_one_liner(project)
    scheduled = sum(len(d.items) for d in project.shoot_days)
    assert one_liner["scene_count"] == scheduled
    assert sum(d["scene_count"] for d in one_liner["days"]) == scheduled


def test_days_are_in_shoot_order_and_scenes_in_slot_order(project):
    one_liner = build_one_liner(project)
    assert [d["day_number"] for d in one_liner["days"]] == [3, 4, 5, 6]
    for day in one_liner["days"]:
        starts = [row["start"] for row in day["scenes"]]
        assert starts == sorted(starts)


def test_the_hero_day_reads_four_scenes_and_four_and_seven_eighths_pages(project):
    day4 = _day(build_one_liner(project), 4)
    assert day4["scene_count"] == 4
    assert day4["total_label"] == "4 7/8"
    assert [r["scene"] for r in day4["scenes"]] == ["31", "19", "48", "42"]


def test_the_production_total_is_the_sum_of_the_days(project):
    one_liner = build_one_liner(project)
    assert one_liner["total_eighths"] == sum(d["total_eighths"] for d in one_liner["days"] if d["scene_count"])
    assert one_liner["total_label"] == "7 7/8"


def test_a_missing_page_count_withholds_the_day_and_the_production_total(project):
    """The same rule the call sheet follows: a partial total understates what is being shot."""
    project.scene("sc_48").eighths = None
    one_liner = build_one_liner(project)
    assert _day(one_liner, 4)["total_eighths"] is None
    assert one_liner["total_eighths"] is None
    assert "withheld" in one_liner["unpriced_reason"]
    # The days that are complete still total; only the one with the gap withholds.
    assert _day(one_liner, 5)["total_label"] == "6/8"


def test_a_day_working_more_than_one_set_reports_its_company_moves(project):
    one_liner = build_one_liner(project)
    # Day 4 works four sets, which is three moves; Day 5 is a single stage day.
    assert _day(one_liner, 4)["company_moves"] == 3
    assert _day(one_liner, 5)["company_moves"] == 0
    assert len(_day(one_liner, 4)["sets"]) == 4


def test_cast_are_listed_in_billing_order_with_their_numbers(project):
    rooftop = next(r for r in _day(build_one_liner(project), 4)["scenes"] if r["scene"] == "42")
    assert [c["cast_number"] for c in rooftop["cast"]] == [1, 4]


def test_an_override_builds_the_schedule_that_is_no_longer_committed(project):
    """This is what makes a before/after diff attributable: only one day's items differ."""
    day = project.shoot_day(DAY4_ID)
    baseline = [i.model_copy(deep=True) for i in day.items]
    day.items = [i for i in day.items if project.scene(i.scene_id).number != "48"]

    after = build_one_liner(project)
    before = build_one_liner(project, overrides={DAY4_ID: baseline})

    assert _day(after, 4)["scene_count"] == 3
    assert _day(before, 4)["scene_count"] == 4

    # Every other day's own content is identical by construction. `velocity` is deliberately excluded
    # and deliberately not expected to match: it compares a day against the *production's* average,
    # and taking a scene off Day 4 genuinely moves that average. A Day 5 that shoots exactly what it
    # always did really has changed its standing relative to the schedule around it.
    own = lambda d: {k: v for k, v in d.items() if k != "velocity"}  # noqa: E731
    assert [own(_day(after, n)) for n in (3, 5, 6)] == [own(_day(before, n)) for n in (3, 5, 6)]
    assert _day(after, 5)["velocity"]["average_eighths"] != _day(before, 5)["velocity"]["average_eighths"]


def test_the_hero_recovery_moves_exactly_the_three_scenes_it_says_it_does(project):
    """Sc 48 carried out of the day, Sc 27 pulled in as cover, Sc 42 pushed past the rain."""
    from scenepilot.services.changeset import apply_changeset, build_changeset

    day = project.shoot_day(DAY4_ID)
    baseline = [i.model_copy(deep=True) for i in day.items]
    disruption = make_fixture_disruption(project.id, DAY4_ID, "rain_pm")
    option = generate_candidates(project, day, disruption, analyze_impact(project, day, disruption))[0]
    changeset = build_changeset(project, day, option, disruption, run_id=None)
    apply_changeset(project, changeset)

    moves = one_liner_moves(build_one_liner(project, overrides={DAY4_ID: baseline}), build_one_liner(project))

    by_scene = {m["scene"]: m for m in moves}
    assert set(by_scene) == {"27", "42", "48"}
    # Carried out of the schedule entirely — it has no downstream day it can legally land on.
    assert by_scene["48"]["carried_out"] is True and by_scene["48"]["to_day"] is None
    # Pulled in as the cover set.
    assert by_scene["27"]["from_day"] is None and by_scene["27"]["to_day"] == 4
    # Pushed later on the same day, past the rain window.
    assert by_scene["42"]["from_day"] == by_scene["42"]["to_day"] == 4
    assert by_scene["42"]["from_slot"] != by_scene["42"]["to_slot"]


def test_two_identical_schedules_report_no_moves(project):
    one_liner = build_one_liner(project)
    assert one_liner_moves(one_liner, one_liner) == []


def test_each_row_names_the_unit_that_shoots_it(project):
    """The engine prices cross-unit contention; a sheet that never names a unit hides what it is about."""
    from scenepilot.services.oneliner import build_one_liner

    days = {d["day_number"]: d for d in build_one_liner(project)["days"]}
    splinter = next(r for r in days[3]["scenes"] if r["scene"] == "12")
    assert splinter["unit"] == "SPLINTER"  # the dawn aerial plate, seeded as its own unit
    assert all(r["unit"] == "MAIN" for r in days[4]["scenes"])
