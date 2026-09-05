"""Rolling an approved recovery back off the schedule.

Reversibility is most of the difference between a tool a producer trusts and a demo. Approving a
recovery rewrites a shoot day — scenes move, one is carried off, equipment calls and transport are
re-derived — and until now that was a one-way door: the only way back was `POST /reset`, which
rebuilds the entire production from the seed and throws away everything else with it.

Two decisions shape this, and both follow rules the codebase already holds:

**A revert is a new event, never an edit to the old one.** `_revalidate_open_rescues` already refuses
to re-verdict an APPLIED run because "rewriting its verdicts would edit history to match a later
opinion". The same applies here: the original ChangeSet stays exactly as approved, and the revert is
recorded as its own inverted ChangeSet with its own reason. The audit trail should read *approved,
then reverted* — not as though the approval never happened.

**The baseline is the source of truth, not the ChangeSet.** The ChangeSet is a faithful record of
what changed but not a complete one: a deferred scene is recorded only as `start: <time> → None`,
with no `end`, no location and no status, and the item itself was deleted from the day. Inverting
that alone would restore a scene with half its fields. `RescueState.baseline` is a full snapshot of
the day's items taken before the rescue touched anything, so that is what gets restored, and the
derived state (equipment calls, transport) is recomputed from it exactly as approval recomputed it.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import RunKind, RunStatus, ScheduleItemStatus, ShootDayStatus
from ..domain.models import Change, ChangeSet, Project, ShootDay, WorkflowRun, utcnow
from .changeset import derive_equipment_calls, derive_transport
from .commit_ripple import _revalidate
from .timeutil import to_minutes


class RevertRefused(ValueError):
    """A revert the engine will not make. The message is the reason a producer needs."""


def release_day(project: Project, run: WorkflowRun, *, restore_items: bool = True) -> ShootDay | None:
    """Hand a day back from a rescue run that is ending without an applied recovery.

    Three restorations, each guarded so this only ever undoes what *this* run did:

      * the schedule, from `RescueState.baseline` — the half the workflow's own release never had.
        `_step_impact` marks every directly-affected strip `AT_RISK`, and nothing put those back, so
        a run that failed or found nothing left a day reading healthy at the top and alarmed on every
        row. Restoring the snapshot wholesale is the same move `revert_changeset` makes below, and
        for the same reason: a deferral is recorded as `start → None`, so inverting a change list
        would put back half a scene.
      * the day's status, only while the day still carries one this pipeline set. A day somebody has
        since wrapped, or applied a different recovery to, keeps what it has.
      * the pointer at the disruption, only while it is still this run's disruption.

    The disruption itself stays on the production. It happened, it was reported, and a producer
    asking "did anyone log the crane fault" is entitled to find it.

    Pure: mutates `project` in place and never persists. The caller saves.
    """
    state = run.rescue
    if state is None:
        return None
    try:
        day = project.shoot_day(state.shoot_day_id)
    except KeyError:
        return None

    if restore_items and state.baseline:
        day.items = [i.model_copy(deep=True) for i in state.baseline]
        day.items.sort(key=lambda i: to_minutes(i.start))
        day.equipment_calls = derive_equipment_calls(project, day, day.items)
        day.transport = derive_transport(project, day, day.items)
    if day.active_disruption_id == state.disruption_id:
        day.active_disruption_id = None
    if state.prior_day_status is not None and day.status in (ShootDayStatus.AT_RISK, ShootDayStatus.RECOVERY_PROPOSED):
        day.status = state.prior_day_status
    project.updated_at = utcnow()
    return day


def stand_down(project: Project, run: WorkflowRun, *, stood_down_by: str, reason: str) -> dict[str, Any]:
    """End a rescue without approving anything, and give the day back.

    The state this closes was the only one in the product a producer could enter and not leave.
    Reporting a disruption drives the run to `AWAITING_APPROVAL`, and `approve` refuses an option
    that is not feasible — so a disruption no legal schedule survives left the day holding a
    recommendation it could not take, with the fixture picker and the manual entry form both hidden
    because a disruption was live. The only exit was resetting the whole production.

    Distinct from a revert, which un-applies a change already on the schedule. Nothing was applied
    here; what ends is the *asking*. The options are kept for exactly that reason — they are the
    record of what was offered and declined.
    """
    if run.kind != RunKind.RESCUE or run.rescue is None:
        raise RevertRefused("That run is not a rescue, so there is no day being held by it.")
    state = run.rescue
    if run.status == RunStatus.APPLIED:
        raise RevertRefused(
            f"Run {run.id} applied a change set to this day. Standing down would leave that change on the "
            "schedule with nothing on record ending it — roll it back first, and the run returns here."
        )
    if run.status in (RunStatus.PENDING, RunStatus.RUNNING):
        raise RevertRefused(
            f"Run {run.id} is still {run.status.value.lower()}. Its background task would overwrite anything "
            "stood down now: wait for the recommendation, or for it to fail."
        )
    if run.status != RunStatus.AWAITING_APPROVAL:
        raise RevertRefused(f"Run {run.id} has already ended ({run.status.value.lower().replace('_', ' ')}); no day is being held by it.")

    try:
        day = project.shoot_day(state.shoot_day_id)
    except KeyError:
        raise RevertRefused("The shoot day this rescue was holding is no longer on the production.")
    if day.status == ShootDayStatus.WRAPPED:
        raise RevertRefused(
            f"Day {day.day_number} has wrapped. What it shot is a matter of record now, and standing down "
            "the recommendation it never took would not change that."
        )

    released = release_day(project, run, restore_items=True)
    state.stood_down_reason = reason
    state.stood_down_by = stood_down_by
    state.stood_down_at = utcnow()
    run.status = RunStatus.COMPLETED
    return {"day": released or day, "run": run}


def revert_changeset(project: Project, run: WorkflowRun, changeset: ChangeSet, *, reverted_by: str, reason: str) -> dict[str, Any]:
    """Put the day back the way it was before this recovery was approved."""
    if run.status != RunStatus.APPLIED:
        raise RevertRefused(f"This recovery is {run.status.value.lower().replace('_', ' ')}, so there is nothing applied to roll back.")
    if run.rescue is None or not run.rescue.baseline:
        raise RevertRefused(
            "This run holds no pre-recovery snapshot of the day, so the schedule it replaced cannot be "
            "restored faithfully. Reverting from the change list alone would put back a partial scene."
        )
    if changeset.id not in project.changeset_ids:
        raise RevertRefused("This change set is not applied to the production.")

    try:
        day = project.shoot_day(run.rescue.shoot_day_id)
    except KeyError:
        raise RevertRefused("The shoot day this recovery changed is no longer on the production.")
    if day.status == ShootDayStatus.WRAPPED:
        raise RevertRefused(
            f"Day {day.day_number} has wrapped. What it shot is a matter of record now, and un-approving "
            "the recovery it shot under would not change that."
        )

    # A revert puts the carried scenes back on this day. If one of them has since been committed
    # somewhere else — a downstream placement, or a materialised pickup day created to catch exactly
    # this scene — restoring the snapshot would book it on both, and both would look legitimate.
    # Refused rather than reconciled: un-committing another day's schedule is a second decision, and
    # it is the producer's, not this function's. The message names the day so it can be undone there
    # first.
    elsewhere: list[str] = []
    for item in run.rescue.baseline:
        for other in project.shoot_days:
            if other.id == day.id:
                continue
            if any(i.scene_id == item.scene_id and i.status != ScheduleItemStatus.DEFERRED for i in other.items):
                elsewhere.append(f"Scene {project.scene(item.scene_id).number} on Day {other.day_number}")
    if elsewhere:
        raise RevertRefused(
            "This recovery cannot be reverted while the scenes it carried are booked elsewhere: "
            + "; ".join(sorted(set(elsewhere)))
            + ". Release that placement first, or the same scene would sit on two days."
        )

    before = [i.model_copy(deep=True) for i in day.items]

    # The full snapshot, not the inverted diff — see the module docstring.
    day.items = [i.model_copy(deep=True) for i in run.rescue.baseline]
    day.items.sort(key=lambda i: to_minutes(i.start))
    day.equipment_calls = derive_equipment_calls(project, day, day.items)
    day.transport = derive_transport(project, day, day.items)

    # The status the day carried before the recovery, taken from the change set's own record of it.
    status_change = next((c for c in changeset.changes if c.entity_type == "shoot_day" and c.field == "status"), None)
    if status_change and status_change.before:
        day.status = ShootDayStatus(status_change.before)

    project.changeset_ids.remove(changeset.id)
    run.status = RunStatus.AWAITING_APPROVAL
    run.rescue.changeset = None
    run.rescue.actions = []
    project.updated_at = utcnow()

    inverted = ChangeSet(
        project_id=project.id,
        shoot_day_id=day.id,
        run_id=run.id,
        disruption_id=changeset.disruption_id,
        recovery_option_id=changeset.recovery_option_id,
        changes=[
            Change(
                entity_type=c.entity_type,
                entity_id=c.entity_id,
                label=c.label,
                field=c.field,
                before=c.after,
                after=c.before,
                reason=f"Reverted by {reverted_by}: {reason}",
            )
            for c in changeset.changes
        ],
        summary=f"Reverted {changeset.summary} — {reason}",
    )
    inverted.approved_by = reverted_by
    inverted.applied_at = utcnow()

    violations = _revalidate(project, day)
    return {
        "changeset": inverted,
        "reverted_changeset_id": changeset.id,
        "day": day,
        "restored_items": len(day.items),
        "removed_items": len(before) - len(day.items),
        # The day is back under the disruption it was rescued from, so it may well be infeasible
        # again — that is the state being restored, not a fault in the restore.
        "hard_violations": [v.model_dump(mode="json") for v in violations if v.hard],
        "note": (
            f"Day {day.day_number} is back to the schedule it held before the recovery was approved. The "
            "recovery options are awaiting a decision again; the original change set stays on the record as "
            "approved, with this revert recorded against it."
        ),
    }
