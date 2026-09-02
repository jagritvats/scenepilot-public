"""Rescue Orchestrator — Workflow 2 (hero).

production state + disruption → Parallel verification → impact analysis → candidate
generation → deterministic validation → ranking → Gemini proposals/explanations →
human approval → ChangeSet → coordination actions.

Executed as an ADK `Workflow` graph (see `graph.py`), and the graph is worth reading: it **ends at a
producer**. `awaiting_approval` is terminal — nothing downstream of it exists, because applying a
change is not something the pipeline is allowed to do on its own. Approval arrives later, through
`approve()`, from a person. Its one branch is `impact`, which routes to a second terminal when the
disruption turns out to touch nothing on the day: an empty impact is an answer, not a reason to
enumerate schedules.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from dataclasses import dataclass, field

from ..agents.schemas import DisruptionVerificationOutput, RescueExplanationOutput, RescueProposalOutput
from ..domain.enums import ClaimKind, DisruptionType, RunStatus, ScheduleItemStatus, ShootDayStatus, VerificationStatus
from ..domain.models import Disruption, Evidence, RecoveryOption, RescueState, SearchRun, ShootDay
from ..services.changeset import apply_changeset, build_changeset
from ..services.coordination import derive_actions
from ..services.evidence import authority_for, combined_confidence, freshness_for
from ..services.impact import analyze_impact, applicability
from ..services.recovery import add_proposed_option, baseline_blocked_scenes, generate_candidates, rank_options
from ..services.revert import release_day
from ..services.schedule import availability_windows
from ..services.scoring import explain_score
from ..services.timeutil import to_hhmm, to_minutes
from ..tools.parallel_search import format_results_for_prompt
from .context import RunContext
from .graph import Failure, node, run_workflow

log = logging.getLogger(__name__)

EXTERNALLY_VERIFIABLE = {DisruptionType.WEATHER, DisruptionType.TRANSPORT, DisruptionType.REGULATORY}


async def run_rescue(ctx: RunContext) -> None:
    failure = Failure()
    try:
        await run_workflow(build_rescue_workflow(ctx, failure), ctx, failure)
    except Exception as exc:  # noqa: BLE001
        log.error("rescue run failed: %s\n%s", exc, traceback.format_exc())
        ctx.run.status = RunStatus.FAILED
        ctx.run.error = f"{type(exc).__name__}: {exc}"[:800]
        # A failed run must not keep the day. It used to leave it AT_RISK carrying an
        # `active_disruption_id`, which is the state the day page renders as "under a disruption":
        # the fixture picker and the manual entry form both disappear, there are no options, and the
        # only thing on screen is a red card with no retry. The day stayed there until POST /reset.
        try:
            _release_day(ctx)
        except Exception:  # noqa: BLE001 — releasing the day must never mask the original failure
            log.exception("could not release the day after a failed rescue run")
        ctx.stage("failed", f"Rescue run failed: {ctx.run.error}")


def _release_day(ctx: RunContext) -> None:
    """Give the day back when this run ends without an applied recovery.

    The restore itself lives in `services/revert.py` beside the other one, so a stand-down, a failed
    run and a nothing-to-recover all hand a day back the same way. It used to live here and restore
    only the day's status and its disruption pointer — never the per-item `AT_RISK` that
    `_step_impact` wrote — so a failed run left orange strips on a day reported as healthy.
    """
    if ctx.run.rescue is None:
        return
    release_day(ctx.project, ctx.run, restore_items=True)
    ctx.save_project()


# --------------------------------------------------------------------------- #
# The graph
# --------------------------------------------------------------------------- #


@dataclass
class _Recovery:
    """Per-run scratch: the two values that travel between nodes but are not domain state yet.

    `options` reaches `RescueState` only once it is explained and ranked — a half-scored option list
    is not something a producer should ever be shown.
    """

    confidence: float | None = None
    options: list[RecoveryOption] = field(default_factory=list)


def build_rescue_workflow(ctx: RunContext | None, failure: Failure):
    """disruption → verify → impact → candidates → proposals → explain → a producer.

    Nearly a straight line by design: every branch a *recovery* could take is a schedule alternative,
    and those are enumerated and scored inside `candidates` by deterministic code — not chosen by a
    router. The one real branch sits upstream of all of it. `impact` decides whether there is
    anything to recover at all, because the alternative is what this pipeline used to do: report that
    a crane fault touched nothing on the day, and then recommend moving two scenes for it anyway.

    Both ends are terminal and neither is an action: one waits for a producer, the other tells them
    there is nothing to wait for.
    """
    from google.adk.workflow import START, Workflow

    rec = _Recovery()
    step = lambda fn, name, description: node(fn, name=name, run_ctx=ctx, failure=failure, description=description)  # noqa: E731

    impact = step(_step_impact, "impact", "Deterministic propagation: what the disruption actually touches.")
    candidates = step(lambda c: _step_candidates(c, rec), "candidates", "Enumerate orderings and let hard constraints reject them.")
    nothing = step(_step_nothing_to_recover, "nothing_to_recover", "Terminal: the report is on the record and the schedule stands.")

    return Workflow(
        name="scenepilot_rescue",
        description="Recover a shoot day after the real world moves.",
        edges=[
            (
                START,
                step(_step_disruption, "disruption", "Snapshot the baseline schedule before anything moves."),
                step(lambda c: _step_verify(c, rec), "verify", "Parallel Search checks whether the report is even true."),
                impact,
            ),
            (impact, {"candidates": candidates, "nothing_to_recover": nothing}),
            (
                candidates,
                step(lambda c: _step_proposals(c, rec), "proposals", "Gemini proposes alternatives; the same validator judges them."),
                step(lambda c: _step_explain(c, rec), "explain", "Gemini turns constraint arithmetic into a 1st AD's rationale."),
                step(lambda c: _step_awaiting_approval(c, rec), "awaiting_approval", "Terminal: the recommendation waits for a producer."),
            ),
        ],
    )


# --------------------------------------------------------------------------- #
# The steps
# --------------------------------------------------------------------------- #


def _day_and_disruption(ctx: RunContext):
    state: RescueState = ctx.run.rescue  # type: ignore[assignment]
    return state, ctx.project.shoot_day(state.shoot_day_id), ctx.project.disruption(state.disruption_id)


async def _step_disruption(ctx: RunContext) -> None:
    """Snapshot what the day was, and nothing else.

    Marking the day AT_RISK used to happen here, two nodes before anything knew whether it was. A
    disruption that turned out to touch nothing still left the day at risk under it, and a run that
    failed on a malformed window left it there for good. The status moves in `_step_impact` now,
    which is the first moment it is true.
    """
    state, day, disruption = _day_and_disruption(ctx)
    ctx.run.status = RunStatus.RUNNING
    state.baseline = [i.model_copy() for i in day.items]
    state.prior_day_status = day.status
    ctx.save_project()
    ctx.stage("disruption", f"{disruption.type.value.replace('_', ' ').title()} disruption received: {disruption.title}")


async def _step_verify(ctx: RunContext, rec: _Recovery) -> None:
    state, day, disruption = _day_and_disruption(ctx)
    ctx.stage("verify")
    rec.confidence = await _verify_disruption(ctx, day, disruption, state)
    ctx.save_project()


async def _step_impact(ctx: RunContext) -> str:
    """The one branch in the graph: is there anything here to recover?

    Returns the route. "Nothing to recover" is a real outcome and the pipeline had no way to reach
    it: on Day 4 a crane fault that touched nothing produced a repack that *outscored* the untouched
    baseline 94 to 93 — `pack_day` restarts the cursor at unit call — and a producer was shown
    "move Sc 48 13:30->13:10; move Sc 42 16:30->16:37" as the recommendation. "Hold the existing
    schedule" was not even in the list to compare it against: it packs to the same strategic key as
    the repack, so the distinctness dedup dropped it.
    """
    state, day, disruption = _day_and_disruption(ctx)
    ctx.stage("impact")
    impact = analyze_impact(ctx.project, day, disruption)
    state.impact = impact
    affected = ", ".join(f"Sc {ctx.project.scene(_scene_of(day, i)).number}" for i in impact.directly_affected_item_ids)
    ctx.log("deterministic", f"{len(impact.directly_affected_item_ids)} scheduled scene(s) affected: {affected or 'none'}", {"item_ids": impact.directly_affected_item_ids})
    # The same second source `generate_candidates` consults: a scene the schedule already cannot
    # legally shoot is something to recover even when the disruption itself missed it.
    blocked = baseline_blocked_scenes(ctx.project, day, disruption, sorted(day.items, key=lambda i: to_minutes(i.start)))

    if not impact.directly_affected_item_ids and not blocked:
        _, why = applicability(ctx.project, day, disruption)
        state.no_impact_reason = (
            (f"{why} " if why else "")
            + f"No scene scheduled on Day {day.day_number} is exposed to it inside its window, and the day "
            "breaks no hard constraint without it — there is nothing to recover. The report stays on the record."
        )
        ctx.log("deterministic", f"The disruption does not touch anything scheduled on Day {day.day_number}: no recovery is needed", {"reason": state.no_impact_reason})
        return "nothing_to_recover"

    day.status = ShootDayStatus.AT_RISK
    day.active_disruption_id = disruption.id
    for iid in impact.directly_affected_item_ids:
        for it in day.items:
            if it.id == iid:
                it.status = ScheduleItemStatus.AT_RISK
    ctx.save_project()
    ctx.log("deterministic", f"{len(impact.violated_requirements)} requirement(s) violated · {len(impact.implicated_resource_ids)} resource(s) implicated · {len(impact.movable)} movable · {len(impact.immovable)} window-bound", {})
    return "candidates"


async def _step_nothing_to_recover(ctx: RunContext) -> None:
    """The other end of the graph. Reporting that something happened is not claiming it changed anything.

    The disruption stays on the production and on the day page — it was real, and a producer asking
    "did anyone log the crane fault" is entitled to find it. What does not happen is a recovery, and
    `RescueState.no_impact_reason` is what the day page prints instead of an empty option list.
    """
    state, day, disruption = _day_and_disruption(ctx)
    _release_day(ctx)
    ctx.run.status = RunStatus.COMPLETED
    ctx.stage("nothing_to_recover", f"Nothing to recover: {disruption.title} does not change Day {day.day_number}'s schedule")
    ctx.log("deterministic", state.no_impact_reason or "", {"disruption_id": disruption.id})


async def _step_candidates(ctx: RunContext, rec: _Recovery) -> None:
    state, day, disruption = _day_and_disruption(ctx)
    impact = state.impact
    ctx.stage("candidates", f"Evaluating schedule alternatives for {len(day.items)} scheduled + {len(impact.cover_scene_ids)} cover scene(s)")
    options = generate_candidates(ctx.project, day, disruption, impact, verification_confidence=rec.confidence)
    feasible = [o for o in options if o.feasible]
    rejected = [o for o in options if not o.feasible]
    ctx.log("deterministic", f"{len(feasible)} feasible recovery schedule(s) shortlisted, {len(rejected)} rejected by hard constraints", {"labels": [o.label for o in options]})
    for o in rejected:
        ctx.log("deterministic", f"Option {o.label} rejected: {o.rejected_reason}", {"option_id": o.id})
    rec.options = options


async def _step_proposals(ctx: RunContext, rec: _Recovery) -> None:
    """Gemini proposals → the same deterministic validation. Optional: the enumerated options stand."""
    state, day, disruption = _day_and_disruption(ctx)
    ctx.stage("proposals")
    try:
        proposals = await ctx.gemini.run_structured("rescue_planner", _proposal_prompt(ctx.project, day, disruption, state.impact, rec.options), RescueProposalOutput)
        added = 0
        for p in proposals.proposals[:2]:
            opt = add_proposed_option(ctx.project, day, disruption, rec.options, p.order_scene_numbers, p.deferred_scene_numbers, p.title, p.strategy, rec.confidence)
            if opt is None:
                continue
            added += 1
            rec.options.append(opt)
            verdict = "feasible" if opt.feasible else f"rejected — {opt.rejected_reason}"
            ctx.log("deterministic", f"Gemini proposal '{p.title}' validated: {verdict}", {"option_id": opt.id})
        if added:
            rec.options = rank_options(rec.options)
        ctx.log("gemini", f"Rescue Planner proposed {len(proposals.proposals)} alternative(s); {added} new after de-duplication", {})
    except Exception as exc:  # noqa: BLE001 — proposals are optional; deterministic options stand
        ctx.log("warning", f"Gemini proposals skipped: {type(exc).__name__}: {exc}", {})


async def _step_explain(ctx: RunContext, rec: _Recovery) -> None:
    state, day, disruption = _day_and_disruption(ctx)
    ctx.stage("explain")
    recommended = next((o for o in rec.options if o.feasible), None)
    try:
        expl = await ctx.gemini.run_structured("rescue_explainer", _explain_prompt(ctx.project, day, disruption, rec.options), RescueExplanationOutput)
        by_label = {e.label.strip().upper(): e for e in expl.options}
        for o in rec.options:
            e = by_label.get(o.label)
            if e:
                o.explanation = e.explanation
                o.trade_offs = e.trade_offs
        state.recommendation_rationale = expl.recommendation_rationale
        if expl.headline and recommended:
            recommended.title = expl.headline.strip().rstrip('.')[:90]
    except Exception as exc:  # noqa: BLE001
        ctx.log("warning", f"Gemini explanation skipped: {type(exc).__name__}: {exc}", {})
        state.recommendation_rationale = _fallback_rationale(recommended, rec.options)
    for o in rec.options:
        if not o.explanation:
            o.explanation = o.rejected_reason or o.strategy


async def _step_awaiting_approval(ctx: RunContext, rec: _Recovery) -> None:
    """The end of the graph. What happens next is a person's decision, not a node."""
    state, day, _disruption = _day_and_disruption(ctx)
    recommended = next((o for o in rec.options if o.feasible), None)
    state.options = rec.options
    state.recommended_option_id = recommended.id if recommended else None
    ctx.run.status = RunStatus.AWAITING_APPROVAL
    day.status = ShootDayStatus.RECOVERY_PROPOSED
    ctx.save_project()
    ctx.stage("awaiting_approval", f"Recovery recommendation ready: option {recommended.label if recommended else '—'} — awaiting producer approval")


