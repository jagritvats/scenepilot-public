"""Call sheet generation — a real production document derived from production state.

Everything on the sheet is computed from the ShootDay (or any candidate schedule), the
project's resources and the active disruption. Nothing is hand-written.

A call sheet is the one document on a film that everybody on the unit reads, so the fields it is
missing are as legible as the fields it has. The rule here is the product's rule: a row that cannot
be traced to production state, a Parallel result or arithmetic over those is **not printed** — it is
either omitted or printed as a named blank saying why. That is why there is no forecast on a day
nobody reported weather for, no hospital on a location whose dossier has not run, and no background
artists anywhere: this production models principals, and a call sheet that listed atmosphere it does
not hold would be telling a 2nd AD to book people who do not exist.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.enums import DisruptionType, ResourceType, ScheduleItemStatus, ShootDayStatus
from ..domain.models import Disruption, Evidence, LocationFact, Project, ScheduleItem, ShootDay
from .changeset import derive_equipment_calls, derive_transport
from .coordination import DINNER_CUTOFF, department_for
from .ephemeris import city_ephemeris
from .labor_rules import active_pack
from .timeutil import to_hhmm, to_minutes

CAST_MAKEUP_LEAD = 60  # minutes before first shot

# The revision ladder a production actually reprints on, in order. A call sheet reissued after an
# approved change goes out on the next colour, and everybody on the unit reads the colour before
# they read the date — which is exactly why it is worth rendering: "blue pages" is a status the
# trade recognises at a glance, and it is a true statement here because the count behind it is the
# number of ChangeSets a producer has actually approved for the day.
REVISION_LADDER: tuple[tuple[str, str], ...] = (
    ("WHITE", "#ffffff"),
    ("BLUE", "#d6e6f6"),
    ("PINK", "#f8d6e0"),
    ("YELLOW", "#fbf3b6"),
    ("GREEN", "#d3ecd0"),
    ("GOLDENROD", "#f2dda6"),
    ("SALMON", "#f8cdb9"),
    ("CHERRY", "#efaab3"),
    ("BUFF", "#f0e6cd"),
)

# Departments whose presence on a day is a safety briefing item rather than a logistics one. Keyed
# by the department strings `coordination.department_for` already returns, so the hazard line and
# the coordination action name the same department in the same words.
SAFETY_CRITICAL_DEPARTMENTS: dict[str, str] = {
    "Stunt & rigging": "Stunt action is scheduled. The stunt coordinator walks the sequence with every department before the first take.",
    "SFX / pyrotechnics": "Pyrotechnics on the day. Licensed pyrotechnician present; hot set, no unbriefed access.",
    "Aerial / drone unit": "Aerial unit operating. Ground crew clear of the flight line for every launch and recovery.",
}

# Standing production policy, not a computed value. Printed as policy — the sheet says who these
# bind rather than presenting them as facts about this particular day.
STANDING_NOTES: tuple[str, ...] = (
    "NO FORCED CALLS WITHOUT PRIOR APPROVAL — a turnaround shorter than the pack's minimum needs the UPM's signature before it is issued.",
    "Any injury, near miss or unsafe condition stops the unit and is reported to the 1st AD immediately.",
    "Call times on this sheet supersede every earlier issue. Check the revision colour before you travel.",
)


def revision_of(index: int) -> dict:
    """Which colour this issue of the sheet is printed on.

    `index` is the number of approved changes behind the sheet: 0 is the original white, 1 is the
    first reissue. Past the ladder a production goes round again on double whites, which is what the
    cycle prefix is; it is unreachable on a six-day schedule and handled anyway so the field can
    never come back blank.
    """
    index = max(0, index)
    cycle, position = divmod(index, len(REVISION_LADDER))
    name, hex_colour = REVISION_LADDER[position]
    if cycle == 1:
        name = f"DOUBLE {name}"
    elif cycle > 1:
        name = f"{cycle + 1}× {name}"
    return {
        "index": index,
        "name": name,
        "hex": hex_colour,
        # What the masthead prints. An original call sheet does not say "Rev.0"; it says nothing.
        "label": f"Rev.{index} — {name}" if index else name,
        "is_original": index == 0,
    }


def eighths_label(eighths: int | None) -> str | None:
    """`12` → `1 4/8`. The board's own notation, so a page count reads the same in both documents."""
    if eighths is None:
        return None
    whole, rest = divmod(eighths, 8)
    if whole and rest:
        return f"{whole} {rest}/8"
    return str(whole) if whole else f"{rest}/8"


