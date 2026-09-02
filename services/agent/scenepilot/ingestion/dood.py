"""Day-Out-Of-Days (DOOD) matrix — which performer is needed on which day, and what the gaps cost.

The DOOD is the document a UPM reads to answer one question: how many days is the production paying
each performer for, and how many of those does it actually shoot them on? Everything else on it
exists to make that comparison legible.

The codes are the trade's, and only the ones this production's state can support are ever emitted:

* ``SW``  — start work (a performer's first shooting day)
* ``W``   — work
* ``WF``  — work finish (their last)
* ``SWF`` — start-work-finish, a single-day engagement
* ``H``   — hold: a day between their first and last that the production pays for and does not shoot

``TR`` travel, ``FT`` fitting and ``R`` rehearsal are real DOOD codes and are deliberately absent:
all three are prep events and this schedule begins at Day 3 of principal photography, so a matrix
that printed a fitting day nobody booked would be a wardrobe call invented by a renderer. The legend
names them as unmodelled instead — the gap is stated rather than filled.

``D``/``P`` — drop and pickup — is the exception, and it is the one that matters, because it is the
only lever a production has against hold-day cost. Whether a hold run *can* be released is a term of
the agreement in force rather than a scheduling choice, so it is read off the labor pack and reported
per performer as an advisory: what the pack permits, how long the runs actually are, and what
releasing them would be worth. No cell is ever marked ``D``/``P`` off that, because releasing a
performer is a producer's decision with consequences this system does not model.

Hold days are the expensive part and therefore the part that must not be guessed. The cost of one is
the performer's own contracted `day_rate_inr`; a performer the production has stated no rate for is
counted but not priced, and the matrix says so rather than defaulting. It used to default: every
performer was worth a flat ₹25,000 a day, which made a lead's idle day and a stunt double's the same
money and put a rupee figure on screen that no contract contained.
"""

from __future__ import annotations

import re
from typing import Any

from ..domain.breakdown_models import CastDOODEntry
from ..domain.enums import ResourceType
from ..domain.models import Project, ScheduleItem
from ..services.labor_rules import active_pack

# What each emitted code means, for the legend the matrix prints beside itself.
DOOD_CODES: dict[str, str] = {
    "SW": "Start work — the performer's first shooting day",
    "W": "Work",
    "WF": "Work finish — their last shooting day",
    "SWF": "Start-work-finish — a single-day engagement",
    "H": "Hold — a day between the first and last that the production pays for and does not shoot",
}

# Codes a real DOOD carries that this production has no state behind. Named so the matrix reports
# the gap rather than looking complete.
#
# All three are *prep* events, and this production's schedule begins at Day 3 of principal
# photography — there is no prep window here for them to sit in. They stay listed rather than being
# quietly dropped, because a DOOD without a travel column looks complete to somebody who does not
# know to miss it. `D`/`P` is no longer here: it is not unmodelled any more, it is *decided*, by the
# labor pack, and the answer is reported per performer — see `_drop_pickup`.
UNMODELLED_CODES: dict[str, str] = {
    "TR": "Travel day — this production books no cast travel, so no day is a travel day",
    "FT": "Fitting — no wardrobe fittings are scheduled; the schedule starts at Day 3 of principal photography",
    "R": "Rehearsal — no rehearsal days are scheduled, for the same reason",
}


