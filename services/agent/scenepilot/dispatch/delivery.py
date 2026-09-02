"""Call-sheet field dispatch — a simulated multi-channel delivery log.

Nothing is transmitted. ScenePilot has no WhatsApp, SMS or email integration and is not meant to
grow one: a message to a crew of 45 is a consequential act, and the one thing this product will not
do is fire one without a human. What this module does instead is build the message each recipient
would receive, address it to somebody the production actually models, and open a tracking row
against it — so the delivery view can be shown, and shown as the simulation it is.

Three rules keep the log to the same standard as the rest of the app:

* **Every recipient is a real `Resource`.** The cast come from the scenes the day schedules; the
  department heads are the seed's `CREW` rows, one per department the coordination engine already
  addresses (`services/coordination.py`). Nobody is invented here — an earlier version of this file
  carried seven crew heads and seven Mumbai mobile numbers that existed nowhere else in the product.
* **No state is stamped that nobody observed.** Generation opens every row at `QUEUED` and stops.
  `READ` and `ACKNOWLEDGED` are reachable only through the explicit endpoints behind the buttons in
  the tracking view, so a receipt on screen is always something a person in this session put there.
  A `READ` written at generation time would be a receipt for a message that was never sent.
* **No number is invented to fill a column.** The seed carries no phone numbers at all — every
  `Resource.contact` in it is a desk or a liaison ("Grip vendor", "Mill estate office — R.
  Kulkarni") — and this module does not add any. A recipient with no contact on file dispatches with
  `contact=None`, which the view prints as such. India reserves no documentation number range the
  way Ofcom and the NANP do, so any plausible-looking `+91` mobile here would be somebody's real
  number; the honest column is an empty one.

Call times are read straight out of `build_call_sheet`, so the time in a message cannot drift from
the time on the sheet it was built from.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from ..domain.enums import ResourceType
from ..domain.models import Project, ShootDay
from ..services.callsheet import build_call_sheet

Channel = Literal["WHATSAPP", "SMS", "EMAIL"]
CHANNELS: tuple[Channel, ...] = ("WHATSAPP", "SMS", "EMAIL")

SIMULATION_NOTE = (
    "Simulated delivery log. ScenePilot has no messaging integration — nothing was transmitted over "
    "WhatsApp, SMS or email. Records exist only after an explicit broadcast, and every read and "
    "acknowledged state on them was set by hand in this view."
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DispatchRecipient(BaseModel):
    """Somebody a broadcast would address, resolved from the production's own resources."""

    resource_id: str
    cast_number: int | None = None  # cast only; a crew recipient is not numbered
    name: str
    role: str
    department: str
    contact: str | None = None
    call_time: str
    scenes: list[str] = Field(default_factory=list)
    payload_preview: str = ""


class CrewDispatchRecord(BaseModel):
    id: str
    recipient_id: str
    recipient_name: str
    recipient_role: str
    department: str
    channel: Channel
    contact: str | None = None
    call_time: str
    # No SENT and no DELIVERED: neither ever happened, so neither is a state this log can reach.
    status: Literal["QUEUED", "READ", "ACKNOWLEDGED"] = "QUEUED"
    simulated: bool = True
    queued_at: str = Field(default_factory=_utcnow_iso)
    read_at: str | None = None
    acknowledged_at: str | None = None
    payload_preview: str = ""


# Delivery logs are held in memory on the agent and keyed by the project as well as the day: `day_4`
# is a shoot-day id, not a globally unique one, and two projects reset in the same process must not
# read each other's dispatches.
_DISPATCH_STORE: dict[tuple[str, str], list[CrewDispatchRecord]] = {}


def _cast_role(role: object) -> str:
    return f"Cast ({role})" if role else "Cast"


