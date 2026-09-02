"""Tests for multi-day cascading ripple solver."""

from __future__ import annotations

import pytest
from scenepilot.domain.enums import ResourceType, TimeOfDay
from scenepilot.domain.models import Availability, Project, Resource, Scene, ScheduleItem, ShootDay
from scenepilot.services.multiday_solver import resolve_deferred_scenes_multiday


def _two_days() -> tuple[ShootDay, ShootDay]:
    source = ShootDay(id="d4", project_id="p1", day_number=4, date="2026-09-04", unit_call="06:30", standard_hours=12.0, hard_wrap="21:00", items=[])
    downstream = ShootDay(
        id="d5",
        project_id="p1",
        day_number=5,
        date="2026-09-05",
        unit_call="08:00",
        standard_hours=12.0,
        hard_wrap="21:00",
        overtime_rate_per_hour=7500,
        items=[ScheduleItem(id="i5_1", scene_id="sc_1", start="08:30", end="11:30")],
    )
    return source, downstream


def test_multiday_solver_places_deferred_scene_downstream():
    d4, d5 = _two_days()
    # A stage interior, which is what can legally take the midday slot the solver offers.
    sc42 = Scene(id="sc_42", number="42", heading="INT. STUDIO B - DAY", int_ext="INT", time_of_day=TimeOfDay.DAY, estimated_minutes=120)
    sc1 = Scene(id="sc_1", number="1", heading="INT. STUDIO - DAY", int_ext="INT", time_of_day=TimeOfDay.DAY, estimated_minutes=180)

    p = Project(id="p1", title="Test MultiDay", scenes=[sc42, sc1], shoot_days=[d4, d5])

    plan = resolve_deferred_scenes_multiday(p, source_day_id="d4", deferred_scene_ids=["sc_42"])

    assert len(plan.placements) == 1
    p42 = plan.placements[0]
    assert p42.scene_id == "sc_42"
    assert p42.shoot_day_id == "d5"
    assert p42.day_number == 5
    assert p42.scheduled_start == "12:00"  # 11:30 + 30 min buffer
    assert p42.scheduled_end == "14:00"
    assert plan.synthesized_pickup_day is None
    assert "absorbed into downstream shoot days" in plan.summary


def test_a_sunset_scene_is_not_placed_at_midday_because_a_slot_happened_to_be_free():
    """The defect this pins: the solver used to answer the feasibility question itself, and badly.

    It checked the hard wrap and nothing else, so a golden-hour exterior was placed wherever the
    previous scene finished — and the producer was shown it in green as FEASIBLE. The hero rescue's
    own deferred scene, `EXT. MARKET STREET — DAY`, was landing on the Day 5 night unit at 22:00.
    """
    d4, d5 = _two_days()
    sunset = Scene(id="sc_42", number="42", heading="EXT. ROOFTOP - SUNSET", int_ext="EXT", time_of_day=TimeOfDay.SUNSET, estimated_minutes=120)
    sc1 = Scene(id="sc_1", number="1", heading="INT. STUDIO - DAY", int_ext="INT", time_of_day=TimeOfDay.DAY, estimated_minutes=180)

    p = Project(id="p1", title="Test MultiDay", scenes=[sunset, sc1], shoot_days=[d4, d5])

    plan = resolve_deferred_scenes_multiday(p, source_day_id="d4", deferred_scene_ids=["sc_42"])

    # Refused, and the day it could not take it is the reason a pickup day gets synthesized at all.
    assert plan.placements == []
    assert plan.synthesized_pickup_day is not None


def test_a_downstream_day_cannot_take_a_scene_whose_location_is_not_booked_on_it():
    """`availability_windows` reads "no row for this day" as unavailable — the solver never asked."""
    d4, d5 = _two_days()
    street = Resource(
        id="loc_street",
        type=ResourceType.LOCATION,
        name="Market street",
        # Permitted on the source day only, which is the whole point of a permit window.
        availability=[Availability(shoot_day_id="d4", start="13:00", end="18:00")],
    )
    deferred = Scene(id="sc_48", number="48", heading="EXT. MARKET STREET - DAY", int_ext="EXT", time_of_day=TimeOfDay.DAY, location_id="loc_street", estimated_minutes=120)
    sc1 = Scene(id="sc_1", number="1", heading="INT. STUDIO - DAY", int_ext="INT", time_of_day=TimeOfDay.DAY, estimated_minutes=180)

    p = Project(id="p1", title="Test Permit", scenes=[deferred, sc1], resources=[street], shoot_days=[d4, d5])

    plan = resolve_deferred_scenes_multiday(p, source_day_id="d4", deferred_scene_ids=["sc_48"])

    assert plan.placements == []
    assert plan.synthesized_pickup_day is not None


