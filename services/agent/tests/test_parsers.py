"""Tests for screenplay parsers: Fountain and Final Draft XML (.fdx)."""

from __future__ import annotations

import pytest
from scenepilot.domain.enums import IntExt, TimeOfDay
from scenepilot.ingestion.parsers import parse_fdx, parse_fountain, parse_screenplay

FOUNTAIN_SAMPLE = """Title: Project Nightfall
Author: Dev Team

EXT. MUMBAI ROOFTOP — SUNSET #42#

A motorcycle tears across adjoining rooftops.
A drone follows while fireworks explode over the skyline.

AARAV
(into helmet radio)
Package is secured. Moving to exfil now.

Rain begins as the rider jumps to an adjacent building.

INT. APARTMENT KITCHEN — MORNING #27#

ZOYA
Did you get it?

AARAV
Barely made the jump.

EXT. MARKET STREET — DAY #48#

Inspector Dalvi scans the bustling fruit stalls.
Sixty vendors yell out their prices.
"""

FDX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<FinalDraft DocumentType="Script" Template="No" Version="1">
<Content>
<Paragraph Type="Scene Heading" Number="12">
<Text>EXT. SEA LINK — DAWN</Text>
</Paragraph>
<Paragraph Type="Action">
<Text>The Mumbai skyline emerges from morning mist.</Text>
</Paragraph>
<Paragraph Type="Scene Heading" Number="19">
<Text>INT. APARTMENT — DAY</Text>
</Paragraph>
<Paragraph Type="Character">
<Text>ZOYA</Text>
</Paragraph>
<Paragraph Type="Dialogue">
<Text>He knows where we are hiding.</Text>
</Paragraph>
</Content>
</FinalDraft>
"""


def test_parse_fountain_scenes_and_metadata():
    scenes = parse_fountain(FOUNTAIN_SAMPLE)
    assert len(scenes) == 3

    # Scene 42
    sc42 = scenes[0]
    assert sc42.scene_number == "42"
    assert sc42.int_ext == IntExt.EXT
    assert sc42.time_of_day == TimeOfDay.SUNSET
    assert "MUMBAI ROOFTOP" in sc42.heading
    assert sc42.eighths >= 1
    assert len(sc42.dialogue) == 1
    assert sc42.dialogue[0].character == "AARAV"
    assert sc42.dialogue[0].parenthetical == "into helmet radio"
    assert "Package is secured" in sc42.dialogue[0].text

    # Scene 27
    sc27 = scenes[1]
    assert sc27.scene_number == "27"
    assert sc27.int_ext == IntExt.INT
    assert sc27.time_of_day == TimeOfDay.DAY or sc27.time_of_day == TimeOfDay.ANY
    assert len(sc27.dialogue) == 2
    assert sc27.dialogue[0].character == "ZOYA"
    assert sc27.dialogue[1].character == "AARAV"

    # Scene 48
    sc48 = scenes[2]
    assert sc48.scene_number == "48"
    assert sc48.int_ext == IntExt.EXT
    assert sc48.time_of_day == TimeOfDay.DAY


def test_parse_fdx_xml():
    scenes = parse_fdx(FDX_SAMPLE)
    assert len(scenes) == 2

    sc12 = scenes[0]
    assert sc12.scene_number == "12"
    assert sc12.int_ext == IntExt.EXT
    assert sc12.time_of_day == TimeOfDay.DAWN
    assert "SEA LINK" in sc12.heading
    assert "skyline emerges" in sc12.action_text

    sc19 = scenes[1]
    assert sc19.scene_number == "19"
    assert sc19.int_ext == IntExt.INT
    assert len(sc19.dialogue) == 1
    assert sc19.dialogue[0].character == "ZOYA"
    assert sc19.dialogue[0].text == "He knows where we are hiding."


def test_auto_detect_format():
    fountain_res = parse_screenplay(FOUNTAIN_SAMPLE)
    assert len(fountain_res) == 3

    fdx_res = parse_screenplay(FDX_SAMPLE)
    assert len(fdx_res) == 2
