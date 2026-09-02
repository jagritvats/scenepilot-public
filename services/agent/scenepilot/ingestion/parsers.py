"""Screenplay parsers for Fountain and Final Draft XML (.fdx).

Parses full screenplays into structured scenes with scene numbers, slugline
metadata (INT/EXT, Time of Day, Setting), action lines, dialogue blocks,
and accurate industry standard eighths-of-a-page pagination.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from ..domain.breakdown_models import ParsedDialogue, ParsedSceneData
from ..domain.enums import IntExt, TimeOfDay

# Standard screenplay layout: ~54 lines per page -> ~6.75 lines per 1/8 page.
LINES_PER_PAGE = 54
LINES_PER_EIGHTH = 6.75

SCENE_HEADING_REGEX = re.compile(
    r"^(?P<prefix>INT\./EXT\.|EXT\./INT\.|INT/EXT|EXT/INT|INT\.|EXT\.|I/E\.|INT\b|EXT\b|\.)\s*(?P<slug>[^#]+?)(?:\s+#(?P<number>[^#]+)#)?$",
    re.IGNORECASE,
)

TRANSITION_REGEX = re.compile(
    r"^(CUT TO:|FADE IN:|FADE OUT\.|FADE TO BLACK\.|DISSOLVE TO:|MATCH CUT TO:|SMASH CUT TO:|>.*<|>\s*.*)$",
    re.IGNORECASE,
)

TIME_OF_DAY_MAP: list[tuple[re.Pattern, TimeOfDay]] = [
    (re.compile(r"\b(DAWN|SUNRISE)\b", re.IGNORECASE), TimeOfDay.DAWN),
    (re.compile(r"\b(SUNSET|DUSK|GOLDEN HOUR)\b", re.IGNORECASE), TimeOfDay.SUNSET),
    (re.compile(r"\b(NIGHT|EVENING|MIDNIGHT|LATE NIGHT)\b", re.IGNORECASE), TimeOfDay.NIGHT),
    (re.compile(r"\b(DAY|MORNING|AFTERNOON|NOON)\b", re.IGNORECASE), TimeOfDay.DAY),
    (re.compile(r"\b(ANY|CONTINUOUS|LATER|SAME TIME|MOMENTS LATER)\b", re.IGNORECASE), TimeOfDay.ANY),
]


def _parse_slugline(slug: str, prefix: str) -> tuple[IntExt, TimeOfDay, str]:
    norm_prefix = prefix.upper().strip()
    if norm_prefix.startswith("INT"):
        int_ext = IntExt.INT
    elif norm_prefix.startswith("EXT"):
        int_ext = IntExt.EXT
    elif "INT" in norm_prefix and "EXT" in norm_prefix:
        int_ext = IntExt.EXT  # exterior cover / practical vehicle
    else:
        int_ext = IntExt.EXT

    # Match time of day from the slug (usually at the end: " - DAY")
    tod = TimeOfDay.DAY if int_ext == IntExt.EXT else TimeOfDay.ANY
    setting = slug.strip()

    # Split on hyphens or dashes
    parts = re.split(r"\s+[-–—]\s+", slug)
    if len(parts) > 1:
        tail = parts[-1].strip()
        matched = False
        for pattern, val in TIME_OF_DAY_MAP:
            if pattern.search(tail):
                tod = val
                matched = True
                break
        if matched:
            setting = " — ".join(p.strip() for p in parts[:-1])
        else:
            setting = " — ".join(p.strip() for p in parts)

    return int_ext, tod, setting


def calculate_eighths(lines_count: int, word_count: int) -> int:
    """Calculate scene length in 1/8ths of a page."""
    # Blend line count and word count (avg 250 words per page = ~31 words per eighth)
    eighths_by_line = lines_count / LINES_PER_EIGHTH
    eighths_by_word = word_count / 31.25
    estimated = max(1.0, (eighths_by_line * 0.7 + eighths_by_word * 0.3))
    return max(1, round(estimated))


def parse_fountain(text: str) -> list[ParsedSceneData]:
    """Parse Fountain screenplay text into structured ParsedSceneData objects."""
    lines = text.splitlines()
    scenes: list[ParsedSceneData] = []
    
    # Strip metadata title page header if present
    # Title pages in fountain start at line 0 and have "Key: Value" until first empty line
    start_idx = 0
    in_title_page = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i == 0 and ":" in stripped and not SCENE_HEADING_REGEX.match(stripped):
            in_title_page = True
            continue
        if in_title_page:
            if stripped == "":
                in_title_page = False
                start_idx = i + 1
                break
            continue
        break

    current_scene: dict[str, Any] | None = None
    current_char: str | None = None
    current_paren: str | None = None
    dialogue_buffer: list[str] = []
    action_lines: list[str] = []
    raw_lines: list[str] = []
    scene_counter = 1
    total_eighths = 0

    def finalize_dialogue():
        nonlocal current_char, current_paren, dialogue_buffer
        if current_char and (dialogue_buffer or current_paren):
            dt = " ".join(dialogue_buffer).strip()
            if current_scene is not None:
                current_scene["dialogue"].append(
                    ParsedDialogue(
                        character=current_char,
                        parenthetical=current_paren,
                        text=dt,
                    )
                )
        current_char = None
        current_paren = None
        dialogue_buffer = []

    def finalize_scene():
        nonlocal current_scene, total_eighths, scene_counter
        if current_scene is None:
            return
        finalize_dialogue()
        
        all_raw = "\n".join(raw_lines).strip()
        words = len(all_raw.split())
        line_count = max(1, len([l for l in raw_lines if l.strip()]))
        eighths = calculate_eighths(line_count, words)

        page_start = round(1.0 + (total_eighths / 8.0), 2)
        total_eighths += eighths
        page_end = round(1.0 + (total_eighths / 8.0), 2)

        scene_obj = ParsedSceneData(
            scene_number=current_scene["number"],
            heading=current_scene["heading"],
            int_ext=current_scene["int_ext"],
            time_of_day=current_scene["time_of_day"],
            setting=current_scene["setting"],
            page_start=page_start,
            page_end=page_end,
            eighths=eighths,
            action_text="\n".join(action_lines).strip(),
            dialogue=current_scene["dialogue"],
            raw_text=all_raw,
        )
        scenes.append(scene_obj)
        current_scene = None

    for line in lines[start_idx:]:
        raw = line
        stripped = line.strip()

        # Check for Scene Heading
        heading_match = SCENE_HEADING_REGEX.match(stripped)
        if heading_match and (stripped.startswith(".") or not stripped.startswith("..")):
            # Don't false-positive on ellipses like "...and then"
            prefix = heading_match.group("prefix")
            slug = heading_match.group("slug").strip()
            explicit_num = heading_match.group("number")

            finalize_scene()

            int_ext, tod, setting = _parse_slugline(slug, prefix)
            heading_text = f"{prefix.upper().rstrip('.')} {slug.upper()}" if not prefix.startswith(".") else slug.upper()
            sc_num = explicit_num.strip() if explicit_num else str(scene_counter)
            scene_counter += 1

            current_scene = {
                "number": sc_num,
                "heading": heading_text,
                "int_ext": int_ext,
                "time_of_day": tod,
                "setting": setting,
                "dialogue": [],
            }
            action_lines = []
            raw_lines = [raw]
            continue

        if current_scene is None:
            # Lines before first scene heading: synthesize scene 1 if non-empty
            if stripped:
                current_scene = {
                    "number": str(scene_counter),
                    "heading": "SCENE 1",
                    "int_ext": IntExt.EXT,
                    "time_of_day": TimeOfDay.DAY,
                    "setting": "LOCATION",
                    "dialogue": [],
                }
                scene_counter += 1
                action_lines = []
                raw_lines = [raw]
            else:
                continue

        raw_lines.append(raw)

        if not stripped:
            finalize_dialogue()
            continue

        # Parenthetical check: starts with ( and ends with )
        if stripped.startswith("(") and stripped.endswith(")"):
            current_paren = stripped[1:-1].strip()
            continue

        # Character cue check: uppercase line, not transition, preceded by empty line or dialogue
        # e.g., "AARAV", "MEERA (V.O.)", "@OFFICER"
        is_all_caps = stripped.isupper() or (stripped.startswith("@") and len(stripped) > 1)
        if is_all_caps and not TRANSITION_REGEX.match(stripped) and not heading_match:
            char_clean = stripped.lstrip("@").split("(")[0].strip()
            if char_clean and len(char_clean) <= 35:
                finalize_dialogue()
                current_char = char_clean
                if "(" in stripped and stripped.endswith(")"):
                    current_paren = stripped.split("(", 1)[1].rstrip(")").strip()
                continue

        # Dialogue line check
        if current_char:
            dialogue_buffer.append(stripped)
        else:
            action_lines.append(stripped)

    finalize_scene()
    return scenes


def parse_fdx(xml_content: str) -> list[ParsedSceneData]:
    """Parse Final Draft XML (.fdx) into structured ParsedSceneData objects."""
    root = ET.fromstring(xml_content)
    scenes: list[ParsedSceneData] = []
    
    current_scene: dict[str, Any] | None = None
    current_char: str | None = None
    current_paren: str | None = None
    dialogue_buffer: list[str] = []
    action_lines: list[str] = []
    raw_lines: list[str] = []
    scene_counter = 1
    total_eighths = 0

    def finalize_dialogue():
        nonlocal current_char, current_paren, dialogue_buffer
        if current_char and (dialogue_buffer or current_paren):
            dt = " ".join(dialogue_buffer).strip()
            if current_scene is not None:
                current_scene["dialogue"].append(
                    ParsedDialogue(
                        character=current_char,
                        parenthetical=current_paren,
                        text=dt,
                    )
                )
        current_char = None
        current_paren = None
        dialogue_buffer = []

    def finalize_scene():
        nonlocal current_scene, total_eighths, scene_counter
        if current_scene is None:
            return
        finalize_dialogue()
        
        all_raw = "\n".join(raw_lines).strip()
        words = len(all_raw.split())
        line_count = max(1, len([l for l in raw_lines if l.strip()]))
        eighths = calculate_eighths(line_count, words)

        page_start = round(1.0 + (total_eighths / 8.0), 2)
        total_eighths += eighths
        page_end = round(1.0 + (total_eighths / 8.0), 2)

        scene_obj = ParsedSceneData(
            scene_number=current_scene["number"],
            heading=current_scene["heading"],
            int_ext=current_scene["int_ext"],
            time_of_day=current_scene["time_of_day"],
            setting=current_scene["setting"],
            page_start=page_start,
            page_end=page_end,
            eighths=eighths,
            action_text="\n".join(action_lines).strip(),
            dialogue=current_scene["dialogue"],
            raw_text=all_raw,
        )
        scenes.append(scene_obj)
        current_scene = None

    # Search for Paragraph tags inside Content
    paragraphs = root.findall(".//Paragraph")
    for p in paragraphs:
        p_type = p.attrib.get("Type", "Action")
        # Extract text elements
        text_nodes = [t.text for t in p.findall("Text") if t.text]
        text_line = "".join(text_nodes).strip()
        if not text_line:
            continue

        if p_type == "Scene Heading":
            finalize_scene()
            sc_num_attr = p.attrib.get("Number")
            heading_match = SCENE_HEADING_REGEX.match(text_line)
            if heading_match:
                prefix = heading_match.group("prefix")
                slug = heading_match.group("slug").strip()
                explicit_num = heading_match.group("number") or sc_num_attr
                int_ext, tod, setting = _parse_slugline(slug, prefix)
                heading_text = f"{prefix.upper().rstrip('.')} {slug.upper()}" if not prefix.startswith(".") else slug.upper()
            else:
                int_ext, tod, setting = IntExt.EXT, TimeOfDay.DAY, text_line
                heading_text = text_line.upper()
                explicit_num = sc_num_attr

            sc_num = explicit_num.strip() if explicit_num else str(scene_counter)
            scene_counter += 1

            current_scene = {
                "number": sc_num,
                "heading": heading_text,
                "int_ext": int_ext,
                "time_of_day": tod,
                "setting": setting,
                "dialogue": [],
            }
            action_lines = []
            raw_lines = [text_line]
            continue

        if current_scene is None:
            current_scene = {
                "number": str(scene_counter),
                "heading": "SCENE 1",
                "int_ext": IntExt.EXT,
                "time_of_day": TimeOfDay.DAY,
                "setting": "LOCATION",
                "dialogue": [],
            }
            scene_counter += 1
            action_lines = []
            raw_lines = []

        raw_lines.append(text_line)

        if p_type == "Character":
            finalize_dialogue()
            char_clean = text_line.split("(")[0].strip()
            current_char = char_clean
            if "(" in text_line and text_line.endswith(")"):
                current_paren = text_line.split("(", 1)[1].rstrip(")").strip()
        elif p_type == "Parenthetical":
            current_paren = text_line.strip("()")
        elif p_type == "Dialogue":
            dialogue_buffer.append(text_line)
        else:  # Action, Shot, General, etc.
            finalize_dialogue()
            action_lines.append(text_line)

    finalize_scene()
    return scenes


def parse_screenplay(raw_content: str, format_hint: str = "auto") -> list[ParsedSceneData]:
    """Auto-detect format and parse screenplay content."""
    stripped = raw_content.strip()
    if format_hint == "fdx" or stripped.startswith("<?xml") or "<FinalDraft" in stripped[:500]:
        return parse_fdx(raw_content)
    return parse_fountain(raw_content)
