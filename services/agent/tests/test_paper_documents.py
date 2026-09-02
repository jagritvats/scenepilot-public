"""The movement order and the sides packet — two documents that must not overstate what they hold.

Both are assembled from state that is deliberately incomplete: this production holds travel times for
some location pairs and not others, and its Studio holds a five-scene excerpt against a nine-scene
board. What is tested here is mostly the absence: no arrival invented for an untimed leg, no scene
silently dropped from a packet, and a conflict printed rather than smoothed.
"""

from fastapi.testclient import TestClient

from scenepilot.seed.nightfall import DAY4_ID, build_project
from scenepilot.seed.warm import warm_screenplay
from scenepilot.services.movement_order import build_movement_order
from scenepilot.services.sides import build_sides


# --------------------------------------------------------------------------- #
# Movement order
# --------------------------------------------------------------------------- #


def test_the_order_lists_every_company_move_in_order():
    p = build_project()
    order = build_movement_order(p, p.shoot_day(DAY4_ID))
    assert order["move_count"] == 3 and [leg["index"] for leg in order["legs"]] == [1, 2, 3]
    assert order["legs"][0]["from_name"].startswith("Service alley")
    assert order["legs"][-1]["to_name"].startswith("Rooftop A")


def test_arrival_is_departure_plus_the_productions_own_travel_time():
    p = build_project()
    leg = build_movement_order(p, p.shoot_day(DAY4_ID))["legs"][0]
    assert leg["departure"] == "09:30" and leg["travel_minutes"] == 30 and leg["arrival"] == "10:00"


def test_an_untimed_leg_gets_no_invented_arrival():
    """The scheduler's flat fallback places a scene; it is not a time to hand a driver."""
    p = build_project()
    p.travel_times = []  # the production has measured nothing
    order = build_movement_order(p, p.shoot_day(DAY4_ID))
    assert order["legs"], "the moves still exist"
    for leg in order["legs"]:
        assert leg["untimed"] is True and leg["travel_minutes"] is None and leg["arrival"] is None
    assert order["total_travel_minutes"] is None


def test_no_leg_departs_before_the_unit_wraps():
    """`derive_transport` works back from the arrival and then floors at the wrap.

    It used to emit only the first half of that, which put the cast van on the road at 09:15 for a
    unit shooting until 09:30 — and the seeded legs, written to match, said the same. Two parts of
    the engine disagreed about one move: `validate_schedule` passes a company move when the gap
    covers the travel, while the transport deriver wanted the travel *plus* fifteen minutes of load.
    """
    p = build_project()
    for leg in build_movement_order(p, p.shoot_day(DAY4_ID))["legs"]:
        assert leg["departure_before_wrap_minutes"] is None


def test_a_hand_entered_leg_that_leaves_too_early_is_still_reported():
    """The deriver cannot produce one any more; a hand-edited schedule still can."""
    p = build_project()
    day = p.shoot_day(DAY4_ID)
    day.transport[0].departure = "09:00"  # fifteen minutes before Sc 31 wraps
    leg = build_movement_order(p, day)["legs"][0]
    assert leg["departure_before_wrap_minutes"] == 30


def test_a_move_with_no_room_to_load_says_so():
    """Where the gap cannot spare the loading time, the sheet says the load is being squeezed."""
    p = build_project()
    legs = build_movement_order(p, p.shoot_day(DAY4_ID))["legs"]
    squeezed = [leg for leg in legs if leg["load_squeezed"]]
    assert squeezed, "Day 4 leaves the alley the minute it wraps"
    assert squeezed[0]["load_margin_minutes"] == 0


def test_a_day_on_one_location_says_there_is_nothing_to_order():
    p = build_project()
    order = build_movement_order(p, p.shoot_day("day_3"))
    assert order["legs"] == [] and order["move_count"] == 0
    assert "no company move" in order["note"]


def test_the_order_carries_the_provenance_of_every_number_it_prints():
    p = build_project()
    basis = build_movement_order(p, p.shoot_day(DAY4_ID))["basis"]
    assert "straight-line" in basis["distance"].lower() or "great-circle" in basis["distance"].lower()
    assert basis["travel_minutes"] and basis["coordinates"]


# --------------------------------------------------------------------------- #
# Sides
# --------------------------------------------------------------------------- #


def test_the_packet_holds_every_scheduled_scene_in_shooting_order():
    p = build_project()
    warm_screenplay(p)
    sides = build_sides(p, p.shoot_day(DAY4_ID))
    assert [s["scene_number"] for s in sides["scenes"]] == ["31", "19", "48", "42"]
    assert sides["complete"] is True and sides["scenes_with_text"] == 4


def test_a_scene_the_studio_does_not_hold_is_a_named_gap_not_a_missing_page():
    p = build_project()
    warm_screenplay(p)
    sides = build_sides(p, p.shoot_day("day_6"))
    assert sides["scene_count"] == 2 and sides["scenes_with_text"] == 0 and sides["complete"] is False
    for scene in sides["scenes"]:
        assert scene["has_text"] is False
        assert scene["gap_reason"] and scene["scene_number"] in scene["gap_reason"]
        assert scene["heading"] and scene["start"]  # still called, still on the sheet
    assert "0 of 2" in sides["coverage_note"]


def test_the_packet_states_its_own_coverage_so_it_cannot_pass_as_complete():
    p = build_project()
    warm_screenplay(p)
    assert build_sides(p, p.shoot_day(DAY4_ID))["coverage_note"] is None
    assert build_sides(p, p.shoot_day("day_5"))["coverage_note"]


def test_dialogue_and_action_come_through_for_a_scene_with_pages():
    p = build_project()
    warm_screenplay(p)
    hero = next(s for s in build_sides(p, p.shoot_day(DAY4_ID))["scenes"] if s["scene_number"] == "42")
    assert "motorcycle" in hero["action_text"].lower()
    assert hero["dialogue"] and hero["dialogue"][0]["character"] == "AARAV"


def test_both_documents_are_served_and_404_on_an_unknown_day(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    with TestClient(app_module.app) as c:
        order = c.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}/movement-order")
        assert order.status_code == 200 and order.json()["movement_order"]["move_count"] == 3

        sides = c.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}/sides")
        assert sides.status_code == 200 and sides.json()["sides"]["scene_count"] == 4

        assert c.get("/api/projects/proj_nightfall/shoot-days/day_nope/sides").status_code == 404
        assert c.get("/api/projects/proj_nightfall/shoot-days/day_nope/movement-order").status_code == 404