def approve(ctx: RunContext, option_id: str, approved_by: str = "producer") -> None:
    """Human approval → ChangeSet → apply → coordination actions. Synchronous & deterministic."""
    run, project = ctx.run, ctx.project
    state: RescueState = run.rescue  # type: ignore[assignment]
    if run.status != RunStatus.AWAITING_APPROVAL:
        raise ValueError(f"Run is {run.status.value}, not awaiting approval")
    option = next((o for o in state.options if o.id == option_id), None)
    if option is None:
        raise ValueError("Unknown recovery option")
    if not option.feasible:
        raise ValueError(f"Option {option.label} is infeasible: {option.rejected_reason}")
    day = project.shoot_day(state.shoot_day_id)
    disruption = project.disruption(state.disruption_id)
    ctx.log("approval", f"Producer approved recovery option {option.label}", {"option_id": option.id, "approved_by": approved_by})
    cs = build_changeset(project, day, option, disruption, run.id)
    ctx.log("deterministic", f"ChangeSet generated: {cs.summary}", {"changeset_id": cs.id, "changes": len(cs.changes)})
    day_after = apply_changeset(project, cs, approved_by=approved_by)
    ctx.repo.save_changeset(cs)
    substitutes = [r for r in ctx.repo.list_findall_runs(project_id=project.id) if any(v.selected for v in r.candidates)]
    actions = derive_actions(project, day_after, cs, substitutes=substitutes)
    state.changeset = cs
    state.actions = actions
    run.status = RunStatus.APPLIED
    ctx.save_project()
    ctx.stage("applied", f"ChangeSet applied — {len(actions)} coordination action(s) generated")
    for a in actions:
        ctx.log("action", a.title, {"kind": a.kind.value, "target": a.target})


