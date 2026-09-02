"""ChangeSet generation (diff), validation and application with an audit trail."""

from __future__ import annotations

from ..domain.enums import ScheduleItemStatus, ShootDayStatus
from ..domain.models import (
    Change,
    ChangeSet,
    Disruption,
    EquipmentCall,
    Project,
    RecoveryOption,
    ScheduleItem,
    ShootDay,
    TransportLeg,
    utcnow,
)
from .timeutil import to_hhmm, to_minutes

VEHICLE_LOAD_MINUTES = 15


def derive_equipment_calls(project: Project, day: ShootDay, schedule: list[ScheduleItem]) -> list[EquipmentCall]:
    earliest: dict[str, int] = {}
    for item in schedule:
        scene = project.scene(item.scene_id)
        for eid in scene.equipment_ids:
            r = project.resource(eid)
            call = to_minutes(item.start) - r.prep_minutes
            earliest[eid] = min(earliest.get(eid, 10**6), call)
    calls = []
    for eid, call in earliest.items():
        call = max(call, to_minutes(day.unit_call))
        calls.append(EquipmentCall(resource_id=eid, call_time=to_hhmm(call)))
    return sorted(calls, key=lambda c: (to_minutes(c.call_time), c.resource_id))


def derive_transport(project: Project, day: ShootDay, schedule: list[ScheduleItem]) -> list[TransportLeg]:
    """Company moves: one cast-van leg per location change."""
    vehicle = next((r.id for r in project.resources if r.type.value == "VEHICLE" and r.attributes.get("role") == "cast"), None)
    if vehicle is None:
        vehicle = next((r.id for r in project.resources if r.type.value == "VEHICLE"), "veh_1")
    ordered = sorted(schedule, key=lambda i: to_minutes(i.start))
    legs: list[TransportLeg] = []
    n = 1
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.location_id == nxt.location_id:
            continue
        travel = project.travel_minutes(prev.location_id, nxt.location_id)
        # Work back from when the van has to arrive — then clamp to when it can actually leave.
        #
        # Two parts of this engine disagreed about the same move: `validate_schedule` passes a company
        # move when the gap covers the travel, while this wanted the travel *plus* fifteen minutes of
        # loading. On the hero day that difference put the cast van on the road at 09:15 for a unit
        # that shoots until 09:30 — a departure nobody could make, printed on a transport sheet as if
        # they could. A van cannot leave a set the unit is still shooting on, so the wrap is the floor.
        # Where the clamp bites, the loading time is what gets squeezed, and the movement order says so
        # rather than the schedule quietly absorbing it.
        dep = max(to_minutes(nxt.start) - travel - VEHICLE_LOAD_MINUTES, to_minutes(prev.end))
        legs.append(TransportLeg(id=f"leg_{n}", vehicle_id=vehicle, from_location_id=prev.location_id, to_location_id=nxt.location_id, departure=to_hhmm(dep)))
        n += 1
    return legs


