"""Breakdown agent module: uses Gemini with structured output to break down scenes into production elements."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ..agents.runtime import GeminiRuntime
from ..agents.schemas import ComprehensiveBreakdownOutput
from ..domain.breakdown_models import BreakdownElement

log = logging.getLogger(__name__)


def _prompt_user_text(scene_heading: str, action_text: str, dialogue_text: str = "") -> str:
    parts = [
        f"SCENE HEADING: {scene_heading}",
        f"ACTION / DESCRIPTION:\n{action_text.strip()}",
    ]
    if dialogue_text.strip():
        parts.append(f"DIALOGUE:\n{dialogue_text.strip()}")
    return "\n\n".join(parts)


def _deterministic_fallback_breakdown(scene_heading: str, action_text: str) -> ComprehensiveBreakdownOutput:
    """Deterministic fallback breakdown when Gemini API is unavailable."""
    text_lower = (scene_heading + " " + action_text).lower()
    elements = []
    stop_conditions = []

    # Detect common keywords
    if "motorcycle" in text_lower or "bike" in text_lower:
        elements.append({
            "category": "VEHICLES", "name": "Hero Motorcycle", "description": "Motorcycle used in scene", "count": 1, "implied": False
        })
        elements.append({
            "category": "STUNTS", "name": "Motorcycle Stunt Rider", "description": "Precision rider for motorcycle maneuvers", "count": 1, "implied": True
        })
    if "drone" in text_lower:
        elements.append({
            "category": "SPECIAL_EQUIPMENT", "name": "FPV Tracking Drone", "description": "Drone for aerial sequence", "count": 1, "implied": False
        })
        stop_conditions.append("High winds (> 25 km/h) or ambient rain prohibits drone flight.")
    if "crane" in text_lower:
        elements.append({
            "category": "SPECIAL_EQUIPMENT", "name": "Telescopic Camera Crane", "description": "30ft crane for camera moves", "count": 1, "implied": False
        })
    if "firework" in text_lower or "pyro" in text_lower:
        elements.append({
            "category": "SFX", "name": "Pyrotechnics Display Rig", "description": "Skyline fireworks display with licensed pyro technician", "count": 1, "implied": False
        })
        elements.append({
            "category": "SAFETY", "name": "Pyrotechnic Fallout Zone", "description": "Cleared fallout radius, fire standby and licensed technician on set", "count": 1, "implied": True
        })
        stop_conditions.append("Pyrotechnic safety zone must be clear of personnel; ambient wind must be within legal limits.")
    if "rain" in text_lower:
        elements.append({
            "category": "SFX", "name": "Practical Rain Rig", "description": "Rain machines or water towers for on-cue rain effect", "count": 1, "implied": False
        })
        elements.append({
            "category": "WARDROBE", "name": "Duplicate Wet Wardrobe Suits", "description": "Matching dry suits for retakes", "count": 3, "implied": True
        })
        stop_conditions.append("Wet surface creates slip hazard for vehicles; require 30-min dry-out buffer.")
    if "jump" in text_lower:
        elements.append({
            "category": "STUNTS", "name": "Rooftop Gap Jump", "description": "Rooftop gap jump with safety rig and decelerator lines", "count": 1, "implied": False
        })
        elements.append({
            "category": "STUNT_RIGGING", "name": "Landing Rig and Decelerator Lines", "description": "Ramps, crash pads and decelerator lines; pre-rigged and struck separately from the stunt", "count": 1, "implied": True
        })
        stop_conditions.append("Stunt jump stop condition: wet or slippery landing surface.")
    if "rooftop" in text_lower or "ledge" in text_lower:
        elements.append({
            "category": "SAFETY", "name": "Rooftop Edge Protection", "description": "Edge barriers, fall arrest and a safety supervisor for work near an unprotected drop", "count": 1, "implied": True
        })
    if any(w in text_lower for w in ("crowd", "market", "bystander", "passers", "shoppers", "traffic")):
        elements.append({
            "category": "BACKGROUND_ATMOSPHERE", "name": "Street Atmosphere", "description": "Background performers dressing the location as a working street", "count": 20, "implied": True
        })

    if not elements:
        elements.append({
            "category": "CAMERA", "name": "Standard Camera Package", "description": "Cinema camera package", "count": 1, "implied": True
        })

    return ComprehensiveBreakdownOutput(
        scene_summary=f"Breakdown for {scene_heading}",
        elements=elements,
        stop_conditions=stop_conditions,
        continuity_notes=["Check costume and prop continuity from previous sequence."],
    )


async def run_breakdown_agent(
    scene_heading: str,
    action_text: str,
    dialogue_text: str = "",
    runtime: GeminiRuntime | None = None,
) -> ComprehensiveBreakdownOutput:
    """Run the Gemini Creative Breakdown Agent on scene text."""
    user_text = _prompt_user_text(scene_heading, action_text, dialogue_text)
    rt = runtime or GeminiRuntime()

    try:
        output = await rt.run_structured("breakdown_agent", user_text, ComprehensiveBreakdownOutput)
        return output
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini breakdown agent call failed (%s); using deterministic fallback", exc)
        return _deterministic_fallback_breakdown(scene_heading, action_text)


def map_breakdown_to_elements(output: ComprehensiveBreakdownOutput) -> list[BreakdownElement]:
    """Map the schema items to domain BreakdownElement instances."""
    res = []
    for item in output.elements:
        eid = f"elem_{uuid.uuid4().hex[:8]}"
        res.append(
            BreakdownElement(
                id=eid,
                category=item.category,
                name=item.name,
                description=item.description,
                count=item.count,
                implied=item.implied,
                safety_notes=item.safety_notes,
            )
        )
    return res
