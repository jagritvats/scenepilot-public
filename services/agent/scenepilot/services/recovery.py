"""Recovery candidate generation, validation and ranking.

Candidates are *orderings* of scenes (plus deferrals and cover scenes). A deterministic
packer turns an ordering into concrete times; `validate_schedule` checks hard/soft
constraints; `score_option` produces transparent components. Gemini may propose extra
orderings — they go through exactly the same validation.
"""

from __future__ import annotations

import hashlib
from itertools import combinations, permutations

from ..domain.enums import ConstraintKind
from ..domain.models import (
    Disruption,
    ImpactAnalysis,
    Project,
    RecoveryOption,
    RescueState,
    ScheduleItem,
    ShootDay,
)
from .schedule import ValidationContext, pack_day, validate_schedule
from .scoring import score_option
from .timeutil import to_minutes

MAX_DEFERRALS = 2
MAX_FEASIBLE = 3
MAX_INFEASIBLE = 2
LABELS = "ABCDEFGH"


def next_day_call(project: Project, day: ShootDay) -> int | None:
    """Unit call of the next shoot day (by date), as minutes from this day's midnight."""
    from datetime import date

    try:
        this = date.fromisoformat(day.date)
        following = sorted((date.fromisoformat(d.date), d) for d in project.shoot_days if d.date > day.date)
    except ValueError:
        return None
    if not following:
        return None
    nd_date, nd = following[0]
    return (nd_date - this).days * 24 * 60 + to_minutes(nd.unit_call)


def _baseline_order(day: ShootDay) -> list[str]:
    return [i.scene_id for i in sorted(day.items, key=lambda i: to_minutes(i.start))]


def build_option(
    project: Project,
    day: ShootDay,
    disruption: Disruption | None,
    baseline: list[ScheduleItem],
    order: list[str],
    deferred: list[str],
    origin: str,
    title: str | None = None,
    strategy: str | None = None,
    verification_confidence: float | None = None,
    schedule_override: list[ScheduleItem] | None = None,
) -> RecoveryOption:
    item_ids = {i.scene_id: i.id for i in baseline}
    schedule = schedule_override or pack_day(project, day, order, disruption, item_ids)
    # Accepted, machine-checkable facts Parallel discovered are hard constraints like any other:
    # an option that breaks a real noise curfew is rejected, with the citation on the violation.
    # No `labor_pack` is passed and none should be: `ValidationContext.pack` resolves the
    # production's own agreement, so the meal and turnaround rules an option is scored against are
    # exactly the ones the day page names — see `services/labor_rules.active_pack`.
    ctx = ValidationContext(project=project, day=day, disruption=disruption, baseline_items=baseline, deferred_scene_ids=deferred, next_day_call=next_day_call(project, day), location_facts=[f for f in project.location_facts if f.binds])
    violations = validate_schedule(ctx, schedule)
    score = score_option(project, day, baseline, schedule, deferred, violations, verification_confidence)
    feasible = score.feasible
    hard = [v for v in violations if v.hard]
    opt = RecoveryOption(
        id="opt_" + hashlib.sha1(("|".join(f"{s.scene_id}@{s.start}" for s in schedule) + "||" + ",".join(sorted(deferred))).encode()).hexdigest()[:10],
        label="?",
        title=title or describe_strategy(project, day, baseline, schedule, deferred),
        strategy=strategy or describe_strategy(project, day, baseline, schedule, deferred),
        origin=origin,
        schedule=schedule,
        deferred_scene_ids=list(deferred),
        violations=violations,
        feasible=feasible,
        score=score,
        rejected_reason=None if feasible else "; ".join(dict.fromkeys(v.message for v in hard)),
        checks=build_checks(project, violations, schedule, deferred),
    )
    return opt


