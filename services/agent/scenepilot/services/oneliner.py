"""The one-liner: the whole shoot on one page, one line a scene.

A call sheet answers "what is this unit doing tomorrow". The one-liner answers the other question a
producer asks — "what is the shape of the whole picture, and what did that change do to it" — and it
is the document that gets circulated when a schedule moves, because the entire before and after fits
side by side on a single sheet in a way no board or Gantt does.

Every column is already somewhere else in the product; the point of this module is that they are all
in one row: the day banner a stripboard prints, the scene number and slugline, the cast numbers the
call sheet leads with, and the page eighths the board totals. Nothing new is computed here except
the day and production totals, and those are withheld — not estimated — wherever a scene on the day
carries no page count, exactly as the call sheet withholds them.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import ShootDayStatus
from ..domain.models import Project, ScheduleItem, ShootDay
from .callsheet import eighths_label
from .labor_rules import active_pack
from .timeutil import to_minutes


def _rows(project: Project, day: ShootDay, items: list[ScheduleItem]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda i: to_minutes(i.start)):
        scene = project.scene(item.scene_id)
        location_id = item.location_id or scene.location_id
        cast = sorted(
            ({"cast_number": project.resource(c).cast_number, "name": project.resource(c).name} for c in scene.cast_ids),
            key=lambda c: (c["cast_number"] is None, c["cast_number"] or 0, c["name"]),
        )
        rows.append({
            "item_id": item.id,
            "scene_id": scene.id,
            "scene": scene.number,
            "heading": scene.heading,
            "int_ext": scene.int_ext.value,
            "time_of_day": scene.time_of_day.value,
            "synopsis": scene.synopsis,
            "start": item.start,
            "end": item.end,
            "minutes": to_minutes(item.end) - to_minutes(item.start),
            "eighths": scene.eighths,
            "pages": eighths_label(scene.eighths),
            "cast": cast,
            "location": project.resource(location_id).name if location_id else None,
            "status": item.status.value,
            "cover": scene.is_cover,
            # Which unit shoots it. The engine prices cross-unit contention for cast and equipment,
            # so a sheet that never names a unit is hiding the thing that contention is about.
            "unit": item.unit,
        })
    return rows


def _total(rows: list[dict[str, Any]]) -> tuple[int | None, str | None]:
    """Summed only where every row has a count, so a partial total never reads as the whole."""
    counts = [r["eighths"] for r in rows]
    if not rows or any(c is None for c in counts):
        return None, None
    total = sum(counts)
    return total, eighths_label(total)


def _rest_before(pack, previous: dict[str, Any] | None, day: ShootDay, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Rest between the previous day's camera wrap and this day's unit call.

    The turnaround rule is already enforced — the validator prices a breach as a soft violation and
    the recovery options are scored under it — but it was only ever legible as a line of text inside
    one option's cost breakdown. Between two day banners is where a producer actually reads the
    shape of a week, and it is where a short turnaround is visible as a short gap.

    `None` for the first day (there is no previous wrap to measure from) and for any day where either
    side has no scheduled scene: rest measured from a day that shoots nothing is not rest, it is an
    empty day, and the two must not print alike.
    """
    if previous is None or not previous.get("scenes") or not rows:
        return None
    wrap = max(to_minutes(r["end"]) for r in previous["scenes"])
    # The next call sits on the following calendar day; the wrap may itself run past midnight.
    call = to_minutes(day.unit_call) + 24 * 60
    minutes = call - wrap
    required = int(pack.minimum_turnaround_hours * 60)
    return {
        "minutes": minutes,
        "required_minutes": required,
        "hours_label": f"{minutes // 60}h{minutes % 60:02d}",
        "required_label": f"{pack.minimum_turnaround_hours:g}h",
        "breach": minutes < required,
        "deficit_minutes": max(0, required - minutes),
        "from_wrap": previous["scenes"][-1]["end"],
        "to_call": day.unit_call,
        "pack": pack.name,
    }


