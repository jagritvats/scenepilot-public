"""What a shoot day that has already happened delivered.

Every other panel in the product is forward-looking: it asks what a day will cost, what could go
wrong with it, and what to do when something does. A wrapped day answers none of those questions,
and asking them of it is how Day 3 came to be offered a ₹60,000 recovery for a scene that was in the
can. But a wrapped day is not empty either — it is the only day in the schedule whose numbers are
facts rather than estimates — so the honest replacement for the rescue panel is the record: what was
shot, what it cost, and what did not make it.

Nothing here is stated; all of it is read back off the day's own schedule items, so a day with an
item nobody marked `COMPLETED` reports it as outstanding rather than quietly counting it as shot.
"""

from __future__ import annotations

import math
from typing import Any

from ..domain.enums import ScheduleItemStatus, ShootDayStatus
from ..domain.models import Project, ScheduleItem, ShootDay
from .callsheet import eighths_label
from .timeutil import to_hhmm, to_minutes


def _row(project: Project, day: ShootDay, item: ScheduleItem) -> dict[str, Any]:
    scene = project.scene(item.scene_id)
    location_id = item.location_id or scene.location_id
    return {
        "item_id": item.id,
        "scene_id": scene.id,
        "scene_number": scene.number,
        "heading": scene.heading,
        "start": item.start,
        "end": item.end,
        "minutes": to_minutes(item.end) - to_minutes(item.start),
        "eighths": scene.eighths,
        "unit": item.unit,
        "location": project.resource(location_id).name if location_id else None,
        "cast": [project.resource(c).name for c in scene.cast_ids],
        "status": item.status.value,
        "note": item.note,
    }


def day_completion(project: Project, day: ShootDay) -> dict[str, Any] | None:
    """The record of a wrapped day, or None while the day is still ahead of the production.

    `None` is the point: a day that has not been shot has no delivery to report, and a panel that
    printed one anyway would be reporting an estimate under the heading of a fact.
    """
    if day.status != ShootDayStatus.WRAPPED or not day.items:
        return None

    items = sorted(day.items, key=lambda i: to_minutes(i.start))
    completed = [i for i in items if i.status == ScheduleItemStatus.COMPLETED]
    outstanding = [i for i in items if i.status != ScheduleItemStatus.COMPLETED]

    call = to_minutes(day.unit_call)
    # What the day actually wrapped at, when somebody recorded it. Falling back to `max(end)` reads
    # a carried scene's scheduled end as a wrap time: a strip nobody shot would date the day and
    # inflate the overtime derived from it.
    wrap = to_minutes(day.camera_wrap) if day.camera_wrap else max(to_minutes(i.end) for i in items)
    # Measured off the wrap this record just established, not off `items`. `overtime_minutes` takes
    # `max(end)` across everything on the strip — including the scenes that were carried and never
    # shot — so a wrapped day billed overtime for work it did not do, and contradicted the wrap time
    # printed two lines above it. Same arithmetic as `overtime_minutes`, against the day that
    # happened rather than the day that was planned.
    ot = max(0, wrap - (call + int(day.standard_hours * 60)))
    ot_cost = math.ceil(ot / 60 * day.overtime_rate_per_hour) if ot else 0
    carry_cost = len(outstanding) * day.carry_over_cost

    shot = [_row(project, day, i) for i in completed]
    carried = [_row(project, day, i) for i in outstanding]
    minutes_shot = sum(r["minutes"] for r in shot)
    # Page counts are only summed where every completed scene has one; a partial total read as a
    # day's page count is a smaller number than the day actually did.
    eighths = [r["eighths"] for r in shot]
    eighths_shot = sum(e for e in eighths if e is not None) if eighths and all(e is not None for e in eighths) else None

    units = sorted({i.unit for i in items})
    unit_label = units[0].lower() if len(units) == 1 else "mixed"
    elapsed = wrap - call
    worked = f"{elapsed // 60} h {elapsed % 60:02d} min" if elapsed % 60 else f"{elapsed // 60} h"
    parts = [
        f"Day {day.day_number} wrapped {to_hhmm(wrap)} — {worked} into its "
        f"{day.standard_hours:g} h call at {day.unit_call} ({day.crew_size} crew, {unit_label} unit)."
    ]
    if shot:
        pages = f", {eighths_label(eighths_shot)} pg" if eighths_shot is not None else ""
        parts.append(
            f"{len(shot)} scene(s) completed — {', '.join('Sc ' + r['scene_number'] for r in shot)} "
            f"({minutes_shot} min{pages})."
        )
    parts.append(
        f"{len(carried)} scene(s) outstanding (≈₹{carry_cost:,} to carry)." if carried else "Nothing carried."
    )
    parts.append(f"{ot} min overtime (≈₹{ot_cost:,})." if ot else "No overtime.")

    return {
        "wrapped": True,
        "unit_call": day.unit_call,
        "first_shot": items[0].start,
        "wrap": to_hhmm(wrap),
        "elapsed_minutes": wrap - call,
        "standard_minutes": int(day.standard_hours * 60),
        "overtime_minutes": ot,
        "overtime_cost_inr": ot_cost,
        "carry_over_cost_inr": carry_cost,
        "cost_inr": ot_cost + carry_cost,
        "scenes_completed": shot,
        "scenes_carried": carried,
        "minutes_shot": minutes_shot,
        "eighths_shot": eighths_shot,
        "locations": list(dict.fromkeys(r["location"] for r in shot + carried if r["location"])),
        "units": units,
        "summary": " ".join(parts),
    }
