"""The seed has to describe a production that actually holds together.

Two different failures live here, and only one of them is visible from a page.

The first is *referential*: a scene that names a location nobody declared, a schedule item on a day
whose cast has no booking window, an equipment call for a vendor the day never books. None of that
raises — the engine reads a missing availability row as "unavailable" and a missing location as
"—" — so it surfaces as a page that reads broken, or, worse, as the deterministic validator
rejecting a day's own seeded schedule and offering to spend ₹60,000 fixing it.

The second is that Day 4 is load-bearing. Its five options, their costs and the two rejections are
quoted verbatim in `docs/TRAILER_SCRIPT.md` and are what a judge is walked through. Enriching a
neighbouring day must not move a single number on it, and the cheapest way to keep that true is to
pin the numbers here, where a seed edit that shifts them fails in six seconds rather than on stage.
"""

from __future__ import annotations

import pytest

from scenepilot.domain.enums import ConstraintKind, ResourceType, ScheduleItemStatus, ShootDayStatus
from scenepilot.seed.nightfall import DAY4_ID, LOCATION_COORDINATES, build_project, make_fixture_disruption
from scenepilot.services.changeset import apply_changeset, build_changeset, derive_equipment_calls
from scenepilot.services.coordination import derive_actions
from scenepilot.services.impact import analyze_impact
from scenepilot.services.recovery import generate_candidates
from scenepilot.services.schedule import ValidationContext, available_on_other_days, validate_schedule
from scenepilot.services.timeutil import to_minutes


@pytest.fixture()
def project():
    return build_project()


def _days(project):
    return sorted(project.shoot_days, key=lambda d: d.day_number)


# --------------------------------------------------------------------------- #
# Referential integrity: nothing points at something that is not there
# --------------------------------------------------------------------------- #


def test_every_id_a_scene_names_is_a_resource_that_exists(project):
    ids = {r.id for r in project.resources}
    for scene in project.scenes:
        missing = [x for x in [scene.location_id, *scene.cast_ids, *scene.equipment_ids] if x and x not in ids]
        assert not missing, f"Scene {scene.number} names resources that do not exist: {missing}"
        for req in scene.requirements:
            assert not [x for x in req.resource_ids if x not in ids], f"{req.id} names a resource that does not exist"


def test_every_scheduled_scene_has_a_set_to_shoot_it_on(project):
    """A schedule item with no location prints "—" on the call sheet and draws no map at all."""
    for day in project.shoot_days:
        for item in day.items:
            located = item.location_id or project.scene(item.scene_id).location_id
            assert located, f"{day.id}/{item.id} (Sc {project.scene(item.scene_id).number}) has no location"
            assert located in {r.id for r in project.resources}


def test_every_seeded_location_is_in_the_coordinate_table(project):
    """`migrate.py` backfills coordinates from that table; a set missing from it never reaches the map."""
    locations = {r.id for r in project.resources if r.type == ResourceType.LOCATION}
    assert locations == set(LOCATION_COORDINATES)
    assert all(project.resource(rid).has_coordinates for rid in locations)


def test_the_equipment_calls_a_day_states_match_what_the_call_sheet_recomputes(project):
    """Two numbers for one vendor is how a day page and its call sheet come to disagree on screen."""
    for day in project.shoot_days:
        if not day.equipment_calls:
            continue
        stated = {c.resource_id: c.call_time for c in day.equipment_calls}
        derived = {c.resource_id: c.call_time for c in derive_equipment_calls(project, day, day.items)}
        assert stated == derived, f"{day.id} states equipment calls the deriver does not agree with"


# --------------------------------------------------------------------------- #
# Every seeded day is a day the engine would accept
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("day_id", ["day_3", DAY4_ID, "day_5", "day_6"])
def test_the_engine_accepts_every_days_own_seeded_schedule(project, day_id):
    """No hard violation on a board the production has already committed to.

    Day 3 and Day 5 used to fail this: every `Availability` row named Day 4 or Day 6, and
    `availability_windows` reads "no row for this day" as unavailable — so the validator rejected
    the dawn splinter's own drone and the night unit's own lead and stage, and each day's only
    feasible "recovery" was to shoot nothing.
    """
    day = project.shoot_day(day_id)
    hard = [v for v in validate_schedule(ValidationContext(project=project, day=day), day.items) if v.hard]
    assert hard == [], f"{day_id} rejects its own schedule: {[v.message for v in hard]}"