def _velocity(day_eighths: int | None, scheduled: list[dict[str, Any]], wrapped: int) -> dict[str, Any] | None:
    """This day's page count against the production's own scheduled average, in eighths.

    Two pages a day is the number a producer carries in their head, and the board has always held
    everything needed to state it. What it must not do is average over a schedule it cannot total:
    a production where one scheduled scene carries no page count has no average, and printing one
    computed from the rest would quote a velocity for a schedule that is partly unmeasured.

    The denominator is days that actually carry a scene. A day with nothing scheduled on it is not a
    day the unit shot slowly; it is a day nobody has filled, and dividing by it would report a
    slowdown that never happened.

    It is called the **scheduled** average deliberately. Most of these days have not been shot, so
    the figure is a property of the plan, not of the unit's delivery — and this codebase separates
    those everywhere else (`day_completion` refuses to report an unshot day at all; the cost card
    carries `basis: projected | record`). `days_wrapped` says how much of the average is already
    history, so a caller can qualify the claim rather than having to guess.
    """
    if day_eighths is None or not scheduled:
        return None
    counts = [d["total_eighths"] for d in scheduled]
    if any(c is None for c in counts):
        return {
            "day_eighths": day_eighths,
            "day_label": eighths_label(day_eighths),
            "average_eighths": None,
            "average_label": None,
            "delta_label": None,
            "days_counted": len(scheduled),
            "days_wrapped": wrapped,
            "withheld_reason": (
                "At least one scheduled day carries a scene with no page count, so this production has no average "
                "to compare against rather than one summed from the days that happen to be measured."
            ),
        }

    average = sum(counts) / len(scheduled)
    rounded = int(round(average))
    delta = day_eighths - average
    if abs(delta) < 0.5:  # inside half an eighth of the average — not a difference worth a claim
        delta_label = "on the production average"
    else:
        direction = "above" if delta > 0 else "below"
        delta_label = f"{eighths_label(int(round(abs(delta))))} {direction} the average"
    return {
        "day_eighths": day_eighths,
        "day_label": eighths_label(day_eighths),
        "average_eighths": rounded,
        "average_label": eighths_label(rounded),
        "delta_label": delta_label,
        "days_counted": len(scheduled),
        # How many of the days behind the average are a record rather than a plan.
        "days_wrapped": wrapped,
        "withheld_reason": None,
    }


def build_one_liner(project: Project, overrides: dict[str, list[ScheduleItem]] | None = None) -> dict[str, Any]:
    """The production's condensed shoot order, day by day.

    `overrides` replaces one day's items — the rescue's own pre-recovery baseline — so a *before*
    one-liner can be built against a schedule that is no longer committed, with every other day
    identical by construction.
    """
    pack = active_pack(project)
    days: list[dict[str, Any]] = []
    for day in sorted(project.shoot_days, key=lambda d: (d.day_number, d.date)):
        items = overrides.get(day.id, day.items) if overrides else day.items
        rows = _rows(project, day, list(items))
        total_eighths, total_label = _total(rows)
        sets: list[str] = []
        for row in rows:
            if row["location"] and row["location"] not in sets:
                sets.append(row["location"])
        days.append({
            "shoot_day_id": day.id,
            "day_number": day.day_number,
            "date": day.date,
            "unit_call": day.unit_call,
            "status": day.status.value,
            "scenes": rows,
            "scene_count": len(rows),
            "total_eighths": total_eighths,
            "total_label": total_label,
            "sets": sets,
            # A day whose sets change mid-day is a company move, and a producer reads the one-liner
            # to spot exactly that: two sets on one line is where the day gets expensive.
            "company_moves": max(0, len(sets) - 1),
            # Rest since the previous day wrapped, so the gap between two banners can be read as one.
            "rest_before": _rest_before(pack, days[-1] if days else None, day, rows),
        })
    scheduled = [d for d in days if d["scene_count"]]
    wrapped_count = len([d for d in scheduled if d["status"] == ShootDayStatus.WRAPPED.value])
    for day_row in days:
        day_row["velocity"] = _velocity(day_row["total_eighths"], scheduled, wrapped_count)
    grand_eighths = sum(d["total_eighths"] for d in scheduled) if scheduled and all(d["total_eighths"] is not None for d in scheduled) else None
    return {
        "production": project.title,
        "days": days,
        "scene_count": sum(d["scene_count"] for d in days),
        "total_eighths": grand_eighths,
        "total_label": eighths_label(grand_eighths),
        "unpriced_reason": None if grand_eighths is not None else (
            "At least one scheduled scene carries no page count, so the production total is withheld rather than "
            "summed from part of the schedule."
        ),
    }


def one_liner_moves(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Which scenes the change moved, named — the reason a one-liner is circulated at all.

    A scene reaches this list because its day or its slot actually differs between the two versions.
    Both are built over the same days from the same project, so nothing else can account for it.
    """
    def index(one_liner: dict[str, Any]) -> dict[str, tuple[int, str, str]]:
        return {
            row["scene"]: (day["day_number"], row["start"], row["end"])
            for day in one_liner["days"]
            for row in day["scenes"]
        }

    was, now = index(before), index(after)
    moves: list[dict[str, Any]] = []
    for scene in sorted(set(was) | set(now), key=lambda s: (len(s), s)):
        old, new = was.get(scene), now.get(scene)
        if old == new:
            continue
        moves.append({
            "scene": scene,
            "from_day": old[0] if old else None,
            "from_slot": f"{old[1]}–{old[2]}" if old else None,
            "to_day": new[0] if new else None,
            "to_slot": f"{new[1]}–{new[2]}" if new else None,
            # A scene in the before and not the after was carried out of the schedule entirely.
            "carried_out": new is None,
        })
    return moves
