"""Deterministic scheduling / constraint tests."""

from scenepilot.domain.enums import ConstraintKind
from scenepilot.domain.models import Availability, ScheduleItem
from scenepilot.seed.nightfall import DAY4_ID, build_project, make_fixture_disruption
from scenepilot.services.schedule import (
    GOLDEN_HOUR_MIN_OVERLAP_SOFT,
    ValidationContext,
    is_available,
    lighting_check,
    overtime_minutes,
    pack_day,
    validate_schedule,
)
from scenepilot.services.timeutil import overlap_minutes, to_hhmm, to_minutes


def _ctx(disruption=None, deferred=None):
    p = build_project()
    day = p.shoot_day(DAY4_ID)
    return p, day, ValidationContext(project=p, day=day, disruption=disruption, baseline_items=list(day.items), deferred_scene_ids=deferred or [])


def test_timeutil_roundtrip_and_overlap():
    assert to_minutes("07:30") == 450
    assert to_hhmm(450) == "07:30"
    assert to_hhmm(25 * 60 + 5) == "25:05"  # night shoots may pass midnight
    assert overlap_minutes(600, 720, 660, 800) == 60
    assert overlap_minutes(600, 660, 660, 800) == 0


def test_baseline_day_has_no_hard_violations():
    p, day, ctx = _ctx()
    violations = validate_schedule(ctx, day.items)
    assert not [v for v in violations if v.hard], [v.message for v in violations if v.hard]
    assert overtime_minutes(day, day.items) == 0


def test_cast_availability_is_checked_deterministically():
    p, day, ctx = _ctx()
    vikram = p.resource("cast_vikram")
    assert is_available(vikram, day, to_minutes("09:00"), to_minutes("11:00"))
    assert not is_available(vikram, day, to_minutes("08:00"), to_minutes("10:00"))
    items = [ScheduleItem(id="x", scene_id="sc_48", start="13:30", end="16:00", location_id="loc_street")]
    vikram.availability = [Availability(shoot_day_id=DAY4_ID, start="16:30", end="20:00")]
    v = validate_schedule(ctx, items)
    assert any(x.kind == ConstraintKind.CAST_UNAVAILABLE and x.hard for x in v)


def test_location_permit_window_is_a_hard_constraint():
    p, day, ctx = _ctx()
    items = [ScheduleItem(id="x", scene_id="sc_48", start="10:00", end="12:30", location_id="loc_street")]
    v = validate_schedule(ctx, items)
    kinds = {x.kind for x in v if x.hard}
    assert ConstraintKind.LOCATION_UNAVAILABLE in kinds


def test_overlap_and_travel_are_detected():
    p, day, ctx = _ctx()
    a = ScheduleItem(id="a", scene_id="sc_31", start="07:00", end="09:30", location_id="loc_alley")
    b = ScheduleItem(id="b", scene_id="sc_19", start="09:00", end="11:30", location_id="loc_apartment")
    v = validate_schedule(ctx, [a, b])
    assert any(x.kind == ConstraintKind.ITEM_OVERLAP for x in v)
    b2 = ScheduleItem(id="b", scene_id="sc_19", start="09:40", end="12:10", location_id="loc_apartment")  # 10 min gap, needs 30
    v = validate_schedule(ctx, [a, b2])
    assert any(x.kind == ConstraintKind.TRAVEL_OVERLAP for x in v)


def test_sunset_scene_lighting_rules():
    """Against the day's real golden hour — about 43 minutes at 19°N, not a 90-minute default."""
    p, day, _ = _ctx()
    sc42 = p.scene("sc_42")
    gs, ge = to_minutes(day.golden_hour_dusk[0]), to_minutes(day.golden_hour_dusk[1])

    assert lighting_check(day, sc42, gs, gs + sc42.estimated_minutes) is None  # catches the whole window
    soft = lighting_check(day, sc42, to_minutes("16:30"), to_minutes("19:00"))  # the seeded slot gives up the tail
    assert soft is not None and not soft.hard and soft.kind == ConstraintKind.LIGHTING_COMPROMISE
    hard = lighting_check(day, sc42, to_minutes("09:00"), to_minutes("11:30"))
    assert hard is not None and hard.hard and hard.kind == ConstraintKind.TIME_OF_DAY_INCOMPATIBLE