def build_changeset(project: Project, day: ShootDay, option: RecoveryOption, disruption: Disruption | None, run_id: str | None) -> ChangeSet:
    cs = ChangeSet(project_id=project.id, shoot_day_id=day.id, run_id=run_id, disruption_id=disruption.id if disruption else None, recovery_option_id=option.id)
    why_base = f"{disruption.title}" if disruption else "recovery"
    base = {i.scene_id: i for i in day.items}
    new = {i.scene_id: i for i in option.schedule}

    # Schedule items
    for sid, bi in base.items():
        scene = project.scene(sid)
        if sid in option.deferred_scene_ids:
            cs.changes.append(Change(entity_type="schedule_item", entity_id=bi.id, label=f"Scene {scene.number}", field="start", before=bi.start, after=None, reason=f"{why_base}: scene exposed and no feasible slot on Day {day.day_number}; carried over"))
            continue
        ni = new.get(sid)
        if ni and (ni.start != bi.start or ni.end != bi.end):
            reason = _reason_for_move(project, day, scene, bi, ni, disruption)
            cs.changes.append(Change(entity_type="schedule_item", entity_id=bi.id, label=f"Scene {scene.number}", field="start", before=bi.start, after=ni.start, reason=reason))
            cs.changes.append(Change(entity_type="schedule_item", entity_id=bi.id, label=f"Scene {scene.number}", field="end", before=bi.end, after=ni.end, reason=reason))
    for sid, ni in new.items():
        if sid not in base:
            scene = project.scene(sid)
            cs.changes.append(Change(entity_type="schedule_item", entity_id=ni.id, label=f"Scene {scene.number}", field="start", before=None, after=ni.start, reason=f"cover scene pulled forward to use the {why_base.lower()} window productively"))
            cs.changes.append(Change(entity_type="schedule_item", entity_id=ni.id, label=f"Scene {scene.number}", field="end", before=None, after=ni.end, reason="cover scene pulled forward"))

    # Equipment calls
    old_calls = {c.resource_id: c.call_time for c in day.equipment_calls}
    new_calls = {c.resource_id: c.call_time for c in derive_equipment_calls(project, day, option.schedule)}
    for eid in sorted(set(old_calls) | set(new_calls)):
        b, a = old_calls.get(eid), new_calls.get(eid)
        if b != a:
            r = project.resource(eid)
            reason = "first scene using it moved" if (a and b) else ("no longer needed on this day" if b else "now needed on this day")
            cs.changes.append(Change(entity_type="equipment_call", entity_id=eid, label=r.name, field="call_time", before=b, after=a, reason=reason))

    # Transport (legs keyed by destination: a re-timed move reads as before → after)
    old_legs = {l.to_location_id: l for l in day.transport}
    new_legs = {l.to_location_id: l for l in derive_transport(project, day, option.schedule)}
    for dest in sorted(set(old_legs) | set(new_legs), key=lambda k: str(k)):
        b, a = old_legs.get(dest), new_legs.get(dest)
        to_name = project.resource(dest).name if dest else "?"
        vehicle = project.resource((b or a).vehicle_id).name
        label = f"{vehicle} → {to_name}"
        if b and a and b.departure != a.departure:
            cs.changes.append(Change(entity_type="transport", entity_id=b.id, label=label, field="departure", before=b.departure, after=a.departure, reason="company move re-timed to the new schedule"))
        elif b and not a:
            cs.changes.append(Change(entity_type="transport", entity_id=b.id, label=label, field="departure", before=b.departure, after=None, reason="company move no longer required"))
        elif a and not b:
            cs.changes.append(Change(entity_type="transport", entity_id=a.id, label=label, field="departure", before=None, after=a.departure, reason="new company move required by the new schedule"))

    # Day status
    cs.changes.append(Change(entity_type="shoot_day", entity_id=day.id, label=f"Day {day.day_number}", field="status", before=day.status.value, after=ShootDayStatus.RECOVERED.value, reason=f"recovery option {option.label} approved"))

    moved = sum(1 for c in cs.changes if c.entity_type == "schedule_item" and c.field == "start" and c.before and c.after)
    deferred = len(option.deferred_scene_ids)
    added = sum(1 for c in cs.changes if c.entity_type == "schedule_item" and c.field == "start" and c.before is None)
    cs.summary = f"{moved} scene(s) re-timed, {added} pulled forward, {deferred} carried over; {sum(1 for c in cs.changes if c.entity_type == 'equipment_call')} equipment call(s) and {sum(1 for c in cs.changes if c.entity_type == 'transport')} transport leg(s) updated."
    return cs


def _reason_for_move(project: Project, day: ShootDay, scene, before: ScheduleItem, after: ScheduleItem, disruption: Disruption | None) -> str:
    if disruption and disruption.window_start and disruption.window_end:
        ws, we = to_minutes(disruption.window_start), to_minutes(disruption.window_end) + disruption.dry_out_minutes
        bs, be = to_minutes(before.start), to_minutes(before.end)
        if max(bs, ws) < min(be, we):
            if to_minutes(after.start) >= we:
                return f"{disruption.title}: moved past the window (+{disruption.dry_out_minutes} min dry-out)"
            return f"{disruption.title}: moved clear of the window"
    return "re-sequenced to keep the day feasible after the disruption"


def apply_changeset(project: Project, changeset: ChangeSet, approved_by: str = "producer") -> ShootDay:
    """Apply a ChangeSet to production state. Idempotent per changeset id."""
    day = project.shoot_day(changeset.shoot_day_id)
    if changeset.id in project.changeset_ids:
        return day
    items_by_id = {i.id: i for i in day.items}
    for c in changeset.changes:
        if c.entity_type == "schedule_item":
            item = items_by_id.get(c.entity_id)
            if item is None:
                if c.after is None:
                    continue
                scene_number = c.label.replace("Scene ", "")
                scene = project.scene_by_number(scene_number)
                item = ScheduleItem(id=c.entity_id, scene_id=scene.id, start="00:00", end="00:00", location_id=scene.location_id, status=ScheduleItemStatus.MOVED, note="pulled forward")
                items_by_id[item.id] = item
                day.items.append(item)
            if c.after is None:
                item.status = ScheduleItemStatus.DEFERRED
                item.note = c.reason
            else:
                setattr(item, c.field, c.after)
                if item.status != ScheduleItemStatus.DEFERRED and c.before is not None:
                    item.status = ScheduleItemStatus.MOVED
                    item.note = c.reason
        elif c.entity_type == "shoot_day" and c.field == "status":
            day.status = ShootDayStatus(c.after)
    # deferred items leave the day's timeline (kept in carry-over list via status)
    day.items = [i for i in day.items if i.status != ScheduleItemStatus.DEFERRED]
    # recompute derived calls/transport from the applied schedule
    day.equipment_calls = derive_equipment_calls(project, day, day.items)
    day.transport = derive_transport(project, day, day.items)
    changeset.approved_by = approved_by
    changeset.applied_at = utcnow()
    project.changeset_ids.append(changeset.id)
    project.updated_at = utcnow()
    return day
