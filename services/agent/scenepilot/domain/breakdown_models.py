"""Screenplay breakdown and Day-Out-Of-Days (DOOD) domain models."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from .enums import IntExt, TimeOfDay


# The breakdown element categories, and the only list of them.
#
# `agents/schemas.py` builds the Gemini output `Literal` from this tuple and the prompt enumerates
# the same names, so the schema, the model's instructions and the chips a producer counts on screen
# cannot disagree — they used to: 16 in the schema, 16 mirrored by hand here, 13 in the prompt.
#
# 29 of these are the Movie Magic Scheduling / StudioBinder default element set, which is what a 1st
# AD or line producer expects a breakdown sheet to carry. Three are ScenePilot's own and are here
# because something in the product reads them, not to lengthen the list: STUNT_RIGGING (a pre-rig is
# scheduled separately from the stunt it serves), LIGHTING (the time-of-day validator reasons about
# it) and SAFETY (it is where a scene's stop conditions attach).
BREAKDOWN_CATEGORIES: tuple[str, ...] = (
    # performers
    "CAST",
    "BACKGROUND_ATMOSPHERE",
    "EXTRAS",
    "STAND_INS",
    "STUNTS",
    "STUNT_RIGGING",
    # things in front of the camera
    "PROPS",
    "SET_DRESSING",
    "GREENERY",
    "ART_DEPARTMENT",
    "VEHICLES",
    "ANIMALS",
    "ANIMAL_WRANGLER",
    "LIVESTOCK",
    # on the performer
    "WARDROBE",
    "MAKEUP",
    "HAIR",
    "SPECIAL_EFFECTS_MAKEUP",
    # effects
    "SFX",
    "MECHANICAL_EFFECTS",
    "OPTICAL_EFFECTS",
    "VFX",
    # equipment and departments
    "CAMERA",
    "SPECIAL_EQUIPMENT",
    "LIGHTING",
    "SOUND",
    "MUSIC",
    # people and paperwork the day needs
    "SECURITY",
    "ADDITIONAL_LABOR",
    "SAFETY",
    "MISCELLANEOUS",
    "NOTES",
)


class BreakdownCategory:
    """Attribute access to `BREAKDOWN_CATEGORIES` (`BreakdownCategory.PROPS`), generated from it.

    Written by hand once, beside a `Literal` that was also written by hand; the two were kept in
    step by attention alone. Generating it means a category can only ever be added in one place.
    """


for _category in BREAKDOWN_CATEGORIES:
    setattr(BreakdownCategory, _category, _category)


class BreakdownElement(BaseModel):
    id: str
    category: str
    name: str
    description: str = ""
    count: int = 1
    implied: bool = False
    safety_notes: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ParsedDialogue(BaseModel):
    character: str
    parenthetical: str | None = None
    text: str


class ParsedSceneData(BaseModel):
    scene_number: str
    heading: str
    int_ext: IntExt = IntExt.EXT
    time_of_day: TimeOfDay = TimeOfDay.DAY
    setting: str = ""
    page_start: float = 1.0
    page_end: float = 1.0
    eighths: int = 1  # 1 standard page = 8 eighths
    action_text: str = ""
    dialogue: list[ParsedDialogue] = Field(default_factory=list)
    elements: list[BreakdownElement] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    continuity_notes: list[str] = Field(default_factory=list)
    raw_text: str = ""


class CastDOODEntry(BaseModel):
    cast_id: str
    cast_number: int | None = None  # the performer's stable cast number; the DOOD is ordered by it
    name: str
    day_status: dict[str, str] = Field(default_factory=dict)  # shoot_day_id -> SW, W, WF, SWF, H
    total_work_days: int = 0
    total_hold_days: int = 0
    # First call to last, inclusive — what the production is engaged for, which is the figure a UPM
    # compares the work days against. Work + hold, by construction.
    total_engaged_days: int = 0
    # The performer's own contracted day rate, or `None` where the production has stated none.
    day_rate_inr: int | None = None
    hold_day_cost_warning: bool = False
    # `None` rather than 0 when there is no rate to price the holds with: zero is a cost, and this
    # is the absence of one.
    estimated_hold_cost_inr: int | None = None
    warning_message: str | None = None
    # Whether this performer's hold runs could be released under the agreement in force, and what
    # that would be worth. Advisory: `available` is `None` when there are no holds to decide about.
    drop_pickup: dict[str, Any] = Field(default_factory=dict)
