"""Disruption → impact propagation (deterministic)."""

from __future__ import annotations

from ..domain.enums import IntExt, RequirementCategory, ResourceType, TimeOfDay
from ..domain.models import Disruption, ImpactAnalysis, ItemMobility, Project, Scene, ShootDay, ViolatedRequirement
from .schedule import availability_windows, disruption_window, lighting_check, scene_exposed
from .timeutil import overlaps, to_hhmm, to_minutes

WEATHER_CATEGORIES = {
    RequirementCategory.WEATHER,
    RequirementCategory.SAFETY,
    RequirementCategory.TECHNICAL,
    RequirementCategory.EQUIPMENT,
}


def applicability(project: Project, day: ShootDay, disruption: Disruption) -> tuple[bool, str | None]:
    """Can this disruption reach this day's schedule at all? `(applicable, the reason it cannot)`.

    The three seeded fixtures used to be offered on every unwrapped day, because the only guard was
    `_refuse_if_wrapped`. Day 6 is a 16:00 night unit that calls no crane and no Vikram, and it
    answered a crane hydraulic fault with "move Sc 62 17:00->16:30; pull cover Sc 27 into 19:00" —
    a recovery for a fault on a unit that has no crane, scored and ranked like a real one.

    Derived from the two predicates the solver itself uses — `scene_exposed` for reach and
    `overlaps` for timing — so what the day page offers and what the engine can act on cannot
    drift. Deliberately *not* "run the solver and look for an empty impact": on Day 4 the crane
    fixture also produces an empty impact, and dropping it there would delete the demo's own
    scenario rather than fix it.
    """
    dwin = disruption_window(disruption)
    day_win = (to_minutes(day.unit_call), to_minutes(day.hard_wrap))
    reasons: list[str] = []

    unreachable = _unreachable(project, day, disruption)
    if unreachable:
        reasons.append(f"Day {day.day_number} {unreachable}")
    if dwin and not overlaps(dwin[0], dwin[1], day_win[0], day_win[1]):
        window = f"the {disruption.window_start}–{disruption.window_end} window"
        if dwin[1] <= day_win[0]:
            when = f"ends before the {day.unit_call} unit call"
        elif dwin[0] >= day_win[1]:
            when = f"starts after the {day.hard_wrap} hard wrap"
        else:  # pragma: no cover - two intervals that miss are one of the two cases above
            when = f"falls outside the day's operating window {day.unit_call}–{day.hard_wrap}"
        reasons.append(f"{window} {'also ' if reasons else ''}{when}")
    if not reasons:
        return True, None
    return False, "; ".join(reasons) + "."


def _unreachable(project: Project, day: ShootDay, disruption: Disruption) -> str | None:
    """What this day has none of, or `None` when something on it is exposed.

    Equipment *calls* count alongside scheduled scenes: a vendor booked for the day is on the day
    even on the reading where no scene lists it.
    """
    if any(scene_exposed(project, project.scene(i.scene_id), disruption)[0] for i in day.items):
        return None
    if any(c.resource_id in disruption.affects_resource_ids for c in day.equipment_calls):
        return None
    if disruption.affects_resource_ids:
        return f"calls no {_names(project, disruption.affects_resource_ids)}"
    if disruption.affects_location_ids:
        return f"shoots at none of {_names(project, disruption.affects_location_ids)}"
    if disruption.affects_exteriors:
        return "schedules no exterior scene exposed to it"
    return "schedules nothing it can reach"


def _names(project: Project, resource_ids: list[str]) -> str:
    out: list[str] = []
    for rid in resource_ids:
        try:
            out.append(project.resource(rid).name.split(" (")[0])
        except KeyError:
            out.append(rid)
    return ", ".join(out)


