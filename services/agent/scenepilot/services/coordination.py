"""Derive downstream coordination actions from an approved ChangeSet.

Everything here is computed from the ChangeSet + production state — never hardcoded.
Actions are emitted through `ActionSink` so real adapters (email, calendar, production
software) can replace the simulated sink later.
"""

from __future__ import annotations

from typing import Protocol

from ..domain.enums import CoordinationKind, ResourceType
from ..domain.models import ChangeSet, CoordinationAction, Project, ScheduleItem, ShootDay
from .timeutil import to_hhmm, to_minutes

DINNER_CUTOFF = "19:00"
DEPARTMENTS_BY_EQUIPMENT = {
    "drone": "Aerial / drone unit",
    "crane": "Grip department",
    "lighting": "Electric department",
    "fireworks": "SFX / pyrotechnics",
    "motorcycle": "Stunt & rigging",
    "camera": "Camera department",
}


class ActionSink(Protocol):
    def deliver(self, action: CoordinationAction) -> None: ...


class SimulatedSink:
    """MVP sink: marks actions as simulated; a real adapter would send email/calendar updates."""

    def deliver(self, action: CoordinationAction) -> None:
        action.channel = "simulated"


def department_for(resource_name: str) -> str:
    low = resource_name.lower()
    for key, dept in DEPARTMENTS_BY_EQUIPMENT.items():
        if key in low:
            return dept
    return "Production office"


