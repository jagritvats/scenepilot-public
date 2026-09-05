"""Recovery generation, ranking and — importantly — infeasible-option rejection."""

from scenepilot.domain.enums import ConstraintKind
from scenepilot.seed.nightfall import DAY4_ID, build_project, make_fixture_disruption
from scenepilot.services.impact import analyze_impact
from scenepilot.services.recovery import add_proposed_option, generate_candidates, rank_options
from scenepilot.services.scoring import WEIGHTS


def _setup(fixture="rain_pm"):
    p = build_project()
    day = p.shoot_day(DAY4_ID)
    d = make_fixture_disruption(p.id, day.id, fixture)
    impact = analyze_impact(p, day, d)
    return p, day, d, impact


def test_impact_identifies_affected_scenes_requirements_and_resources():
    p, day, d, impact = _setup()
    assert set(impact.directly_affected_item_ids) == {"it_48", "it_42"}
    assert {v.scene_id for v in impact.violated_requirements} == {"sc_48", "sc_42"}
    assert "eq_drone" in impact.implicated_resource_ids and "loc_street" in impact.implicated_resource_ids
    assert impact.cover_scene_ids == ["sc_27"]
    assert any(m.scene_id == "sc_19" for m in impact.movable)


def test_recommended_option_is_feasible_and_ranked_first():
    p, day, d, impact = _setup()
    options = generate_candidates(p, day, d, impact, verification_confidence=0.8)
    assert options, "no candidates generated"
    best = options[0]
    assert best.feasible and best.rank == 1 and best.label == "A"
    assert not [v for v in best.violations if v.hard]
    assert best.score and best.score.total > 0
    # the hero recovery: Sc 42 survives the day, past the rain window, inside golden hour
    by = {i.scene_id: i for i in best.schedule}
    assert "sc_42" in by and by["sc_42"].start == "17:30"
    assert "sc_48" in best.deferred_scene_ids
    # ranking is monotone in total score among feasible options
    feas = [o for o in options if o.feasible]
    totals = [o.score.total for o in feas]
    assert totals == sorted(totals, reverse=True)


def test_infeasible_options_are_rejected_with_real_constraints():
    p, day, d, impact = _setup()
    options = generate_candidates(p, day, d, impact, verification_confidence=0.8)
    rejected = [o for o in options if not o.feasible]
    assert rejected, "expected at least one rejected option to be surfaced"
    for o in rejected:
        assert o.score.total == 0
        assert o.rejected_reason
        assert any(v.hard for v in o.violations)
    hold = next((o for o in rejected if o.strategy.startswith("Shoot through")), None)
    assert hold is not None
    assert {v.kind for v in hold.violations if v.hard} == {ConstraintKind.DISRUPTION_EXPOSURE}


def test_gemini_proposal_that_breaks_permit_window_is_rejected():
    p, day, d, impact = _setup()
    options = generate_candidates(p, day, d, impact, verification_confidence=0.8)
    proposed = add_proposed_option(p, day, d, options, order_numbers=["48", "31", "19", "27", "42"], deferred_numbers=[], title="Shoot the market first", strategy="front-load the street", verification_confidence=0.8)
    assert proposed is not None and proposed.origin == "gemini"
    assert not proposed.feasible
    kinds = {v.kind for v in proposed.violations if v.hard}
    assert ConstraintKind.LOCATION_UNAVAILABLE in kinds  # street permit is 13:00–18:00 only


def test_gemini_proposal_duplicate_of_existing_is_deduped():
    p, day, d, impact = _setup()
    options = generate_candidates(p, day, d, impact, verification_confidence=0.8)
    best = options[0]
    order = [p.scene(i.scene_id).number for i in best.schedule]
    deferred = [p.scene(s).number for s in best.deferred_scene_ids]
    assert add_proposed_option(p, day, d, options, order, deferred, "dup", "dup", 0.8) is None
    assert "gemini" in best.origin


def test_gemini_proposal_with_unknown_scene_is_ignored():
    p, day, d, impact = _setup()
    assert add_proposed_option(p, day, d, [], ["999"], [], "x", "x") is None