def describe_strategy(project: Project, day: ShootDay, baseline: list[ScheduleItem], schedule: list[ScheduleItem], deferred: list[str]) -> str:
    base = {i.scene_id: i for i in baseline}
    new = {i.scene_id: i for i in schedule}
    parts: list[str] = []
    if schedule and all(sid in base and base[sid].start == new[sid].start for sid in new) and not deferred and len(new) == len(base):
        return "Hold the existing schedule"
    for sid in deferred:
        parts.append(f"defer Sc {project.scene(sid).number}")
    for sid, it in new.items():
        if sid not in base:
            parts.append(f"pull cover Sc {project.scene(sid).number} into {it.start}")
        elif base[sid].start != it.start:
            direction = "earlier" if to_minutes(it.start) < to_minutes(base[sid].start) else "later"
            parts.append(f"move Sc {project.scene(sid).number} {base[sid].start}→{it.start} ({direction})")
    return "; ".join(parts) if parts else "Hold the existing schedule"


def build_checks(project: Project, violations, schedule: list[ScheduleItem], deferred: list[str]) -> list[dict]:
    def has(kind: ConstraintKind) -> list:
        return [v for v in violations if v.kind == kind]

    checks = []
    for label, kinds in [
        ("no cast conflicts", [ConstraintKind.CAST_UNAVAILABLE]),
        ("locations available", [ConstraintKind.LOCATION_UNAVAILABLE]),
        ("equipment available", [ConstraintKind.EQUIPMENT_UNAVAILABLE, ConstraintKind.WEATHER_SENSITIVE_EQUIPMENT]),
        ("clear of disruption window", [ConstraintKind.DISRUPTION_EXPOSURE]),
        ("lighting requirements met", [ConstraintKind.TIME_OF_DAY_INCOMPATIBLE]),
        ("no overlaps / travel conflicts", [ConstraintKind.ITEM_OVERLAP, ConstraintKind.TRAVEL_OVERLAP, ConstraintKind.DAY_BOUNDS]),
        ("external rules (permits, curfews)", [ConstraintKind.EXTERNAL_RULE]),
    ]:
        hits = [v for k in kinds for v in has(k)]
        checks.append({"label": label, "ok": not hits, "hard": True, "detail": hits[0].message if hits else None})
    for label, kind in [
        ("overtime exposure", ConstraintKind.OVERTIME),
        ("additional company moves", ConstraintKind.EXTRA_COMPANY_MOVE),
        ("lighting compromise", ConstraintKind.LIGHTING_COMPROMISE),
        ("equipment re-rental", ConstraintKind.EQUIPMENT_RERENTAL),
        ("continuity split", ConstraintKind.CONTINUITY_SPLIT),
        ("lunch break", ConstraintKind.MEAL_BREAK),
        ("turnaround before next call", ConstraintKind.TURNAROUND),
    ]:
        hits = has(kind)
        if hits:
            total_cost = sum(v.cost_inr for v in hits)
            detail = hits[0].message if len(hits) == 1 else f"{len(hits)} × {label}"
            if total_cost:
                detail = f"₹{total_cost:,} — {detail}"
            checks.append({"label": label, "ok": False, "hard": False, "detail": detail})
    if deferred:
        nums = ", ".join(f"Sc {project.scene(s).number}" for s in deferred)
        checks.append({"label": "scenes carried over", "ok": False, "hard": False, "detail": nums})
    return checks


def baseline_blocked_scenes(project: Project, day: ShootDay, disruption: Disruption | None, baseline: list[ScheduleItem]) -> list[str]:
    """Scenes the current schedule already violates a hard constraint on, disruption aside."""
    ctx = ValidationContext(project=project, day=day, disruption=disruption, baseline_items=baseline, location_facts=[f for f in project.location_facts if f.binds])
    return list(dict.fromkeys(v.scene_id for v in validate_schedule(ctx, baseline) if v.hard and v.scene_id))


