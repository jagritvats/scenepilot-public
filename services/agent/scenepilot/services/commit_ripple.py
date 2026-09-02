"""Committing the multi-day plan — turning a ripple proposal into production state.

The ripple solver has always been able to say *where a carried scene could go* and *what a dedicated
pickup day would cost*, and a producer could act on neither. The single-day story ends in a decision
— approve, and a ChangeSet is applied with an audit trail — while the multi-day story ended in a
report. This closes it with the same mechanism: a placement or a pickup day becomes committed state
through a `ChangeSet`, or it does not happen.

Three things this refuses to do, each because the alternative would invent production state:

1. **It never trusts a proposed time.** The placement is recomputed here against the real validator on
   the target day's *current* schedule, because the proposal was made against the day as it stood
   when the panel was drawn and a producer may have moved something since.
2. **It re-validates after committing, and refuses on a hard violation.** `_can_accommodate` scores a
   projection with a deliberately narrow context — no disruption, no next-day call, only the
   violations attributable to that one scene. A commit has to answer for the whole day.
3. **It does not book anybody onto a pickup day.** Availability is a fact about what the production
   has cleared with a performer or a location owner, and a day nobody has cleared has none. Minting
   availability rows so the new day would validate cleanly would be asserting that a cast member had
   agreed to work. So the day is materialized *uncleared*, and the resources needing clearance are
   named — which is why `pending_clearance` is part of the result and not an afterthought.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import ResourceType, ScheduleItemStatus, ShootDayStatus
from ..domain.models import Change, ChangeSet, Project, ScheduleItem, ShootDay, utcnow
from .changeset import apply_changeset, derive_equipment_calls, derive_transport
from .multiday_solver import _can_accommodate
from .recovery import next_day_call
from .labor_rules import active_pack
from .schedule import ValidationContext, availability_windows, validate_schedule
from .timeutil import to_minutes


class CommitRefused(ValueError):
    """A commit the engine will not make. The message is the reason a producer needs."""


def _binding_facts(project: Project) -> list:
    return [f for f in project.location_facts if f.binds]


def _revalidate(project: Project, day: ShootDay, *, baseline: list[ScheduleItem] | None = None) -> list:
    """The whole day, under the same context the board and the cost card use.

    `baseline` is what the day looked like *before* the edit under test. It defaults to `day.items`,
    which is right for a placement — the new scene is the only thing that moved. It is wrong for a
    board a producer has re-timed by hand: by the time this runs, `day.items` **is** the edit, so the
    `EXTRA_COMPANY_MOVE` rule would compare the new board against itself and report no extra moves
    however many the edit introduced.
    """
    ctx = ValidationContext(
        project=project,
        day=day,
        baseline_items=day.items if baseline is None else baseline,
        next_day_call=next_day_call(project, day),
        location_facts=_binding_facts(project),
    )
    return validate_schedule(ctx, day.items)


def _scene_is_unscheduled(project: Project, scene_id: str) -> bool:
    """Is this scene still looking for a day?

    A `DEFERRED` item does not count as a booking. Every existing path removes such an item from the
    day — `apply_changeset` filters them out, a revert restores the pre-recovery snapshot, and the
    seed has none — so today this reads exactly as it did. It stops reading that way the moment a day
    can be wrapped: a strip marked *carried* stays on the wrapped day precisely so the completion
    record and the DPR can report it, and treating that record as a booking would refuse the scene
    the one route it has onward.
    """
    return not any(
        i.scene_id == scene_id and i.status != ScheduleItemStatus.DEFERRED
        for d in project.shoot_days
        for i in d.items
    )


def commit_placement(project: Project, target_day_id: str, scene_id: str, *, committed_by: str = "producer") -> dict[str, Any]:
    """Place one carried scene onto a downstream day, for real.

    Returns the applied ChangeSet, the re-validated day and what the placement cost.
    """
    try:
        day = project.shoot_day(target_day_id)
    except KeyError:
        raise CommitRefused("that shoot day is not on this production")
    try:
        scene = project.scene(scene_id)
    except KeyError:
        raise CommitRefused("that scene is not on this production")

    if day.status == ShootDayStatus.WRAPPED:
        raise CommitRefused(
            f"Day {day.day_number} wrapped on {day.date}. A scene cannot be added to a day that has already been shot."
        )
    if not _scene_is_unscheduled(project, scene_id):
        raise CommitRefused(
            f"Scene {scene.number} is already scheduled. Only a scene carried off its day can be placed on another one."
        )

    # Recomputed here, never taken from the caller: the proposal was made against the day as it was.
    feasible, ot_cost, start, end, notes = _can_accommodate(project, day, scene, _binding_facts(project))
    if not feasible:
        raise CommitRefused(
            f"Day {day.day_number} cannot take Scene {scene.number}: " + "; ".join(notes)
        )

    item_id = f"it_{scene.number}_d{day.day_number}"
    changeset = ChangeSet(
        project_id=project.id,
        shoot_day_id=day.id,
        recovery_option_id=None,
        changes=[
            Change(
                entity_type="schedule_item",
                entity_id=item_id,
                label=f"Scene {scene.number}",
                field=field,
                before=None,
                after=value,
                reason=f"Carried scene placed on Day {day.day_number} by the producer",
            )
            for field, value in (("start", start), ("end", end))
        ],
        summary=f"Scene {scene.number} placed on Day {day.day_number} at {start}",
    )

    before_items = [i.model_copy(deep=True) for i in day.items]
    apply_changeset(project, changeset, approved_by=committed_by)

    # `apply_changeset` mints the item through the create path; make sure it carries the location and
    # reads as what it is — a scene brought in from another day, not one that was always here.
    placed = next((i for i in day.items if i.scene_id == scene_id), None)
    if placed is None:  # the create path could not resolve the scene by its label
        day.items = before_items
        raise CommitRefused(f"Scene {scene.number} could not be placed on Day {day.day_number}.")
    placed.location_id = placed.location_id or scene.location_id
    placed.status = ScheduleItemStatus.MOVED
    placed.note = f"Carried from another day and placed here on {utcnow().date().isoformat()}"

    day.items.sort(key=lambda i: to_minutes(i.start))
    day.equipment_calls = derive_equipment_calls(project, day, day.items)
    day.transport = derive_transport(project, day, day.items)

    violations = _revalidate(project, day)
    hard = [v for v in violations if v.hard]
    if hard:
        # The projection answered only for this scene; the day has to answer for itself.
        day.items = before_items
        day.equipment_calls = derive_equipment_calls(project, day, day.items)
        day.transport = derive_transport(project, day, day.items)
        if changeset.id in project.changeset_ids:
            project.changeset_ids.remove(changeset.id)
        raise CommitRefused(
            f"Placing Scene {scene.number} on Day {day.day_number} breaks the day: "
            + "; ".join(dict.fromkeys(v.message for v in hard))
        )

    return {
        "changeset": changeset,
        "day": day,
        "scene_number": scene.number,
        "start": start,
        "end": end,
        "added_overtime_cost_inr": ot_cost,
        "notes": notes,
        "soft_violations": [v.model_dump(mode="json") for v in violations if not v.hard],
    }


def commit_board(
    project: Project,
    day_id: str,
    edits: list[dict[str, str]],
    *,
    reason: str | None = None,
    committed_by: str = "producer",
) -> dict[str, Any]:
    """Keep a board a producer nudged by hand — under the agreement the production is actually held to.

    The interactive stripboard is the headline of this product's second phase and it was a pure
    what-if: `/simulate-strip-move` validated and priced arbitrary times and returned them, and every
    edit died on reload. This is the counterpart that was never written.

    It takes no `labor_preset`. The simulate endpoint accepts one because previewing a board under
    DGA/SAG rules is a real question; committing under them is not, because this production is held
    to `active_pack(project)` whatever the selector says. `_revalidate` builds its context without a
    pack, so the fall-through is the enforced one — and the two must stay separate endpoints for
    exactly that reason.

    Adds and removes nothing: a strip arrives by `commit_placement` and leaves by a wrap or a
    recovery. This only ever re-times what is already on the day.
    """
    try:
        day = project.shoot_day(day_id)
    except KeyError:
        raise CommitRefused("That shoot day is not on this production.")
    if day.status == ShootDayStatus.WRAPPED:
        raise CommitRefused(
            f"Day {day.day_number} wrapped on {day.date}. Its schedule is the record of what was shot, "
            "and re-timing it now would edit that record."
        )
    if day.active_disruption_id is not None:
        raise CommitRefused(
            f"Day {day.day_number} is under a live disruption. This validates the whole day with the "
            "disruption set aside, so a board committed now would be called legal here and exposed by the "
            "disruption panel on the same screen. End the rescue first — approve it, revert it, or stand it down."
        )

    by_id = {e["item_id"]: e for e in edits}
    if len(by_id) != len(edits):
        raise CommitRefused("The same strip appears twice in the board.")
    unknown = sorted(i for i in by_id if not any(x.id == i for x in day.items))
    if unknown:
        raise CommitRefused(f"{', '.join(unknown)} is not on Day {day.day_number}.")
    # Every strip, both directions. Treating an unnamed one as unchanged would let a dropped row in
    # the payload silently keep its old time — the failure mode nobody can see.
    missing = [i for i in day.items if i.id not in by_id]
    if missing:
        names = ", ".join(f"Sc {project.scene(i.scene_id).number}" for i in missing)
        raise CommitRefused(f"The board does not account for {names}. Commit the whole day or none of it.")

    before_items = [i.model_copy(deep=True) for i in day.items]
    changes: list[Change] = []
    for item in day.items:
        edit = by_id[item.id]
        scene = project.scene(item.scene_id)
        for field in ("start", "end"):
            before = getattr(item, field)
            after = edit[field]
            if before == after:
                continue
            changes.append(Change(
                entity_type="schedule_item", entity_id=item.id, label=f"Scene {scene.number}", field=field,
                before=before, after=after,
                reason=reason or f"Board committed by {committed_by}",
            ))
            setattr(item, field, after)

    if not changes:
        raise CommitRefused("This board is the schedule already on the day; there is nothing to commit.")

    day.items.sort(key=lambda i: to_minutes(i.start))
    day.equipment_calls = derive_equipment_calls(project, day, day.items)
    day.transport = derive_transport(project, day, day.items)

    violations = _revalidate(project, day, baseline=before_items)
    hard = [v for v in violations if v.hard]
    if hard:
        day.items = before_items
        day.equipment_calls = derive_equipment_calls(project, day, day.items)
        day.transport = derive_transport(project, day, day.items)
        raise CommitRefused(
            f"Day {day.day_number} cannot be committed as edited: " + "; ".join(dict.fromkeys(v.message for v in hard))
        )

    changeset = ChangeSet(
        project_id=project.id, shoot_day_id=day.id, changes=changes,
        summary=f"Day {day.day_number} board committed by {committed_by} — {len(changes)} time(s) changed.",
    )
    changeset.approved_by = committed_by
    changeset.applied_at = utcnow()
    project.changeset_ids.append(changeset.id)
    project.updated_at = utcnow()

    notes = [f"{c.label} {c.field} {c.before} → {c.after}" for c in changes]
    notes.append(f"Validated under {active_pack(project).name}")
    notes.extend(dict.fromkeys(v.message for v in violations if not v.hard))
    return {"changeset": changeset, "day": day, "notes": notes}


def pending_clearance(project: Project, day: ShootDay) -> list[dict[str, Any]]:
    """Resources this day's scenes need that nobody has cleared for it.

    A resource with no availability rows anywhere is unconstrained and needs nothing. A resource that
    *has* rows but none covering this day is, to the validator, unavailable — and that is the honest
    reading: the production books people onto days, and nobody has been booked onto this one.
    """
    needed: dict[str, ScheduleItem] = {}
    for item in day.items:
        # Guarded like the resource lookup below: this now runs on every shoot-day GET, not only
        # after a pickup day is committed, so a strip naming a scene the production has since
        # dropped would have 500'd the whole page.
        try:
            scene = project.scene(item.scene_id)
        except KeyError:
            continue
        for rid in [*scene.cast_ids, *scene.equipment_ids, item.location_id or scene.location_id]:
            if rid:
                needed.setdefault(rid, item)

    pending: list[dict[str, Any]] = []
    for rid in needed:
        try:
            resource = project.resource(rid)
        except KeyError:
            continue
        if not resource.availability:
            continue  # unconstrained — nothing to clear
        if availability_windows(resource, day):
            continue  # already cleared for this day
        pending.append({
            "resource_id": resource.id,
            "name": resource.name,
            "type": resource.type.value,
            "reason": f"{resource.name} has booked days on this production but none on Day {day.day_number}.",
        })
    pending.sort(key=lambda r: (r["type"], r["name"]))
    return pending


def materialize_pickup_day(project: Project, pickup: ShootDay, *, committed_by: str = "producer") -> dict[str, Any]:
    """Commit a synthesized pickup day into the schedule, uncleared and saying so."""
    if any(d.id == pickup.id for d in project.shoot_days):
        raise CommitRefused(f"Day {pickup.day_number} has already been committed to this schedule.")
    if any(d.date == pickup.date for d in project.shoot_days):
        # Two days on one date breaks `next_day_call` and the "available on other days" test, both of
        # which compare dates rather than day numbers.
        raise CommitRefused(
            f"The production already has a shoot day on {pickup.date}. A pickup day needs a date of its own."
        )
    if not pickup.items:
        raise CommitRefused("A pickup day with no scenes on it is not a day; it is a placeholder.")

    day = pickup.model_copy(deep=True)
    for item in day.items:
        scene = project.scene(item.scene_id)
        item.location_id = item.location_id or scene.location_id
        item.status = ScheduleItemStatus.MOVED
        item.note = "Carried scene; this day was created to shoot it"
    day.items.sort(key=lambda i: to_minutes(i.start))
    day.equipment_calls = derive_equipment_calls(project, day, day.items)
    day.transport = derive_transport(project, day, day.items)
    day.notes = f"Pickup unit created by {committed_by} to absorb scenes no existing day could take."

    project.shoot_days.append(day)
    project.shoot_days.sort(key=lambda d: (d.day_number, d.date))
    project.updated_at = utcnow()

    changeset = ChangeSet(
        project_id=project.id,
        shoot_day_id=day.id,
        changes=[
            Change(
                entity_type="shoot_day",
                entity_id=day.id,
                label=f"Day {day.day_number}",
                field="status",
                before=None,
                after=day.status.value,
                reason="Pickup unit day created to absorb carried scenes",
            ),
            *[
                Change(
                    entity_type="schedule_item",
                    entity_id=item.id,
                    label=f"Scene {project.scene(item.scene_id).number}",
                    field="start",
                    before=None,
                    after=item.start,
                    reason=f"Scheduled on the new Day {day.day_number}",
                )
                for item in day.items
            ],
        ],
        summary=f"Day {day.day_number} created as a pickup unit on {day.date}, {len(day.items)} scene(s)",
    )
    changeset.approved_by = committed_by
    changeset.applied_at = utcnow()
    project.changeset_ids.append(changeset.id)

    clearance = pending_clearance(project, day)
    violations = _revalidate(project, day)
    return {
        "changeset": changeset,
        "day": day,
        "pending_clearance": clearance,
        # Stated rather than hidden: the day is committed, and it is not yet shootable until the
        # production clears the people and places on it. That is a producer's job, not the tool's.
        "clearance_note": (
            None
            if not clearance
            else f"Day {day.day_number} is on the schedule but nobody is booked onto it yet. "
                 f"{len(clearance)} resource(s) need clearing before it validates — until then the day reports them "
                 "as unavailable, which is what they are."
        ),
        "hard_violations": [v.model_dump(mode="json") for v in violations if v.hard],
    }