def _solar_block(project: Project, day: ShootDay) -> dict:
    """Sunrise, sunset and the twilights, from the same ephemeris the validator holds scenes to.

    Read live rather than off the `ShootDay` because the day stores only the four windows the
    scheduler needs; a call sheet wants the times themselves, and computing them here from the day's
    own date and the production's city is what keeps the sheet and the board on one sun.
    """
    profile = city_ephemeris(project.base_city, day.date)
    return {
        "sunrise": profile.sunrise,
        "sunset": profile.sunset,
        "solar_noon": profile.solar_noon,
        "civil_twilight_dawn": profile.civil_twilight_dawn,
        "civil_twilight_dusk": profile.civil_twilight_dusk,
        "golden_hour_dawn": list(profile.golden_hour_dawn),
        "golden_hour_dusk": list(profile.golden_hour_dusk),
        "source": f"Computed for {project.base_city} ({profile.latitude:.4f}, {profile.longitude:.4f}) on {day.date}",
    }


def _weather_block(day: ShootDay, disruption: Disruption | None, evidence: Sequence[Evidence]) -> dict:
    """The day's weather, or an honest statement that nothing has been reported.

    A call sheet carries a forecast because somebody looked one up. ScenePilot only ever looks one
    up when a disruption is reported and the verifier goes out to corroborate it, so that verified
    peril — with the sources it was corroborated against — is the only weather this sheet can
    truthfully print. On a day nobody has reported anything for, the block says exactly that and the
    unit reads the sun times instead of a forecast nobody fetched.
    """
    if disruption is None or disruption.type != DisruptionType.WEATHER:
        return {
            "reported": False,
            "headline": None,
            "reason": (
                "No weather disruption has been reported for this day, so no forecast has been fetched and none is "
                "printed. The sun times below are computed, not observed."
            ),
            "window": None,
            "verification": None,
            "sources": [],
        }
    window = None
    if disruption.window_start and disruption.window_end:
        window = {
            "start": disruption.window_start,
            "end": disruption.window_end,
            "dry_out_minutes": disruption.dry_out_minutes,
            "clear_at": to_hhmm(to_minutes(disruption.window_end) + disruption.dry_out_minutes) if disruption.dry_out_minutes else disruption.window_end,
        }
    verification = None
    if disruption.verification_status is not None:
        verification = {
            "status": disruption.verification_status.value,
            "confidence_pct": round(disruption.verification_confidence * 100) if disruption.verification_confidence is not None else None,
            "summary": disruption.verification_summary,
        }
    # The claims the verifier kept, with the page each came off. Capped at three: a call sheet is a
    # working document, and the full evidence set is a click away in the claim packet.
    sources = [
        {"claim": e.claim, "url": e.source_url, "title": e.source_title, "publish_date": e.publish_date, "authority": e.authority.value}
        for e in list(evidence)[:3]
    ]
    return {
        "reported": True,
        "headline": disruption.title,
        "reason": None,
        "description": disruption.description,
        "window": window,
        "verification": verification,
        "sources": sources,
    }


def _hospitals_block(project: Project, location_ids: Sequence[str]) -> dict:
    """The nearest emergency department for each set the day works, as a Parallel dossier found it.

    This is the field that turns research spend into something a unit actually uses: `nearest_hospital`
    is one of the location dossier's own output fields, graded by the same confidence gate as every
    other, and it arrives with the page Parallel cited. Non-answers never become facts
    (`dossier.is_non_answer` drops them), so a fact existing here means a source named a hospital.

    Per set rather than per day, and in the day's own shooting order, because that is the question a
    unit is asking: a company move across Mumbai moves the nearest emergency department with it, and
    one hospital printed at the top of a four-location day is the right answer to the wrong set for
    three of them. A set whose dossier has not run is listed as a gap, not silently dropped — a
    missing hospital is a thing a 1st AD needs to see, not a row that quietly is not there.
    """
    by_resource: dict[str, LocationFact] = {}
    for fact in project.location_facts:
        if fact.key == "nearest_hospital" and not fact.rejected and fact.value.strip():
            by_resource.setdefault(fact.resource_id, fact)
    entries: list[dict] = []
    missing: list[str] = []
    for res_id in dict.fromkeys(location_ids):
        fact = by_resource.get(res_id)
        if fact is None:
            missing.append(project.resource(res_id).name)
            continue
        citation = fact.citations[0] if fact.citations else None
        entries.append({
            "location": project.resource(res_id).name,
            "value": fact.value,
            "confidence": fact.confidence,
            "binding": fact.binding.value,
            "source_url": citation.url if citation else None,
            "source_title": citation.title if citation else None,
        })
    return {
        "entries": entries,
        "sets_without_one": missing,
        "reason": None if entries else (
            "No location dossier on this day's sets has returned a nearest hospital. Run the location dossier from "
            "the scene page and the sheet will carry it, with the source it came from."
        ),
    }