def generate_candidates(
    project: Project,
    day: ShootDay,
    disruption: Disruption,
    impact: ImpactAnalysis,
    verification_confidence: float | None = None,
) -> list[RecoveryOption]:
    """Enumerate orderings × deferral subsets × cover inclusion; validate and score each."""
    baseline = sorted(day.items, key=lambda i: to_minutes(i.start))
    base_scene_ids = [i.scene_id for i in baseline]
    affected_scenes = [next(i.scene_id for i in day.items if i.id == iid) for iid in impact.directly_affected_item_ids]
    # A scene the baseline already cannot legally shoot — typically an accepted external rule such as a
    # noise curfew — is just as movable as a disruption-hit one, and a producer would treat it that way.
    for sid in baseline_blocked_scenes(project, day, disruption, baseline):
        if sid not in affected_scenes:
            affected_scenes.append(sid)
    covers = list(impact.cover_scene_ids)

    seen: dict[str, RecoveryOption] = {}

    def consider(order: list[str], deferred: list[str], origin: str = "deterministic", **kw) -> RecoveryOption:
        opt = build_option(project, day, disruption, baseline, order, deferred, origin, verification_confidence=verification_confidence, **kw)
        sig = "|".join(f"{i.scene_id}@{i.start}" for i in opt.schedule) + "||" + ",".join(sorted(deferred))
        if sig not in seen:
            seen[sig] = opt
        return seen[sig]

    # 0. Hold: baseline schedule unchanged (evaluated against the disruption)
    consider(base_scene_ids, [], title="Hold the existing schedule", strategy="Shoot through as planned", schedule_override=baseline)

    # 1. Deferral subsets (only affected scenes may be deferred), cover subsets
    deferral_subsets: list[list[str]] = [[]]
    for k in range(1, min(MAX_DEFERRALS, len(affected_scenes)) + 1):
        deferral_subsets += [list(c) for c in combinations(affected_scenes, k)]
    cover_subsets: list[list[str]] = [[]] + [[c] for c in covers]
    if len(covers) > 1:
        cover_subsets.append(covers[:2])

    for deferred in deferral_subsets:
        keep = [s for s in base_scene_ids if s not in deferred]
        for cov in cover_subsets:
            pool = keep + cov
            if len(pool) > 6:
                continue  # 720 permutations max
            for perm in permutations(pool):
                consider(list(perm), deferred)

    options = list(seen.values())
    return rank_options(options)


def rank_options(options: list[RecoveryOption]) -> list[RecoveryOption]:
    feasible = [o for o in options if o.feasible]
    infeasible = [o for o in options if not o.feasible]
    feasible.sort(key=lambda o: (-(o.score.total if o.score else 0), len(o.deferred_scene_ids), o.title))

    # keep strategically distinct feasible options (different deferral sets / cover use)
    chosen: list[RecoveryOption] = []
    seen_keys: set[tuple] = set()
    for o in feasible:
        key = (tuple(sorted(o.deferred_scene_ids)), tuple(sorted(s.scene_id for s in o.schedule)))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        chosen.append(o)
        if len(chosen) >= MAX_FEASIBLE:
            break

    # infeasible: the "hold" option first (most instructive), then best near-miss by soft components
    def soft_total(o: RecoveryOption) -> float:
        s = o.score
        if not s:
            return 0
        return s.schedule_preservation + s.cost_impact + s.overtime_risk

    hold = [o for o in infeasible if o.strategy.startswith("Shoot through")]
    others = sorted([o for o in infeasible if o not in hold], key=lambda o: -soft_total(o))
    # prefer near-misses with a single, understandable hard reason
    others.sort(key=lambda o: (len({v.kind for v in o.violations if v.hard}), -soft_total(o)))
    rejected = (hold + others)[:MAX_INFEASIBLE]
    # Gemini-proposed options are always shown (feasible or rejected) so the validation verdict is visible
    for o in options:
        if "gemini" in o.origin and o not in chosen and o not in rejected:
            (chosen if o.feasible else rejected).append(o)

    ranked = chosen + rejected
    for idx, o in enumerate(ranked):
        o.label = LABELS[idx] if idx < len(LABELS) else str(idx + 1)
        o.rank = idx + 1
    return ranked