def test_a_scene_is_never_asked_for_more_golden_hour_than_the_day_has():
    """Mumbai's dusk window is ~43 min, so an uncapped 60-minute soft bar could never be met."""
    p, day, _ = _ctx()
    sc42 = p.scene("sc_42")
    gs, ge = to_minutes(day.golden_hour_dusk[0]), to_minutes(day.golden_hour_dusk[1])
    assert ge - gs < GOLDEN_HOUR_MIN_OVERLAP_SOFT

    soft = lighting_check(day, sc42, to_minutes("16:30"), to_minutes("19:00"))
    assert f"instead of {ge - gs}" in soft.message
    assert lighting_check(day, sc42, gs - 60, ge + 60) is None  # every available minute is enough


def test_day_scene_after_dark_is_hard():
    p, day, _ = _ctx()
    sc48 = p.scene("sc_48")
    hard = lighting_check(day, sc48, to_minutes("18:00"), to_minutes("20:30"))
    assert hard is not None and hard.hard


def test_pack_day_pushes_exposed_scene_past_disruption_window():
    p, day, _ = _ctx()
    d = make_fixture_disruption(p.id, day.id, "rain_pm")  # 13:00–17:00 + 30 min dry-out
    items = pack_day(p, day, ["sc_31", "sc_19", "sc_27", "sc_42"], d)
    by = {i.scene_id: i for i in items}
    assert by["sc_27"].start == "13:00"  # interior may run in the rain
    assert by["sc_42"].start == "17:30" and by["sc_42"].end == "20:00"  # exterior pushed past window + dry-out
    ctx = ValidationContext(project=p, day=day, disruption=d, baseline_items=list(day.items), deferred_scene_ids=["sc_48"])
    v = validate_schedule(ctx, items)
    assert not [x for x in v if x.hard]
    ot = [x for x in v if x.kind == ConstraintKind.OVERTIME]
    assert ot and ot[0].minutes == 60 and ot[0].cost_inr == 7500


def test_exposure_is_hard_for_exterior_during_window():
    p, day, _ = _ctx()
    d = make_fixture_disruption(p.id, day.id, "rain_pm")
    ctx = ValidationContext(project=p, day=day, disruption=d, baseline_items=list(day.items))
    v = validate_schedule(ctx, day.items)
    exposed = [x for x in v if x.kind == ConstraintKind.DISRUPTION_EXPOSURE]
    assert {x.scene_id for x in exposed} == {"sc_48", "sc_42"}
    assert all(x.hard for x in exposed)


def test_resource_disruption_uses_affected_resources():
    p, day, _ = _ctx()
    d = make_fixture_disruption(p.id, day.id, "crane_failure")  # crane out until 16:00
    ctx = ValidationContext(project=p, day=day, disruption=d, baseline_items=list(day.items))
    v = validate_schedule(ctx, day.items)
    # Sc 42 at 16:30 starts after the crane is back → no exposure
    assert not [x for x in v if x.hard]
    early = [ScheduleItem(id="it_42", scene_id="sc_42", start="15:00", end="17:30", location_id="loc_rooftop")]
    v2 = validate_schedule(ctx, early)
    assert any(x.kind == ConstraintKind.DISRUPTION_EXPOSURE for x in v2)


def test_lunch_break_soft_constraint():
    p, day, ctx = _ctx()
    # baseline has a 12:30-13:30 gap -> no meal-break violation
    assert not [x for x in validate_schedule(ctx, day.items) if x.kind == ConstraintKind.MEAL_BREAK]
    # back-to-back interiors through the lunch window -> soft violation, never hard
    items = [
        ScheduleItem(id="a", scene_id="sc_19", start="09:30", end="12:00", location_id="loc_apartment"),
        ScheduleItem(id="b", scene_id="sc_27", start="12:10", end="13:25", location_id="loc_apartment"),
        ScheduleItem(id="c", scene_id="sc_19", start="13:35", end="16:05", location_id="loc_apartment"),
    ]
    v = [x for x in validate_schedule(ctx, items) if x.kind == ConstraintKind.MEAL_BREAK]
    assert v and not v[0].hard and v[0].cost_inr > 0


def test_authority_heuristics():
    from scenepilot.domain.enums import Authority
    from scenepilot.services.evidence import authority_for

    assert authority_for("https://mumbairain.tropmet.res.in/") == Authority.OFFICIAL  # IITM Pune (govt research institute)
    assert authority_for("https://mausam.imd.gov.in/mumbai/") == Authority.OFFICIAL
    assert authority_for("https://timesofindia.indiatimes.com/x") == Authority.NEWS
    assert authority_for("https://www.reddit.com/r/drones") == Authority.COMMUNITY


