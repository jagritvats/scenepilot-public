"""Multi-day cascading ripple solver for deferred scenes.

When a disruption forces scenes to be postponed from hero Day 4, this solver
evaluates downstream shoot days (Day 5, Day 6, etc.) to place them without
cascading overtime, labor violations, or actor booking conflicts.

If all future days are saturated, it synthesizes a dedicated Pickup Unit Day
with full cost and labor accounting.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from pydantic import BaseModel, Field

from ..domain.models import LocationFact, Project, Scene, ScheduleItem, ShootDay
from .ephemeris import apply_solar_windows
from .schedule import ValidationContext, overtime_minutes, pack_day, validate_schedule
from .timeutil import to_hhmm, to_minutes


class DownstreamDayPlacement(BaseModel):
    shoot_day_id: str
    day_number: int
    date: str
    scene_id: str
    scene_number: str
    scheduled_start: str
    scheduled_end: str
    added_overtime_minutes: int = 0
    added_cost_inr: int = 0
    feasible: bool = True
    notes: list[str] = Field(default_factory=list)


class CastRetentionProjection(BaseModel):
    """What placing a deferred scene downstream would cost in cast the production has to keep.

    A projection, and labelled as one everywhere it renders: nothing here has been approved, so the
    hold days below do not exist yet. They are what the DOOD *would* say if the placements above
    were committed — which is exactly the number a producer wants before committing them, because it
    is the half of the ripple cost the schedule itself does not show. A performer who finishes on
    Day 4 today and is wanted again on Day 6 is not free on Day 5: the production is paying to keep
    them, and no line on any board says so.
    """

    cast_id: str
    cast_number: int | None = None
    name: str
    hold_days_added: int
    day_rate_inr: int | None = None
    added_cost_inr: int | None = None
    reason: str = ""


class MultiDayRipplePlan(BaseModel):
    recovery_option_id: str
    deferred_scene_ids: list[str]
    placements: list[DownstreamDayPlacement] = Field(default_factory=list)
    synthesized_pickup_day: ShootDay | None = None
    total_ripple_cost_inr: int = 0
    # The cast half of the ripple, projected from the placements above. Empty when placing the
    # scenes moves nobody's engagement — which is a real answer and the common one.
    cast_retention: list[CastRetentionProjection] = Field(default_factory=list)
    cast_retention_cost_inr: int | None = None
    summary: str = ""


def _can_accommodate(
    project: Project,
    day: ShootDay,
    scene: Scene,
    location_facts: list[LocationFact] | None = None,
) -> tuple[bool, int, str, str, list[str]]:
    """Test whether a downstream shoot day can take an extra scene — against the real validator.

    This used to answer the question itself, with three checks of its own, and got it wrong in the
    three ways a hand-rolled scheduler always does:

    * **No day/night.** Nothing compared `scene.time_of_day` to the day's own `day_window`, so the
      hero rescue's deferred `EXT. MARKET STREET — DAY` was placed on Day 5 at **22:00–24:30** — a
      daylight exterior scheduled on a night unit, printed to the producer in green as FEASIBLE.
    * **No location booking.** Bhuleshwar is permitted 13:00–18:00 on Day 4 and holds no window on
      any other day, so the placement above put a unit on a street it has no permit for.
    * **A cast check that could not fail.** It only rejected a zero-length availability row, so a
      performer booked 18:00–28:00 passed for an 06:00 call, and a performer with no row for that
      day at all — which `availability_windows` reads as unavailable — passed too.

    The fix is not better checks here; it is not having checks here. `validate_schedule` is the one
    engine that decides whether a board is shootable, it is what every recovery option is ranked by,
    and it already knows about day windows, permit windows, turnaround, meals and accepted Parallel
    facts. A downstream placement is a board like any other, so it now gets the same answer — which
    also means a constraint added there can never again be missing from here.
    """
    ordered = sorted(day.items, key=lambda i: to_minutes(i.start))
    if not ordered:
        start_min = to_minutes(day.unit_call)
    else:
        last_end = max(to_minutes(i.end) for i in ordered)
        # 30 min company move / reset buffer
        start_min = last_end + 30

    end_min = start_min + scene.estimated_minutes
    placed = ScheduleItem(
        id=f"projected_{scene.id}",
        scene_id=scene.id,
        location_id=scene.location_id,
        start=to_hhmm(start_min),
        end=to_hhmm(end_min),
        unit="MAIN",
    )
    proposed = [*(i.model_copy(deep=True) for i in ordered), placed]
    ctx = ValidationContext(project=project, day=day, location_facts=location_facts)
    violations = validate_schedule(ctx, proposed)

    # Only what this scene is responsible for. A day that already breaks a rule on its own committed
    # schedule is not made infeasible by being asked to take another scene — that violation was
    # there before, and reporting it here would blame this placement for it.
    mine = [v for v in violations if v.scene_id == scene.id or v.item_id == placed.id]
    blocking = [v for v in mine if v.hard]
    if blocking:
        return False, 0, "", "", [v.message for v in blocking]

    std_wrap_min = to_minutes(day.unit_call) + int(day.standard_hours * 60)
    ot = max(0, end_min - std_wrap_min)
    ot_cost = round(ot / 60.0 * day.overtime_rate_per_hour) if ot > 0 else 0

    notes: list[str] = []
    if ot > 0:
        notes.append(f"Requires {ot} min overtime on Day {day.day_number} (≈₹{ot_cost:,})")
    else:
        notes.append(f"Fits within standard {int(day.standard_hours)}h shift on Day {day.day_number}")
    # Soft violations are not blocking and are still worth reading before committing to a placement.
    notes.extend(v.message for v in mine if not v.hard)

    return True, ot_cost, to_hhmm(start_min), to_hhmm(end_min), notes


def resolve_deferred_scenes_multiday(
    project: Project,
    source_day_id: str,
    deferred_scene_ids: list[str],
    option_id: str = "opt_recovery",
    location_facts: list[LocationFact] | None = None,
) -> MultiDayRipplePlan:
    """Resolve deferred scenes across downstream shoot days or synthesize a pickup day.

    `location_facts` are the accepted, machine-checkable constraints Parallel discovered — a noise
    curfew, a drone ban. Passed through to the validator so a downstream placement is held to the
    same external rules the board is: a scene pushed onto a night that a curfew closes is not a
    placement, it is a violation nobody checked.
    """
    if not deferred_scene_ids:
        return MultiDayRipplePlan(
            recovery_option_id=option_id,
            deferred_scene_ids=[],
            summary="No scenes deferred; zero downstream ripple.",
        )

    try:
        source_day = project.shoot_day(source_day_id)
        source_date = date.fromisoformat(source_day.date)
    except Exception:
        source_day = None
        source_date = date(2026, 9, 4)

    # Downstream days sorted chronologically
    downstream_days = sorted(
        [d for d in project.shoot_days if d.date > (source_day.date if source_day else "")],
        key=lambda d: (d.day_number, d.date),
    )

    placements: list[DownstreamDayPlacement] = []
    unplaced_scene_ids: list[str] = []
    total_cost = 0

    for sid in deferred_scene_ids:
        try:
            scene = project.scene(sid)
        except KeyError:
            continue

        placed = False
        # Try fitting into downstream days
        for candidate_day in downstream_days:
            fits, extra_cost, start_hhmm, end_hhmm, notes = _can_accommodate(project, candidate_day, scene, location_facts)
            if fits:
                placements.append(
                    DownstreamDayPlacement(
                        shoot_day_id=candidate_day.id,
                        day_number=candidate_day.day_number,
                        date=candidate_day.date,
                        scene_id=scene.id,
                        scene_number=scene.number,
                        scheduled_start=start_hhmm,
                        scheduled_end=end_hhmm,
                        added_overtime_minutes=max(0, to_minutes(end_hhmm) - (to_minutes(candidate_day.unit_call) + int(candidate_day.standard_hours * 60))),
                        added_cost_inr=extra_cost,
                        feasible=True,
                        notes=notes,
                    )
                )
                total_cost += extra_cost
                placed = True
                break

        if not placed:
            unplaced_scene_ids.append(sid)

    synthesized_day: ShootDay | None = None
    if unplaced_scene_ids:
        # Synthesize a dedicated Pickup Unit Shoot Day
        last_day_num = max([d.day_number for d in project.shoot_days] or [4])
        next_day_num = last_day_num + 1

        if downstream_days:
            last_date = max(date.fromisoformat(d.date) for d in downstream_days)
        else:
            last_date = source_date
        pickup_date = (last_date + timedelta(days=2)).isoformat()

        pickup_items = []
        cur_min = 8 * 60  # 08:00 call
        for uid in unplaced_scene_ids:
            try:
                sc = project.scene(uid)
                dur = sc.estimated_minutes
                it = ScheduleItem(
                    id=f"item_pickup_{sc.number}",
                    scene_id=sc.id,
                    start=to_hhmm(cur_min),
                    end=to_hhmm(cur_min + dur),
                    unit="MAIN",
                )
                pickup_items.append(it)
                cur_min += dur + 30
            except KeyError:
                continue

        synthesized_day = ShootDay(
            id=f"day_{next_day_num}_pickup",
            project_id=project.id,
            day_number=next_day_num,
            date=pickup_date,
            unit_call="08:00",
            standard_hours=10.0,
            hard_wrap="20:00",
            crew_size=25,
            overtime_rate_per_hour=8500,
            items=pickup_items,
        )
        # A synthesized day gets the sun of the date it is synthesized on, not a window typed here.
        apply_solar_windows(synthesized_day, project.base_city)
        pickup_cost = source_day.pickup_day_cost if source_day else ShootDay.model_fields["pickup_day_cost"].default
        total_cost += pickup_cost

    retention = _project_cast_retention(project, placements)
    retention_cost = sum(r.added_cost_inr for r in retention if r.added_cost_inr) or None

    summary_parts = []
    if placements:
        summary_parts.append(
            f"{len(placements)} scene(s) absorbed into downstream shoot days ({', '.join(f'Sc {p.scene_number} → Day {p.day_number}' for p in placements)})."
        )
    if synthesized_day:
        summary_parts.append(
            f"Downstream days saturated: synthesized dedicated Day {synthesized_day.day_number} Pickup Unit on {synthesized_day.date} (est. ₹{pickup_cost:,} at this production's pickup-day rate)."
        )
    if retention_cost:
        who = ", ".join(f"{r.name.split(' (')[0]} +{r.hold_days_added}" for r in retention if r.added_cost_inr)
        summary_parts.append(
            f"Placing them would also hold cast the production would otherwise have released — {who} — "
            f"≈₹{retention_cost:,} in retention that no line on the board shows."
        )

    return MultiDayRipplePlan(
        recovery_option_id=option_id,
        deferred_scene_ids=deferred_scene_ids,
        placements=placements,
        synthesized_pickup_day=synthesized_day,
        total_ripple_cost_inr=total_cost,
        cast_retention=retention,
        cast_retention_cost_inr=retention_cost,
        summary=" ".join(summary_parts),
    )


def _project_cast_retention(project: Project, placements: list[DownstreamDayPlacement]) -> list[CastRetentionProjection]:
    """Run the DOOD against the proposed placements and report whose engagement gets longer.

    Deliberately a *projection*: it overlays the placements on a copy of the schedule and compares
    the two matrices, without touching committed state. A scene moved to a later day extends the
    span between a performer's first and last call, and every day inside that span the production
    does not shoot them is a day it pays for anyway — which is why the ripple's rupee figure is
    incomplete without it.

    Only extensions are reported. A placement that shortens somebody's engagement is a saving, not a
    cost, and this is the cost half of the ripple; the DOOD itself carries the whole picture.
    """
    from ..ingestion.dood import build_dood_matrix

    if not placements:
        return []
    overrides: dict[str, list[ScheduleItem]] = {}
    for placement in placements:
        day = next((d for d in project.shoot_days if d.id == placement.shoot_day_id), None)
        if day is None:
            continue
        items = overrides.setdefault(placement.shoot_day_id, [i.model_copy(deep=True) for i in day.items])
        items.append(ScheduleItem(
            id=f"projected_{placement.scene_id}",
            scene_id=placement.scene_id,
            start=placement.scheduled_start,
            end=placement.scheduled_end,
            unit="MAIN",
        ))

    before = {e.cast_id: e for e in build_dood_matrix(project)}
    after = {e.cast_id: e for e in build_dood_matrix(project, overrides=overrides)}

    projections: list[CastRetentionProjection] = []
    for cast_id, projected in after.items():
        current = before.get(cast_id)
        if current is None:
            continue
        added = projected.total_hold_days - current.total_hold_days
        if added <= 0:
            continue
        rate = projected.day_rate_inr
        days = ", ".join(f"Day {d.day_number}" for d in sorted(project.shoot_days, key=lambda x: x.day_number)
                         if projected.day_status.get(d.id) == "H" and current.day_status.get(d.id) != "H")
        projections.append(CastRetentionProjection(
            cast_id=cast_id,
            cast_number=projected.cast_number,
            name=projected.name,
            hold_days_added=added,
            day_rate_inr=rate,
            added_cost_inr=added * rate if rate else None,
            reason=(
                f"Placing the deferred scene extends this performer's engagement, so {days} becomes a paid hold."
                if rate else
                f"Placing the deferred scene makes {days} a paid hold. The production states no day rate for this "
                "performer, so the hold is counted and not priced."
            ),
        ))
    return sorted(projections, key=lambda r: (r.added_cost_inr is None, -(r.added_cost_inr or 0)))
