"""ScenePilot's stripboard, rendered as XML.

An XML view of one shoot day — strips in shooting order, breakdown sheets, cast and locations —
shaped after the way scheduling tools exchange a stripboard. The schema is ScenePilot's own and
unofficial: it is not written or validated by Movie Magic Scheduling, and no claim is made that any
scheduling package imports it as-is. (Movie Magic's real exchange files are `.mmsx` for v10+ and
`.sex` for v5/6; neither format is implemented here, and neither extension is served.)

Interoperability is still the point — the element names are deliberately the vocabulary a line
producer's tools already use, so mapping this onto whatever a given package ingests is a small,
readable transform rather than a re-derivation. Stating that honestly is worth more than a
compatibility claim nobody here has tested.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.dom import minidom

from ..domain.models import Project, ScheduleItem, ShootDay
from .timeutil import to_minutes


def generate_mmsx_xml(
    project: Project,
    day: ShootDay,
    items: list[ScheduleItem] | None = None,
) -> str:
    """Render this shoot day's stripboard as a ScenePilot XML document (unofficial schema)."""
    schedule_items = sorted(
        items if items is not None else day.items,
        key=lambda i: to_minutes(i.start),
    )

    # The root carries the disclaimer, so the claim travels with the file rather than living only in
    # the UI that offered the download. No product version and no schema version: inventing either
    # would assert conformance to a specification this file has never been checked against.
    root = ET.Element(
        "ScenePilotStripboard",
        attrib={
            "generator": "ScenePilot",
            "format": "stripboard-xml",
            "official": "false",
            "note": "ScenePilot's own schema, shaped after how scheduling tools exchange a stripboard. Not written or validated by Movie Magic Scheduling.",
        },
    )

    # Project details
    proj_elem = ET.SubElement(root, "Project")
    ET.SubElement(proj_elem, "ID").text = project.id
    ET.SubElement(proj_elem, "Title").text = project.title
    ET.SubElement(proj_elem, "BaseCity").text = project.base_city
    ET.SubElement(proj_elem, "TargetCurrency").text = "INR"

    # Shoot day header
    day_elem = ET.SubElement(root, "ShootDay")
    ET.SubElement(day_elem, "ID").text = day.id
    ET.SubElement(day_elem, "DayNumber").text = str(day.day_number)
    ET.SubElement(day_elem, "Date").text = day.date
    ET.SubElement(day_elem, "UnitCall").text = day.unit_call
    ET.SubElement(day_elem, "HardWrap").text = day.hard_wrap
    ET.SubElement(day_elem, "StandardHours").text = str(day.standard_hours)
    ET.SubElement(day_elem, "CrewSize").text = str(day.crew_size)
    ET.SubElement(day_elem, "OvertimeRatePerHour").text = str(day.overtime_rate_per_hour)

    # Stripboard Sequence
    stripboard_elem = ET.SubElement(root, "Stripboard")
    ET.SubElement(stripboard_elem, "TotalStrips").text = str(len(schedule_items))

    for seq, it in enumerate(schedule_items, start=1):
        scene = project.scene(it.scene_id)
        loc_id = it.location_id or scene.location_id
        loc = project.resource(loc_id) if loc_id else None
        dur = to_minutes(it.end) - to_minutes(it.start)

        strip_elem = ET.SubElement(
            stripboard_elem,
            "Strip",
            attrib={"sequence": str(seq), "id": it.id},
        )
        ET.SubElement(strip_elem, "SceneNumber").text = str(scene.number)
        ET.SubElement(strip_elem, "Heading").text = scene.heading
        ET.SubElement(strip_elem, "IntExt").text = scene.int_ext.value
        ET.SubElement(strip_elem, "TimeOfDay").text = scene.time_of_day.value
        # The scene's own page count, and nothing when it has none. This read `pages_in_eighths`,
        # a field `Scene` has never had, behind an `or 8` — so every strip in every export claimed
        # a flat one page, two inches under a board printing the real count for the same scene. A
        # missing count is an absent element rather than a default: an importer can ask why a strip
        # is unpaginated, but it cannot tell an invented page from a measured one.
        if scene.eighths is not None:
            ET.SubElement(strip_elem, "PagesEighths").text = str(scene.eighths)
        ET.SubElement(strip_elem, "DurationMinutes").text = str(dur)
        ET.SubElement(strip_elem, "ScheduledStart").text = it.start
        ET.SubElement(strip_elem, "ScheduledEnd").text = it.end
        ET.SubElement(strip_elem, "Status").text = it.status.value
        ET.SubElement(strip_elem, "IsCover").text = "true" if scene.is_cover else "false"

        # Location
        loc_elem = ET.SubElement(strip_elem, "Location")
        ET.SubElement(loc_elem, "Name").text = loc.name if loc else "TBD"
        loc_addr = loc.attributes.get("address", loc.name) if (loc and loc.attributes) else (loc.name if loc else "")
        ET.SubElement(loc_elem, "Address").text = str(loc_addr)

        # Cast
        cast_elem = ET.SubElement(strip_elem, "Cast")
        for cid in scene.cast_ids:
            cast_res = project.resource(cid)
            # `number` is the production's cast number — the join key a stripboard's cast column
            # carries — and it is absent rather than invented for a performer who has none.
            attrib = {"id": cid}
            if cast_res.cast_number is not None:
                attrib["number"] = str(cast_res.cast_number)
            perf = ET.SubElement(cast_elem, "Performer", attrib=attrib)
            perf.text = cast_res.name

    # Breakdown Sheets element
    sheets_elem = ET.SubElement(root, "BreakdownSheets")
    for it in schedule_items:
        scene = project.scene(it.scene_id)
        sheet = ET.SubElement(sheets_elem, "Sheet", attrib={"sceneNumber": str(scene.number)})
        ET.SubElement(sheet, "Heading").text = scene.heading
        ET.SubElement(sheet, "Synopsis").text = scene.synopsis or ""

        # Elements breakdown
        elements_elem = ET.SubElement(sheet, "Elements")
        for cid in scene.cast_ids:
            c_res = project.resource(cid)
            ET.SubElement(elements_elem, "Element", attrib={"category": "CAST", "name": c_res.name})
        for eid in scene.equipment_ids:
            e_res = project.resource(eid)
            ET.SubElement(elements_elem, "Element", attrib={"category": "EQUIPMENT", "name": e_res.name})

    # Format nicely with indentation
    xml_raw = ET.tostring(root, encoding="utf-8")
    reparsed = minidom.parseString(xml_raw)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