# --------------------------------------------------------------------------- #
# Verification via Parallel
# --------------------------------------------------------------------------- #


async def _verify_disruption(ctx: RunContext, day: ShootDay, d: Disruption, state: RescueState) -> float | None:
    project = ctx.project
    if d.type not in EXTERNALLY_VERIFIABLE:
        d.verification_status = None
        d.verification_summary = "Internal disruption — external verification not applicable."
        ctx.log("info", "Internal disruption: skipping external verification", {"type": d.type.value})
        return None
    ctx.log("info", "Checking external evidence via Parallel Search", {"type": d.type.value})
    searches = _verification_searches(project, day, d)
    runs: list[SearchRun] = list(await asyncio.gather(*(
        asyncio.to_thread(ctx.parallel.search, objective, queries, purpose="disruption_verification", round=1, mode=VERIFY_MODE, max_age_seconds=VERIFY_MAX_AGE_SECONDS, **extra)
        for objective, queries, extra in searches
    )))
    for sr in runs:
        d.search_run_ids.append(sr.id)
    total = sum(len(r.results) for r in runs)
    try:
        out = await ctx.gemini.run_structured("disruption_verifier", _verify_prompt(project, day, d, runs), DisruptionVerificationOutput)
    except Exception as exc:  # noqa: BLE001 — verification failure must not block recovery
        ctx.log("warning", f"Verification analysis unavailable ({type(exc).__name__}); continuing with unverified report", {})
        d.verification_status = VerificationStatus.UNCORROBORATED
        d.verification_summary = f"External verification could not be completed: {exc}"[:300]
        d.verification_confidence = 0.5
        return 0.5
    d.verification_status = VerificationStatus(out.status)
    d.verification_summary = out.summary
    d.verification_confidence = out.confidence
    lookup = {sr.id: sr for sr in runs}
    kept = 0
    for ev in out.evidence:
        ref = (ev.source_ref or "").strip().strip("[]")
        sr_id, _, n = ref.rpartition("#")
        sr = lookup.get(sr_id.strip())
        try:
            item = sr.results[int(n) - 1] if sr else None
        except (ValueError, IndexError):
            item = None
        if sr is None or item is None:
            continue
        authority, freshness = authority_for(item.url), freshness_for(item.publish_date)
        e = Evidence(question_id=None, search_run_id=sr.id, claim=ev.claim, source_url=item.url, source_title=item.title, excerpt=(item.excerpts[0] if item.excerpts else "")[:600], publish_date=item.publish_date, freshness=freshness, relevance=ev.relevance, authority=authority, confidence=combined_confidence(ev.confidence, authority, freshness, ev.relevance), kind=ClaimKind.FACT, production_implication=ev.production_implication or None)
        state.evidence.append(e)
        d.evidence_ids.append(e.id)
        kept += 1
    ctx.log("parallel", f"Disruption {out.status.replace('_', ' ').lower()} by {kept} source(s) out of {total} returned (confidence {out.confidence:.0%})", {"status": out.status, "confidence": out.confidence})
    for note in out.notes_for_planning[:3]:
        ctx.log("gemini", f"Planning note: {note}", {})
    return out.confidence


