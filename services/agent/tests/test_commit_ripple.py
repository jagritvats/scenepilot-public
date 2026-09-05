"""Committing the multi-day plan — the decision the ripple panel could only ever propose.

The single-day story ends in a decision; the multi-day one ended in a report. What is pinned here is
mostly what a commit refuses to do: trust a proposed time, add a scene to a day that cannot hold it,
or book people onto a day nobody has cleared with them.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from scenepilot.seed.nightfall import DAY4_ID, build_project
from scenepilot.services.commit_ripple import (
    CommitRefused,
    commit_placement,
    materialize_pickup_day,
)
from scenepilot.services.multiday_solver import resolve_deferred_scenes_multiday


def _carry(project, scene_id: str, day_id: str = DAY4_ID):
    """Carry a scene off its day, the way an approved recovery does."""
    day = project.shoot_day(day_id)
    day.items = [i for i in day.items if i.scene_id != scene_id]
    return day


def _plan(project, scene_ids, day_id: str = DAY4_ID):
    return resolve_deferred_scenes_multiday(
        project, day_id, scene_ids, location_facts=[f for f in project.location_facts if f.binds]
    )


# --------------------------------------------------------------------------- #
# Placing a carried scene on a downstream day
# --------------------------------------------------------------------------- #


def test_a_scene_still_on_the_board_cannot_be_placed_again():
    p = build_project()
    with pytest.raises(CommitRefused, match="already scheduled"):
        commit_placement(p, "day_5", "sc_42")


def test_a_wrapped_day_will_not_take_a_carried_scene():
    p = build_project()
    _carry(p, "sc_48")
    with pytest.raises(CommitRefused, match="wrapped"):
        commit_placement(p, "day_3", "sc_48")


def test_a_placement_the_validator_rejects_is_refused_not_committed():
    """`_can_accommodate` answers only for the scene; the commit answers for the whole day."""
    p = build_project()
    _carry(p, "sc_48")
    day5 = p.shoot_day("day_5")
    before = [(i.scene_id, i.start) for i in day5.items]

    with pytest.raises(CommitRefused):
        commit_placement(p, "day_5", "sc_48")  # daylight exterior, night unit, no permit

    assert [(i.scene_id, i.start) for i in day5.items] == before
    assert not p.changeset_ids, "a refused commit leaves no audit trail behind"


def test_a_feasible_placement_lands_with_its_own_changeset():
    """The rooftop scene carried off Day 4 fits back into its own golden-hour slot."""
    p = build_project()
    _carry(p, "sc_42")
    assert not any(i.scene_id == "sc_42" for d in p.shoot_days for i in d.items)

    result = commit_placement(p, DAY4_ID, "sc_42")

    placed = next(i for i in p.shoot_day(DAY4_ID).items if i.scene_id == "sc_42")
    assert placed.start == result["start"] and placed.location_id
    assert placed.note and "Carried" in placed.note
    assert result["changeset"].id in p.changeset_ids
    assert result["changeset"].shoot_day_id == DAY4_ID
    # The day is re-derived around the new item rather than left with stale calls.
    assert p.shoot_day(DAY4_ID).equipment_calls
    # And the whole day still validates — that is what the commit checked before writing.
    assert result["added_overtime_cost_inr"] == 0


def test_an_unknown_day_or_scene_is_refused():
    p = build_project()
    with pytest.raises(CommitRefused, match="not on this production"):
        commit_placement(p, "day_nope", "sc_48")
    with pytest.raises(CommitRefused, match="not on this production"):
        commit_placement(p, "day_5", "sc_nope")


# --------------------------------------------------------------------------- #
# Materializing the pickup day
# --------------------------------------------------------------------------- #


def test_the_hero_carry_has_no_downstream_home_and_builds_a_pickup_day():
    p = build_project()
    _carry(p, "sc_48")
    plan = _plan(p, ["sc_48"])
    assert plan.placements == [], "no existing day can legally take the market street scene"
    assert plan.synthesized_pickup_day is not None


def test_materializing_puts_a_real_day_on_the_schedule():
    p = build_project()
    _carry(p, "sc_48")
    plan = _plan(p, ["sc_48"])
    before = len(p.shoot_days)

    result = materialize_pickup_day(p, plan.synthesized_pickup_day)
    day = result["day"]

    assert len(p.shoot_days) == before + 1
    assert p.shoot_day(day.id).day_number == day.day_number
    assert [d.day_number for d in p.shoot_days] == sorted(d.day_number for d in p.shoot_days)
    assert all(i.location_id for i in day.items), "a scene with no location books no transport and no permit"
    assert result["changeset"].id in p.changeset_ids


def test_a_committed_pickup_day_is_honest_about_nobody_being_booked_on_it():
    """The trap: minting availability so the day validates would assert a performer had agreed to work."""
    p = build_project()
    _carry(p, "sc_48")
    result = materialize_pickup_day(p, _plan(p, ["sc_48"]).synthesized_pickup_day)

    clearance = result["pending_clearance"]
    assert clearance, "the day is not cleared with anyone yet, and says so"
    assert {c["type"] for c in clearance} <= {"CAST", "LOCATION", "EQUIPMENT"}
    assert "nobody is booked onto it yet" in result["clearance_note"]
    # And the availability itself was not fabricated.
    for entry in clearance:
        resource = p.resource(entry["resource_id"])
        assert all(a.shoot_day_id != result["day"].id for a in resource.availability)


def test_only_a_resource_booked_elsewhere_needs_clearing():
    """A resource with no availability rows at all is unconstrained, not unbooked."""
    p = build_project()
    _carry(p, "sc_48")
    result = materialize_pickup_day(p, _plan(p, ["sc_48"]).synthesized_pickup_day)
    for entry in result["pending_clearance"]:
        assert p.resource(entry["resource_id"]).availability


def test_a_pickup_day_is_committed_once():
    p = build_project()
    _carry(p, "sc_48")
    pickup = _plan(p, ["sc_48"]).synthesized_pickup_day
    materialize_pickup_day(p, pickup)
    with pytest.raises(CommitRefused, match="already been committed"):
        materialize_pickup_day(p, pickup)


def test_two_shoot_days_may_not_share_one_date():
    """`next_day_call` and the "available on other days" test both compare dates, not day numbers."""
    p = build_project()
    _carry(p, "sc_48")
    pickup = _plan(p, ["sc_48"]).synthesized_pickup_day
    pickup.date = p.shoot_day("day_6").date
    with pytest.raises(CommitRefused, match="date of its own"):
        materialize_pickup_day(p, pickup)


def test_the_new_day_survives_the_seed_re_anchoring_the_week():
    """The seed slides every day on each read; a committed pickup day must move with them."""
    from scenepilot.seed.nightfall import reanchor_shoot_days

    p = build_project()
    _carry(p, "sc_48")
    day = materialize_pickup_day(p, _plan(p, ["sc_48"]).synthesized_pickup_day)["day"]
    gap_before = (date.fromisoformat(day.date) - date.fromisoformat(p.shoot_day("day_6").date)).days

    reanchor_shoot_days(p)

    after = p.shoot_day(day.id)
    gap_after = (date.fromisoformat(after.date) - date.fromisoformat(p.shoot_day("day_6").date)).days
    assert gap_after == gap_before, "the pickup day keeps its place in the week"


# --------------------------------------------------------------------------- #
# The endpoints
# --------------------------------------------------------------------------- #


def test_the_commit_endpoints(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    monkeypatch.setattr(app_module, "repo", repo)

    with TestClient(app_module.app) as c:
        project = repo.get_project("proj_nightfall")
        _carry(project, "sc_48")
        repo.save_project(project)

        refused = c.post(
            "/api/projects/proj_nightfall/shoot-days/day_5/commit-placement",
            json={"scene_id": "sc_48"},
        )
        assert refused.status_code == 409 and "Day 5" in refused.json()["detail"]

        committed = c.post(
            "/api/projects/proj_nightfall/shoot-days/day_4/commit-pickup-day",
            json={"deferred_scene_ids": ["sc_48"]},
        )
        assert committed.status_code == 200, committed.text
        body = committed.json()
        assert body["day"]["day_number"] == 7 and body["pending_clearance"]
        assert body["clearance_note"]

        # It is on the schedule now, and the audit trail records who put it there.
        days = c.get("/api/projects/proj_nightfall").json()["project"]["shoot_days"]
        assert any(d["day_number"] == 7 for d in days)
        log = c.get("/api/projects/proj_nightfall/activity").json()["events"]
        assert any(e["kind"] == "approval" and "pickup unit" in e["message"] for e in log)


# --------------------------------------------------------------------------- #
# The same scene cannot end up on two days
# --------------------------------------------------------------------------- #


def test_a_pickup_day_will_not_be_committed_while_its_scene_is_still_booked():
    """`commit_placement` asked this and `materialize_pickup_day` did not.

    The pickup day exists to catch a scene that came *off* a day. Committing one while the recovery
    is still awaiting approval — the scene still sitting on Day 4, because the producer has not
    approved the deferral yet — put that scene on the new day as well, and the schedule then held it
    twice, on two days, both looking legitimate. Both routes are advertised buttons, so this is two
    clicks in the order a producer would naturally make them.
    """
    p = build_project()
    plan = _plan(p, ["sc_48"])  # planned *without* carrying it off Day 4 first
    pickup = plan.synthesized_pickup_day
    assert pickup is not None, "the hero carry has no downstream home, so a pickup day is synthesized"

    with pytest.raises(CommitRefused, match="still scheduled on another day"):
        materialize_pickup_day(p, pickup)

    # Released first, the same commit is allowed — the guard blocks a double booking, not the feature.
    _carry(p, "sc_48")
    materialize_pickup_day(p, pickup)
    booked = [(d.id, i.scene_id) for d in p.shoot_days for i in d.items if i.scene_id == "sc_48"]
    assert len(booked) == 1, f"sc_48 is on more than one day: {booked}"
