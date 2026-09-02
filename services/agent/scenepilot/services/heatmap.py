"""Where this production is tight — every resource against every day.

The board answers "is this day shootable". This answers the question behind it: *which bookings have
no slack left*, so a producer can see why one rainy afternoon cascades and another does not. Nothing
here is new state; it is availability, the schedule and the validator, arranged as a grid.

The one subtlety worth stating, because getting it wrong would invert the meaning of a cell: a
resource's availability is **three-valued**, not two.

* No availability rows at all → *unconstrained*. `availability_windows` returns a whole-day window
  for it (`services/schedule.py`), and it is genuinely unrestricted: crew and vehicles are seeded this
  way deliberately.
* Rows exist and one covers this day → *windowed*, and the margin against its booking is meaningful.
* Rows exist but none covers this day → **unavailable**, which is what the validator concludes. This
  is not "no data": the production books people onto days, and this one has not been booked.

A grid that painted the first and third the same colour would report the crew as maximally
constrained and a lead with no booking as free. So each cell carries its `availability` kind and the
UI keys off that rather than off the number.

Tightness is measured against the *span* a resource is held for — its earliest call to its last wrap,
including equipment prep — not the sum of its scenes. A camera booked at 09:00 and again at 17:00 is
held all day whatever happens in between, and a producer trying to release it needs the span.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import ResourceType
from ..domain.models import Project, Resource, ShootDay
from .recovery import next_day_call
from .schedule import ValidationContext, availability_windows, validate_schedule
from .timeutil import to_hhmm, to_minutes

# Cast, locations and equipment are what the validator actually constrains a schedule by; crew and
# vehicles carry no availability rows by design and would render as uniformly slack.
GRADED_TYPES = (ResourceType.CAST, ResourceType.LOCATION, ResourceType.EQUIPMENT)


def _booked_intervals(project: Project, resource: Resource, day: ShootDay) -> list[tuple[int, int]]:
    """When this day holds this resource, read the way the validator reads it."""
    spans: list[tuple[int, int]] = []
    for item in day.items:
        scene = project.scene(item.scene_id)
        used = (
            resource.id in scene.cast_ids
            or resource.id in scene.equipment_ids
            or resource.id == (item.location_id or scene.location_id)
        )
        if used:
            spans.append((to_minutes(item.start), to_minutes(item.end)))
    return sorted(spans)


def _cell(project: Project, resource: Resource, day: ShootDay, violations_by_resource: dict[str, list]) -> dict[str, Any]:
    booked = _booked_intervals(project, resource, day)
    windows = availability_windows(resource, day)
    unconstrained = not resource.availability

    if not booked:
        return {
            "booked": False,
            "availability": "unconstrained" if unconstrained else ("windowed" if windows else "not_booked"),
            "booked_minutes": 0,
            "span_minutes": 0,
            "available_minutes": sum(e - s for s, e in windows),
            "margin_minutes": None,
            "pressure": None,
            "conflicts": [],
            "detail": "not called on this day",
        }

    start = min(s for s, _ in booked)
    end = max(e for _, e in booked)
    # Equipment is held from its call, which is earlier than its first scene by its prep time.
    call = next((c.call_time for c in day.equipment_calls if c.resource_id == resource.id), None)
    if call:
        start = min(start, to_minutes(call))

    span = end - start
    worked = sum(e - s for s, e in booked)
    available = sum(e - s for s, e in windows)
    conflicts = [v.message for v in violations_by_resource.get(resource.id, [])]

    if unconstrained:
        availability, margin, pressure = "unconstrained", None, None
    elif not windows:
        # Booked on a day nobody cleared them for — the validator's hard rejection, in one cell.
        availability, margin, pressure = "not_booked", None, 1.0
    else:
        availability = "windowed"
        margin = available - span
        pressure = min(1.0, span / available) if available else 1.0

    return {
        "booked": True,
        "availability": availability,
        "booked_minutes": worked,
        "span_minutes": span,
        "available_minutes": available,
        "margin_minutes": margin,
        "pressure": round(pressure, 3) if pressure is not None else None,
        "conflicts": conflicts,
        "held_from": to_hhmm(start),
        "held_to": to_hhmm(end),
        "detail": (
            f"held {to_hhmm(start)}–{to_hhmm(end)}"
            + (f", available {', '.join(f'{to_hhmm(s)}–{to_hhmm(e)}' for s, e in windows)}" if windows and not unconstrained else "")
            + (", no availability on file for this day" if availability == "not_booked" else "")
        ),
    }


def build_heatmap(project: Project) -> dict[str, Any]:
    """A resource × day grid of booking tightness. Deterministic; reads state only."""
    days = sorted(project.shoot_days, key=lambda d: (d.day_number, d.date))

    # The validator's own verdict per day, bucketed by resource, so a red cell cites a real rejection
    # rather than this module's arithmetic.
    violations_by_day: dict[str, dict[str, list]] = {}
    for day in days:
        ctx = ValidationContext(
            project=project,
            day=day,
            baseline_items=day.items,
            next_day_call=next_day_call(project, day),
            location_facts=[f for f in project.location_facts if f.binds],
        )
        buckets: dict[str, list] = {}
        for v in validate_schedule(ctx, day.items):
            if v.hard and v.resource_id:
                buckets.setdefault(v.resource_id, []).append(v)
        violations_by_day[day.id] = buckets

    rows: list[dict[str, Any]] = []
    for resource in project.resources:
        if resource.type not in GRADED_TYPES:
            continue
        cells = [_cell(project, resource, day, violations_by_day[day.id]) for day in days]
        if not any(c["booked"] for c in cells):
            continue  # a resource this schedule never calls has no pressure to report
        rows.append({
            "resource_id": resource.id,
            "name": resource.name,
            "type": resource.type.value,
            "cast_number": resource.cast_number,
            "cells": cells,
            "days_booked": len([c for c in cells if c["booked"]]),
            "conflict_days": len([c for c in cells if c["conflicts"]]),
            # The tightest day this resource has, which is what makes a row worth reading.
            "peak_pressure": max((c["pressure"] for c in cells if c["pressure"] is not None), default=None),
        })

    rows.sort(key=lambda r: (-(r["conflict_days"]), -(r["peak_pressure"] or 0), r["type"], r["name"]))
    return {
        "days": [{"shoot_day_id": d.id, "day_number": d.day_number, "date": d.date, "status": d.status.value} for d in days],
        "rows": rows,
        "legend": {
            "unconstrained": "No availability on file anywhere — genuinely unrestricted, not unknown.",
            "windowed": "Booked inside a stated window; the margin is what is left of it.",
            "not_booked": "Has booked days on this production but none here — which the validator reads as unavailable.",
        },
        "provenance": (
            "Pressure is the span a resource is held for — earliest call to last wrap, including equipment prep — "
            "against the window the production has cleared for it. Conflicts are the deterministic validator's own "
            "hard rejections for that resource on that day, not this view's arithmetic."
        ),
    }