VERIFY_MAX_AGE_SECONDS = 3600  # accept pages fetched within the last hour (forced live fetch costs 30-60 s)
VERIFY_MODE = "fast"  # ~1 s; advanced mode adds 15-20 s per search for little gain on forecasts
VERIFY_RECENT_DAYS = 30  # open weather search only considers pages published in the last N days
OFFICIAL_WEATHER_DOMAINS = ["mausam.imd.gov.in", "imd.gov.in", "ndma.gov.in"]


def _verification_searches(project, day: ShootDay, d: Disruption) -> list[tuple[str, list[str], dict]]:
    """(objective, queries, explicit Parallel advanced-setting opt-ins).

    Parallel's guidance: use advanced settings only when strictly required. Verification is the one
    place geo-targeting and freshness genuinely matter, so `location`/`after_date`/`max_results` are
    sent here only. The IMD-restricted search uses `include_domains` because the India Meteorological
    Department is the single authoritative publisher of warnings ("restrict only for … a single known
    publisher"); everywhere else the source preference is stated in the objective instead.
    """
    city = project.base_city
    country = (project.country_code or "").lower() or None
    geo = {"location": country, "max_results": 6, "max_chars_per_result": 1200}
    if d.type == DisruptionType.WEATHER:
        from datetime import date, timedelta

        try:
            recent = (date.fromisoformat(day.date) - timedelta(days=VERIFY_RECENT_DAYS)).isoformat()
        except ValueError:
            recent = None
        return [
            (f"Current weather forecast and official rain or thunderstorm warnings for {city}, India on {day.date}, especially afternoon rain timing and wind; prefer IMD, Skymet and established news sources.", [f"{city} weather forecast today", f"IMD {city} rain warning", f"{city} weather tomorrow"], {**geo, **({"after_date": recent} if recent else {})}),
            (f"India Meteorological Department nowcast, warnings or district forecast for {city} for the next 24-48 hours.", [f"IMD {city} nowcast warning", f"{city} district forecast", f"{city} rainfall warning"], {**geo, "include_domains": OFFICIAL_WEATHER_DOMAINS}),
        ]
    if d.type == DisruptionType.TRANSPORT:
        return [(f"Current traffic disruptions, road closures or transport strikes in {city} around {day.date}; prefer traffic police and established news sources.", [f"{city} traffic disruption today", f"{city} road closure advisory", f"{city} traffic police diversion"], geo)]
    return [(f"Current regulatory or permit changes affecting film shooting in {city} around {day.date}; prefer municipal, police and film-facilitation sources.", [f"{city} film shooting permission update", f"{city} shooting restrictions notice", f"{city} film permit rules"], geo)]