def _hold_runs(day_status: dict[str, str], ordered_day_ids: list[str]) -> list[list[str]]:
    """Consecutive stretches of `H`. A drop-and-pickup releases a *run*, never a scattered day."""
    runs: list[list[str]] = []
    current: list[str] = []
    for day_id in ordered_day_ids:
        if day_status.get(day_id) == "H":
            current.append(day_id)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _drop_pickup(pack, day_status: dict[str, str], ordered_day_ids: list[str], day_rate: int | None) -> dict[str, Any]:
    """Whether any of this performer's hold runs could be released under the agreement in force.

    This is the question a DOOD exists to answer. A hold day is money the production spends to not
    shoot somebody, and the only lever against it is the drop-and-pickup provision: release the
    performer, re-engage them later, pay for neither the gap nor a new start. Whether that is
    available is a term of the agreement, not a scheduling choice — so it is read off the pack, and a
    pack that models no such provision gets that answer, not a blank.

    Deliberately advisory. Releasing a performer is a producer's decision with consequences this
    system does not model (availability, goodwill, a re-engagement that may not be at the same rate),
    so nothing here is applied to the matrix or netted off a cost. It states what the agreement
    permits and what it would be worth; the producer decides.
    """
    runs = _hold_runs(day_status, ordered_day_ids)
    total_hold = sum(len(r) for r in runs)
    longest = max((len(r) for r in runs), default=0)
    minimum = pack.drop_pickup_minimum_days

    if total_hold == 0:
        return {"available": None, "minimum_days": minimum, "longest_hold_run": 0, "releasable_days": 0, "saving_inr": None,
                "note": "No hold days, so there is nothing to release."}
    if minimum is None:
        return {
            "available": False, "minimum_days": None, "longest_hold_run": longest, "releasable_days": 0, "saving_inr": None,
            "note": (
                f"{pack.name} models no drop-and-pickup provision, so all {total_hold} hold day(s) are paid. "
                "Under this agreement a performer engaged across a gap is held through it and no rescheduling changes that."
            ),
        }
    releasable = sum(len(r) for r in runs if len(r) >= minimum)
    saving = releasable * day_rate if releasable and day_rate else None
    if releasable:
        note = (
            f"{releasable} of {total_hold} hold day(s) sit in a run of {minimum}+ days and could be released under "
            f"{pack.name}'s drop-and-pickup. Advisory only — re-engaging a released performer is a producer's decision."
        )
    else:
        note = (
            f"The longest hold run is {longest} day(s); {pack.name} allows a drop and pickup only from "
            f"{minimum} days, so none of the {total_hold} hold day(s) can be released."
        )
    return {"available": bool(releasable), "minimum_days": minimum, "longest_hold_run": longest,
            "releasable_days": releasable, "saving_inr": saving, "note": note}


def unlinked_characters(project: Project) -> list[dict[str, Any]]:
    """Characters the breakdown found in the draft that no performer resource is attached to.

    This replaces a branch that used to fold those characters straight into the matrix as *work days*.
    It never fired — it compared a breakdown element's name (`AARAV`) against a resource's full name
    (`Aarav Mehta (Rider / lead)`), which cannot match — and it should not have: a work day asserted
    from a language model's read of a draft is a day on a UPM's budget that nobody cast and nobody
    scheduled. `Scene.cast_ids` is the production's own casting and is the only thing this matrix
    counts.

    The signal underneath it is real, though, and worth keeping as what it actually is: a *linking
    gap*. A scene whose dialogue names a character with no performer attached is a scene somebody
    still has to cast, and that is a question for the producer rather than an entry in the grid.
    """
    scheduled_ids = {i.scene_id for d in project.shoot_days for i in d.items}
    attached = {
        part
        for r in project.resources if r.type == ResourceType.CAST
        for part in _name_tokens(r.name)
    }

    def is_scheduled(scene_number: str) -> bool:
        """Resolved through the project's own lookup, not by rebuilding an `sc_{n}` id.

        The id convention holds for the seed and for nothing else, and a helper that assumes it
        answers "not scheduled" for every scene in any project that numbers ids differently — which
        is the quiet kind of wrong, because the row still renders.
        """
        try:
            return project.scene_by_number(scene_number).id in scheduled_ids
        except KeyError:
            return False

    gaps: dict[str, set[str]] = {}
    for parsed in getattr(project, "parsed_screenplay_scenes", []):
        for element in parsed.elements:
            if element.category != "CAST":
                continue
            if not (set(_name_tokens(element.name)) & attached):
                gaps.setdefault(element.name, set()).add(parsed.scene_number)
    return [
        {
            "character": name,
            "scenes": sorted(scenes),
            "scheduled": any(is_scheduled(n) for n in scenes),
        }
        for name, scenes in sorted(gaps.items())
    ]


def _name_tokens(name: str) -> set[str]:
    """`Meera Iyer (Zoya)` → {meera, iyer, zoya}. Enough to tell "cast" from "not cast", nothing more."""
    return {w for w in re.split(r"[^\w]+", name.lower()) if len(w) > 2}


def _scene_days(project: Project, days: list, overrides: dict[str, list[ScheduleItem]] | None) -> dict[str, list[str]]:
    """scene id → the shoot days it is scheduled on, with any day's items overridden.

    `overrides` is what lets a *before* matrix be built from a schedule that is no longer committed:
    the rescue's baseline for one day, against the real items for every other.
    """
    scene_to_days: dict[str, list[str]] = {}
    for day in days:
        items = overrides.get(day.id, day.items) if overrides else day.items
        for item in items:
            scene_to_days.setdefault(item.scene_id, []).append(day.id)
    return scene_to_days


