"""A company move drawn from real geography, or not drawn at all.

The panel this feeds replaces one that invented its own street addresses, ward names, vehicle plates
and kilometres — several of which contradicted the locations rendered a few pixels away. So the
tests here are mostly about provenance: the coordinates are real Mumbai localities, the distance is
a great circle and is named as one, and the minutes are the production's own travel times rather
than the scheduler's fallback guess for a pair nobody has measured.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from scenepilot.domain.enums import ResourceType
from scenepilot.seed.nightfall import DAY4_ID, PROJECT_ID, build_project
from scenepilot.services.geo import company_moves, day_geography, haversine_km, seeded_travel_minutes, straight_line_km

# Greater Mumbai, generously: Colaba in the south to Dahisar in the north, the harbour to the sea.
MUMBAI_BOX = (18.87, 19.32, 72.75, 73.02)


def _locations(project):
    return [r for r in project.resources if r.type == ResourceType.LOCATION]


# --------------------------------------------------------------------------- #
# Coordinates
# --------------------------------------------------------------------------- #


def test_every_seeded_location_has_coordinates():
    """A location without a coordinate cannot be drawn, and nothing may be drawn at a guess."""
    missing = [r.name for r in _locations(build_project()) if not r.has_coordinates]
    assert missing == []


def test_the_seeded_coordinates_are_the_real_mumbai_localities_they_claim():
    lat_min, lat_max, lon_min, lon_max = MUMBAI_BOX
    for r in _locations(build_project()):
        assert lat_min <= r.latitude <= lat_max, r.name
        assert lon_min <= r.longitude <= lon_max, r.name
        assert r.locality and "Mumbai" in r.locality

    project = build_project()
    # Film City is in the northern suburbs; the other three are south Mumbai, in that order.
    by_id = {r.id: r for r in _locations(project)}
    assert by_id["loc_apartment"].latitude > by_id["loc_rooftop"].latitude
    assert by_id["loc_rooftop"].latitude > by_id["loc_street"].latitude > by_id["loc_alley"].latitude


def test_a_resource_that_is_not_a_place_carries_no_coordinates():
    project = build_project()
    assert not any(r.has_coordinates for r in project.resources if r.type != ResourceType.LOCATION)


# --------------------------------------------------------------------------- #
# The distance itself
# --------------------------------------------------------------------------- #


def test_haversine_matches_a_known_pair():
    """Mumbai to Delhi is ~1148 km great-circle (19.0760,72.8777 → 28.6139,77.2090)."""
    km = haversine_km(19.0760, 72.8777, 28.6139, 77.2090)
    assert abs(km - 1148.0) < 5.0


def test_haversine_is_zero_for_one_point_and_symmetric():
    assert haversine_km(18.9276, 72.8320, 18.9276, 72.8320) == 0.0
    there = haversine_km(18.9276, 72.8320, 19.1580, 72.8660)
    back = haversine_km(19.1580, 72.8660, 18.9276, 72.8320)
    assert there == back and there > 0


def test_a_degree_of_latitude_is_about_111_km():
    assert abs(haversine_km(0.0, 0.0, 1.0, 0.0) - 111.19) < 0.1


def test_a_location_without_coordinates_has_no_distance_rather_than_a_default():
    project = build_project()
    alley, rooftop = project.resource("loc_alley"), project.resource("loc_rooftop")
    assert straight_line_km(alley, rooftop) > 0
    rooftop.latitude, rooftop.longitude = None, None
    assert straight_line_km(alley, rooftop) is None


# --------------------------------------------------------------------------- #
# Minutes come from the production, not from the scheduler's fallback
# --------------------------------------------------------------------------- #


def test_travel_minutes_are_the_seeded_ones():
    project = build_project()
    assert seeded_travel_minutes(project, "loc_alley", "loc_apartment") == 30
    assert seeded_travel_minutes(project, "loc_apartment", "loc_alley") == 30  # the table is undirected
    assert seeded_travel_minutes(project, "loc_rooftop", "loc_rooftop") == 0


def test_an_untimed_pair_reports_no_minutes_rather_than_the_schedulers_guess():
    """`Project.travel_minutes` answers 30 for an unknown pair so the engine stays conservative.

    That is a scheduling assumption, and it must never reach a screen as if it were a measurement.
    """
    project = build_project()
    project.travel_times = [t for t in project.travel_times if {t.from_location_id, t.to_location_id} != {"loc_alley", "loc_apartment"}]
    assert project.travel_minutes("loc_alley", "loc_apartment") == 30
    assert seeded_travel_minutes(project, "loc_alley", "loc_apartment") is None

    move = next(m for m in company_moves(project, project.shoot_day(DAY4_ID)) if m["to_location_id"] == "loc_apartment")
    assert move["travel_minutes"] is None
    assert day_geography(project, project.shoot_day(DAY4_ID))["total_travel_minutes"] is None


# --------------------------------------------------------------------------- #
# The day as a whole
# --------------------------------------------------------------------------- #


def test_the_hero_day_is_four_locations_and_three_moves_in_shooting_order():
    project = build_project()
    geo = day_geography(project, project.shoot_day(DAY4_ID))

    assert [l["id"] for l in geo["locations"]] == ["loc_alley", "loc_apartment", "loc_street", "loc_rooftop"]
    assert [l["order"] for l in geo["locations"]] == [1, 2, 3, 4]
    assert [l["scene_numbers"] for l in geo["locations"]] == [["31"], ["19"], ["48"], ["42"]]
    assert geo["move_count"] == 3
    assert [(m["from_location_id"], m["to_location_id"]) for m in geo["moves"]] == [
        ("loc_alley", "loc_apartment"), ("loc_apartment", "loc_street"), ("loc_street", "loc_rooftop"),
    ]
    assert [m["travel_minutes"] for m in geo["moves"]] == [30, 40, 25]
    assert geo["total_travel_minutes"] == 95
    assert all(m["straight_line_km"] > 0 for m in geo["moves"])
    assert geo["total_straight_line_km"] == round(sum(m["straight_line_km"] for m in geo["moves"]), 1)
    assert geo["locations_missing_coordinates"] == []


def test_each_move_carries_the_days_own_transport_leg():
    project = build_project()
    moves = company_moves(project, project.shoot_day(DAY4_ID))
    assert [m["transport_leg_id"] for m in moves] == ["leg_1", "leg_2", "leg_3"]
    # Each departure is the latest the van can leave and still arrive, floored at the wrap it
    # follows — a van cannot leave a set the unit is still shooting on.
    assert [m["departure"] for m in moves] == ["09:30", "12:35", "16:00"]
    assert {m["vehicle_name"] for m in moves} == {"Cast van 2"}
    assert [(m["wrap_at"], m["next_shot_at"]) for m in moves] == [("09:30", "10:00"), ("12:30", "13:30"), ("16:00", "16:30")]


def test_the_distance_is_never_published_as_a_road_distance():
    project = build_project()
    geo = day_geography(project, project.shoot_day(DAY4_ID))
    move = geo["moves"][0]
    assert "straight_line_km" in move
    assert not any(k in move for k in ("distance_km", "road_km", "driving_km", "route_km"))
    assert "Not a road distance" in geo["distance_basis"]


def test_a_day_at_one_location_is_no_move_at_all():
    project = build_project()
    geo = day_geography(project, project.shoot_day("day_5"))
    assert len(geo["locations"]) == 1 and geo["moves"] == []
    assert geo["move_count"] == 0 and geo["total_straight_line_km"] == 0 and geo["total_travel_minutes"] == 0


def test_two_consecutive_scenes_at_the_same_location_are_not_a_move():
    project = build_project()
    day = project.shoot_day(DAY4_ID)
    day.items[1].location_id = "loc_alley"  # Sc 19 pulled to the alley: alley → alley → street → rooftop
    geo = day_geography(project, day)
    assert geo["move_count"] == 2
    assert [l["id"] for l in geo["locations"]] == ["loc_alley", "loc_street", "loc_rooftop"]
    assert geo["locations"][0]["scene_numbers"] == ["31", "19"] and geo["locations"][0]["last_end"] == "12:30"


def test_the_dawn_splinter_unit_has_one_locality_and_never_relocates():
    """Day 3 shot one aerial plate from the Worli approach: a place to draw, and no move."""
    project = build_project()
    geo = day_geography(project, project.shoot_day("day_3"))
    assert [l["id"] for l in geo["locations"]] == ["loc_sea_link"]
    assert geo["locations"][0]["locality"] == "Worli, Mumbai" and geo["locations"][0]["scene_numbers"] == ["12"]
    assert geo["moves"] == [] and geo["move_count"] == 0


def test_a_day_with_no_located_scenes_has_no_geography_to_draw():
    project = build_project()
    day = project.shoot_day("day_3")
    day.items[0].location_id = None
    project.scene("sc_12").location_id = None
    geo = day_geography(project, day)
    assert geo["locations"] == [] and geo["moves"] == []


def test_the_night_unit_moves_once_from_the_stage_to_the_roof():
    project = build_project()
    geo = day_geography(project, project.shoot_day("day_6"))
    assert geo["move_count"] == 1
    move = geo["moves"][0]
    assert (move["from_location_id"], move["to_location_id"]) == ("loc_apartment", "loc_rooftop")
    assert move["travel_minutes"] == 45 and move["departure"] == "20:00"


# --------------------------------------------------------------------------- #
# Through the service
# --------------------------------------------------------------------------- #


def _api(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    monkeypatch.setattr(app_module, "repo", repo)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, warm_demo=False))
    return app_module, repo


def test_the_shoot_day_payload_carries_the_geography_the_day_page_needs(monkeypatch):
    app_module, _ = _api(monkeypatch)
    with TestClient(app_module.app) as c:
        body = c.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}").json()

    geo = body["geography"]
    assert set(geo) == {
        "locations", "moves", "move_count", "total_straight_line_km", "total_travel_minutes",
        "locations_missing_coordinates", "distance_basis", "travel_minutes_basis", "coordinates_basis",
    }
    assert set(geo["locations"][0]) == {"id", "name", "kind", "locality", "latitude", "longitude", "order", "scene_numbers", "first_start", "last_end"}
    assert set(geo["moves"][0]) == {
        "from_location_id", "from_name", "from_latitude", "from_longitude",
        "to_location_id", "to_name", "to_latitude", "to_longitude",
        "straight_line_km", "travel_minutes", "after_scene", "before_scene",
        "wrap_at", "next_shot_at", "departure", "transport_leg_id", "vehicle_id", "vehicle_name",
    }
    # The same coordinates the resource dictionary on the page already carries — one geography, not two.
    assert body["resources"]["loc_rooftop"]["latitude"] == geo["locations"][3]["latitude"]
    assert isinstance(geo["moves"][0]["straight_line_km"], float) and isinstance(geo["moves"][0]["travel_minutes"], int)
