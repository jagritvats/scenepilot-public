"""ChangeSet generation, application, audit trail and derived coordination actions."""

from scenepilot.domain.enums import CoordinationKind, ScheduleItemStatus, ShootDayStatus
from scenepilot.seed.nightfall import DAY4_ID, build_project, make_fixture_disruption
from scenepilot.services.changeset import apply_changeset, build_changeset
from scenepilot.services.coordination import derive_actions
from scenepilot.services.impact import analyze_impact
from scenepilot.services.recovery import generate_candidates


def _recovered():
    p = build_project()
    day = p.shoot_day(DAY4_ID)
    d = make_fixture_disruption(p.id, day.id, "rain_pm")
    impact = analyze_impact(p, day, d)
    best = generate_candidates(p, day, d, impact, 0.8)[0]
    cs = build_changeset(p, day, best, d, run_id="run_test")
    return p, day, d, best, cs


def test_changeset_records_before_after_and_reason():
    p, day, d, best, cs = _recovered()
    assert cs.changes
    for c in cs.changes:
        assert c.reason
        assert c.before != c.after
    sc42_start = next(c for c in cs.changes if c.label == "Scene 42" and c.field == "start")
    assert sc42_start.before == "16:30" and sc42_start.after == "17:30"
    assert "Rain" in sc42_start.reason
    deferred = next(c for c in cs.changes if c.label == "Scene 48" and c.field == "start")
    assert deferred.after is None
    crane = next(c for c in cs.changes if c.entity_type == "equipment_call" and c.entity_id == "eq_crane")
    assert crane.before == "15:00" and crane.after == "16:00"  # 90 min prep before 17:30


def test_apply_changeset_updates_state_and_is_idempotent():
    p, day, d, best, cs = _recovered()
    before_ids = list(p.changeset_ids)
    day_after = apply_changeset(p, cs, approved_by="producer")
    assert day_after.status == ShootDayStatus.RECOVERED
    by = {i.scene_id: i for i in day_after.items}
    assert "sc_48" not in by  # carried over
    assert by["sc_42"].start == "17:30" and by["sc_42"].status == ScheduleItemStatus.MOVED
    assert by["sc_27"].start == "13:00"
    assert cs.applied_at is not None and cs.approved_by == "producer"
    assert p.changeset_ids == before_ids + [cs.id]
    calls = {c.resource_id: c.call_time for c in day_after.equipment_calls}
    assert calls["eq_crane"] == "16:00" and calls["eq_drone"] == "16:30"
    # idempotent
    apply_changeset(p, cs)
    assert p.changeset_ids.count(cs.id) == 1


def test_coordination_actions_are_derived_from_changeset():
    p, day, d, best, cs = _recovered()
    day_after = apply_changeset(p, cs)
    actions = derive_actions(p, day_after, cs)
    kinds = {a.kind for a in actions}
    assert {
        CoordinationKind.SCHEDULE_REGENERATED,
        CoordinationKind.CALL_SHEET_REGENERATED,
        CoordinationKind.EQUIPMENT_CALL_UPDATED,
        CoordinationKind.TRANSPORT_UPDATED,
        CoordinationKind.MEAL_COUNT_UPDATED,
        CoordinationKind.LOCATION_CONTACT_UPDATE,
        CoordinationKind.SCENE_CARRY_OVER,
        CoordinationKind.CAST_NOTIFICATION,
    } <= kinds
    meals = next(a for a in actions if a.kind == CoordinationKind.MEAL_COUNT_UPDATED)
    assert meals.payload["dinner_delta"] == day_after.crew_size + 4  # wrap 20:00 → dinner for crew + 4 cast
    street = [a for a in actions if a.kind == CoordinationKind.LOCATION_CONTACT_UPDATE and "Bhuleshwar" in a.title]
    assert street and "will not shoot today" in street[0].details[0]
    grip = [a for a in actions if a.kind == CoordinationKind.CREW_NOTIFICATION and a.target == "Grip department"]
    assert grip and "15:00 → 16:00" in grip[0].details[0]
    assert all(a.channel == "simulated" for a in actions)


def test_call_sheet_is_derived_from_state_before_and_after():
    from scenepilot.services.callsheet import build_call_sheet

    p, day, d, best, cs = _recovered()
    before = build_call_sheet(p, day, None, d, label="before")
    assert [r["scene"] for r in before["schedule"]] == ["31", "19", "48", "42"]
    assert before["estimated_wrap"] == "19:00" and before["meals"]["dinner"]["time"] is None
    assert any("Rain expected" in a for a in before["advisories"])
    day_after = apply_changeset(p, cs)
    after = build_call_sheet(p, day_after, None, d, label="after")
    assert [r["scene"] for r in after["schedule"]] == ["31", "19", "27", "42"]
    assert after["estimated_wrap"] == "20:00" and after["meals"]["dinner"]["count"] == day.crew_size + len(after["cast"])
    assert any("cover set" in a for a in after["advisories"]) and any("Overtime" in a for a in after["advisories"])
    crane = next(e for e in after["equipment"] if "crane" in e["name"])
    assert crane["call"] == "16:00"
    vikram = next(c for c in after["cast"] if "Vikram" in c["name"])
    assert vikram["scenes"] == ["19"]  # Sc 48 carried over