def build_dood_matrix(project: Project, overrides: dict[str, list[ScheduleItem]] | None = None) -> list[CastDOODEntry]:
    """Generate the DOOD matrix for every performer in a project."""
    sorted_days = sorted(project.shoot_days, key=lambda d: (d.day_number, d.date))
    if not sorted_days:
        return []

    scene_to_days = _scene_days(project, sorted_days, overrides)
    # The same agreement the board is priced and rejected under, so the matrix cannot offer a release
    # the validator's pack does not provide for.
    pack = active_pack(project)

    # Cast in cast-number order, which is the order a DOOD is read in and the order it is printed
    # in: 1 first, then down the billing. A performer with no number yet sorts last, by name, rather
    # than being handed a position that would look like a number it does not have.
    cast_resources = sorted(
        (r for r in project.resources if r.type == ResourceType.CAST),
        key=lambda r: (r.cast_number is None, r.cast_number or 0, r.name),
    )

    entries: list[CastDOODEntry] = []

    for cast in cast_resources:
        # The production's own casting, and only that. See `unlinked_characters` for why a
        # breakdown element never adds a work day here.
        working_day_ids: set[str] = set()
        for scene in project.scenes:
            if cast.id in scene.cast_ids:
                for day_id in scene_to_days.get(scene.id, []):
                    working_day_ids.add(day_id)

        working_indices = [i for i, d in enumerate(sorted_days) if d.id in working_day_ids]

        day_status: dict[str, str] = {}
        total_work = len(working_indices)
        total_hold = 0

        if not working_indices:
            entries.append(
                CastDOODEntry(
                    cast_id=cast.id,
                    cast_number=cast.cast_number,
                    name=cast.name,
                    day_status={d.id: "" for d in sorted_days},
                    total_work_days=0,
                    total_hold_days=0,
                    total_engaged_days=0,
                    day_rate_inr=cast.day_rate_inr or None,
                    hold_day_cost_warning=False,
                    drop_pickup={"available": None, "minimum_days": pack.drop_pickup_minimum_days,
                                 "longest_hold_run": 0, "releasable_days": 0, "saving_inr": None,
                                 "note": "This performer is not on the schedule, so there is no engagement to hold."},
                )
            )
            continue

        first_idx = working_indices[0]
        last_idx = working_indices[-1]

        for i, d in enumerate(sorted_days):
            if i in working_indices:
                if total_work == 1:
                    day_status[d.id] = "SWF"  # Start-Work-Finish
                elif i == first_idx:
                    day_status[d.id] = "SW"   # Start-Work
                elif i == last_idx:
                    day_status[d.id] = "WF"   # Work-Finish
                else:
                    day_status[d.id] = "W"    # Work
            elif first_idx < i < last_idx:
                day_status[d.id] = "H"        # Hold day
                total_hold += 1
            else:
                day_status[d.id] = ""         # Not active

        # The performer's own contracted rate. `None` where the production has stated none, which is
        # what stops the matrix quoting a retention cost no contract contains.
        day_rate = cast.day_rate_inr or None
        est_hold_cost = total_hold * day_rate if day_rate else None

        warning = False
        warn_msg = None
        if total_hold >= 2:
            warning = True
            cost = f" (approx. ₹{est_hold_cost:,} retention cost)" if est_hold_cost else " (no day rate on file, so the retention cost is not priced)"
            warn_msg = (
                f"{cast.name} has {total_hold} hold day(s) between active calls{cost}. Consider clustering scenes."
            )

        entries.append(
            CastDOODEntry(
                cast_id=cast.id,
                cast_number=cast.cast_number,
                name=cast.name,
                day_status=day_status,
                total_work_days=total_work,
                total_hold_days=total_hold,
                # First call to last, inclusive: what the production is engaged for, which is the
                # figure a UPM compares against the work days beside it.
                total_engaged_days=last_idx - first_idx + 1,
                day_rate_inr=day_rate,
                hold_day_cost_warning=warning,
                estimated_hold_cost_inr=est_hold_cost,
                warning_message=warn_msg,
                drop_pickup=_drop_pickup(pack, day_status, [d.id for d in sorted_days], day_rate),
            )
        )

    return entries


