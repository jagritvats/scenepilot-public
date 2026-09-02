"""The movement order — the transport sheet a unit is handed when the day moves.

Everything here is already computed for the day page's company-move panel; this is the same state
laid out as the one-page document a transport captain actually carries. The only arithmetic added is
arrival time, which is departure plus the production's own travel minutes.

What it refuses to print is the point of the document being trustworthy:

* **No route and no road distance.** `services/geo.py` measures great-circle kilometres between real
  coordinates and says so; a driving route would need road data this production does not hold, and a
  transport sheet that implied one would be sending a van down a line on a map.
* **No arrival where the leg is untimed.** Travel minutes come only from the production's own
  `TravelTime` rows. The scheduler falls back to a flat 30 minutes when it must place a scene, but
  that fallback is a scheduling assumption, not a promise to a driver, so it is never printed here.
* **No pickups, no manifest, no driver.** The production models vehicles and legs, not who rides in
  which van or who is driving it. Those are named as gaps for the transport captain to fill.

A leg whose departure falls before the previous scene wraps is printed exactly as it stands, with the
overlap shown. That is a real conflict on a real sheet and quietly re-timing it here would hide the
one thing this document exists to catch.
"""

from __future__ import annotations

from typing import Any

from ..domain.models import Project, ShootDay
from .changeset import VEHICLE_LOAD_MINUTES as LOAD_MINUTES
from .geo import day_geography
from .timeutil import to_hhmm, to_minutes

# Fields a printed movement order carries that this production holds no state for.
TO_BE_COMPLETED: list[dict[str, str]] = [
    {"field": "Driver and contact", "reason": "Vehicles are modelled; the drivers assigned to them are held by the transport captain."},
    {"field": "Passenger manifest", "reason": "The production books vehicles per leg, not seats per person."},
    {"field": "Pickup from base or hotel", "reason": "No base or accommodation is modelled — every leg here runs set to set."},
    {"field": "Parking and unit base", "reason": "Not in production state; agreed with each location on the day."},
]


def build_movement_order(project: Project, day: ShootDay) -> dict[str, Any]:
    """The day's transport, as one printable sheet. Always returns a document, even for a still day."""
    geography = day_geography(project, day)
    legs: list[dict[str, Any]] = []

    for index, move in enumerate(geography["moves"], start=1):
        departure = move.get("departure")
        travel = move.get("travel_minutes")
        arrival = to_hhmm(to_minutes(departure) + travel) if departure and travel is not None else None

        wrap_at, next_shot = move.get("wrap_at"), move.get("next_shot_at")
        gap = to_minutes(next_shot) - to_minutes(wrap_at) if wrap_at and next_shot else None
        slack = gap - travel if gap is not None and travel is not None else None
        # A departure earlier than the wrap it follows is a conflict, not a rounding artefact. It
        # should no longer arise from a derived leg — `derive_transport` clamps to the wrap — but a
        # hand-entered leg can still say it, and a transport sheet must not quietly correct it.
        overlap = to_minutes(wrap_at) - to_minutes(departure) if departure and wrap_at and to_minutes(departure) < to_minutes(wrap_at) else None
        # Fifteen minutes is what loading a van takes; where the gap cannot spare it, the load is what
        # gets squeezed, and the driver should read that on the sheet rather than discover it.
        load_margin = to_minutes(departure) - to_minutes(wrap_at) if departure and wrap_at else None

        legs.append({
            "index": index,
            "from_name": move["from_name"],
            "to_name": move["to_name"],
            "from_latitude": move.get("from_latitude"),
            "from_longitude": move.get("from_longitude"),
            "to_latitude": move.get("to_latitude"),
            "to_longitude": move.get("to_longitude"),
            "straight_line_km": move.get("straight_line_km"),
            "travel_minutes": travel,
            "departure": departure,
            "arrival": arrival,
            "wrap_at": wrap_at,
            "next_shot_at": next_shot,
            "after_scene": move.get("after_scene"),
            "before_scene": move.get("before_scene"),
            "gap_minutes": gap,
            "slack_minutes": slack,
            "departure_before_wrap_minutes": overlap,
            "load_margin_minutes": load_margin,
            "load_squeezed": load_margin is not None and 0 <= load_margin < LOAD_MINUTES,
            "vehicle_name": move.get("vehicle_name"),
            "transport_leg_id": move.get("transport_leg_id"),
            "untimed": travel is None,
        })

    return {
        "production": project.title,
        "fictional": True,
        "day_number": day.day_number,
        "day_of_total": len(project.shoot_days),
        "date": day.date,
        "status": day.status.value,
        "unit_call": day.unit_call,
        "legs": legs,
        "locations": geography["locations"],
        "move_count": geography["move_count"],
        "total_straight_line_km": geography["total_straight_line_km"],
        "total_travel_minutes": geography["total_travel_minutes"],
        "locations_missing_coordinates": geography["locations_missing_coordinates"],
        "basis": {
            "distance": geography["distance_basis"],
            "travel_minutes": geography["travel_minutes_basis"],
            "coordinates": geography["coordinates_basis"],
        },
        "to_be_completed": TO_BE_COMPLETED,
        "note": None if legs else "The unit stays on one location all day. There is no company move to order.",
    }
