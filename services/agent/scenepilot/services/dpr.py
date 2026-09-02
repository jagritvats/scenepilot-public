"""The Daily Production Report — what a day actually delivered, issued the evening it wrapped.

The call sheet is the day's instruction; this is its receipt, and it is the document a producer, a
completion bond and an insurer all read first. The whole of its body already existed in state and
appeared on no screen: `day_completion` has computed scenes shot against scenes carried, page counts,
overtime minutes and cost since long before this file, and nothing in the web app ever read it.

Two rules give the document its shape, and they are the same rule twice:

1. **It refuses to be issued for a day that has not happened.** `day_completion` returns `None` for
   anything but a wrapped day, and this returns `None` with it. A DPR for tomorrow is a forecast
   wearing a report's clothes — it would be the most authoritative-looking lie in the product.
2. **It reports; it does not re-estimate.** Cost comes from `day_cost` on its record branch, so the
   figure here is the same figure the day page and the production cost strip show for this day. A
   report that quietly re-priced the day would be a second opinion presented as a record, and the
   producer would have no way to tell which of the two the money followed.

What the production has no state for — who was sick, why the second set-up ran long, what the
director thought — is left off rather than invented. A DPR that fabricates its narrative section is
worse than one that admits the unit still has to write it, which is what `to_be_completed` says.
"""

from __future__ import annotations

from typing import Any

from ..domain.models import Project, ShootDay
from .callsheet import advance_block, eighths_label
from .completion import day_completion
from .day_cost import day_cost
from .timeutil import to_minutes


# Fields a real DPR carries that this production holds no state for. Named rather than blanked, so
# the sheet reads as a form with parts still to fill in and never as a complete record that is wrong.
TO_BE_COMPLETED: list[dict[str, str]] = [
    {"field": "Crew meal count", "reason": "Catering returns the number actually served; the production records the call, not the plate count."},
    {"field": "Accidents / incidents", "reason": "Reported by the unit safety officer at wrap. Nothing in the schedule can assert one either way."},
    {"field": "Sickness and absence", "reason": "Held by the production office, not the schedule."},
    {"field": "Director's notes", "reason": "Written by the 1st AD from the floor at wrap."},
]


def build_dpr(project: Project, day: ShootDay) -> dict[str, Any] | None:
    """The day's report, or `None` when the day has not wrapped and there is nothing to report."""
    completion = day_completion(project, day)
    if completion is None:
        return None

    cost = day_cost(project, day)
    items = sorted(day.items, key=lambda i: to_minutes(i.start))

    # Who actually worked, from the scenes that were shot. Named off the completed rows only: a
    # performer whose scene was carried was held, not worked, and the DOOD prices that separately.
    worked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in completion["scenes_completed"]:
        scene = project.scene(row["scene_id"])
        for cast_id in scene.cast_ids:
            if cast_id in seen:
                continue
            seen.add(cast_id)
            resource = project.resource(cast_id)
            scenes_for_cast = [
                r["scene_number"] for r in completion["scenes_completed"]
                if cast_id in project.scene(r["scene_id"]).cast_ids
            ]
            worked.append({
                "cast_id": cast_id,
                "cast_number": resource.cast_number,
                "name": resource.name,
                "scenes": scenes_for_cast,
            })
    worked.sort(key=lambda c: (c["cast_number"] is None, c["cast_number"] or 0, c["name"]))

    scheduled_eighths = [project.scene(i.scene_id).eighths for i in items]
    scheduled_total = sum(e for e in scheduled_eighths if e is not None) if scheduled_eighths and all(e is not None for e in scheduled_eighths) else None

    return {
        "production": project.title,
        "fictional": True,
        "day_number": day.day_number,
        "day_of_total": len(project.shoot_days),
        "date": day.date,
        "status": day.status.value,
        "unit_call": completion["unit_call"],
        "first_shot": completion["first_shot"],
        "wrap": completion["wrap"],
        "elapsed_minutes": completion["elapsed_minutes"],
        "standard_minutes": completion["standard_minutes"],
        "hard_wrap": day.hard_wrap,
        "crew_size": day.crew_size,
        "units": completion["units"],
        "locations": completion["locations"],
        "scenes_completed": completion["scenes_completed"],
        "scenes_carried": completion["scenes_carried"],
        "minutes_shot": completion["minutes_shot"],
        "pages": {
            # Shot against scheduled, both withheld rather than part-summed where any scene on the
            # day carries no count — the same rule the call sheet and the one-liner apply.
            "shot_eighths": completion["eighths_shot"],
            "shot_label": eighths_label(completion["eighths_shot"]) if completion["eighths_shot"] is not None else None,
            "scheduled_eighths": scheduled_total,
            "scheduled_label": eighths_label(scheduled_total) if scheduled_total is not None else None,
            "reason": None if completion["eighths_shot"] is not None else "A scene shot on this day carries no page count, so the day's total is withheld rather than part-summed.",
        },
        "cast_worked": worked,
        # The same cost object the day page and the cost strip print for this day, on its record
        # branch — one figure for the day, not a second opinion.
        "cost": cost,
        "advance": advance_block(project, day),
        "to_be_completed": TO_BE_COMPLETED,
        "summary": completion["summary"],
        "provenance": (
            "Assembled from this production's own schedule and completion record. Scenes, times and page "
            "counts are the day's committed state; costs are the same figures the day page reports."
        ),
    }
