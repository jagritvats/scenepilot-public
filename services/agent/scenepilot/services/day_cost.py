"""What a shoot day costs in consequences — one roll-up, from terms that already exist.

Every number here is already computed somewhere: the validator prices overtime, meal penalties,
turnaround breaches, carry-overs and equipment re-rentals; the DOOD prices held performers; the day
carries its own company-move rate. What has never existed is the sum, which is the number a producer
actually manages to. So this composes, and prices nothing new.

Composing costs is where a report starts lying, so three rules hold it together:

1. **One source per term.** Each line names exactly one origin, listed in `SOURCES` below. The most
   important consequence: company moves are counted from the day's own schedule
   (`count_company_moves × company_move_cost`) and *not* from the `EXTRA_COMPANY_MOVE` violation,
   which measures moves against a baseline and is therefore always zero for a day priced against
   itself. Reading both would count a real move twice or a real cost never.
2. **A wrapped day reports its record, not an estimate.** `day_completion` already reports what a
   shot day actually cost; re-deriving meals and holds for it would print a forecast under a
   record's heading. Those terms are withheld by name instead.
3. **What cannot be priced is named, never zeroed.** A performer on hold with no contracted day rate
   appears in `not_priced` by name. A zero there would quietly shrink the total, and the total is
   the whole point.

The pickup day is deliberately absent: it is synthesized inside a multi-day plan a producer has to
ask for, and is never persisted, so a day page that charged for it would be pricing a decision
nobody has made.
"""

from __future__ import annotations

from typing import Any

from ..domain.breakdown_models import CastDOODEntry
from ..domain.enums import ConstraintKind, ShootDayStatus
from ..domain.models import Project, ShootDay
from .completion import day_completion
from .recovery import next_day_call
from .schedule import ValidationContext, count_company_moves, validate_schedule

# The one place each term is allowed to come from. Read as documentation and enforced by the tests.
SOURCES: dict[str, str] = {
    "overtime": "soft OVERTIME violations (raw overtime and golden-time surcharge, both real)",
    "meal": "soft MEAL_BREAK violation, priced by the labor pack against the day's meal count",
    "turnaround": "soft TURNAROUND violation — the forced-call penalty, when the pack charges one",
    "carry_over": "soft SCENE_DEFERRED violations, one per scene carried off this day",
    "rerental": "soft EQUIPMENT_RERENTAL violations, for kit booked only for this day",
    "company_moves": "count_company_moves() × ShootDay.company_move_cost — the day's own schedule",
    "cast_holds": "DOOD 'H' days × each performer's contracted day rate",
}

# Violation kind → the line it belongs to. Kinds absent here carry no money, or carry it somewhere
# else: EXTRA_COMPANY_MOVE is baseline-relative (see rule 1) and the hard kinds price nothing.
_KIND_LINES: dict[ConstraintKind, tuple[str, str]] = {
    ConstraintKind.OVERTIME: ("overtime", "Overtime and golden time"),
    ConstraintKind.MEAL_BREAK: ("meal", "Meal penalty"),
    ConstraintKind.TURNAROUND: ("turnaround", "Forced call (short turnaround)"),
    ConstraintKind.SCENE_DEFERRED: ("carry_over", "Scenes carried to another day"),
    ConstraintKind.EQUIPMENT_RERENTAL: ("rerental", "Equipment re-rental"),
}


def _hold_cost(day: ShootDay, entries: list[CastDOODEntry]) -> tuple[int, list[str], int]:
    """Cost of the performers this day holds: (priced total, names with no rate, held count)."""
    total, unpriced, held = 0, [], 0
    for e in entries:
        if e.day_status.get(day.id) != "H":
            continue
        held += 1
        if e.day_rate_inr:
            total += e.day_rate_inr
        else:
            unpriced.append(e.name)
    return total, unpriced, held