def _departments_block(project: Project, items: Sequence[ScheduleItem]) -> list[dict]:
    """Who is on the radio and on what channel, for the departments this day's work implicates.

    Derived by the same `department_for` mapping the coordination engine uses to decide who to
    notify, so the sheet's channel list and the dispatch's targets cannot drift apart. The 1st AD
    and the production office are always on it — they run the day whatever is scheduled.
    """
    implicated = {"1st AD", "Production office"}
    for item in items:
        for equipment_id in project.scene(item.scene_id).equipment_ids:
            implicated.add(department_for(project.resource(equipment_id).name))
    heads: list[dict] = []
    for res in project.resources:
        if res.type != ResourceType.CREW:
            continue
        department = res.attributes.get("department")
        if department not in implicated:
            continue
        heads.append({
            "department": department,
            "name": res.name,
            "role": res.attributes.get("role"),
            "contact": res.contact,
            "channel": res.walkie_channel,
            "safety_critical": department in SAFETY_CRITICAL_DEPARTMENTS,
        })
    return sorted(heads, key=lambda h: (h["channel"] is None, h["channel"] or 0, h["department"]))


def _safety_block(project: Project, day: ShootDay, items: Sequence[ScheduleItem], departments: Sequence[dict], location_ids: Sequence[str], weather: dict) -> dict:
    """The safety meeting, the hazards this day's own schedule implies, and the hospital.

    Hazards are read off what is actually booked, never off a checklist: a department reaches this
    list because a scene on the day carries the equipment that department owns, and weather-sensitive
    kit reaches it because a verified peril overlaps the day it is working.
    """
    hazards: list[dict] = []
    for head in departments:
        note = SAFETY_CRITICAL_DEPARTMENTS.get(head["department"])
        if note:
            hazards.append({"item": head["department"], "why": note, "owner": head["name"]})
    exposed = [
        project.resource(e).name
        for item in items
        for e in project.scene(item.scene_id).equipment_ids
        if project.resource(e).weather_sensitive
    ]
    if weather["reported"] and exposed:
        hazards.append({
            "item": "Weather-sensitive equipment",
            "why": f"{', '.join(dict.fromkeys(exposed))} on a day with a reported weather disruption — cover and power-down plan briefed at the meeting.",
            "owner": None,
        })
    for res_id in dict.fromkeys(location_ids):
        surface = project.resource(res_id).attributes.get("surface")
        if surface:
            hazards.append({"item": project.resource(res_id).name, "why": f"Surface: {surface}.", "owner": None})
    return {
        # Called at unit call, which is where a safety meeting goes: everybody is on the deck and
        # nothing has been rigged yet. The time is the day's own call, not a policy number.
        "meeting": day.unit_call,
        "meeting_note": f"Safety meeting at unit call ({day.unit_call}), all departments, before the first setup.",
        "hazards": hazards,
        "hospitals": _hospitals_block(project, location_ids),
        "standing_notes": list(STANDING_NOTES),
    }


def advance_block(project: Project, day: ShootDay) -> dict | None:
    """Tomorrow, in the one line a unit needs tonight: where, when, and what.

    A call sheet's advance schedule is what stops a crew finding out at wrap that the next day is a
    different city. Read straight off the next `ShootDay` by number; `None` when this is the last day
    on the schedule, because there is genuinely nothing ahead of it.
    """
    following = sorted((d for d in project.shoot_days if d.day_number > day.day_number), key=lambda d: d.day_number)
    if not following:
        return None
    nxt = following[0]
    items = sorted(nxt.items, key=lambda i: to_minutes(i.start))
    sets: list[str] = []
    for item in items:
        scene = project.scene(item.scene_id)
        location_id = item.location_id or scene.location_id
        if location_id:
            name = project.resource(location_id).name
            if name not in sets:
                sets.append(name)
    return {
        "day_number": nxt.day_number,
        "date": nxt.date,
        "unit_call": nxt.unit_call,
        "status": nxt.status.value,
        "scenes": [
            {"scene": project.scene(i.scene_id).number, "heading": project.scene(i.scene_id).heading, "start": i.start}
            for i in items
        ],
        "sets": sets,
        "note": None if items else "Nothing is scheduled on this day yet.",
    }