def _verify_prompt(project, day: ShootDay, d: Disruption, runs: list[SearchRun]) -> str:
    return (
        f"DISRUPTION REPORT (received by the production office of a fictional production shooting in {project.base_city}, {project.country_code}):\n"
        f"type: {d.type.value}\ntitle: {d.title}\ndetails: {d.description}\nwindow: {d.window_start or '?'}–{d.window_end or '?'} local time on {day.date} (Shoot Day {day.day_number})\n\n"
        f"SEARCH RESULTS (Parallel Search API):\n{format_results_for_prompt(runs)}\n"
    )


# --------------------------------------------------------------------------- #
# Gemini prompt builders for rescue
# --------------------------------------------------------------------------- #


def _scene_of(day: ShootDay, item_id: str) -> str:
    return next(i.scene_id for i in day.items if i.id == item_id)


def _constraints_block(project, day: ShootDay, d: Disruption) -> str:
    lines = [f"SHOOT DAY {day.day_number} ({day.date}): unit call {day.unit_call}, standard day {day.standard_hours:g} h (overtime after {to_hhmm(int(day.standard_hours * 60) + _m(day.unit_call))} at ₹{day.overtime_rate_per_hour:,}/h), hard wrap {day.hard_wrap}, golden hour {day.golden_hour_dusk[0]}–{day.golden_hour_dusk[1]}"]
    lines.append(f"DISRUPTION: {d.title} — window {d.window_start}–{d.window_end} (+{d.dry_out_minutes} min dry-out), affects exteriors={d.affects_exteriors}, affected resources={d.affects_resource_ids or 'none'}")
    lines.append("BASELINE SCHEDULE:")
    for it in sorted(day.items, key=lambda i: i.start):
        s = project.scene(it.scene_id)
        lines.append(f"  {it.start}–{it.end} Sc {s.number} {s.heading} [{s.int_ext.value}/{s.time_of_day.value}, {s.estimated_minutes} min] cast={', '.join(project.resource(c).name.split(' (')[0] for c in s.cast_ids)} equipment={', '.join(project.resource(e).name for e in s.equipment_ids)}")
    scheduled = {i.scene_id for i in day.items}
    covers = [s for s in project.scenes if s.is_cover and s.id not in scheduled]
    if covers:
        lines.append("COVER SCENES (unscheduled, can be pulled forward): " + "; ".join(f"Sc {s.number} {s.heading} [{s.int_ext.value}, {s.estimated_minutes} min] cast={', '.join(project.resource(c).name.split(' (')[0] for c in s.cast_ids)}" for s in covers))
    lines.append("AVAILABILITY WINDOWS TODAY:")
    for r in project.resources:
        if r.type.value in ("CAST", "LOCATION", "EQUIPMENT") and r.availability:
            wins = ", ".join(f"{to_hhmm(a)}–{to_hhmm(b)}" for a, b in availability_windows(r, day))
            note = next((a.note for a in r.availability if a.note), None)
            lines.append(f"  {r.type.value} {r.name}: {wins}{' — ' + note if note else ''}{' (weather-sensitive)' if r.weather_sensitive else ''}")
    lines.append("TRAVEL (min): " + ", ".join(f"{project.resource(t.from_location_id).name.split(' —')[0]}↔{project.resource(t.to_location_id).name.split(' —')[0]} {t.minutes}" for t in project.travel_times))
    return "\n".join(lines)