def test_turnaround_rest_before_next_day_call():
    from scenepilot.services.recovery import next_day_call

    p, day, ctx = _ctx()
    nd = next_day_call(p, day)
    assert nd == 24 * 60 + to_minutes(p.shoot_day("day_5").unit_call)  # Day 5 is the next date
    ctx.next_day_call = nd
    assert not [x for x in validate_schedule(ctx, day.items) if x.kind == ConstraintKind.TURNAROUND]  # 18:00 night call: no pressure

    ctx.next_day_call = 24 * 60 + to_minutes("06:30")  # a main unit tomorrow instead of Day 5's night unit
    late = [ScheduleItem(id="it_42", scene_id="sc_42", start="18:00", end="21:00", location_id="loc_rooftop")]
    v = [x for x in validate_schedule(ctx, late) if x.kind == ConstraintKind.TURNAROUND]
    # 21:00 → 06:30 = 9h30, under the 10 h FWICE/CINTAA norm. The message names the pack it applied,
    # so a reader can check it against the rule the day page prints.
    assert v and not v[0].hard and "9h30" in v[0].message and "06:30" in v[0].message
    assert "10h union norm" in v[0].message and "FWICE / CINTAA" in v[0].message
    ok = [ScheduleItem(id="it_42", scene_id="sc_42", start="17:30", end="20:00", location_id="loc_rooftop")]
    assert not [x for x in validate_schedule(ctx, ok) if x.kind == ConstraintKind.TURNAROUND]


def test_the_pack_the_ui_names_is_the_pack_a_recovery_option_is_validated_under():
    """The golden-hour bug's shape, in the labor rules: two validators, two answers.

    `/labor-rules` printed the DGA/SAG pack's 12 h turnaround and ±15 min lunch window on the day
    page while every option a producer could approve was generated by a `ValidationContext` built
    with no pack at all, and scored against a loose 10 h / ±90 min fallback. There is now one
    resolution point, and this pins the two ends of it together.
    """
    from scenepilot.api.app import app as api_app
    from scenepilot.seed.nightfall import PROJECT_ID
    from scenepilot.services.labor_rules import active_pack
    from fastapi.testclient import TestClient

    p, day, _ = _ctx()
    named = TestClient(api_app).get(f"/api/projects/{PROJECT_ID}/labor-rules").json()
    enforced = active_pack(p)
    assert named["active_preset"] == enforced.preset.value
    card = named["presets"][named["active_preset"]]

    # 20:00 wrap against an 07:00 call is 11 h of rest: a breach under a 12 h pack, clean under 10 h.
    ctx = ValidationContext(project=p, day=day, next_day_call=24 * 60 + to_minutes("07:00"))
    items = [ScheduleItem(id="it_42", scene_id="sc_42", start="17:30", end="20:00", location_id="loc_rooftop")]
    breached = [x for x in validate_schedule(ctx, items) if x.kind == ConstraintKind.TURNAROUND]
    assert bool(breached) is (11 * 60 < card["minimum_turnaround_hours"] * 60)
    assert not breached  # and under FWICE/CINTAA specifically, 11 h clears the 10 h norm

    # the same equality for the lunch window the card advertises
    assert card["lunch_window_slack_minutes"] == enforced.lunch_window_slack_minutes
    assert card["minimum_turnaround_hours"] == enforced.minimum_turnaround_hours


def test_a_cross_unit_clash_names_both_units():
    """Two units booking one performer at one hour is hard — and the message has to say which two."""
    from scenepilot.domain.enums import ConstraintKind
    from scenepilot.domain.models import ScheduleItem
    from scenepilot.seed.nightfall import DAY4_ID, build_project
    from scenepilot.services.schedule import ValidationContext, validate_schedule

    p = build_project()
    day = p.shoot_day(DAY4_ID)
    a = next(i for i in day.items if p.scene(i.scene_id).cast_ids)
    shared = p.scene(a.scene_id).cast_ids[0]
    second = next(s for s in p.scenes if s.id != a.scene_id and shared in s.cast_ids)
    items = [
        a.model_copy(update={"unit": "MAIN"}),
        ScheduleItem(id="it_second_unit", scene_id=second.id, start=a.start, end=a.end, unit="SECOND"),
    ]

    clashes = [v for v in validate_schedule(ValidationContext(project=p, day=day), items) if v.kind == ConstraintKind.CAST_UNAVAILABLE and v.hard]
    assert clashes, "one performer booked by two units at the same hour is a hard conflict"
    assert "MAIN Unit" in clashes[0].message and "SECOND Unit" in clashes[0].message