def dood_totals(project: Project, entries: list[CastDOODEntry]) -> dict[str, Any]:
    """The bottom line a UPM reads first: how many days is this cast costing, and how many shoot.

    The ratio is the whole point of the document. A cast worked 8 days out of 12 engaged is a cast
    the production is paying a third of the time to do nothing, and no individual row says that.

    Retention is summed only across performers who have a rate; the count of those who do not is
    reported beside it, so the figure reads as the floor it is rather than as the total.
    """
    engaged = [e for e in entries if e.total_engaged_days > 0]
    priced = [e for e in engaged if e.estimated_hold_cost_inr is not None]
    unpriced = [e for e in engaged if e.estimated_hold_cost_inr is None and e.total_hold_days > 0]
    hold_cost = sum(e.estimated_hold_cost_inr or 0 for e in priced)
    pack = active_pack(project)
    return {
        "performers": len(entries),
        "performers_engaged": len(engaged),
        "work_days": sum(e.total_work_days for e in entries),
        "hold_days": sum(e.total_hold_days for e in entries),
        "engaged_days": sum(e.total_engaged_days for e in entries),
        "hold_cost_inr": hold_cost if priced else None,
        "unpriced_performers": [e.name for e in unpriced],
        "labor_pack": pack.name,
        "drop_pickup_minimum_days": pack.drop_pickup_minimum_days,
        "releasable_days": sum(e.drop_pickup.get("releasable_days") or 0 for e in entries),
    }


def dood_delta(project: Project, day_id: str, baseline_items: list[ScheduleItem]) -> dict[str, Any]:
    """What an approved recovery did to the cast schedule, performer by performer.

    An aggregate cost delta is an abstraction; "the rain put Vikram Rao on a paid hold day, and that
    day costs ₹95,000" is a person and a number, and it is the sentence a producer actually reacts
    to. Both matrices are built the same way over the same days — only this day's items differ — so
    any change in a cell is attributable to the recovery and nothing else.

    Returns the empty shape when nothing moved, rather than a made-up highlight.
    """
    before = {e.cast_id: e for e in build_dood_matrix(project, overrides={day_id: baseline_items})}
    after = {e.cast_id: e for e in build_dood_matrix(project)}

    changes: list[dict[str, Any]] = []
    for cast_id, now in after.items():
        was = before.get(cast_id)
        if was is None:
            continue
        moved_cells = [
            {"shoot_day_id": d, "before": was.day_status.get(d, ""), "after": now.day_status.get(d, "")}
            for d in now.day_status
            if was.day_status.get(d, "") != now.day_status.get(d, "")
        ]
        gained_holds = now.total_hold_days - was.total_hold_days
        if not moved_cells and gained_holds == 0:
            continue
        cost = gained_holds * now.day_rate_inr if now.day_rate_inr and gained_holds > 0 else None
        changes.append({
            "cast_id": cast_id,
            "cast_number": now.cast_number,
            "name": now.name,
            "cells": moved_cells,
            "hold_days_before": was.total_hold_days,
            "hold_days_after": now.total_hold_days,
            "hold_days_gained": gained_holds,
            "work_days_before": was.total_work_days,
            "work_days_after": now.total_work_days,
            "day_rate_inr": now.day_rate_inr,
            "added_hold_cost_inr": cost,
            "unpriced_reason": None if now.day_rate_inr else "No day rate is on file for this performer, so the added hold is counted and not priced.",
        })

    # The one line worth putting on screen: who this cost the most, and what it cost.
    priced = [c for c in changes if c["added_hold_cost_inr"]]
    headline = None
    if priced:
        worst = max(priced, key=lambda c: c["added_hold_cost_inr"])
        headline = (
            f"{worst['name']} gains {worst['hold_days_gained']} paid hold day"
            f"{'' if worst['hold_days_gained'] == 1 else 's'} — ₹{worst['added_hold_cost_inr']:,} at their "
            f"₹{worst['day_rate_inr']:,}/day rate."
        )
    return {
        "shoot_day_id": day_id,
        "changes": changes,
        "headline": headline,
        "total_added_hold_cost_inr": sum(c["added_hold_cost_inr"] for c in priced) if priced else None,
        "unpriced_performers": [c["name"] for c in changes if c["hold_days_gained"] > 0 and not c["day_rate_inr"]],
    }
