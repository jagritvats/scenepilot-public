"""Force Majeure / weather-delay claim packet, compiled from what the production actually holds.

An underwriter reads a weather claim in three passes — was the peril real, did the production try to
mitigate rather than idle, and what did the mitigation cost — so the packet is those three sections
and nothing else. Every row is a field of the persisted `Disruption`, a result Parallel Search
returned, a violation the deterministic engine raised against a schedule it rejected, a figure on the
approved ChangeSet's recovery option, or an accepted `LocationFact` with the page Parallel cited.

The strongest section is the middle one, and it is the section nobody can fake: an underwriter
refuses a weather claim that cannot show the production tried to work rather than idle, and the
rejected recovery options are a machine-generated record of exactly that attempt — each with the
constraint that killed it, in minutes, against a real permit window or a cited statute.

What is deliberately *absent* is the other half of the argument. A production insurance packet has a
policy number, an insured sum, a deductible and a notice deadline; ScenePilot holds none of them, so
they are emitted as named blanks for the producer to complete. There is likewise no gross unmitigated
loss: a shoot day carries overtime, carry-over, re-rental and company-move rates but no insured day
value, so the loss a full abandonment would have caused cannot be computed from production state and
is therefore not stated. A packet that invented one would not be evidence — it would be a document
shaped like evidence, which is worse than no packet at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from ..domain.enums import ConstraintKind
from ..domain.models import (
    ConstraintViolation,
    Disruption,
    Evidence,
    LocationFact,
    Project,
    RecoveryOption,
    ScheduleItem,
    SearchRun,
    ShootDay,
    WorkflowRun,
)
from .schedule import ValidationContext, validate_schedule
from .timeutil import to_minutes

# Terms of an insurance policy, not facts about a production. Named here so the packet reports the
# gap rather than hiding it, and so the producer knows exactly what is still to be filled in.
NOT_IN_PRODUCTION_STATE: list[tuple[str, str, str]] = [
    ("policy_number", "Policy / completion bond number", "A policy term. ScenePilot holds no policy, so no number is stated."),
    ("insurer", "Insurer or completion guarantor", "Not held in production state."),
    (
        "insured_daily_production_cost_inr",
        "Insured daily production cost",
        "The shoot day carries overtime, carry-over, re-rental and company-move rates but no insured day value, "
        "so no gross unmitigated loss is computed here.",
    ),
    ("deductible_inr", "Deductible / retention", "A policy term. Not held in production state."),
    ("notice_deadline", "Notice deadline", "A policy term. Not held in production state."),
]


def compile_insurance_dossier(
    project: Project,
    day: ShootDay,
    disruption: Disruption | None = None,
    run: WorkflowRun | None = None,
    search_runs: Sequence[SearchRun] | None = None,
) -> dict[str, Any]:
    """Peril evidence → proof of mitigation → cost delta, for one shoot day.

    Nothing is invented when a section has no state behind it: a day with no reported disruption gets
    a packet that says so and carries only the constraints on record, rather than a claim about
    weather nobody reported.
    """
    rescue = run.rescue if run is not None else None
    options = list(rescue.options) if rescue else []
    changeset = rescue.changeset if rescue else None
    selected = _selected_option(options, changeset.recovery_option_id if changeset else (rescue.recommended_option_id if rescue else None))
    findings = _findings(rescue.evidence if rescue else [], disruption)
    searches = list(search_runs or [])
    constraints = _constraints_on_record(project, day, options, rescue.baseline if rescue else [])

    peril = None
    if disruption is not None:
        peril = {
            "peril": _peril(project, disruption),
            "verification": _verification(disruption, searches, findings),
            "certified_sources": [_source_row(sr) for sr in searches],
            "analyst_findings": [_finding_row(e) for e in findings],
        }

    mitigation = None
    if options:
        rejected = [o for o in options if not o.feasible]
        mitigation = {
            "alternatives_evaluated": len(options),
            "rejected_by_hard_constraint": len(rejected),
            "rejected_alternatives": [_rejected_row(project, o) for o in rejected],
            "alternatives_not_selected": [_alternative_row(project, o) for o in options if o.feasible and (selected is None or o.id != selected.id)],
            "selected_option": _selected_row(project, selected) if selected else None,
            "rationale": rescue.recommendation_rationale or None,
            "decision": _decision(changeset),
        }

    return {
        "dossier_id": f"INS-FM-{project.id.upper()}-D{day.day_number}-{day.date.replace('-', '')}",
        "claim_type": "WEATHER_FORCE_MAJEURE_AND_CIVIL_AUTHORITY",
        "claim_status": _claim_status(disruption, options, changeset),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "production": {
            "id": project.id,
            "title": project.title,
            "base_city": project.base_city,
            "country_code": project.country_code,
            "currency": project.currency,
            "fictional": project.synthetic,
        },
        "notice": (
            f"{project.title} is a fictional production. This packet is generated by ScenePilot from its own "
            "production state as a demonstration artifact; it is not a filed insurance claim."
            if project.synthetic
            else "Generated by ScenePilot from persisted production state."
        ),
        "shoot_day": _shoot_day(day),
        "peril_evidence": peril,
        "proof_of_mitigation": mitigation,
        "cost_delta": _cost_delta(project, day, selected, options, approved=changeset is not None and changeset.applied_at is not None),
        "constraints_on_record": constraints,
        "summary": _summary(day, disruption, searches, findings, options, selected, changeset, constraints),
        "provenance": {
            "peril_evidence": "the persisted Disruption record and the Parallel Search runs its verification fired",
            "proof_of_mitigation": "recovery options generated and validated by the deterministic constraint engine",
            "cost_delta": "the selected option's own constraint violations, priced at this shoot day's rates",
            "constraints_on_record": "LocationFacts a producer accepted, with the citations Parallel returned",
        },
    }


# --------------------------------------------------------------------------- #
# Peril evidence
# --------------------------------------------------------------------------- #


def _peril(project: Project, d: Disruption) -> dict[str, Any]:
    return {
        "type": d.type.value,
        "title": d.title,
        "description": d.description,
        "window_start": d.window_start,
        "window_end": d.window_end,
        "window_minutes": (to_minutes(d.window_end) - to_minutes(d.window_start)) if d.window_start and d.window_end else None,
        "dry_out_minutes": d.dry_out_minutes,
        "affects_exteriors": d.affects_exteriors,
        "affected_locations": [_resource_name(project, r) for r in d.affects_location_ids],
        "affected_resources": [_resource_name(project, r) for r in d.affects_resource_ids],
        "reported_at_utc": _iso(d.received_at),
        "reported_via": d.source,
        "fixture_id": d.fixture_id,
        "monitor_id": d.monitor_id,
        "synthetic": d.synthetic,
    }


def _verification(d: Disruption, searches: Sequence[SearchRun], findings: Sequence[Evidence]) -> dict[str, Any]:
    return {
        "verified_by": "Parallel Search API",
        "status": d.verification_status.value if d.verification_status else None,
        "summary": d.verification_summary,
        "confidence": d.verification_confidence,
        "confidence_pct": round(d.verification_confidence * 100) if d.verification_confidence is not None else None,
        "searches_run": len(searches),
        "sources_returned": sum(len(sr.results) for sr in searches),
        "findings_retained": len(findings),
    }


def _source_row(sr: SearchRun) -> dict[str, Any]:
    """One Parallel Search call, exactly as it was sent and exactly as it came back."""
    return {
        "search_run_id": sr.id,
        "provider": sr.provider,
        "mode": sr.mode,
        "status": sr.status,
        "replayed": sr.replayed,
        "ran_at_utc": _iso(sr.started_at),
        "objective": sr.objective,
        "queries": list(sr.queries),
        "settings_sent": sr.advanced_settings,
        "results": [
            {
                "url": item.url,
                "title": item.title,
                "publish_date": item.publish_date,
                "excerpt": item.excerpts[0] if item.excerpts else None,
            }
            for item in sr.results
        ],
    }


def _findings(evidence: Sequence[Evidence], d: Disruption | None) -> list[Evidence]:
    if d is None:
        return []
    ids = set(d.evidence_ids)
    return [e for e in evidence if e.id in ids]


def _finding_row(e: Evidence) -> dict[str, Any]:
    return {
        "claim": e.claim,
        "source_url": e.source_url,
        "source_title": e.source_title,
        "publish_date": e.publish_date,
        "excerpt": e.excerpt,
        "authority": e.authority.value,
        "freshness": e.freshness.value,
        "confidence": e.confidence,
        "production_implication": e.production_implication,
        "search_run_id": e.search_run_id,
    }


# --------------------------------------------------------------------------- #
# Proof of mitigation
# --------------------------------------------------------------------------- #


def _selected_option(options: Sequence[RecoveryOption], option_id: str | None) -> RecoveryOption | None:
    return next((o for o in options if o.id == option_id), None) if option_id else None


def _rejected_row(project: Project, o: RecoveryOption) -> dict[str, Any]:
    """A schedule the engine refused, with the constraint that refused it."""
    return {
        "label": o.label,
        "title": o.title,
        "strategy": _distinct(o.strategy, o.title),
        "origin": o.origin,
        "rejected_reason": o.rejected_reason,
        "violations": [_violation_row(project, v) for v in o.violations if v.hard],
    }


def _alternative_row(project: Project, o: RecoveryOption) -> dict[str, Any]:
    return {
        "label": o.label,
        "title": o.title,
        "strategy": _distinct(o.strategy, o.title),
        "score_total": o.score.total if o.score else None,
        "extra_cost_inr": o.score.estimated_extra_cost_inr if o.score else None,
        "carried_over": [_scene_number(project, s) for s in o.deferred_scene_ids],
    }


def _selected_row(project: Project, o: RecoveryOption) -> dict[str, Any]:
    return {
        "label": o.label,
        "title": o.title,
        "strategy": _distinct(o.strategy, o.title),
        "origin": o.origin,
        "rank": o.rank,
        "feasible": o.feasible,
        # The explainer falls back to the strategy line when Gemini is unavailable; printing it twice
        # would pad the packet with a restatement dressed as a second finding.
        "explanation": _distinct(o.explanation, o.title, o.strategy),
        "trade_offs": list(o.trade_offs),
        "checks": list(o.checks),
        "score": o.score.model_dump(mode="json") if o.score else None,
        "schedule": [_schedule_row(project, i) for i in sorted(o.schedule, key=lambda i: to_minutes(i.start))],
        "carried_over": [_scene_number(project, s) for s in o.deferred_scene_ids],
    }


def _schedule_row(project: Project, item: ScheduleItem) -> dict[str, Any]:
    scene = next((s for s in project.scenes if s.id == item.scene_id), None)
    location_id = item.location_id or (scene.location_id if scene else None)
    return {
        "scene_number": scene.number if scene else item.scene_id,
        "heading": scene.heading if scene else None,
        "start": item.start,
        "end": item.end,
        "minutes": to_minutes(item.end) - to_minutes(item.start),
        "location": _resource_name(project, location_id) if location_id else None,
    }


def _decision(changeset) -> dict[str, Any] | None:
    """The approval itself: who signed it, when, and what it rewrote. None until a producer approves."""
    if changeset is None or changeset.applied_at is None:
        return None
    return {
        "approved_by": changeset.approved_by,
        "approved_at_utc": _iso(changeset.applied_at),
        "changeset_id": changeset.id,
        "summary": changeset.summary,
        "changes": [
            {
                "entity_type": c.entity_type,
                "label": c.label,
                "field": c.field,
                "before": c.before,
                "after": c.after,
                "reason": c.reason,
            }
            for c in changeset.changes
        ],
    }


# --------------------------------------------------------------------------- #
# Cost delta
# --------------------------------------------------------------------------- #


def _cost_delta(project: Project, day: ShootDay, selected: RecoveryOption | None, options: Sequence[RecoveryOption], approved: bool) -> dict[str, Any]:
    """What the mitigation cost, itemised from the constraints the chosen schedule actually broke.

    The line items are the selected option's own soft violations, which is the same list the engine
    summed into `estimated_extra_cost_inr` — so the itemisation and the total cannot drift apart.
    Soft violations the engine does not price (a lighting compromise, a continuity split) are counted
    rather than given an invented rupee figure. `basis` says whether the figure belongs to a schedule
    a producer signed or one that is still only recommended; they are not the same claim.
    """
    priced = [v for v in (selected.violations if selected else []) if not v.hard and v.cost_inr]
    unpriced = [v for v in (selected.violations if selected else []) if not v.hard and not v.cost_inr]
    return {
        "currency": project.currency,
        "basis": (("approved" if approved else "recommended") if selected else None),
        "rates": {
            "overtime_per_hour_inr": day.overtime_rate_per_hour,
            "carry_over_per_scene_inr": day.carry_over_cost,
            "company_move_inr": day.company_move_cost,
        },
        "mitigation_cost_inr": selected.score.estimated_extra_cost_inr if (selected and selected.score) else None,
        "overtime_minutes": selected.score.overtime_minutes if (selected and selected.score) else None,
        "extra_company_moves": selected.score.extra_company_moves if (selected and selected.score) else None,
        "line_items": [_violation_row(project, v) for v in priced],
        "unpriced_constraints": [_violation_row(project, v) for v in unpriced],
        "alternatives_priced": [
            {
                "label": o.label,
                "feasible": o.feasible,
                "extra_cost_inr": o.score.estimated_extra_cost_inr if o.score else None,
                "selected": bool(selected and o.id == selected.id),
            }
            for o in options
            if o.score is not None
        ],
        "not_in_production_state": [
            {"field": field, "label": label, "value": None, "why": why} for field, label, why in NOT_IN_PRODUCTION_STATE
        ],
    }


def _violation_row(project: Project, v: ConstraintViolation) -> dict[str, Any]:
    return {
        "kind": v.kind.value,
        "hard": v.hard,
        "description": v.message,
        "scene_number": _scene_number(project, v.scene_id) if v.scene_id else None,
        "resource": _resource_name(project, v.resource_id) if v.resource_id else None,
        "minutes": v.minutes or None,
        "amount_inr": v.cost_inr or None,
        "fact_id": v.fact_id,
        "evidence_url": v.evidence_url,
    }


# --------------------------------------------------------------------------- #
# Constraints on record — accepted facts, with the page Parallel cited
# --------------------------------------------------------------------------- #


def _day_location_ids(project: Project, day: ShootDay, options: Sequence[RecoveryOption], baseline: Sequence[ScheduleItem]) -> list[str]:
    """Every location this day touches — as it stands, as it stood, and in any schedule considered."""
    items = list(day.items) + list(baseline) + [i for o in options for i in o.schedule]
    ids: list[str] = []
    for item in items:
        scene = next((s for s in project.scenes if s.id == item.scene_id), None)
        loc = item.location_id or (scene.location_id if scene else None)
        if loc and loc not in ids:
            ids.append(loc)
    return ids


def _constraints_on_record(project: Project, day: ShootDay, options: Sequence[RecoveryOption], baseline: Sequence[ScheduleItem]) -> list[dict[str, Any]]:
    """Accepted, machine-checkable facts for this day's locations, and what they rejected.

    `LocationFact.binds` is the filter, because those are the only facts that can refuse a schedule:
    HARD, mechanically checkable, and signed off by a producer. Whether one bites on *this* day is
    computed, not asserted — the day's committed schedule is re-validated against the fact.
    """
    location_ids = _day_location_ids(project, day, options, baseline)
    facts = [f for f in project.location_facts if f.binds and f.resource_id in location_ids]
    if not facts:
        return []
    ctx = ValidationContext(project=project, day=day, location_facts=facts)
    current = [v for v in validate_schedule(ctx, day.items) if v.kind == ConstraintKind.EXTERNAL_RULE]
    return [_fact_row(project, f, current, options) for f in facts]


def _fact_row(project: Project, fact: LocationFact, current: Sequence[ConstraintViolation], options: Sequence[RecoveryOption]) -> dict[str, Any]:
    rejected = [
        {"option_label": o.label, "message": v.message}
        for o in options
        if not o.feasible
        for v in o.violations
        if v.fact_id == fact.id
    ]
    return {
        "fact_id": fact.id,
        "label": fact.label,
        "value": fact.value,
        "location": _resource_name(project, fact.resource_id),
        "binding": fact.binding.value,
        "confidence": fact.confidence,
        "reasoning": fact.reasoning,
        "rule": fact.rule.model_dump(mode="json") if fact.rule else None,
        "citations": [{"url": c.url, "title": c.title, "excerpt": c.excerpts[0] if c.excerpts else None} for c in fact.citations],
        "accepted_by": fact.accepted_by,
        "accepted_at_utc": _iso(fact.accepted_at),
        "discovered_by": "Parallel Task API",
        "task_run_id": fact.task_run_id,
        "current_schedule_violations": [_violation_row(project, v) for v in current if v.fact_id == fact.id],
        "rejected_schedules": rejected,
    }


# --------------------------------------------------------------------------- #
# Headline
# --------------------------------------------------------------------------- #


def _claim_status(disruption: Disruption | None, options: Sequence[RecoveryOption], changeset) -> str:
    if disruption is None:
        return "NO_PERIL_ON_RECORD"
    if changeset is not None and changeset.applied_at is not None:
        return "MITIGATION_APPLIED"
    if options:
        return "AWAITING_PRODUCER_DECISION"
    return "PERIL_REPORTED"


def _shoot_day(day: ShootDay) -> dict[str, Any]:
    return {
        "id": day.id,
        "day_number": day.day_number,
        "date": day.date,
        "unit_call": day.unit_call,
        "standard_hours": day.standard_hours,
        "hard_wrap": day.hard_wrap,
        "crew_size": day.crew_size,
        "status": day.status.value,
    }


def _summary(
    day: ShootDay,
    disruption: Disruption | None,
    searches: Sequence[SearchRun],
    findings: Sequence[Evidence],
    options: Sequence[RecoveryOption],
    selected: RecoveryOption | None,
    changeset,
    constraints: Sequence[dict[str, Any]],
) -> str:
    parts: list[str] = []
    if disruption is None:
        parts.append(f"No disruption is on record for Day {day.day_number} ({day.date}), so there is no peril to evidence.")
    else:
        window = f" over {disruption.window_start}–{disruption.window_end}" if disruption.window_start and disruption.window_end else ""
        parts.append(f"Day {day.day_number} ({day.date}): {disruption.title}{window}, reported via {disruption.source}.")
        if searches:
            verdict = disruption.verification_status.value.replace("_", " ").lower() if disruption.verification_status else "not assessed"
            confidence = f" at {round(disruption.verification_confidence * 100)}% confidence" if disruption.verification_confidence is not None else ""
            parts.append(
                f"{len(searches)} Parallel Search run(s) returned {sum(len(s.results) for s in searches)} source(s); "
                f"{len(findings)} finding(s) retained — {verdict}{confidence}."
            )
        else:
            parts.append("No external verification search is on record for this disruption.")
    if options:
        rejected = [o for o in options if not o.feasible]
        parts.append(f"{len(options)} schedule(s) evaluated, {len(rejected)} rejected by a hard constraint.")
        if selected and selected.score:
            cost = f"₹{selected.score.estimated_extra_cost_inr:,}"
            if changeset is not None and changeset.applied_at is not None:
                parts.append(f"Option {selected.label} approved by {changeset.approved_by} on {changeset.applied_at.date().isoformat()}, at {cost}.")
            else:
                parts.append(f"Option {selected.label} recommended at {cost}; no producer approval on record yet.")
    if constraints:
        biting = sum(len(c["current_schedule_violations"]) for c in constraints)
        parts.append(
            f"{len(constraints)} accepted external constraint(s) apply to this day's locations"
            + (f", {biting} of them broken by the committed schedule." if biting else ", none broken by the committed schedule.")
        )
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Lookups that must never raise inside a document
# --------------------------------------------------------------------------- #


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _distinct(value: str | None, *already_shown: str | None) -> str | None:
    """The value, unless it only repeats something the packet already prints."""
    return value or None if value and value not in already_shown else None


def _resource_name(project: Project, resource_id: str | None) -> str | None:
    if not resource_id:
        return None
    try:
        return project.resource(resource_id).name
    except KeyError:
        return resource_id


def _scene_number(project: Project, scene_id: str | None) -> str | None:
    if not scene_id:
        return None
    try:
        return project.scene(scene_id).number
    except KeyError:
        return scene_id