def test_rank_options_labels_and_limits():
    p, day, d, impact = _setup()
    options = generate_candidates(p, day, d, impact, verification_confidence=0.8)
    labels = [o.label for o in options]
    assert labels == list("ABCDEFGH")[: len(labels)]
    assert len([o for o in options if o.feasible]) <= 3
    assert len([o for o in options if not o.feasible]) <= 2
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_cast_disruption_generates_feasible_recovery():
    p, day, d, impact = _setup("vikram_late")
    assert set(impact.directly_affected_item_ids) == {"it_19", "it_48"}
    options = generate_candidates(p, day, d, impact, verification_confidence=0.9)
    assert options and options[0].feasible
    by = {i.scene_id: i for i in options[0].schedule}
    for sid in ("sc_19", "sc_48"):
        if sid in by:
            assert by[sid].start >= "15:00"


# --------------------------------------------------------------------------- #
# Re-validation after a producer decides a fact.
#
# The property that matters on camera: accepting a cited statute re-verdicts the options that are
# already on screen, *in place*. An option that shuffles or is re-lettered while the producer watches
# reads as a different option, not as this one turning red.
# --------------------------------------------------------------------------- #


def _accepted_curfew(p, resource_id="loc_rooftop", window=("22:00", "06:00")):
    """A HARD, cited, accepted time-window fact — the shape `decide_fact` produces on accept."""
    from scenepilot.domain.enums import FactBinding
    from scenepilot.domain.models import BasisCitation, ExternalRule, LocationFact

    fact = LocationFact(
        project_id=p.id,
        resource_id=resource_id,
        task_run_id="task_test",
        key="noise_curfew",
        label="Noise curfew",
        value=f"{window[0]}-{window[1]}",
        binding=FactBinding.HARD,
        confidence="high",
        citations=[BasisCitation(url="https://indiacode.nic.in/noise", title="Noise Rules 2000", excerpts=["no work between 22:00 and 06:00"])],
        rule=ExternalRule(kind="TIME_WINDOW_BAN", window_start=window[0], window_end=window[1]),
        accepted=True,
    )
    p.location_facts.append(fact)
    return fact


def _night_rescue_state():
    """A frozen option list for the night unit — the day where a 22:00 curfew can actually bind."""
    from scenepilot.domain.models import RescueState
    from scenepilot.seed.nightfall import DAY6_ID
    from scenepilot.services.impact import analyze_impact
    from scenepilot.services.recovery import generate_candidates

    p = build_project()
    day = p.shoot_day(DAY6_ID)
    d = make_fixture_disruption(p.id, day.id, "rain_pm")
    options = generate_candidates(p, day, d, analyze_impact(p, day, d), verification_confidence=0.8)
    state = RescueState(shoot_day_id=day.id, disruption_id=d.id, baseline=[i.model_copy() for i in day.items], options=options)
    return p, day, d, state


def test_accepting_a_curfew_flips_verdicts_without_moving_a_single_label():
    from scenepilot.services.recovery import revalidate_options

    p, day, d, state = _night_rescue_state()
    identity_before = [(o.id, o.label, o.rank, o.title, o.explanation, tuple(o.trade_offs)) for o in state.options]
    # Only the options that actually run the rooftop past 22:00 — the ones that move it earlier are
    # clear of the curfew and are rejected, if at all, for their own unrelated reasons.
    running_late = [o for o in state.options if any(i.scene_id == "sc_58" and i.end > "22:00" for i in o.schedule)]
    assert running_late, "the night unit's rooftop scene runs into the curfew in at least one option"
    assert all(o.feasible for o in running_late), "they are feasible until the fact is accepted"

    fact = _accepted_curfew(p)
    flips = revalidate_options(p, day, d, state)

    # the verdict moved…
    for o in running_late:
        ext = [v for v in o.violations if v.kind == ConstraintKind.EXTERNAL_RULE]
        assert ext and not o.feasible
        assert ext[0].fact_id == fact.id and ext[0].evidence_url == fact.citations[0].url
        assert "curfew" in o.rejected_reason.lower()
        assert next(c for c in o.checks if c["label"] == "external rules (permits, curfews)")["ok"] is False
    # …and nothing that identifies an option to the eye moved with it
    assert [(o.id, o.label, o.rank, o.title, o.explanation, tuple(o.trade_offs)) for o in state.options] == identity_before
    assert {f["option_id"] for f in flips} == {o.id for o in running_late}
    assert all(f["was_feasible"] and not f["now_feasible"] for f in flips)