def test_a_performer_with_no_booking_on_the_downstream_day_blocks_the_placement():
    """The old cast check only rejected a zero-length window, so it could not fail in practice."""
    d4, d5 = _two_days()
    performer = Resource(
        id="cast_1",
        type=ResourceType.CAST,
        name="Lead Actor",
        availability=[Availability(shoot_day_id="d4", start="06:00", end="21:00")],
    )
    deferred = Scene(id="sc_48", number="48", heading="INT. ROOM - DAY", int_ext="INT", time_of_day=TimeOfDay.DAY, cast_ids=["cast_1"], estimated_minutes=120)
    sc1 = Scene(id="sc_1", number="1", heading="INT. STUDIO - DAY", int_ext="INT", time_of_day=TimeOfDay.DAY, estimated_minutes=180)

    p = Project(id="p1", title="Test Cast", scenes=[deferred, sc1], resources=[performer], shoot_days=[d4, d5])

    plan = resolve_deferred_scenes_multiday(p, source_day_id="d4", deferred_scene_ids=["sc_48"])

    assert plan.placements == []


def test_the_hero_deferral_costs_a_pickup_day_because_no_night_unit_can_take_a_day_exterior():
    """End to end on the real seed: the honest answer to "where does Sc 48 go?" is "nowhere cheap".

    Days 5 and 6 are night units and Bhuleshwar is permitted 13:00–18:00 on Day 4 alone, so the
    deferred daylight exterior cannot legally land on either — and the ripple's real cost is a
    dedicated pickup day, not the ₹0 the unchecked solver used to report.
    """
    from scenepilot.seed.nightfall import DAY4_ID, build_project, make_fixture_disruption
    from scenepilot.services.impact import analyze_impact
    from scenepilot.services.recovery import generate_candidates

    project = build_project()
    day = project.shoot_day(DAY4_ID)
    disruption = make_fixture_disruption(project.id, DAY4_ID, "rain_pm")
    option = generate_candidates(project, day, disruption, analyze_impact(project, day, disruption))[0]
    assert option.deferred_scene_ids == ["sc_48"]

    plan = resolve_deferred_scenes_multiday(project, DAY4_ID, option.deferred_scene_ids, option.id)

    assert plan.placements == []
    assert plan.synthesized_pickup_day is not None
    assert plan.total_ripple_cost_inr == day.pickup_day_cost


def test_multiday_solver_synthesizes_pickup_day_when_saturated():
    # Day 4 (source day)
    d4 = ShootDay(id="d4", project_id="p1", day_number=4, date="2026-09-04", unit_call="06:30", standard_hours=12.0, hard_wrap="21:00", items=[])
    # Day 5 is already packed to the brim until 20:50 (hard wrap 21:00)
    d5 = ShootDay(
        id="d5",
        project_id="p1",
        day_number=5,
        date="2026-09-05",
        unit_call="07:00",
        standard_hours=12.0,
        hard_wrap="21:00",
        items=[
            ScheduleItem(id="i5_full", scene_id="sc_1", start="07:00", end="20:45"),
        ],
    )
    sc42 = Scene(id="sc_42", number="42", heading="EXT. ROOFTOP - SUNSET", int_ext="EXT", time_of_day=TimeOfDay.SUNSET, estimated_minutes=120)
    sc1 = Scene(id="sc_1", number="1", heading="INT. STUDIO - DAY", int_ext="INT", time_of_day=TimeOfDay.DAY, estimated_minutes=800)

    p = Project(id="p1", title="Test Saturated", scenes=[sc42, sc1], shoot_days=[d4, d5])

    plan = resolve_deferred_scenes_multiday(p, source_day_id="d4", deferred_scene_ids=["sc_42"])

    # Cannot fit into Day 5 -> Synthesizes Pickup Day 6
    assert len(plan.placements) == 0
    assert plan.synthesized_pickup_day is not None
    assert plan.synthesized_pickup_day.day_number == 6
    assert len(plan.synthesized_pickup_day.items) == 1
    assert plan.synthesized_pickup_day.items[0].scene_id == "sc_42"
    assert "synthesized dedicated Day 6 Pickup Unit" in plan.summary
    assert plan.total_ripple_cost_inr > 0
