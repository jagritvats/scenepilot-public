"""The risk register — every planned scene's risks, in one place, ordered by exposure.

The engine has always produced these: the planning workflow asks Gemini for risks against researched
evidence, grades each one down to an inference unless it carries a citation that survived validation,
and then *weights them into the readiness score every scene page prints*. They were visible only one
scene at a time, which is the one view in which a register is useless — a register exists to be read
across a production.

The honest part is the denominator. Risks arrive per scene, and only for scenes a producer has
actually planned, so an unplanned scene has **no register, not an empty one**. Reporting "0 risks" for
a scene nobody has researched would be the most dangerous sentence in the product: it reads as "this
scene is safe" when it means "nobody has looked". So the register states its own coverage, names the
scenes it cannot speak for, and refuses to imply anything about them.

Exposure ordering mirrors `services/readiness.SEVERITY_WEIGHT` exactly, because a register that
ranked risks differently from the score they feed would be two opinions about one number.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import Severity
from ..domain.models import Project, Risk
from .readiness import SEVERITY_WEIGHT

SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]


def exposure(risk) -> float:
    """Severity × likelihood — the same product the readiness score sums."""
    return SEVERITY_WEIGHT[risk.severity] * risk.likelihood


def find_risk(project: Project, risk_id: str) -> tuple[str, Risk] | None:
    """Locate a risk across every planned scene. `(scene_id, risk)`, or None.

    Risks live inside `ProductionPlan.risks` inside `Project.plans`, keyed by scene — there is no
    index, and the register is built by walking the same dict. A linear scan is what the register
    itself does, over the same handful of plans.
    """
    for scene_id, plan in project.plans.items():
        for risk in plan.risks:
            if risk.id == risk_id:
                return scene_id, risk
    return None


def build_risk_register(project: Project) -> dict[str, Any]:
    """Every risk on record, with the coverage that says how much of the production it speaks for."""
    planned_ids = set(project.plans.keys())
    rows: list[dict[str, Any]] = []

    for scene_id, plan in project.plans.items():
        try:
            scene = project.scene(scene_id)
        except KeyError:  # a plan for a scene that has since been removed
            continue
        # Where the scene is scheduled, so a risk can be read against the day that carries it.
        scheduled = [
            {"shoot_day_id": d.id, "day_number": d.day_number, "date": d.date}
            for d in project.shoot_days
            for i in d.items
            if i.scene_id == scene_id
        ]
        for risk in plan.risks:
            rows.append({
                "id": risk.id,
                "scene_id": scene_id,
                "scene_number": scene.number,
                "scene_heading": scene.heading,
                "scheduled_on": scheduled,
                "title": risk.title,
                "description": risk.description,
                "severity": risk.severity.value,
                "likelihood": risk.likelihood,
                "confidence": risk.confidence,
                "kind": risk.kind.value,
                "mitigations": list(risk.mitigations),
                "evidence_ids": list(risk.evidence_ids),
                "exposure": round(exposure(risk), 4),
                # The decision half. A closed risk keeps its row — a register that hides what it has
                # settled is a to-do list, and the settled rows are the part worth reading back.
                "status": risk.status.value,
                "owner": risk.owner,
                "decision_note": risk.decision_note,
                "decided_by": risk.decided_by,
                "decided_at": risk.decided_at.isoformat() if risk.decided_at else None,
            })

    rows.sort(key=lambda r: (-r["exposure"], SEVERITY_ORDER.index(Severity(r["severity"])), r["scene_number"]))

    by_severity: dict[str, list[dict[str, Any]]] = {s.value: [] for s in SEVERITY_ORDER}
    for row in rows:
        by_severity[row["severity"]].append(row)

    unplanned = [
        {"scene_id": s.id, "scene_number": s.number, "heading": s.heading}
        for s in project.scenes
        if s.id not in planned_ids
    ]
    unplanned.sort(key=lambda s: s["scene_number"])

    return {
        "production": project.title,
        "risks": rows,
        "by_severity": by_severity,
        "counts": {s.value: len(by_severity[s.value]) for s in SEVERITY_ORDER},
        "total": len(rows),
        "scenes_planned": len(planned_ids),
        "scenes_total": len(project.scenes),
        "unplanned_scenes": unplanned,
        # Said plainly, because the difference between "no risks" and "nobody has looked" is the
        # whole value of the document.
        "coverage_note": (
            f"{len(planned_ids)} of {len(project.scenes)} scene(s) have been planned. A scene that has not been "
            "planned has no risk register — not an empty one — and nothing here should be read as saying it is safe."
        ),
        "empty_note": (
            None
            if rows
            else "No scene has been planned yet, so this production has no risks on record. Plan a scene to research "
                 "its risks — they are produced by the planning run, against evidence, and graded down to an "
                 "inference wherever a citation did not survive validation."
        ),
        "provenance": (
            "Risks are written by the planning workflow from researched evidence, graded by the same confidence gate "
            "the readiness score uses, and ordered here by severity × likelihood — the identical product that score sums."
        ),
    }