@pytest.mark.parametrize("day_id", ["day_3", DAY4_ID, "day_5", "day_6"])
def test_every_day_can_say_what_is_constrained_on_it(project, day_id):
    """The day page lists resources holding a window for the day; with none, the panel is blank."""
    day = project.shoot_day(day_id)
    rows = [r for r in project.resources if any(a.shoot_day_id == day.id for a in r.availability)]
    assert rows, f"{day_id} has no resource with a booking window, so its constraints panel is empty"
    needed = {i.location_id for i in day.items if i.location_id}
    assert needed <= {r.id for r in rows}, f"{day_id} schedules a set it holds no window for"


# --------------------------------------------------------------------------- #
# The wrapped day
# --------------------------------------------------------------------------- #


def test_the_wrapped_day_reads_as_shot_rather_than_pending(project):
    day = project.shoot_day("day_3")
    assert day.status == ShootDayStatus.WRAPPED
    assert [i.status for i in day.items] == [ScheduleItemStatus.COMPLETED]
    assert day.items[0].unit == "SPLINTER"  # not the main unit every other day carries


def test_the_scene_that_wrapped_on_day_3_carries_what_it_demonstrably_had(project):
    """A plate that was shot had a set, a charter and a page count. It did not have cast."""
    sc12 = project.scene("sc_12")
    assert sc12.location_id == "loc_sea_link" and sc12.eighths
    assert sc12.equipment_ids == ["eq_drone"] and sc12.requirements
    # Deliberately empty: nobody was called for a second-unit aerial plate, so the DOOD Day-3 column
    # is honestly blank. Filling it would mean inventing a performer onto a day they never worked.
    assert sc12.cast_ids == []


# --------------------------------------------------------------------------- #
# Pagination: the page count and the minutes are printed side by side, so they must agree
# --------------------------------------------------------------------------- #

# How long a unit may plausibly spend covering one eighth of a page. The floor is a shade under a
# page an hour (7.5 min/eighth), which is a brisk day; the ceiling is a page per four and a half
# hours, which is what a stunt sequence or a dawn aerial plate with one usable pass a morning
# actually costs. Outside that band the two numbers on the strip are describing different films.
MIN_PER_EIGHTH_FLOOR = 6.0
MIN_PER_EIGHTH_CEILING = 35.0


def test_every_scene_carries_a_page_count(project):
    """A scene with no `eighths` prints "—" on the board and drops out of the day's page total."""
    assert [s.number for s in project.scenes if not s.eighths] == []


def test_no_scene_claims_a_page_count_its_own_minutes_cannot_support(project):
    """The regression: five scenes at 1/8 of a page and 150 scheduled minutes each.

    The Fountain draft is a five-scene excerpt, so the parser paginated six lines and returned 1 —
    correctly, for the text it was given. Adopting that as production state made the hero day read
    "4 sc · 4/8 pgs" in a 12.5 h day, which is the one number on the board a line producer checks
    against the clock. The page count is stated in the seed now, and this is the guard on it.
    """
    for scene in project.scenes:
        rate = scene.estimated_minutes / scene.eighths
        assert MIN_PER_EIGHTH_FLOOR <= rate <= MIN_PER_EIGHTH_CEILING, (
            f"Sc {scene.number}: {scene.eighths}/8 of a page in {scene.estimated_minutes} min is "
            f"{rate:.0f} min per eighth, outside the plausible "
            f"{MIN_PER_EIGHTH_FLOOR:.0f}–{MIN_PER_EIGHTH_CEILING:.0f}"
        )


def test_every_days_page_total_matches_the_minutes_printed_beside_it(project):
    """The board prints "N sc · P pgs" against the day's own hours; the header must hold up too."""
    for day in project.shoot_days:
        scenes = [project.scene(i.scene_id) for i in day.items]
        eighths = sum(s.eighths for s in scenes)
        minutes = sum(to_minutes(i.end) - to_minutes(i.start) for i in day.items)
        rate = minutes / eighths
        assert MIN_PER_EIGHTH_FLOOR <= rate <= MIN_PER_EIGHTH_CEILING, (
            f"Day {day.day_number} schedules {minutes} min against {eighths}/8 of a page "
            f"({rate:.0f} min per eighth)"
        )


def test_the_stage_interiors_state_what_they_need(project):
    """Sc 55 rendered as a labelled empty box: a requirements table with nothing in it."""
    assert len(project.scene("sc_55").requirements) >= 3
    assert all(s.requirements for s in project.scenes), "a scene with no requirements renders an empty table"