def add_proposed_option(
    project: Project,
    day: ShootDay,
    disruption: Disruption,
    existing: list[RecoveryOption],
    order_numbers: list[str],
    deferred_numbers: list[str],
    title: str,
    strategy: str,
    verification_confidence: float | None = None,
) -> RecoveryOption | None:
    """Validate a Gemini-proposed ordering through the same deterministic pipeline."""
    baseline = sorted(day.items, key=lambda i: to_minutes(i.start))
    try:
        order = [project.scene_by_number(n).id for n in order_numbers]
        deferred = [project.scene_by_number(n).id for n in deferred_numbers]
    except KeyError:
        return None
    if not order:
        return None
    opt = build_option(project, day, disruption, baseline, order, deferred, origin="gemini", title=title, strategy=strategy, verification_confidence=verification_confidence)
    sig = tuple((i.scene_id, i.start) for i in opt.schedule)
    for e in existing:
        if tuple((i.scene_id, i.start) for i in e.schedule) == sig and set(e.deferred_scene_ids) == set(deferred):
            e.origin = f"{e.origin}+gemini" if "gemini" not in e.origin else e.origin
            return None
    return opt


def revalidate_options(project: Project, day: ShootDay, disruption: Disruption | None, state: RescueState) -> list[dict]:
    """Re-run the deterministic validator over an option list that was already produced.

    Accepting a cited statute is a decision about what binds, and until now it changed nothing that
    was already on screen: the options had been validated against the facts as they stood when the
    rescue ran, so the producer had to re-report the disruption to see their own decision take
    effect. This re-runs exactly the validation `build_option` ran, against the facts as they are
    now, and writes the verdict back onto the same option objects.

    What moves: `violations`, `feasible`, `score`, `rejected_reason`, `checks` — all deterministic,
    all recomputed from state. What does not move, deliberately:

    * `label`, `rank`, `title`, `id` — `rank_options` would re-sort and re-letter the list, and an
      option that changes places while the producer is looking at it reads as a different option,
      not as the same one turning red. Ranking is a property of the run; the verdict is not.
    * `explanation` and `trade_offs` — Gemini wrote those, and re-running Gemini here would be a
      paid model call inside a click that must stay instant, keyed on a recording that would then
      have to exist. A rejected option's prose can therefore describe the previous reason while the
      violations beside it show the current one; the violations are the load-bearing half.

    Returns one entry per option whose feasibility actually flipped, so the caller can say what
    changed rather than announcing a re-validation that moved nothing.
    """
    baseline = state.baseline or sorted(day.items, key=lambda i: to_minutes(i.start))
    ndc = next_day_call(project, day)
    # The confidence the disruption was verified with, not the option's own 0-100 score component.
    confidence = disruption.verification_confidence if disruption else None
    binding = [f for f in project.location_facts if f.binds]

    flips: list[dict] = []
    for o in state.options:
        ctx = ValidationContext(
            project=project,
            day=day,
            disruption=disruption,
            baseline_items=baseline,
            deferred_scene_ids=o.deferred_scene_ids,
            next_day_call=ndc,
            location_facts=binding,
        )
        violations = validate_schedule(ctx, o.schedule)
        score = score_option(project, day, baseline, o.schedule, o.deferred_scene_ids, violations, confidence)
        hard = [v for v in violations if v.hard]
        was_feasible = o.feasible

        o.violations = violations
        o.feasible = score.feasible
        o.score = score
        o.rejected_reason = None if score.feasible else "; ".join(dict.fromkeys(v.message for v in hard))
        o.checks = build_checks(project, violations, o.schedule, o.deferred_scene_ids)

        if was_feasible != o.feasible:
            flips.append({"option_id": o.id, "label": o.label, "was_feasible": was_feasible, "now_feasible": o.feasible})
    return flips