def dispatch_roster(project: Project, day: ShootDay) -> list[DispatchRecipient]:
    """Who a broadcast would address on this day, and at what time. Reads state; never writes it.

    The distribution list is the call sheet's own cast table plus the production's `CREW` resources,
    so the panel can show exactly who is on it before anybody presses the button — and so the crew
    rows the seed declares are read by the same surface that dispatches to them.
    """
    sheet = build_call_sheet(project, day)
    header = f"{project.title} · Day {day.day_number} ({sheet['date']})"
    wrap = sheet["estimated_wrap"]

    cast_by_name = {r.name: r for r in project.resources if r.type == ResourceType.CAST}
    roster: list[DispatchRecipient] = []
    for row in sheet["cast"]:
        r = cast_by_name.get(row["name"])
        if r is None:
            continue
        scenes = list(row["scenes"])
        roster.append(
            DispatchRecipient(
                resource_id=r.id,
                cast_number=r.cast_number,
                name=r.name,
                role=_cast_role(r.attributes.get("role")),
                department="Cast",
                contact=r.contact,
                call_time=row["call"],
                scenes=scenes,
                # Call and on-set only. The sheet's staggered pickup/HMU/wardrobe breakdown is
                # computed off the first shot rather than off the call, so it can sit earlier than
                # the call time beside it; quoting both here would print a contradiction in one
                # sentence. The two numbers the sheet's cast table leads with agree, so those go.
                payload_preview=(
                    f"{header} — your call {row['call']}, on set {row['on_set']}. "
                    f"Sc {', '.join(scenes) or '—'}. Est. wrap {wrap}."
                ),
            )
        )

    for r in project.resources:
        if r.type != ResourceType.CREW:
            continue
        roster.append(
            DispatchRecipient(
                resource_id=r.id,
                name=r.name,
                role=str(r.attributes.get("role") or "Crew"),
                department=str(r.attributes.get("department") or "Production office"),
                contact=r.contact,
                # A department head is called at the unit call — that is what a unit call is. The
                # gear its department is responsible for has its own call on the sheet's equipment
                # table, derived from the schedule, and is quoted rather than restated here.
                call_time=sheet["unit_call"],
                payload_preview=(
                    f"{header} call sheet — unit call {sheet['unit_call']}, first shot "
                    f"{sheet['first_shot'] or '—'}, est. wrap {wrap}. {sheet['sun']}."
                ),
            )
        )
    return roster


def generate_crew_dispatches(
    project: Project,
    day: ShootDay,
    channels: list[Channel] | None = None,
) -> list[CrewDispatchRecord]:
    """Open one queued delivery row per recipient per channel. Replaces any earlier log for the day."""
    selected = [c for c in (channels or CHANNELS) if c in CHANNELS]
    records = [
        CrewDispatchRecord(
            id=f"disp_{uuid.uuid4().hex[:8]}",
            recipient_id=person.resource_id,
            recipient_name=person.name,
            recipient_role=person.role,
            department=person.department,
            channel=ch,
            contact=person.contact,
            call_time=person.call_time,
            payload_preview=person.payload_preview,
        )
        for person in dispatch_roster(project, day)
        for ch in selected
    ]
    _DISPATCH_STORE[(project.id, day.id)] = records
    return records


def get_dispatches_for_day(project_id: str, day_id: str) -> list[CrewDispatchRecord]:
    """The log as it stands. An empty list means nothing was broadcast — it is not a cue to build one."""
    return _DISPATCH_STORE.get((project_id, day_id), [])


def _find(project_id: str, day_id: str, dispatch_id: str) -> CrewDispatchRecord | None:
    return next((r for r in get_dispatches_for_day(project_id, day_id) if r.id == dispatch_id), None)


def mark_dispatch_read(project_id: str, day_id: str, dispatch_id: str) -> CrewDispatchRecord | None:
    rec = _find(project_id, day_id, dispatch_id)
    if rec is None or rec.status == "ACKNOWLEDGED":
        return rec
    rec.status = "READ"
    rec.read_at = _utcnow_iso()
    return rec


def acknowledge_dispatch(project_id: str, day_id: str, dispatch_id: str) -> CrewDispatchRecord | None:
    rec = _find(project_id, day_id, dispatch_id)
    if rec is None:
        return None
    now = _utcnow_iso()
    rec.read_at = rec.read_at or now
    rec.status = "ACKNOWLEDGED"
    rec.acknowledged_at = now
    return rec


def re_ping_unacknowledged(project_id: str, day_id: str) -> list[CrewDispatchRecord]:
    """Re-queue every row nobody has acknowledged. A read row stays read — re-sending is not un-reading."""
    now = _utcnow_iso()
    repinged: list[CrewDispatchRecord] = []
    for rec in get_dispatches_for_day(project_id, day_id):
        if rec.status == "ACKNOWLEDGED":
            continue
        rec.queued_at = now
        if not rec.payload_preview.startswith("[RE-SEND]"):
            rec.payload_preview = f"[RE-SEND] {rec.payload_preview}"
        repinged.append(rec)
    return repinged