def derive_actions(project: Project, day_after: ShootDay, changeset: ChangeSet, sink: ActionSink | None = None, substitutes: list | None = None) -> list[CoordinationAction]:
    sink = sink or SimulatedSink()
    actions: list[CoordinationAction] = []
    idx = {i: c for i, c in enumerate(changeset.changes)}

    def change_ids(pred) -> list[int]:
        return [i for i, c in idx.items() if pred(c)]

    ordered = sorted(day_after.items, key=lambda i: to_minutes(i.start))
    lines = [f"{i.start}–{i.end}  Sc {project.scene(i.scene_id).number}  {project.scene(i.scene_id).heading}" for i in ordered]
    actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.SCHEDULE_REGENERATED, title=f"Shooting schedule regenerated — Day {day_after.day_number}", details=lines, target="1st AD", derived_from_change_ids=change_ids(lambda c: c.entity_type == "schedule_item")))

    # Call sheet
    cast_calls: dict[str, int] = {}
    for it in ordered:
        for cid in project.scene(it.scene_id).cast_ids:
            cast_calls[cid] = min(cast_calls.get(cid, 10**6), max(to_minutes(it.start) - 60, to_minutes(day_after.unit_call)))
    call_lines = [f"Unit call {day_after.unit_call}"] + [f"{project.resource(c).name}: {to_hhmm(t)}" for c, t in sorted(cast_calls.items(), key=lambda x: x[1])]
    actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.CALL_SHEET_REGENERATED, title="Call sheet regenerated", details=call_lines, target="All crew", derived_from_change_ids=change_ids(lambda c: c.entity_type == "schedule_item")))

    # Cast notifications for moved scenes
    sched_changes = [c for c in changeset.changes if c.entity_type == "schedule_item" and c.field == "start"]
    notified: dict[str, list[str]] = {}
    for c in sched_changes:
        scene = project.scene_by_number(c.label.replace("Scene ", ""))
        for cid in scene.cast_ids:
            if c.after is None:
                msg = f"Sc {scene.number} carried over — not shooting today"
            elif c.before is None:
                msg = f"Sc {scene.number} added today at {c.after}"
            else:
                msg = f"Sc {scene.number} moved {c.before} → {c.after}"
            notified.setdefault(cid, []).append(msg)
    for cid, msgs in notified.items():
        r = project.resource(cid)
        new_call = cast_calls.get(cid)
        details = msgs + ([f"New call time: {to_hhmm(new_call)}"] if new_call is not None else ["Released for the day"])
        actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.CAST_NOTIFICATION, title=f"Notify {r.name}", details=details, target=r.name, payload={"resource_id": cid}, derived_from_change_ids=change_ids(lambda c: c.entity_type == "schedule_item")))

    # Equipment + crew notifications
    for i, c in idx.items():
        if c.entity_type != "equipment_call":
            continue
        r = project.resource(c.entity_id)
        dept = department_for(r.name)
        if c.after is None:
            detail = f"{r.name}: call cancelled for today ({c.reason})"
        elif c.before is None:
            detail = f"{r.name}: new call {c.after}"
        else:
            detail = f"{r.name}: call {c.before} → {c.after}"
        actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.EQUIPMENT_CALL_UPDATED, title=f"Equipment call updated — {r.name}", details=[detail], target=dept, payload={"resource_id": r.id}, derived_from_change_ids=[i]))
        actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.CREW_NOTIFICATION, title=f"Notify {dept}", details=[detail, "Reason: " + c.reason], target=dept, derived_from_change_ids=[i]))

    # Transport
    for i, c in idx.items():
        if c.entity_type != "transport":
            continue
        if c.after is None:
            detail = f"{c.label}: leg cancelled"
        elif c.before is None:
            detail = f"{c.label}: new departure {c.after}"
        else:
            detail = f"{c.label}: departure {c.before} → {c.after}"
        actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.TRANSPORT_UPDATED, title="Transport updated", details=[detail], target="Transport captain", derived_from_change_ids=[i]))

    # Meals
    wrap = max((to_minutes(i.end) for i in ordered), default=0)
    headcount = day_after.crew_size + len(cast_calls)
    if wrap > to_minutes(DINNER_CUTOFF):
        actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.MEAL_COUNT_UPDATED, title="Meal count updated", details=[f"Wrap now {to_hhmm(wrap)} (after {DINNER_CUTOFF}) → add crew dinner for {headcount}", f"Lunch count unchanged ({headcount})"], target="Catering", payload={"dinner_delta": headcount}, derived_from_change_ids=change_ids(lambda c: c.entity_type == "schedule_item")))
    else:
        actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.MEAL_COUNT_UPDATED, title="Meal count confirmed", details=[f"Wrap {to_hhmm(wrap)} — lunch only for {headcount}, no dinner"], target="Catering", payload={"dinner_delta": 0}, derived_from_change_ids=[]))

    # Location contacts
    loc_usage_before: dict[str, tuple[int, int]] = {}
    loc_usage_after: dict[str, tuple[int, int]] = {}
    for c in sched_changes:
        scene = project.scene_by_number(c.label.replace("Scene ", ""))
        if scene.location_id is None:
            continue
        loc = project.resource(scene.location_id)
        if c.after is None:
            actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.LOCATION_CONTACT_UPDATE, title=f"Location update — {loc.name}", details=[f"Sc {scene.number} will not shoot today; request a new date", f"Contact: {loc.contact or 'n/a'}"], target=loc.contact or loc.name, payload={"resource_id": loc.id}))
        else:
            end = next(i.end for i in day_after.items if i.scene_id == scene.id)
            actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.LOCATION_CONTACT_UPDATE, title=f"Location update — {loc.name}", details=[f"Sc {scene.number} now {c.after}–{end}", f"Contact: {loc.contact or 'n/a'}"], target=loc.contact or loc.name, payload={"resource_id": loc.id}))

    # Carry-over
    for c in sched_changes:
        if c.after is None:
            scene = project.scene_by_number(c.label.replace("Scene ", ""))
            actions.append(CoordinationAction(changeset_id=changeset.id, kind=CoordinationKind.SCENE_CARRY_OVER, title=f"Sc {scene.number} carried over", details=["Added to the carry-over list for the next available day", f"Needs: {', '.join(project.resource(r).name for r in scene.equipment_ids) or 'no special equipment'}"], target="Production office", payload={"scene_id": scene.id}))

    # Substitute suppliers the producer chose from a Parallel FindAll run. These are real companies
    # with a citation, so the action carries the source — a 1st AD ringing a vendor should be able to
    # see where the number came from.
    for run in substitutes or []:
        chosen = next((v for v in run.candidates if v.selected), None)
        if chosen is None or not run.resource_id:
            continue
        try:
            replaced = project.resource(run.resource_id)
        except KeyError:
            continue
        details = [f"Replacing {replaced.name}", chosen.description or chosen.url]
        if chosen.phone:
            details.append(f"Phone: {chosen.phone}")
        if chosen.address:
            details.append(f"Address: {chosen.address}")
        if chosen.day_rate_band:
            details.append(f"Indicative day rate: {chosen.day_rate_band}")
        details.append(f"Found by Parallel {'Entity Search' if run.mode == 'entity_search' else 'FindAll'} · source: {(chosen.citations[0].url if chosen.citations else chosen.url)}")
        actions.append(CoordinationAction(
            changeset_id=changeset.id,
            kind=CoordinationKind.EQUIPMENT_SUBSTITUTE,
            title=f"Book replacement — {chosen.name}",
            details=details,
            target=department_for(replaced.name),
            payload={"resource_id": replaced.id, "vendor_id": chosen.id, "vendor_url": chosen.url, "findall_run_id": run.id},
        ))

    for a in actions:
        sink.deliver(a)
    return actions
