"""Deterministic schedule logic: availability, constraint validation and day packing.

Gemini never decides whether two intervals overlap or whether an actor is available —
this module does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..domain.enums import ConstraintKind, IntExt, ResourceType, TimeOfDay
from ..domain.models import (
    ConstraintViolation,
    Disruption,
    LocationFact,
    Project,
    Resource,
    ScheduleItem,
    Scene,
    ShootDay,
)
from .labor_rules import LaborRulePack, active_pack
from .timeutil import overlap_minutes, overlaps, to_hhmm, to_minutes

TURNAROUND_MINUTES = 30  # minimum reset between consecutive scenes at the same location
FIRST_SHOT_OFFSET = 30  # minutes after unit call before first shot
LIGHTING_HARD_TOLERANCE = 30  # minutes outside usable light before it's a hard violation
GOLDEN_HOUR_MIN_OVERLAP_HARD = 30
GOLDEN_HOUR_MIN_OVERLAP_SOFT = 60

# The meal and turnaround norms used to live here as constants *as well as* in the rule packs, and
# the two disagreed: the day page named the active pack's 12 h turnaround and ±15 min lunch window
# while every recovery option a producer could actually approve was validated against a loose 10 h
# and ±90 min, because `ValidationContext` was built without a pack and fell through to these. There
# is now one implementation and it always names the pack it applied — see `ValidationContext.pack`.


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


def availability_windows(resource: Resource, day: ShootDay) -> list[tuple[int, int]]:
    """Availability windows (minutes) of a resource on a given shoot day.

    A resource with no availability entries is treated as available all day.
    """
    if not resource.availability:
        return [(0, 48 * 60)]
    windows: list[tuple[int, int]] = []
    for a in resource.availability:
        if a.shoot_day_id is not None and a.shoot_day_id != day.id:
            continue
        if a.date is not None and a.date != day.date:
            continue
        windows.append((to_minutes(a.start), to_minutes(a.end)))
    return windows


def is_available(resource: Resource, day: ShootDay, start: int, end: int) -> bool:
    return any(ws <= start and end <= we for ws, we in availability_windows(resource, day))


def available_on_other_days(resource: Resource, day: ShootDay, project: Project | None = None) -> bool:
    """True if the resource is still available on a shoot day a scene could be carried into.

    Carry-over only ever pushes a scene forwards, so a booking that has already been used is not one
    the scene can land on: a drone chartered for the Day-3 dawn plate does not make Scene 42 cheaper
    to move off Day 4. With `project` given, only days dated after this one count; without it the
    older "any other day" reading is kept, so callers that have no project stay put.
    """
    if not resource.availability:
        return True
    later: set[str] | None = None
    later_dates: set[str] | None = None
    if project is not None:
        ahead = [d for d in project.shoot_days if d.date > day.date]
        later, later_dates = {d.id for d in ahead}, {d.date for d in ahead}
    for a in resource.availability:
        if a.shoot_day_id is None and a.date is None:
            return True
        if a.shoot_day_id is not None and a.shoot_day_id != day.id and (later is None or a.shoot_day_id in later):
            return True
        if a.date is not None and a.date != day.date and (later_dates is None or a.date in later_dates):
            return True
    return False


# --------------------------------------------------------------------------- #
# Exposure to a disruption
# --------------------------------------------------------------------------- #


def disruption_window(disruption: Disruption | None) -> tuple[int, int] | None:
    if disruption is None or not disruption.window_start or not disruption.window_end:
        return None
    return to_minutes(disruption.window_start), to_minutes(disruption.window_end) + disruption.dry_out_minutes


def scene_exposed(project: Project, scene: Scene, disruption: Disruption | None) -> tuple[bool, str]:
    """Is this scene exposed to the disruption at all (independent of timing)?"""
    if disruption is None:
        return False, ""
    if scene.location_id and scene.location_id in disruption.affects_location_ids:
        return True, f"location {project.resource(scene.location_id).name} affected"
    hit = [r for r in scene.cast_ids + scene.equipment_ids if r in disruption.affects_resource_ids]
    if hit:
        names = ", ".join(project.resource(r).name for r in hit)
        return True, f"required resource unavailable: {names}"
    if disruption.affects_exteriors and scene.int_ext == IntExt.EXT and not scene.rain_tolerant:
        return True, "exterior scene, not rain-tolerant"
    weather_eq = [
        e for e in scene.equipment_ids if project.resource(e).weather_sensitive and disruption.affects_exteriors
    ]
    if weather_eq and scene.int_ext == IntExt.EXT:
        names = ", ".join(project.resource(e).name for e in weather_eq)
        return True, f"weather-sensitive equipment: {names}"
    return False, ""


# --------------------------------------------------------------------------- #
# Time-of-day compatibility
# --------------------------------------------------------------------------- #


def _window(day: ShootDay, pair: tuple[str, str]) -> tuple[int, int]:
    return to_minutes(pair[0]), to_minutes(pair[1])


def lighting_check(day: ShootDay, scene: Scene, start: int, end: int) -> ConstraintViolation | None:
    dur = end - start
    if scene.time_of_day == TimeOfDay.ANY or dur <= 0:
        return None
    if scene.time_of_day == TimeOfDay.DAY:
        ws, we = _window(day, day.day_window)
        outside = dur - overlap_minutes(start, end, ws, we)
        if outside > LIGHTING_HARD_TOLERANCE:
            return ConstraintViolation(
                kind=ConstraintKind.TIME_OF_DAY_INCOMPATIBLE,
                hard=True,
                message=f"DAY scene {scene.number} has {outside} min outside usable daylight ({day.day_window[0]}–{day.day_window[1]})",
                scene_id=scene.id,
                minutes=outside,
            )
        if outside > 0:
            return ConstraintViolation(
                kind=ConstraintKind.LIGHTING_COMPROMISE,
                hard=False,
                message=f"DAY scene {scene.number} runs {outside} min past usable daylight",
                scene_id=scene.id,
                minutes=outside,
            )
        return None
    if scene.time_of_day == TimeOfDay.NIGHT:
        ws, we = _window(day, day.night_window)
        outside = dur - overlap_minutes(start, end, ws, we)
        if outside > LIGHTING_HARD_TOLERANCE:
            return ConstraintViolation(
                kind=ConstraintKind.TIME_OF_DAY_INCOMPATIBLE,
                hard=True,
                message=f"NIGHT scene {scene.number} has {outside} min before darkness ({day.night_window[0]})",
                scene_id=scene.id,
                minutes=outside,
            )
        if outside > 0:
            return ConstraintViolation(
                kind=ConstraintKind.LIGHTING_COMPROMISE,
                hard=False,
                message=f"NIGHT scene {scene.number} starts {outside} min before full darkness",
                scene_id=scene.id,
                minutes=outside,
            )
        return None
    golden = day.golden_hour_dusk if scene.time_of_day == TimeOfDay.SUNSET else day.golden_hour_dawn
    gs, ge = _window(day, golden)
    ov = overlap_minutes(start, end, gs, ge)
    # Never ask a scene for more golden hour than the day has. These two constants were calibrated
    # against a 90-minute hardcoded window; a real one at 19°N is about 43 minutes, so an uncapped
    # 60-minute soft requirement is unsatisfiable — every sunset scene in Mumbai would carry a
    # permanent lighting compromise for missing time that never existed.
    available = max(0, ge - gs)
    need_hard = min(dur, GOLDEN_HOUR_MIN_OVERLAP_HARD, available)
    need_soft = min(dur, GOLDEN_HOUR_MIN_OVERLAP_SOFT, available)
    label = scene.time_of_day.value
    if ov < need_hard:
        return ConstraintViolation(
            kind=ConstraintKind.TIME_OF_DAY_INCOMPATIBLE,
            hard=True,
            message=f"{label} scene {scene.number} overlaps golden hour ({golden[0]}–{golden[1]}) by only {ov} min",
            scene_id=scene.id,
            minutes=need_hard - ov,
        )
    if ov < need_soft:
        return ConstraintViolation(
            kind=ConstraintKind.LIGHTING_COMPROMISE,
            hard=False,
            message=f"{label} scene {scene.number} gets {ov} min of golden hour instead of {need_soft}",
            scene_id=scene.id,
            minutes=need_soft - ov,
        )
    return None


def external_rule_check(project: Project, facts: list[LocationFact], scene: Scene, loc: Resource, start: int, end: int) -> list[ConstraintViolation]:
    """Hard violations from rules Parallel discovered and a producer accepted.

    Only two shapes are checkable, by design (see services/dossier.py): a ban on working at the
    location during a window, and a ban on an activity there. Each violation carries the fact id and
    the citation URL so the rejection can be traced back to the page it came from.
    """
    out: list[ConstraintViolation] = []
    for fact in facts:
        rule = fact.rule
        if rule is None:
            continue
        url = fact.citations[0].url if fact.citations else None
        if rule.kind == "TIME_WINDOW_BAN" and rule.window_start and rule.window_end:
            ws, we = to_minutes(rule.window_start), to_minutes(rule.window_end)
            # Tile the ban across the frame instead of cutting it at midnight.
            #
            # A shoot day is measured in minutes from its own 00:00 and legitimately runs past 24:00
            # — Day 6's night unit hard-wraps at 28:00. The previous form split a wrapping rule into
            # `(ws, 24:00)`, `(00:00, we)` and `(ws+24h, 48:00)`, which leaves **24:00 to we+24h
            # uncovered**: for 22:00–06:00 nothing at all guarded 24:00–30:00. A night strip nudged
            # past midnight was therefore judged legal — Sc 58 at 24:00–26:30 scored zero minutes
            # inside a curfew it sits wholly within, and a board committed through `commit_board`
            # was accepted. That is the exact rule the demo's climax turns on.
            #
            # Extending the end past midnight instead of truncating it keeps each period one
            # contiguous interval, and repeating it at ±24h covers a scene that starts before the
            # day's own midnight or after it. A non-wrapping rule is the same expression with
            # `end == we`, so both shapes go through one branch.
            period = 24 * 60
            rule_end = we if we > ws else we + period
            windows = [(ws + k * period, rule_end + k * period) for k in (-1, 0, 1)]
            minutes = sum(overlap_minutes(start, end, a, b) for a, b in windows)
            if minutes > 0:
                out.append(ConstraintViolation(
                    kind=ConstraintKind.EXTERNAL_RULE, hard=True,
                    message=f"Scene {scene.number} runs {minutes} min inside the {fact.label.lower()} at {loc.name} ({fact.value})",
                    scene_id=scene.id, resource_id=loc.id, minutes=minutes, fact_id=fact.id, evidence_url=url))
        elif rule.kind == "ACTIVITY_BAN" and rule.activity:
            from .dossier import resource_matches_activity

            for eid in scene.equipment_ids:
                eq = project.resource(eid)
                if resource_matches_activity(eq, rule.activity):
                    out.append(ConstraintViolation(
                        kind=ConstraintKind.EXTERNAL_RULE, hard=True,
                        message=f"{eq.name} cannot be used for Scene {scene.number} at {loc.name} — {fact.value}",
                        scene_id=scene.id, resource_id=eid, fact_id=fact.id, evidence_url=url))
    return out


def preferred_start(day: ShootDay, scene: Scene) -> int:
    """Earliest sensible start for a scene given its time-of-day requirement."""
    call = to_minutes(day.unit_call) + FIRST_SHOT_OFFSET
    if scene.time_of_day == TimeOfDay.DAY:
        return max(call, to_minutes(day.day_window[0]))
    if scene.time_of_day == TimeOfDay.NIGHT:
        return max(call, to_minutes(day.night_window[0]))
    if scene.time_of_day == TimeOfDay.SUNSET:
        gs, ge = _window(day, day.golden_hour_dusk)
        return max(call, ge - scene.estimated_minutes, gs - max(0, scene.estimated_minutes - (ge - gs)))
    if scene.time_of_day == TimeOfDay.DAWN:
        gs, _ = _window(day, day.golden_hour_dawn)
        return max(call, gs)
    return call


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


@dataclass
class ValidationContext:
    project: Project
    day: ShootDay
    disruption: Disruption | None = None
    baseline_items: list[ScheduleItem] | None = None
    deferred_scene_ids: list[str] | None = None
    next_day_call: int | None = None  # next shoot day's unit call, minutes from this day's midnight (e.g. 06:30 tomorrow = 1830)
    location_facts: list[LocationFact] | None = None  # accepted, machine-checkable facts Parallel discovered
    # An *override* — "price this board as if it were a DGA/SAG unit" — not a switch between two
    # rule sets. Left unset, `pack` resolves to the production's own agreement, so a caller cannot
    # get a different answer by forgetting to pass one.
    labor_pack: LaborRulePack | None = None

    @property
    def pack(self) -> LaborRulePack:
        return self.labor_pack or active_pack(self.project)


def count_company_moves(project: Project, items: list[ScheduleItem]) -> int:
    ordered = sorted(items, key=lambda i: to_minutes(i.start))
    moves = 0
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.location_id != nxt.location_id:
            moves += 1
    return moves


def overtime_minutes(day: ShootDay, items: list[ScheduleItem]) -> int:
    if not items:
        return 0
    wrap_target = to_minutes(day.unit_call) + int(day.standard_hours * 60)
    last_end = max(to_minutes(i.end) for i in items)
    return max(0, last_end - wrap_target)


def validate_schedule(ctx: ValidationContext, items: list[ScheduleItem]) -> list[ConstraintViolation]:
    """Return every hard and soft constraint violation for a proposed day schedule."""
    project, day = ctx.project, ctx.day
    v: list[ConstraintViolation] = []
    ordered = sorted(items, key=lambda i: to_minutes(i.start))
    call = to_minutes(day.unit_call)
    hard_wrap = to_minutes(day.hard_wrap)
    dwin = disruption_window(ctx.disruption)

    for item in ordered:
        scene = project.scene(item.scene_id)
        s, e = to_minutes(item.start), to_minutes(item.end)
        if e <= s:
            v.append(ConstraintViolation(kind=ConstraintKind.DAY_BOUNDS, hard=True, message=f"Scene {scene.number} has zero/negative duration", item_id=item.id, scene_id=scene.id))
            continue
        if s < call:
            v.append(ConstraintViolation(kind=ConstraintKind.DAY_BOUNDS, hard=True, message=f"Scene {scene.number} starts {to_hhmm(s)} before unit call {day.unit_call}", item_id=item.id, scene_id=scene.id, minutes=call - s))
        if e > hard_wrap:
            v.append(ConstraintViolation(kind=ConstraintKind.DAY_BOUNDS, hard=True, message=f"Scene {scene.number} ends {to_hhmm(e)} after hard wrap {day.hard_wrap}", item_id=item.id, scene_id=scene.id, minutes=e - hard_wrap))

        # Cast
        for cid in scene.cast_ids:
            r = project.resource(cid)
            if not is_available(r, day, s, e):
                wins = ", ".join(f"{to_hhmm(a)}–{to_hhmm(b)}" for a, b in availability_windows(r, day)) or "not on this day"
                v.append(ConstraintViolation(kind=ConstraintKind.CAST_UNAVAILABLE, hard=True, message=f"{r.name} unavailable for Scene {scene.number} at {item.start}–{item.end} (available {wins})", item_id=item.id, scene_id=scene.id, resource_id=cid))
        # Location
        loc_id = item.location_id or scene.location_id
        if loc_id:
            loc = project.resource(loc_id)
            if not is_available(loc, day, s, e):
                wins = ", ".join(f"{to_hhmm(a)}–{to_hhmm(b)}" for a, b in availability_windows(loc, day)) or "not on this day"
                v.append(ConstraintViolation(kind=ConstraintKind.LOCATION_UNAVAILABLE, hard=True, message=f"{loc.name} not available for Scene {scene.number} at {item.start}–{item.end} (window {wins})", item_id=item.id, scene_id=scene.id, resource_id=loc_id))
            for ev in external_rule_check(project, [f for f in (ctx.location_facts or []) if f.resource_id == loc_id], scene, loc, s, e):
                ev.item_id = item.id
                v.append(ev)
        # Equipment
        for eid in scene.equipment_ids:
            r = project.resource(eid)
            if not is_available(r, day, s, e):
                wins = ", ".join(f"{to_hhmm(a)}–{to_hhmm(b)}" for a, b in availability_windows(r, day)) or "not on this day"
                v.append(ConstraintViolation(kind=ConstraintKind.EQUIPMENT_UNAVAILABLE, hard=True, message=f"{r.name} unavailable for Scene {scene.number} at {item.start}–{item.end} (available {wins})", item_id=item.id, scene_id=scene.id, resource_id=eid))
        # Lighting / time of day
        lc = lighting_check(day, scene, s, e)
        if lc:
            lc.item_id = item.id
            v.append(lc)
        # Disruption exposure
        if dwin and ctx.disruption is not None:
            exposed, why = scene_exposed(project, scene, ctx.disruption)
            if exposed and overlaps(s, e, dwin[0], dwin[1]):
                ov = overlap_minutes(s, e, dwin[0], dwin[1])
                weather_eq = [x for x in scene.equipment_ids if project.resource(x).weather_sensitive]
                kind = ConstraintKind.WEATHER_SENSITIVE_EQUIPMENT if (weather_eq and "equipment" in why) else ConstraintKind.DISRUPTION_EXPOSURE
                v.append(ConstraintViolation(kind=kind, hard=True, message=f"Scene {scene.number} overlaps {ctx.disruption.title.lower()} window by {ov} min ({why})", item_id=item.id, scene_id=scene.id, minutes=ov))

    # Pairwise overlap + travel
    for prev, nxt in zip(ordered, ordered[1:]):
        ps, pe = to_minutes(prev.start), to_minutes(prev.end)
        ns, ne = to_minutes(nxt.start), to_minutes(nxt.end)
        p_scene, n_scene = project.scene(prev.scene_id), project.scene(nxt.scene_id)
        if overlaps(ps, pe, ns, ne):
            v.append(ConstraintViolation(kind=ConstraintKind.ITEM_OVERLAP, hard=True, message=f"Scene {p_scene.number} ({prev.start}–{prev.end}) overlaps Scene {n_scene.number} ({nxt.start}–{nxt.end})", item_id=nxt.id, scene_id=n_scene.id, minutes=overlap_minutes(ps, pe, ns, ne)))
            continue
        travel = project.travel_minutes(prev.location_id or p_scene.location_id, nxt.location_id or n_scene.location_id)
        gap = ns - pe
        if travel and gap < travel:
            v.append(ConstraintViolation(kind=ConstraintKind.TRAVEL_OVERLAP, hard=True, message=f"Only {gap} min between Scene {p_scene.number} and Scene {n_scene.number}; company move needs {travel} min", item_id=nxt.id, scene_id=n_scene.id, minutes=travel - gap))

    # Multi-unit concurrent resource contention check
    for idx_a, it_a in enumerate(items):
        for it_b in items[idx_a + 1 :]:
            if getattr(it_a, "unit", "MAIN") != getattr(it_b, "unit", "MAIN"):
                sa, ea = to_minutes(it_a.start), to_minutes(it_a.end)
                sb, eb = to_minutes(it_b.start), to_minutes(it_b.end)
                if overlaps(sa, ea, sb, eb):
                    sc_a = project.scene(it_a.scene_id)
                    sc_b = project.scene(it_b.scene_id)
                    shared_cast = set(sc_a.cast_ids) & set(sc_b.cast_ids)
                    shared_eq = set(sc_a.equipment_ids) & set(sc_b.equipment_ids)
                    for cid in shared_cast:
                        r = project.resource(cid)
                        v.append(ConstraintViolation(
                            kind=ConstraintKind.CAST_UNAVAILABLE, hard=True,
                            message=f"{r.name} cannot be on {getattr(it_a, 'unit', 'MAIN')} Unit and {getattr(it_b, 'unit', 'MAIN')} Unit simultaneously ({it_a.start}–{it_a.end} vs {it_b.start}–{it_b.end})",
                            item_id=it_b.id, scene_id=sc_b.id, resource_id=cid,
                        ))
                    for eid in shared_eq:
                        r = project.resource(eid)
                        v.append(ConstraintViolation(
                            kind=ConstraintKind.EQUIPMENT_UNAVAILABLE, hard=True,
                            message=f"{r.name} cannot be booked across {getattr(it_a, 'unit', 'MAIN')} and {getattr(it_b, 'unit', 'MAIN')} units at overlapping times",
                            item_id=it_b.id, scene_id=sc_b.id, resource_id=eid,
                        ))

    # Soft: overtime
    ot = overtime_minutes(day, items)
    if ot > 0:
        cost = math.ceil(ot / 60 * day.overtime_rate_per_hour)
        v.append(ConstraintViolation(kind=ConstraintKind.OVERTIME, hard=False, message=f"{ot} min overtime beyond {to_hhmm(call + int(day.standard_hours * 60))} wrap (≈₹{cost:,})", minutes=ot, cost_inr=cost))

    # Soft: labor rules (meal penalties, turnaround rest, golden time) under the production's pack
    if ordered:
        from .labor_rules import evaluate_golden_time, evaluate_meal_penalties, evaluate_turnaround_rest

        pack = ctx.pack
        cast_count = len({cid for i in ordered for cid in project.scene(i.scene_id).cast_ids})
        meal_cost, meal_msgs = evaluate_meal_penalties(pack, call, ordered, crew_size=day.crew_size, cast_count=cast_count)
        for msg in meal_msgs:
            v.append(ConstraintViolation(kind=ConstraintKind.MEAL_BREAK, hard=False, message=msg, cost_inr=meal_cost, minutes=pack.minimum_lunch_minutes))

        last_end = max(to_minutes(i.end) for i in ordered)
        if ctx.next_day_call is not None:
            turnaround_cost, turnaround_msgs = evaluate_turnaround_rest(pack, last_end, ctx.next_day_call)
            for msg in turnaround_msgs:
                v.append(ConstraintViolation(kind=ConstraintKind.TURNAROUND, hard=False, message=msg, cost_inr=turnaround_cost, minutes=max(0, int(pack.minimum_turnaround_hours * 60) - (ctx.next_day_call - last_end))))

        gt_cost, gt_msgs = evaluate_golden_time(pack, call, last_end, hourly_ot_rate=day.overtime_rate_per_hour)
        for msg in gt_msgs:
            v.append(ConstraintViolation(kind=ConstraintKind.OVERTIME, hard=False, message=msg, cost_inr=gt_cost, minutes=max(0, last_end - (call + int(pack.golden_time_threshold_hours * 60)))))

    # Soft: extra company moves vs baseline
    if ctx.baseline_items is not None:
        base_moves = count_company_moves(project, ctx.baseline_items)
        new_moves = count_company_moves(project, items)
        extra = new_moves - base_moves
        if extra > 0:
            v.append(ConstraintViolation(kind=ConstraintKind.EXTRA_COMPANY_MOVE, hard=False, message=f"{extra} additional company move(s) ({new_moves} vs {base_moves})", cost_inr=extra * day.company_move_cost, minutes=0))

    # Soft: deferred scenes, re-rentals, continuity
    scheduled_scene_ids = {i.scene_id for i in items}
    for sid in ctx.deferred_scene_ids or []:
        scene = project.scene(sid)
        v.append(ConstraintViolation(kind=ConstraintKind.SCENE_DEFERRED, hard=False, message=f"Scene {scene.number} carried over to another day (≈₹{day.carry_over_cost:,})", scene_id=sid, cost_inr=day.carry_over_cost))
        for eid in scene.equipment_ids:
            r = project.resource(eid)
            if not available_on_other_days(r, day, project) and r.rerental_cost:
                v.append(ConstraintViolation(kind=ConstraintKind.EQUIPMENT_RERENTAL, hard=False, message=f"{r.name} is only booked for Day {day.day_number}; carrying Scene {scene.number} needs a re-rental (≈₹{r.rerental_cost:,})", scene_id=sid, resource_id=eid, cost_inr=r.rerental_cost))
        if scene.continuity_group:
            siblings = [x for x in project.scenes if x.continuity_group == scene.continuity_group and x.id != sid]
            if any(x.id in scheduled_scene_ids for x in siblings):
                v.append(ConstraintViolation(kind=ConstraintKind.CONTINUITY_SPLIT, hard=False, message=f"Scene {scene.number} split from continuity group '{scene.continuity_group}' shot today", scene_id=sid))
    return v


# --------------------------------------------------------------------------- #
# Packing: ordering → concrete times
# --------------------------------------------------------------------------- #


def pack_day(
    project: Project,
    day: ShootDay,
    ordered_scene_ids: list[str],
    disruption: Disruption | None = None,
    item_ids: dict[str, str] | None = None,
) -> list[ScheduleItem]:
    """Deterministically lay out scenes in the given order.

    Rules: start no earlier than the previous scene's end + max(turnaround, travel);
    respect each scene's preferred lighting start; if a scene is exposed to the
    disruption and would overlap its window (plus dry-out), push it past the window
    unless it already fits entirely before it.
    """
    items: list[ScheduleItem] = []
    cursor = to_minutes(day.unit_call) + FIRST_SHOT_OFFSET
    prev_loc: str | None = None
    dwin = disruption_window(disruption)
    for idx, sid in enumerate(ordered_scene_ids):
        scene = project.scene(sid)
        dur = scene.estimated_minutes
        gap = max(TURNAROUND_MINUTES, project.travel_minutes(prev_loc, scene.location_id)) if items else 0
        start = max(cursor + gap, preferred_start(day, scene))
        if dwin and disruption is not None:
            exposed, _ = scene_exposed(project, scene, disruption)
            if exposed and overlaps(start, start + dur, dwin[0], dwin[1]):
                start = max(start, dwin[1])
                # SUNSET/DAWN scenes must not drift arbitrarily; preferred_start already handled
        end = start + dur
        iid = (item_ids or {}).get(sid) or f"it_{scene.number}"
        items.append(ScheduleItem(id=iid, scene_id=sid, start=to_hhmm(start), end=to_hhmm(end), location_id=scene.location_id))
        cursor = end
        prev_loc = scene.location_id
    return items
