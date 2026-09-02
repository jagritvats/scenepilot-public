"""Where a shoot day physically is, so a company move can be drawn instead of imagined.

The four seeded sets are fictional; the Mumbai localities they sit in are real, and their coordinates
are the only geography in the product. Everything published here is computed from them, from the
day's own schedule, and from the production's `TravelTime` table. Two rules keep the drawing honest:

* the distance is a **great circle** — a straight line between two points on the ground. It is named
  `straight_line_km` in every payload so no caption can quietly promote it to a driving distance;
  Kala Ghoda to Film City is 26 km in a straight line and considerably more by road.
* the minutes are the production's own `TravelTime` entries — the same numbers the scheduler
  enforces against the gap between two scenes — and are `null` for a pair nobody has timed. The
  scheduler's 30-minute fallback for an unknown pair is a scheduling assumption, not a measurement,
  so it is never rendered as one.

Presentation only: nothing here is read by `services/schedule.py`, and adding a coordinate cannot
change what the engine accepts or rejects.
"""

from __future__ import annotations

import math
from typing import Any

from ..domain.enums import ResourceType
from ..domain.models import Project, Resource, ScheduleItem, ShootDay, TransportLeg
from .timeutil import to_minutes

EARTH_RADIUS_KM = 6371.0088  # IUGG mean radius

DISTANCE_BASIS = "Great-circle (straight-line) distance between the two localities. Not a road distance and not a route."
TRAVEL_MINUTES_BASIS = "The production's own travel times — the same minutes the scheduler enforces between two scenes. Null where the pair has no entry."
COORDINATES_BASIS = "Real coordinates of the Mumbai localities these fictional sets sit in — the centre of the locality, not a street address."


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two WGS84 points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def straight_line_km(a: Resource | None, b: Resource | None) -> float | None:
    """Kilometres between two locations, or None if either has no coordinates. Rounded to 100 m."""
    if a is None or b is None or not a.has_coordinates or not b.has_coordinates:
        return None
    return round(haversine_km(a.latitude, a.longitude, b.latitude, b.longitude), 1)


def seeded_travel_minutes(project: Project, from_id: str | None, to_id: str | None) -> int | None:
    """The minutes this production actually has for a pair — never the scheduler's default guess."""
    if from_id is None or to_id is None:
        return None
    if from_id == to_id:
        return 0
    for t in project.travel_times:
        if {t.from_location_id, t.to_location_id} == {from_id, to_id}:
            return t.minutes
    return None


def _location(project: Project, resource_id: str | None) -> Resource | None:
    if resource_id is None:
        return None
    try:
        r = project.resource(resource_id)
    except KeyError:
        return None
    return r if r.type == ResourceType.LOCATION else None


def _leg_for(legs: list[TransportLeg], from_id: str, to_id: str, used: set[str]) -> TransportLeg | None:
    """The day's own transport leg for this move, if it has one — including its re-timed departure."""
    return next((l for l in legs if l.id not in used and l.from_location_id == from_id and l.to_location_id == to_id), None)


def _vehicle(project: Project, resource_id: str | None) -> Resource | None:
    if resource_id is None:
        return None
    try:
        return project.resource(resource_id)
    except KeyError:
        return None


def _scene(project: Project, scene_id: str):
    try:
        return project.scene(scene_id)
    except KeyError:
        return None


def _stops(project: Project, day: ShootDay) -> list[tuple[ScheduleItem, Resource]]:
    """The day's items that happen somewhere, in time order, paired with that place.

    An item whose scene or location has gone missing is skipped rather than raised: this is the
    day page's map, and it may not be the reason the day page fails to load.
    """
    out = []
    for item in sorted(day.items, key=lambda i: to_minutes(i.start)):
        scene = _scene(project, item.scene_id)
        if scene is None:
            continue
        loc = _location(project, item.location_id or scene.location_id)
        if loc is not None:
            out.append((item, loc))
    return out


def day_locations(project: Project, day: ShootDay) -> list[dict[str, Any]]:
    """Every location this day shoots at, in the order the day first reaches it."""
    out: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for item, loc in _stops(project, day):
        scene = project.scene(item.scene_id)
        row = index.get(loc.id)
        if row is None:
            row = {
                "id": loc.id, "name": loc.name, "kind": loc.attributes.get("kind"), "locality": loc.locality,
                "latitude": loc.latitude, "longitude": loc.longitude,
                "order": len(out) + 1, "scene_numbers": [], "first_start": item.start, "last_end": item.end,
            }
            index[loc.id] = row
            out.append(row)
        row["scene_numbers"].append(scene.number)
        row["last_end"] = max(row["last_end"], item.end, key=to_minutes)
    return out


def company_moves(project: Project, day: ShootDay) -> list[dict[str, Any]]:
    """One entry per location change, in time order. Consecutive scenes at one location are no move."""
    stops = _stops(project, day)
    moves: list[dict[str, Any]] = []
    used: set[str] = set()
    for (prev, a), (nxt, b) in zip(stops, stops[1:]):
        if a.id == b.id:
            continue
        leg = _leg_for(day.transport, a.id, b.id, used)
        if leg is not None:
            used.add(leg.id)
        vehicle = _vehicle(project, leg.vehicle_id) if leg else None
        moves.append({
            "from_location_id": a.id, "from_name": a.name, "from_latitude": a.latitude, "from_longitude": a.longitude,
            "to_location_id": b.id, "to_name": b.name, "to_latitude": b.latitude, "to_longitude": b.longitude,
            "straight_line_km": straight_line_km(a, b),
            "travel_minutes": seeded_travel_minutes(project, a.id, b.id),
            "after_scene": project.scene(prev.scene_id).number,
            "before_scene": project.scene(nxt.scene_id).number,
            "wrap_at": prev.end,
            "next_shot_at": nxt.start,
            "departure": leg.departure if leg else None,
            "transport_leg_id": leg.id if leg else None,
            "vehicle_id": leg.vehicle_id if leg else None,
            "vehicle_name": vehicle.name if vehicle else None,
        })
    return moves


def day_geography(project: Project, day: ShootDay) -> dict[str, Any]:
    """The day as a map: where it shoots, and every move between those places.

    A day that never leaves one location totals zero, which is a fact. A day with a move whose
    number is missing totals null rather than a sum over the moves that happen to have one — that
    reads as a day total and is not one.
    """
    locations = day_locations(project, day)
    moves = company_moves(project, day)
    km = [m["straight_line_km"] for m in moves]
    minutes = [m["travel_minutes"] for m in moves]
    return {
        "locations": locations,
        "moves": moves,
        "move_count": len(moves),
        "total_straight_line_km": round(sum(km), 1) if all(v is not None for v in km) else None,
        "total_travel_minutes": sum(minutes) if all(v is not None for v in minutes) else None,
        "locations_missing_coordinates": [{"id": l["id"], "name": l["name"]} for l in locations if l["latitude"] is None or l["longitude"] is None],
        "distance_basis": DISTANCE_BASIS,
        "travel_minutes_basis": TRAVEL_MINUTES_BASIS,
        "coordinates_basis": COORDINATES_BASIS,
    }
