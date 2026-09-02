"""Transparent recovery scoring. Every component is explainable; no hidden AI score."""

from __future__ import annotations

from ..domain.enums import ConstraintKind
from ..domain.models import ConstraintViolation, Project, ScheduleItem, ScoreComponents, ShootDay
from .schedule import count_company_moves, overtime_minutes
from .timeutil import to_minutes

WEIGHTS = {
    "schedule_preservation": 0.30,
    "cost_impact": 0.15,
    "overtime_risk": 0.15,
    "company_moves": 0.10,
    "resource_conflicts": 0.10,
    "creative_compromise": 0.15,
    "confidence": 0.05,
}
COST_SCALE_INR = 150_000  # extra cost at which cost_impact hits 0
OVERTIME_SCALE_MIN = 180
COVER_BONUS = 10  # preservation bonus per cover scene productively pulled forward
MOVED_PENALTY = 4  # preservation penalty per baseline scene whose start moved


def _clamp(x: float) -> int:
    return int(round(max(0, min(100, x))))


def score_option(
    project: Project,
    day: ShootDay,
    baseline: list[ScheduleItem],
    schedule: list[ScheduleItem],
    deferred_scene_ids: list[str],
    violations: list[ConstraintViolation],
    verification_confidence: float | None,
) -> ScoreComponents:
    feasible = not any(v.hard for v in violations)
    base_by_scene = {i.scene_id: i for i in baseline}
    new_by_scene = {i.scene_id: i for i in schedule}
    base_total = sum(project.scene(i.scene_id).estimated_minutes for i in baseline) or 1

    retained = sum(project.scene(sid).estimated_minutes for sid in base_by_scene if sid in new_by_scene)
    moved = sum(1 for sid, it in base_by_scene.items() if sid in new_by_scene and new_by_scene[sid].start != it.start)
    covers = [sid for sid in new_by_scene if sid not in base_by_scene]
    preservation = 100 * retained / base_total - MOVED_PENALTY * moved + COVER_BONUS * len(covers)

    extra_cost = sum(v.cost_inr for v in violations if not v.hard)
    cost_impact = 100 - 100 * extra_cost / COST_SCALE_INR

    ot = overtime_minutes(day, schedule)
    overtime_risk = 100 - 100 * ot / OVERTIME_SCALE_MIN

    extra_moves = max(0, count_company_moves(project, schedule) - count_company_moves(project, baseline))
    company_moves = 100 - 25 * extra_moves

    resource_soft = sum(1 for v in violations if v.kind in {ConstraintKind.EQUIPMENT_RERENTAL, ConstraintKind.CONTINUITY_SPLIT, ConstraintKind.TURNAROUND})
    resource_hard = sum(1 for v in violations if v.hard and v.kind in {ConstraintKind.CAST_UNAVAILABLE, ConstraintKind.LOCATION_UNAVAILABLE, ConstraintKind.EQUIPMENT_UNAVAILABLE, ConstraintKind.WEATHER_SENSITIVE_EQUIPMENT})
    resource_conflicts = 100 - 20 * resource_soft - 40 * resource_hard

    lighting = sum(1 for v in violations if v.kind == ConstraintKind.LIGHTING_COMPROMISE)
    continuity = sum(1 for v in violations if v.kind == ConstraintKind.CONTINUITY_SPLIT)
    creative = 100 - 30 * lighting - 15 * continuity - 10 * len(deferred_scene_ids)

    confidence = 100 * (verification_confidence if verification_confidence is not None else 0.5)

    comps = {
        "schedule_preservation": _clamp(preservation),
        "cost_impact": _clamp(cost_impact),
        "overtime_risk": _clamp(overtime_risk),
        "company_moves": _clamp(company_moves),
        "resource_conflicts": _clamp(resource_conflicts),
        "creative_compromise": _clamp(creative),
        "confidence": _clamp(confidence),
    }
    total = sum(WEIGHTS[k] * comps[k] for k in WEIGHTS)
    return ScoreComponents(
        feasible=feasible,
        total=_clamp(total) if feasible else 0,
        estimated_extra_cost_inr=extra_cost,
        overtime_minutes=ot,
        extra_company_moves=extra_moves,
        deferred_scene_ids=list(deferred_scene_ids),
        **comps,
    )


def explain_score(score: ScoreComponents) -> list[str]:
    lines = []
    for k, w in WEIGHTS.items():
        lines.append(f"{k}: {getattr(score, k)} × {w:.2f}")
    if not score.feasible:
        lines.append("total forced to 0: hard constraint violated")
    return lines


def last_wrap(schedule: list[ScheduleItem]) -> int:
    return max((to_minutes(i.end) for i in schedule), default=0)