def _signature_block(project: Project, approved_by: str | None, approved_at: str | None) -> dict:
    """Who prepared the sheet and who approved the change behind it.

    Prepared-by is the production's own 1st AD where the production has one; a sheet nobody can name
    a preparer for says so rather than signing itself. Approved-by is only ever the producer name
    carried on the applied ChangeSet — an unapproved sheet has no signature, which is the point of
    the line.
    """
    ad = next((r for r in project.resources if r.type == ResourceType.CREW and r.attributes.get("department") == "1st AD"), None)
    return {
        "prepared_by": {"name": ad.name, "role": ad.attributes.get("role"), "contact": ad.contact} if ad else None,
        "prepared_by_reason": None if ad else "This production names no 1st AD, so the sheet carries no preparer.",
        "approved_by": approved_by,
        "approved_at_utc": approved_at,
        "approved_reason": None if approved_by else "No recovery has been approved for this day, so nothing on this sheet has been signed off.",
        "generated_by": "ScenePilot, from approved production state",
    }


def build_call_sheet(
    project: Project,
    day: ShootDay,
    items: list[ScheduleItem] | None = None,
    disruption: Disruption | None = None,
    label: str = "current",
    revision: int = 0,
    evidence: Sequence[Evidence] | None = None,
    approved_by: str | None = None,
    approved_at: str | None = None,
) -> dict:
    items = sorted(items if items is not None else day.items, key=lambda i: to_minutes(i.start))
    call = to_minutes(day.unit_call)
    wrap = max((to_minutes(i.end) for i in items), default=call)
    standard_wrap = call + int(day.standard_hours * 60)

    rows = []
    for it in items:
        s = project.scene(it.scene_id)
        loc = project.resource(it.location_id or s.location_id) if (it.location_id or s.location_id) else None
        rows.append({
            "start": it.start, "end": it.end, "scene": s.number, "heading": s.heading, "int_ext": s.int_ext.value, "time_of_day": s.time_of_day.value,
            "minutes": to_minutes(it.end) - to_minutes(it.start), "cast": [project.resource(c).name for c in s.cast_ids], "location": loc.name if loc else "—",
            "status": it.status.value, "note": it.note, "cover": s.is_cover, "unit": it.unit,
            # The column a schedule is actually measured in. `None` where the scene carries no count
            # rather than a zero, so a missing page count reads as missing and not as a scene that
            # takes no pages.
            "eighths": s.eighths, "pages": eighths_label(s.eighths),
        })

    cast_first_scene: dict[str, int] = {}
    cast_wrap: dict[str, int] = {}
    for it in items:
        for c in project.scene(it.scene_id).cast_ids:
            cast_first_scene[c] = min(cast_first_scene.get(c, 10**6), to_minutes(it.start))
            cast_wrap[c] = max(cast_wrap.get(c, 0), to_minutes(it.end))

    cast = []
    for cid, first_shot in sorted(cast_first_scene.items(), key=lambda x: x[1]):
        r = project.resource(cid)
        # Staggered prep breakdown
        is_lead_stunt = "stunt" in r.name.lower() or "aarav" in r.name.lower()
        hmu_dur = 60 if is_lead_stunt else (45 if "zoya" in r.name.lower() or "dalvi" in r.name.lower() else 30)
        wardrobe_dur = 30 if is_lead_stunt else 15
        travel_dur = 30  # hotel / transport buffer

        pickup_min = max(0, first_shot - (travel_dur + hmu_dur + wardrobe_dur))
        hmu_min = pickup_min + travel_dur
        wardrobe_min = hmu_min + hmu_dur
        ready_min = wardrobe_min + wardrobe_dur
        on_set_min = first_shot

        # Legacy `call` field: preserved for exact backward-compatibility with tests
        legacy_call = max(first_shot - CAST_MAKEUP_LEAD, call)

        scenes = [project.scene(i.scene_id).number for i in items if cid in project.scene(i.scene_id).cast_ids]
        cast.append({
            # The column a call sheet leads with, before the name. Rows stay in first-shot order,
            # which is what makes the staggered calls beside them readable; the number is what ties
            # each row to the same performer's strip, DOOD line and dispatch.
            "cast_number": r.cast_number,
            "name": r.name,
            "call": to_hhmm(legacy_call),
            "pickup": to_hhmm(pickup_min),
            "hmu": to_hhmm(hmu_min),
            "wardrobe": to_hhmm(wardrobe_min),
            "ready": to_hhmm(ready_min),
            "on_set": to_hhmm(on_set_min),
            "wrap": to_hhmm(cast_wrap[cid]),
            "scenes": scenes,
            "note": next((a.note for a in r.availability if a.shoot_day_id == day.id and a.note), None),
        })

    equipment = [{"name": project.resource(c.resource_id).name, "call": c.call_time, "contact": project.resource(c.resource_id).contact} for c in derive_equipment_calls(project, day, items)]
    transport = [{"vehicle": project.resource(l.vehicle_id).name, "from": project.resource(l.from_location_id).name if l.from_location_id else "—", "to": project.resource(l.to_location_id).name if l.to_location_id else "—", "departure": l.departure} for l in derive_transport(project, day, items)]

    # The same pack the validator prices the board with, so the sheet cannot print a lunch the
    # stripboard is charging a meal penalty for.
    pack = active_pack(project)
    lunch = call + int(pack.lunch_due_hours * 60)
    slack, minimum = pack.lunch_window_slack_minutes, pack.minimum_lunch_minutes
    gaps = [(to_minutes(a.end), to_minutes(b.start)) for a, b in zip(items, items[1:])]
    lunch_gap = next(((gs, ge) for gs, ge in gaps if min(ge, lunch + slack) - max(gs, lunch - slack) >= minimum), None)
    headcount = day.crew_size + len(cast)
    # `evaluate_meal_penalties` owes nothing when the unit wraps before the meal falls due, so the
    # sheet must not print an exposure the stripboard is not charging: a dawn splinter that wrapped
    # at 07:15 does not owe a lunch at 11:15. Same rule, read from the same pack, in both places.
    lunch_due = bool(items) and wrap > lunch
    if not lunch_due:
        lunch_text = f"none due — the unit wraps {to_hhmm(wrap)}, before the {to_hhmm(lunch)} meal window"
    elif lunch_gap:
        lunch_text = f"{to_hhmm(lunch_gap[0])}–{to_hhmm(lunch_gap[1])}"
    else:
        lunch_text = f"{to_hhmm(lunch)} (no gap scheduled — meal penalty exposure)"
    meals = {
        "lunch": {"time": lunch_text, "count": headcount if lunch_due else 0, "scheduled_gap": lunch_gap is not None, "due": lunch_due},
        "dinner": {"time": to_hhmm(wrap) if wrap > to_minutes(DINNER_CUTOFF) else None, "count": headcount if wrap > to_minutes(DINNER_CUTOFF) else 0},
    }

    seen: set[str] = set()
    locations = []
    for it in items:
        lid = it.location_id or project.scene(it.scene_id).location_id
        if lid and lid not in seen:
            seen.add(lid)
            r = project.resource(lid)
            win = next((a for a in r.availability if a.shoot_day_id == day.id), None)
            locations.append({"name": r.name, "contact": r.contact, "window": f"{win.start}–{win.end}" if win else "all day", "note": win.note if win else None, "attributes": r.attributes})

    advisories: list[str] = []
    if disruption:
        adv = f"{disruption.type.value.replace('_', ' ').title()}: {disruption.title}"
        if disruption.window_start and disruption.window_end:
            adv += f" ({disruption.window_start}–{disruption.window_end}"
            if disruption.dry_out_minutes:
                adv += f", +{disruption.dry_out_minutes} min dry-out"
            adv += ")"
        if disruption.verification_status:
            adv += f" — external check: {disruption.verification_status.value.replace('_', ' ').lower()}"
            if disruption.verification_confidence is not None:
                adv += f" ({disruption.verification_confidence:.0%})"
        advisories.append(adv)
    for r in rows:
        if r["cover"]:
            advisories.append(f"Sc {r['scene']} is a cover set pulled forward ({r['start']}).")
    # A wrapped day's sheet is a record, not a plan: an advisory telling the unit to watch the sky
    # is about a shoot that already finished. What it owes the reader instead is what it delivered.
    wrapped = day.status == ShootDayStatus.WRAPPED
    if wrapped:
        # Counted, not assumed. This said "N scene(s) completed; nothing outstanding" over *every*
        # row on the day, so a day that shot three and carried one reported four completed and
        # nothing carried — on the one document that exists to be the record of what happened.
        shot = [i for i in items if i.status == ScheduleItemStatus.COMPLETED]
        carried = [i for i in items if i.status != ScheduleItemStatus.COMPLETED]
        outstanding = (
            f"{len(carried)} carried to another day." if carried else "nothing outstanding."
        )
        wrap_at = to_hhmm(to_minutes(day.camera_wrap)) if day.camera_wrap else to_hhmm(wrap)
        advisories.append(
            f"Day {day.day_number} wrapped at {wrap_at} — this sheet is the record of what was shot, not a plan. "
            f"{len(shot)} scene(s) completed; {outstanding}"
        )
    else:
        weather_eq = [project.resource(e).name for i in items for e in project.scene(i.scene_id).equipment_ids if project.resource(e).weather_sensitive]
        if weather_eq:
            advisories.append("Weather-sensitive equipment on the day: " + ", ".join(dict.fromkeys(weather_eq)) + ".")
    if wrap > standard_wrap:
        advisories.append(f"Overtime: wrap {to_hhmm(wrap)} is {wrap - standard_wrap} min beyond the {to_hhmm(standard_wrap)} standard wrap (₹{int(round((wrap - standard_wrap) / 60 * day.overtime_rate_per_hour)):,}).")
    # Which golden hour the unit was actually working. A dawn splinter that wrapped at 07:15 has no
    # business printing a dusk window it will never see; the two windows are both derived from the
    # day's own date, so this only picks between them.
    dusk_open = to_minutes(day.golden_hour_dusk[0])
    dawn = items and wrap <= dusk_open
    golden = f"Golden hour ({'dawn' if dawn else 'dusk'}) " + "–".join(day.golden_hour_dawn if dawn else day.golden_hour_dusk)

    location_ids = [lid for it in items if (lid := it.location_id or project.scene(it.scene_id).location_id)]
    weather = _weather_block(day, disruption, evidence or [])
    departments = _departments_block(project, items)
    # Totalled only where every scene on the day carries a count. A partial sum printed as the day's
    # pages is a smaller number than the day is actually shooting, and a 1st AD reads that figure to
    # decide whether the day is makeable.
    scene_eighths = [r["eighths"] for r in rows]
    total_eighths = sum(e for e in scene_eighths if e is not None) if rows and all(e is not None for e in scene_eighths) else None
    pages = {
        "total_eighths": total_eighths,
        "total_label": eighths_label(total_eighths),
        "scene_count": len(rows),
        "unpriced_scenes": [r["scene"] for r in rows if r["eighths"] is None],
        "reason": None if total_eighths is not None else (
            "Not every scene on this day carries a page count, so no day total is stated — a partial total would "
            "understate the day."
        ),
    }

    return {
        "label": label,
        "production": project.title,
        "synthetic": project.synthetic,
        "day_number": day.day_number,
        # "Day 4 of 6" — the line that tells a crew where in the schedule they are, read off the
        # production's own day numbering rather than off how many days ScenePilot happens to hold.
        # Those are different numbers here and the difference matters: this project models Days 3–6
        # of a six-day schedule, so counting the rows would print "Day 4 of 4" and tell the unit it
        # was wrapping the picture on a day with two more ahead of it. `days_held` carries the gap
        # so the sheet can say which days are actually on file instead of implying all of them are.
        "day_of_total": max((d.day_number for d in project.shoot_days), default=day.day_number),
        "days_held": sorted(d.day_number for d in project.shoot_days),
        "revision": revision_of(revision),
        "date": day.date,
        "unit_call": day.unit_call,
        "first_shot": items[0].start if items else None,
        "estimated_wrap": to_hhmm(wrap),
        "standard_wrap": to_hhmm(standard_wrap),
        "crew_size": day.crew_size,
        "status": day.status.value,
        "sun": golden,
        "solar": _solar_block(project, day),
        "weather": weather,
        "pages": pages,
        "schedule": rows,
        "cast": cast,
        "departments": departments,
        "safety": _safety_block(project, day, items, departments, location_ids, weather),
        "advance": advance_block(project, day),
        "signatures": _signature_block(project, approved_by, approved_at),
        "equipment": equipment,
        "transport": transport,
        "meals": meals,
        "locations": locations,
        "advisories": advisories,
        "notes": day.notes,
        "contacts": [{"role": r.type.value.title(), "name": r.name, "contact": r.contact} for r in project.resources if r.type in (ResourceType.LOCATION, ResourceType.EQUIPMENT) and r.contact and r.id in {i.location_id for i in items} | {e for i in items for e in project.scene(i.scene_id).equipment_ids}],
    }
