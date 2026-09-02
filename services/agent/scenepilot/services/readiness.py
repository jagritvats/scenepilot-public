"""Readiness heuristic — a transparent product metric, not scientific truth."""

from __future__ import annotations

from ..domain.enums import EvidenceStatus, Importance, Severity
from ..domain.models import Evidence, ReadinessBreakdown, ResearchQuestion, Risk, UnresolvedQuestion

PRIORITY_WEIGHT = {Importance.CRITICAL: 3.0, Importance.HIGH: 2.0, Importance.MEDIUM: 1.0, Importance.LOW: 0.5}
STATUS_CREDIT = {EvidenceStatus.SUPPORTED: 1.0, EvidenceStatus.WEAK: 0.5, EvidenceStatus.CONFLICTING: 0.3, EvidenceStatus.MISSING: 0.0}
SEVERITY_WEIGHT = {Severity.CRITICAL: 1.0, Severity.HIGH: 0.6, Severity.MEDIUM: 0.3, Severity.LOW: 0.1}


def compute_readiness(questions: list[ResearchQuestion], evidence: list[Evidence], risks: list[Risk], unresolved: list[UnresolvedQuestion]) -> tuple[int, ReadinessBreakdown]:
    top_level = [q for q in questions if q.parent_question_id is None] or questions
    if top_level:
        wsum = sum(PRIORITY_WEIGHT[q.priority] for q in top_level)
        coverage = sum(PRIORITY_WEIGHT[q.priority] * STATUS_CREDIT.get(q.status or EvidenceStatus.MISSING, 0.0) for q in top_level) / wsum
    else:
        coverage = 0.0
    strength = (sum(e.confidence for e in evidence) / len(evidence)) if evidence else 0.0
    exposure = min(1.0, sum(SEVERITY_WEIGHT[r.severity] * r.likelihood for r in risks) / 2.0) if risks else 0.0
    penalty = min(25, 5 * len(unresolved))
    score = 100 * (0.40 * coverage + 0.25 * strength + 0.35 * (1 - exposure)) - penalty
    score_i = int(round(max(0, min(100, score))))
    explanation = [
        f"requirement/question coverage {coverage:.0%} × 0.40 (priority-weighted SUPPORTED=1, WEAK=0.5, CONFLICTING=0.3, MISSING=0)",
        f"evidence strength {strength:.0%} × 0.25 (mean evidence confidence)",
        f"risk exposure {exposure:.0%} → (1−exposure) × 0.35 (severity × likelihood)",
        f"unresolved penalty −{penalty} ({len(unresolved)} open question(s), 5 each, max 25)",
    ]
    return score_i, ReadinessBreakdown(requirement_coverage=round(coverage, 3), evidence_strength=round(strength, 3), risk_exposure=round(exposure, 3), unresolved_penalty=penalty, explanation=explanation)