def analyze_impact(project: Project, day: ShootDay, disruption: Disruption) -> ImpactAnalysis:
    dwin = disruption_window(disruption)
    affected_ids: list[str] = []
    violated: list[ViolatedRequirement] = []
    implicated: list[str] = []
    immovable: list[ItemMobility] = []
    movable: list[ItemMobility] = []

    for item in sorted(day.items, key=lambda i: to_minutes(i.start)):
        scene = project.scene(item.scene_id)
        s, e = to_minutes(item.start), to_minutes(item.end)
        exposed, why = scene_exposed(project, scene, disruption)
        in_window = dwin is None or overlaps(s, e, dwin[0], dwin[1])
        if exposed and in_window:
            affected_ids.append(item.id)
            reqs = [r for r in scene.requirements if r.weather_sensitive or (r.category in WEATHER_CATEGORIES and disruption.type.value == "WEATHER")]
            if disruption.affects_resource_ids:
                reqs += [r for r in scene.requirements if set(r.resource_ids) & set(disruption.affects_resource_ids)]
            seen = set()
            for r in reqs:
                if r.id in seen:
                    continue
                seen.add(r.id)
                violated.append(ViolatedRequirement(item_id=item.id, scene_id=scene.id, requirement_id=r.id, reason=f"{r.category.value}: {r.description}"))
            if not reqs:
                violated.append(ViolatedRequirement(item_id=item.id, scene_id=scene.id, requirement_id=None, reason=f"Scene {scene.number} is exposed ({why})"))
            for rid in [scene.location_id, *scene.cast_ids, *scene.equipment_ids]:
                if rid and rid not in implicated:
                    implicated.append(rid)
            # vehicles that serve this location
            for leg in day.transport:
                if leg.to_location_id == scene.location_id and leg.vehicle_id not in implicated:
                    implicated.append(leg.vehicle_id)

        # mobility
        slack_reasons: list[str] = []
        pinned_reasons: list[str] = []
        loc = project.resource(scene.location_id) if scene.location_id else None
        if loc:
            wins = availability_windows(loc, day)
            if wins:
                ws, we = min(w[0] for w in wins), max(w[1] for w in wins)
                if (we - ws) - (e - s) < 60:
                    pinned_reasons.append(f"{loc.name} only available {to_hhmm(ws)}–{to_hhmm(we)}")
                elif loc.availability:
                    slack_reasons.append(f"{loc.name} available {to_hhmm(ws)}–{to_hhmm(we)}")
        for cid in scene.cast_ids:
            r = project.resource(cid)
            wins = availability_windows(r, day)
            if wins and r.availability:
                ws, we = min(w[0] for w in wins), max(w[1] for w in wins)
                if (we - ws) - (e - s) < 60:
                    pinned_reasons.append(f"{r.name} only available {to_hhmm(ws)}–{to_hhmm(we)}")
                else:
                    slack_reasons.append(f"{r.name} available {to_hhmm(ws)}–{to_hhmm(we)}")
        if scene.time_of_day in (TimeOfDay.SUNSET, TimeOfDay.DAWN):
            pinned_reasons.append(f"{scene.time_of_day.value} scene — tied to golden hour")
        if scene.int_ext == IntExt.INT:
            slack_reasons.insert(0, "interior — can shoot during the disruption window")
        if pinned_reasons and not (scene.int_ext == IntExt.INT):
            immovable.append(ItemMobility(item_id=item.id, scene_id=scene.id, reason="; ".join(pinned_reasons)))
        else:
            movable.append(ItemMobility(item_id=item.id, scene_id=scene.id, reason="; ".join(slack_reasons or ["no binding window"])))

    scheduled = {i.scene_id for i in day.items}
    # A cover set is only useful if it can actually be lit in the gap it would fill: a NIGHT interior
    # is no help to a day unit losing its afternoon, and the enumerator would only reject it later.
    covers = [
        s.id for s in project.scenes
        if s.is_cover and s.id not in scheduled and s.int_ext == IntExt.INT and _cover_fits(day, s, dwin)
    ]

    affected_numbers = [project.scene(project_item_scene(day, i)).number for i in affected_ids]
    window_txt = f" during {disruption.window_start}–{disruption.window_end}" if disruption.window_start else ""
    summary = (
        f"{len(affected_ids)} scheduled scene(s) directly affected{window_txt}: "
        + (", ".join(f"Sc {n}" for n in affected_numbers) or "none")
        + f". {len(violated)} requirement(s) violated, {len(implicated)} resource(s) implicated, "
        f"{len(immovable)} item(s) pinned, {len(movable)} movable, {len(covers)} cover scene(s) available."
    )
    return ImpactAnalysis(
        disruption_id=disruption.id,
        directly_affected_item_ids=affected_ids,
        violated_requirements=violated,
        implicated_resource_ids=implicated,
        immovable=immovable,
        movable=movable,
        cover_scene_ids=covers,
        summary=summary,
    )


def _cover_fits(day: ShootDay, scene: Scene, dwin: tuple[int, int] | None) -> bool:
    """Could this cover scene be lit inside the window it is meant to fill (or anywhere in the day)?"""
    start = dwin[0] if dwin else to_minutes(day.unit_call)
    return lighting_check(day, scene, start, start + scene.estimated_minutes) is None


def project_item_scene(day: ShootDay, item_id: str) -> str:
    for i in day.items:
        if i.id == item_id:
            return i.scene_id
    raise KeyError(item_id)