def day_cost(
    project: Project,
    day: ShootDay,
    *,
    deferred_scene_ids: list[str] | None = None,
    dood_entries: list[CastDOODEntry] | None = None,
) -> dict[str, Any]:
    """One day's consequence cost, itemised, with everything it refuses to price named.

    `basis` says which kind of number this is: `record` for a wrapped day (what it cost), `projected`
    for a day still ahead (what it would cost as currently scheduled, under the pack in force).
    """
    if dood_entries is None:
        from ..ingestion.dood import build_dood_matrix

        dood_entries = build_dood_matrix(project)

    lines: list[dict[str, Any]] = []
    not_priced: list[dict[str, str]] = []
    hold_total, hold_unpriced, held = _hold_cost(day, dood_entries)

    def add(key: str, label: str, cost: int, *, minutes: int = 0, detail: str = "") -> None:
        if cost:
            lines.append({"key": key, "label": label, "cost_inr": cost, "minutes": minutes, "detail": detail})

    if day.status == ShootDayStatus.WRAPPED:
        record = day_completion(project, day)
        if record is None:  # wrapped with nothing on it — no day to price
            return {"basis": "record", "labor_pack": None, "lines": [], "total_inr": 0, "not_priced": [], "currency": "INR"}
        add("overtime", "Overtime", record["overtime_cost_inr"], minutes=record["overtime_minutes"], detail=f"{record['overtime_minutes']} min past the {day.standard_hours:g} h call")
        add("carry_over", "Scenes carried to another day", record["carry_over_cost_inr"], detail=f"{len(record['scenes_carried'])} scene(s) outstanding at wrap")
        # A hold day is a contracted fact whether or not the day has been shot, so it still prices.
        add("cast_holds", "Cast held (not working)", hold_total, detail=f"{held} performer(s) on hold")
        not_priced.append({
            "key": "as_shot",
            "reason": "Meal penalties, company moves and re-rentals are not re-estimated for a day that is already shot — "
                      "the day's report is a record, and an estimate printed under it would read as one.",
        })
        basis, pack_name = "record", None
    else:
        ctx = ValidationContext(
            project=project,
            day=day,
            baseline_items=day.items,
            deferred_scene_ids=deferred_scene_ids,
            # Without this the turnaround rule silently never fires, and the card would report a day
            # as costing nothing in rest penalties because it never measured them.
            next_day_call=next_day_call(project, day),
        )
        violations = validate_schedule(ctx, day.items)
        pack_name = ctx.pack.name

        grouped: dict[str, dict[str, Any]] = {}
        for v in violations:
            if v.hard or v.kind not in _KIND_LINES or not v.cost_inr:
                continue
            key, label = _KIND_LINES[v.kind]
            row = grouped.setdefault(key, {"label": label, "cost": 0, "minutes": 0, "details": []})
            row["cost"] += v.cost_inr
            row["minutes"] += v.minutes or 0
            row["details"].append(v.message)
        for key, row in grouped.items():
            add(key, row["label"], row["cost"], minutes=row["minutes"], detail="; ".join(row["details"]))

        moves = count_company_moves(project, day.items)
        add("company_moves", "Company moves", moves * day.company_move_cost, detail=f"{moves} move(s) between locations on this day's schedule")
        add("cast_holds", "Cast held (not working)", hold_total, detail=f"{held} performer(s) on hold between their own calls")

        if ctx.next_day_call is None:
            not_priced.append({"key": "turnaround", "reason": "No later shoot day is on the schedule, so there is no next call to measure rest against."})
        basis = "projected"

    if hold_unpriced:
        not_priced.append({
            "key": "cast_holds_unpriced",
            "reason": f"{', '.join(sorted(hold_unpriced))} {'is' if len(hold_unpriced) == 1 else 'are'} on hold with no "
                      "contracted day rate on file, so the retention is left out of the total rather than counted as zero.",
        })
    not_priced.append({
        "key": "pickup_day",
        "reason": "A pickup day is only ever synthesized inside a multi-day plan a producer asks for, and is never "
                  "committed, so it is not charged here.",
    })

    return {
        "basis": basis,
        "labor_pack": pack_name,
        "lines": lines,
        "total_inr": sum(line["cost_inr"] for line in lines),
        "not_priced": not_priced,
        "currency": "INR",
    }


def production_cost_strip(project: Project, deferred_by_day: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Every day's consequence cost and the production's total. Deterministic; reads state only."""
    from ..ingestion.dood import build_dood_matrix

    entries = build_dood_matrix(project)  # once for the whole production, not once per day
    deferred_by_day = deferred_by_day or {}
    days = []
    for day in sorted(project.shoot_days, key=lambda d: (d.day_number, d.date)):
        card = day_cost(project, day, deferred_scene_ids=deferred_by_day.get(day.id), dood_entries=entries)
        days.append({
            "shoot_day_id": day.id,
            "day_number": day.day_number,
            "date": day.date,
            "status": day.status.value,
            **card,
        })
    return {
        "days": days,
        "total_inr": sum(d["total_inr"] for d in days),
        "currency": "INR",
        # Deduplicated across days: the same performer with no rate would otherwise be named six times.
        "unpriced_notes": sorted({n["reason"] for d in days for n in d["not_priced"]}),
    }