def test_withdrawing_the_acceptance_restores_the_options_it_rejected():
    from scenepilot.services.recovery import revalidate_options

    p, day, d, state = _night_rescue_state()
    fact = _accepted_curfew(p)
    revalidate_options(p, day, d, state)
    rejected = [o for o in state.options if not o.feasible]
    assert rejected

    fact.accepted, fact.rejected = False, True  # the producer withdraws it
    flips = revalidate_options(p, day, d, state)

    restored = {f["option_id"] for f in flips if f["now_feasible"]}
    assert restored, "an option rejected only by the withdrawn rule is feasible again"
    for o in state.options:
        assert not [v for v in o.violations if v.kind == ConstraintKind.EXTERNAL_RULE]
        if o.id in restored:
            assert o.feasible and o.rejected_reason is None


def test_revalidation_reports_no_change_when_the_fact_cannot_touch_the_day():
    """Day 4 hard-wraps at 22:00, so the curfew Parallel found can never bind there."""
    from scenepilot.domain.models import RescueState
    from scenepilot.services.impact import analyze_impact
    from scenepilot.services.recovery import generate_candidates, revalidate_options

    p, day, d, impact = _setup()
    options = generate_candidates(p, day, d, impact, verification_confidence=0.8)
    state = RescueState(shoot_day_id=day.id, disruption_id=d.id, baseline=[i.model_copy() for i in day.items], options=options)
    _accepted_curfew(p)
    assert revalidate_options(p, day, d, state) == []


def test_the_feed_names_rejected_options_by_the_letter_the_list_shows():
    """The activity feed and the option list must agree about which option was rejected.

    Rejections used to be logged in `_step_candidates`, before `_step_proposals` could add Gemini
    options and `rank_options` re-letter the whole list. So the feed named an option by the letter it
    held *before* the sort: "Option D rejected — …" beside a list showing D as feasible. Both are on
    one screen, and the demo video reads the letters aloud.
    """
    import asyncio

    from scenepilot.domain.models import RescueState, RunKind, WorkflowRun
    from scenepilot.seed.nightfall import DAY4_ID, build_project, make_fixture_disruption
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo
    from scenepilot.workflows.context import RunContext
    from scenepilot.workflows.rescue import run_rescue

    repo = Repo(make_engine("sqlite:///:memory:"))
    p = build_project()
    d = make_fixture_disruption(p.id, DAY4_ID, "rain_pm")
    p.disruptions.append(d)
    repo.save_project(p)
    run = WorkflowRun(project_id=p.id, kind=RunKind.RESCUE, mode="replay",
                      rescue=RescueState(shoot_day_id=DAY4_ID, disruption_id=d.id))
    repo.save_run(run)
    asyncio.run(run_rescue(RunContext(repo, run, p)))

    saved = repo.get_run(run.id)
    options = {o.label: o.feasible for o in saved.rescue.options}
    logged = [
        e.message.split()[1]
        for e in repo.list_activity(run_id=run.id)
        if e.message.startswith("Option ") and "rejected:" in e.message
    ]
    assert logged, "the feed must say which options were rejected and why"

    # The label check above only bites when Gemini actually adds an option — in replay the recorded
    # run adds none, so `rank_options` never re-letters and the old code looked fine. What is
    # deterministic is *when* the lines are written: they must come after the proposals step, since
    # that is what can add options and re-letter the list. On the old code they were emitted in
    # `_step_candidates`, before it.
    messages = [e.message for e in repo.list_activity(run_id=run.id)]
    # Either ending of the proposals step — it reports what it added, or reports being skipped.
    proposals_at = next(
        (i for i, m in enumerate(messages)
         if m.startswith("Rescue Planner proposed") or m.startswith("Gemini proposals skipped")),
        None,
    )
    assert proposals_at is not None, "the proposals step should report itself either way"
    first_rejection = next(i for i, m in enumerate(messages) if m.startswith("Option ") and "rejected:" in m)
    assert first_rejection > proposals_at, (
        "rejected options are named before the proposals step can re-letter the list, so the letter "
        "in the feed is the one the option held before ranking"
    )
    for label in logged:
        assert label in options, f"the feed names Option {label}, which is not on the list"
        assert options[label] is False, (
            f"the feed says Option {label} was rejected; the list shows it as feasible"
        )