def _m(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _option_block(project, o: RecoveryOption) -> str:
    sched = ", ".join(f"Sc {project.scene(i.scene_id).number} {i.start}–{i.end}" for i in o.schedule)
    deferred = ", ".join(f"Sc {project.scene(s).number}" for s in o.deferred_scene_ids) or "none"
    s = o.score
    comps = f"total {s.total} | preservation {s.schedule_preservation}, cost {s.cost_impact} (₹{s.estimated_extra_cost_inr:,} extra), overtime {s.overtime_risk} ({s.overtime_minutes} min), moves {s.company_moves} (+{s.extra_company_moves}), resources {s.resource_conflicts}, creative {s.creative_compromise}, confidence {s.confidence}" if s else ""
    viol = "; ".join(f"{'HARD' if v.hard else 'soft'}: {v.message}" for v in o.violations) or "none"
    return f"OPTION {o.label} ({'FEASIBLE' if o.feasible else 'REJECTED'}, origin {o.origin}): {o.strategy}\n  schedule: {sched}\n  carried over: {deferred}\n  score: {comps}\n  violations: {viol}"


def _proposal_prompt(project, day: ShootDay, d: Disruption, impact, options: list[RecoveryOption]) -> str:
    return (
        _constraints_block(project, day, d) + "\n\nIMPACT: " + impact.summary + "\n"
        + "\n".join(f"  - violated: {v.reason}" for v in impact.violated_requirements[:8])
        + "\n\nALREADY EVALUATED ORDERINGS (do not repeat):\n" + "\n".join(_option_block(project, o) for o in options)
        + "\n\nPropose up to 2 additional orderings using scene numbers only."
    )


def _explain_prompt(project, day: ShootDay, d: Disruption, options: list[RecoveryOption]) -> str:
    return (
        _constraints_block(project, day, d)
        + f"\n\nDISRUPTION VERIFICATION: {d.verification_status.value if d.verification_status else 'n/a'} — {d.verification_summary or ''}\n\nRANKED OPTIONS:\n"
        + "\n".join(_option_block(project, o) for o in options)
        + "\n\nSCORE WEIGHTS: " + ", ".join(explain_score(options[0].score)[:7]) if options and options[0].score else ""
    )


def _fallback_rationale(recommended: RecoveryOption | None, options: list[RecoveryOption]) -> str:
    if not recommended or not recommended.score:
        return "No feasible recovery option was found; the day cannot be rescued under the current constraints."
    s = recommended.score
    return (
        f"Option {recommended.label} is the highest-scoring feasible schedule (total {s.total}): it preserves {s.schedule_preservation}% of the day's work, "
        f"adds ≈₹{s.estimated_extra_cost_inr:,} ({s.overtime_minutes} min overtime, {s.extra_company_moves} extra company moves) and passes every hard constraint. "
        + "; ".join(f"Option {o.label} was rejected: {o.rejected_reason}" for o in options if not o.feasible)
    )
