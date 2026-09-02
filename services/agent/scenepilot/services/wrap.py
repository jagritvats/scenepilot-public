"""Closing a shoot day out: what was shot, what carries, and when the camera stopped.

`ShootDayStatus.WRAPPED` and `ScheduleItemStatus.COMPLETED` existed from the first commit and were
written by nothing but the seed. So `services/completion.py` computed a full per-scene record on
every day payload that no screen could ever read, `services/day_cost.py`'s record branch could never
fire, and `build_dpr` worked on the one seeded wrapped day and could never work on any other. This
module is the missing verb.

Two decisions here look like bugs until you know why.

**A carried strip stays on the day.** Everywhere else a `DEFERRED` item leaves the schedule —
`apply_changeset` filters it out on its last line. Here it must not: `day_completion` derives what
did not make it from the items still on the day, so filtering would have the DPR print "nothing
outstanding" for a day that carried a scene. That is also why this builds its ChangeSet by hand
rather than routing through `apply_changeset`, which would additionally mark a shot item `MOVED` for
carrying an actual end time, and so count it as outstanding too.

**Equipment calls and transport are not re-derived.** Everywhere else they are, because they are a
function of the schedule. After a wrap they are the record of what was *called* — the crane on the
truck at 15:00 was on the truck, whatever the day ended up shooting — and re-deriving them against a
board where a scene is now deferred would un-call it retrospectively.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..domain.enums import ScheduleItemStatus, ShootDayStatus
from ..domain.models import Change, ChangeSet, Project, ShootDay, utcnow
from .completion import day_completion
from .timeutil import to_hhmm, to_minutes

SHOT, CARRIED = "SHOT", "CARRIED"


class WrapRefused(ValueError):
    """A wrap the engine will not make. The message is the reason a producer needs."""


class WrapOutcome(BaseModel):
    item_id: str
    outcome: str  # SHOT | CARRIED
    actual_end: str | None = None
    note: str | None = Field(default=None, max_length=500)


def _time_or_refuse(label: str, value: str) -> int:
    try:
        return to_minutes(value)
    except ValueError as exc:
        raise WrapRefused(f"{label}: {exc}") from exc


def wrap_day(
    project: Project,
    day_id: str,
    outcomes: list[WrapOutcome],
    *,
    camera_wrap: str | None = None,
    wrapped_by: str = "producer",
) -> dict[str, Any]:
    """Mark every strip shot or carried and close the day out."""
    try:
        day = project.shoot_day(day_id)
    except KeyError:
        raise WrapRefused("That shoot day is not on this production.")
    if day.status == ShootDayStatus.WRAPPED:
        raise WrapRefused(
            f"Day {day.day_number} wrapped on {day.date}. Wrapping it again with different outcomes would "
            "rewrite a record rather than make one."
        )
    if not day.items:
        raise WrapRefused(
            f"Day {day.day_number} has nothing scheduled on it. A day with no strips has no delivery to "
            "record, and wrapping it would leave a day reporting neither a plan nor a result."
        )

    by_id = {o.item_id: o for o in outcomes}
    if len(by_id) != len(outcomes):
        raise WrapRefused("The same strip is accounted for twice.")
    unknown = sorted(i for i in by_id if not any(x.id == i for x in day.items))
    if unknown:
        raise WrapRefused(f"{', '.join(unknown)} is not on Day {day.day_number}.")
    # Both directions. A strip nobody accounted for is not neutral: `day_completion` reads anything
    # short of COMPLETED as outstanding, so an omission would silently record a scene as carried and
    # charge the day this production's carry-over cost for it.
    missing = [i for i in day.items if i.id not in by_id]
    if missing:
        names = ", ".join(f"Sc {project.scene(i.scene_id).number}" for i in missing)
        raise WrapRefused(
            f"{names} {'is' if len(missing) == 1 else 'are'} not accounted for. Every strip has to be marked "
            "shot or carried before the day closes."
        )
    bad = [o.outcome for o in outcomes if o.outcome not in (SHOT, CARRIED)]
    if bad:
        raise WrapRefused(f"Unknown outcome {bad[0]!r}: a strip is either {SHOT} or {CARRIED}.")

    wrap_min = _time_or_refuse("camera_wrap", camera_wrap) if camera_wrap else None
    changes: list[Change] = []
    shot_ends: list[int] = []

    for item in day.items:
        outcome = by_id[item.id]
        scene = project.scene(item.scene_id)
        label = f"Scene {scene.number}"
        before_status = item.status.value

        if outcome.outcome == SHOT:
            if outcome.actual_end:
                end_min = _time_or_refuse(f"Sc {scene.number} actual_end", outcome.actual_end)
                if end_min <= to_minutes(item.start):
                    raise WrapRefused(
                        f"Sc {scene.number} cannot have ended at {outcome.actual_end}; it started at {item.start}."
                    )
                if outcome.actual_end != item.end:
                    changes.append(Change(
                        entity_type="schedule_item", entity_id=item.id, label=label, field="end",
                        before=item.end, after=outcome.actual_end,
                        reason=f"Wrapped by {wrapped_by}: ran to {outcome.actual_end}",
                    ))
                    item.end = outcome.actual_end
            shot_ends.append(to_minutes(item.end))
            item.status = ScheduleItemStatus.COMPLETED
        else:
            item.status = ScheduleItemStatus.DEFERRED

        if outcome.note:
            item.note = outcome.note
        changes.append(Change(
            entity_type="schedule_item", entity_id=item.id, label=label, field="status",
            before=before_status, after=item.status.value,
            reason=outcome.note or (f"Wrapped by {wrapped_by}" if outcome.outcome == SHOT else f"Carried by {wrapped_by}"),
        ))

    if wrap_min is not None and shot_ends and wrap_min < max(shot_ends):
        raise WrapRefused(
            f"The camera cannot have wrapped at {camera_wrap} on a day whose last completed scene ran to "
            f"{to_hhmm(max(shot_ends))}."
        )

    changes.append(Change(
        entity_type="shoot_day", entity_id=day.id, label=f"Day {day.day_number}", field="status",
        before=day.status.value, after=ShootDayStatus.WRAPPED.value,
        reason=f"Wrapped by {wrapped_by}",
    ))
    day.camera_wrap = camera_wrap or (to_hhmm(max(shot_ends)) if shot_ends else None)
    day.status = ShootDayStatus.WRAPPED

    carried = [i.scene_id for i in day.items if i.status != ScheduleItemStatus.COMPLETED]
    shot_count = len(day.items) - len(carried)
    changeset = ChangeSet(
        project_id=project.id, shoot_day_id=day.id, changes=changes,
        summary=(
            f"Day {day.day_number} wrapped at {day.camera_wrap or 'an unrecorded time'} — "
            f"{shot_count} scene(s) completed, {len(carried)} carried."
        ),
    )
    # Stamped by hand rather than through `apply_changeset` — see the module docstring for the two
    # things that function would do to a wrap that it must not.
    changeset.approved_by = wrapped_by
    changeset.applied_at = utcnow()
    project.changeset_ids.append(changeset.id)
    project.updated_at = utcnow()

    return {
        "changeset": changeset,
        "day": day,
        "completion": day_completion(project, day),
        "carried_scene_ids": carried,
    }


def outcomes_from(day: ShootDay) -> list[WrapOutcome]:
    """Every strip shot at its scheduled end — the default a producer edits, not a shortcut past one."""
    return [WrapOutcome(item_id=i.id, outcome=SHOT, actual_end=i.end) for i in day.items]