# --------------------------------------------------------------------------- #
# Carry-over only ever goes forwards
# --------------------------------------------------------------------------- #


def test_a_booking_that_has_already_been_used_is_not_somewhere_to_carry_a_scene(project):
    """The drone is chartered for Day 3's dawn plate and for Day 4. Neither helps Day 4 carry.

    Without this, adding the Day-3 charter would silently delete Scene 42's ₹35,000 re-rental from
    the hero day's Option C — a booking in the past reading as capacity in the future.
    """
    day4, drone = project.shoot_day(DAY4_ID), project.resource("eq_drone")
    assert [a.shoot_day_id for a in drone.availability] == ["day_3", DAY4_ID]
    assert available_on_other_days(drone, day4, project) is False
    # The bike is booked on Day 6, which is ahead of Day 4, so carrying it costs nothing extra.
    assert available_on_other_days(project.resource("eq_bike"), day4, project) is True
    # A resource with no window at all is unconstrained, on this day and every other.
    assert available_on_other_days(project.resource("eq_lighting"), day4, project) is True


# --------------------------------------------------------------------------- #
# Day 4 is load-bearing: the trailer quotes these numbers
# --------------------------------------------------------------------------- #


def _hero_options(project):
    day = project.shoot_day(DAY4_ID)
    d = make_fixture_disruption(project.id, day.id, "rain_pm")
    project.disruptions.append(d)
    impact = analyze_impact(project, day, d)
    return day, d, impact, generate_candidates(project, day, d, impact)


def test_the_hero_rain_day_still_offers_five_options_ranked_a_to_e(project):
    _, _, impact, options = _hero_options(project)
    assert sorted(impact.directly_affected_item_ids) == ["it_42", "it_48"]
    assert [o.label for o in options] == ["A", "B", "C", "D", "E"]
    assert [o.feasible for o in options] == [True, True, True, False, False]


def test_the_recommended_hero_option_still_costs_sixty_seven_thousand_five_hundred(project):
    """Option A: defer Sc 48, pull cover Sc 27, push the rooftop past the rain. 60 min OT, ₹67,500."""
    _, _, _, options = _hero_options(project)
    a = options[0]
    assert a.label == "A" and a.rank == 1 and a.feasible
    assert [i.scene_id for i in a.schedule] == ["sc_31", "sc_19", "sc_27", "sc_42"]
    assert a.deferred_scene_ids == ["sc_48"]
    rooftop = next(i for i in a.schedule if i.scene_id == "sc_42")
    assert (rooftop.start, rooftop.end) == ("17:30", "20:00")
    overtime = [v for v in a.violations if v.kind == ConstraintKind.OVERTIME]
    assert [(v.minutes, v.cost_inr) for v in overtime] == [(60, 7500)]
    assert sum(v.cost_inr for v in a.violations) == 67_500


def test_the_two_hero_rejections_still_name_the_rain_and_the_permit_window(project):
    _, _, _, options = _hero_options(project)
    d, e = options[3], options[4]
    assert {v.kind for v in d.violations if v.hard} == {ConstraintKind.DISRUPTION_EXPOSURE}
    assert "Scene 48 overlaps rain expected 13:00–17:00" in d.rejected_reason
    assert {v.kind for v in e.violations if v.hard} == {ConstraintKind.LOCATION_UNAVAILABLE}
    assert "Bhuleshwar not available for Scene 48 at 10:00–12:30 (window 13:00–18:00)" in e.rejected_reason


def test_carrying_the_hero_scene_still_prices_all_three_one_day_rentals(project):
    """Option C defers Sc 42 as well, and the drone, crane and fireworks are booked for Day 4 only."""
    _, _, _, options = _hero_options(project)
    c = options[2]
    assert set(c.deferred_scene_ids) == {"sc_48", "sc_42"}
    rerentals = {v.resource_id: v.cost_inr for v in c.violations if v.kind == ConstraintKind.EQUIPMENT_RERENTAL}
    assert rerentals == {"eq_drone": 35000, "eq_crane": 45000, "eq_fireworks": 25000}


def test_approving_the_hero_option_still_generates_twenty_one_coordination_actions(project):
    day, d, _, options = _hero_options(project)
    cs = build_changeset(project, day, options[0], d, run_id=None)
    day_after = apply_changeset(project, cs)
    actions = derive_actions(project, day_after, cs)
    assert len(cs.changes) == 12
    assert len(actions) == 21
    assert day_after.status == ShootDayStatus.RECOVERED
