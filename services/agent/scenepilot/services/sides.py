"""Sides — the day's pages, in shooting order, for the people who have to perform them.

Sides are the extract of the screenplay a unit actually carries: only the scenes being shot today,
in the order they are called, with the scene numbers the board and the call sheet use. This assembles
them from the screenplay the Studio already holds, and adds nothing to it.

The honest edge is the whole design. This production's draft is a five-scene excerpt while the board
schedules nine scenes, so a day can be scheduled whose pages the Studio simply does not hold. A sides
packet that quietly skipped those scenes would hand an actor a packet that looks complete and is
missing their scene; one that printed an empty page under the right heading would be worse. So a
scene with no draft text prints as a **named gap** — its number, its heading and the reason — and the
packet says on its face how many of the day's scenes it could actually supply.

Order is the day's schedule, not the screenplay's. Sides are read in the order the day shoots.
"""

from __future__ import annotations

from typing import Any

from ..domain.models import Project, ShootDay
from .callsheet import day_of_total
from .timeutil import to_minutes


def build_sides(project: Project, day: ShootDay) -> dict[str, Any]:
    """The day's sides packet: one entry per scheduled scene, in call order."""
    parsed = {str(ps.scene_number): ps for ps in project.parsed_screenplay_scenes}
    items = sorted(day.items, key=lambda i: to_minutes(i.start))

    scenes: list[dict[str, Any]] = []
    for item in items:
        scene = project.scene(item.scene_id)
        location_id = item.location_id or scene.location_id
        base = {
            "scene_id": scene.id,
            "scene_number": scene.number,
            "heading": scene.heading,
            "int_ext": scene.int_ext.value,
            "time_of_day": scene.time_of_day.value,
            "start": item.start,
            "end": item.end,
            "unit": item.unit,
            "location": project.resource(location_id).name if location_id else None,
            "cast": [
                {"cast_number": project.resource(c).cast_number, "name": project.resource(c).name}
                for c in scene.cast_ids
            ],
            "eighths": scene.eighths,
        }

        draft = parsed.get(str(scene.number))
        if draft is None:
            scenes.append({
                **base,
                "has_text": False,
                "action_text": "",
                "dialogue": [],
                # Named, never blank: the packet must not look complete when it is not.
                "gap_reason": (
                    f"The screenplay in the Studio does not hold Scene {scene.number}. The production schedules it, "
                    "and its breakdown and call time are on the call sheet, but its pages have not been uploaded — "
                    "so there are no sides to print for it."
                ),
            })
            continue

        scenes.append({
            **base,
            "has_text": True,
            # The draft's own slug, kept beside the production's heading: where the two disagree, a
            # performer should see both rather than have one silently overwrite the other.
            "draft_heading": draft.heading,
            "action_text": draft.action_text,
            "dialogue": [{"character": d.character, "parenthetical": d.parenthetical, "text": d.text} for d in draft.dialogue],
            "page_start": draft.page_start,
            "page_end": draft.page_end,
            "gap_reason": None,
        })

    supplied = len([s for s in scenes if s["has_text"]])
    return {
        "production": project.title,
        "fictional": True,
        "day_number": day.day_number,
        "day_of_total": day_of_total(project, day),
        "date": day.date,
        "unit_call": day.unit_call,
        "status": day.status.value,
        "scenes": scenes,
        "scene_count": len(scenes),
        "scenes_with_text": supplied,
        "complete": supplied == len(scenes) and bool(scenes),
        "coverage_note": (
            None
            if supplied == len(scenes) and scenes
            else f"{supplied} of {len(scenes)} scheduled scene(s) have pages in the Studio. The rest are listed with the "
                 "reason rather than omitted, so this packet cannot be mistaken for a complete one."
        ),
        "provenance": (
            "Assembled from the screenplay uploaded to this production's Screenplay Studio, in the order this day "
            "shoots. Scene numbers, call times and cast are the production's own schedule."
        ),
    }
