"""Location dossiers: a cited web fact becomes a production constraint — but only on the producer's word.

The properties that matter, in order of how much damage getting them wrong would do:
  1. an unaccepted HARD fact changes nothing;
  2. only high confidence *with a citation* is ever proposed as HARD;
  3. only two fact shapes can bind at all — a time-window ban and an activity ban;
  4. when one does bind, the rejection carries the URL it came from.
"""

from dataclasses import replace

from scenepilot.config import settings as default_settings
from scenepilot.domain.enums import ConstraintKind, FactBinding
from scenepilot.domain.models import LocationFact, TaskRun
from scenepilot.seed.nightfall import DAY4_ID, build_project
from scenepilot.services.dossier import map_facts, merge_facts, parse_time_range, prohibits
from scenepilot.services.schedule import ValidationContext, validate_schedule
from scenepilot.tools.parallel_task import DOSSIER_SCHEMA, build_task_input, response_payload


def _live_settings(**over):
    base = {"mode": "live", "record": False, "parallel_api_key": "test-key", "parallel_task_enabled": True, "parallel_task_processor": "core-fast"}
    return replace(default_settings, **{**base, **over})


# --------------------------------------------------------------------------- #
# A fake Parallel client shaped like the SDK's TaskRunResult
# --------------------------------------------------------------------------- #


class _Citation:
    def __init__(self, url, title=None, excerpts=None):
        self.url, self.title, self.excerpts = url, title, excerpts or []


class _Basis:
    def __init__(self, field, confidence=None, citations=None, reasoning=""):
        self.field, self.confidence, self.citations, self.reasoning = field, confidence, citations or [], reasoning


class _Output:
    type = "json"

    def __init__(self, content, basis):
        self.content, self.basis = content, basis


class _Run:
    def __init__(self):
        self.run_id = "trun_dossier1"
        self.interaction_id = "int_1"
        self.processor = "core-fast"
        self.warnings = []


class FakeTaskRuns:
    def __init__(self, content, basis, fail: Exception | None = None):
        self._result = type("R", (), {"output": _Output(content, basis), "run": _Run()})()
        self._fail = fail
        self.created: list[dict] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        if self._fail:
            raise self._fail
        return _Run()

    def result(self, run_id, api_timeout=None):
        return self._result


class FakeClient:
    def __init__(self, task_run):
        self.task_run = task_run


CONTENT = {
    "permit_authority": "Brihanmumbai Municipal Corporation (BMC)",
    "noise_curfew": "22:00-06:00",
    "drone_rules": "Drone flights are prohibited over this area without DGCA clearance.",
    "fireworks_rules": "Pyrotechnics are prohibited on mill-estate rooftops.",
    "restrictions": ["No vehicle access after 21:00", "Rooftop load limit applies"],
    "nearest_hospital": "",
}
BASIS = [
    _Basis("permit_authority", "high", [_Citation("https://portal.mcgm.gov.in/permits", "BMC permits")]),
    _Basis("noise_curfew", "high", [_Citation("https://mumbaipolice.gov.in/noise", "Noise rules", ["10 pm to 6 am"])]),
    _Basis("drone_rules", "high", []),  # high confidence but no citation → must not be HARD
    _Basis("fireworks_rules", "high", [_Citation("https://mumbaifire.gov.in/pyro")]),
    _Basis("restrictions.0", "medium", [_Citation("https://example.org/estate")]),
    _Basis("restrictions.1", "low", []),
]


def _run_dossier(settings=None, content=CONTENT, basis=BASIS):
    from scenepilot.tools.parallel_task import ParallelTaskTool

    p = build_project()
    fake = FakeTaskRuns(content, basis)
    tool = ParallelTaskTool(p, settings=settings or _live_settings(), client=FakeClient(fake))
    tr = tool.dossier(p.resource("loc_rooftop"))
    return p, tr, fake


# --------------------------------------------------------------------------- #


def test_task_request_is_by_the_book():
    p, tr, fake = _run_dossier()
    sent = fake.created[0]
    assert sent["processor"] == "core-fast"
    assert sent["task_spec"] == {"output_schema": {"type": "json", "json_schema": DOSSIER_SCHEMA}}
    assert sent["metadata"] == {"project_id": p.id, "resource_id": "loc_rooftop", "kind": "location_dossier"}
    assert "field-basis-2025-11-25" in sent["betas"]  # per-element basis
    # the Task API takes neither of these — sending them would be an error
    assert "session_id" not in sent and "client_model" not in sent
    assert "memory_scope_key" not in sent  # Memory feature is off in these settings
    assert tr.status == "OK" and tr.interaction_id == "int_1" and tr.provider_run_id == "trun_dossier1"
    assert "Rooftop A" in build_task_input(p, p.resource("loc_rooftop")) and "Mumbai" in tr.input


def test_memory_scope_is_passed_only_when_the_memory_feature_is_on():
    from scenepilot.tools.parallel_task import ParallelTaskTool

    p = build_project()
    fake = FakeTaskRuns(CONTENT, BASIS)
    ParallelTaskTool(p, settings=_live_settings(), client=FakeClient(fake), memory_scope_key="scenepilot_proj_nightfall").dossier(p.resource("loc_rooftop"))
    assert fake.created[0]["memory_scope_key"] == "scenepilot_proj_nightfall"


def test_the_confidence_gate_grades_every_field():
    p, tr, _ = _run_dossier()
    facts = {f.key + (f.label[-1] if f.key == "restrictions" else ""): f for f in map_facts(tr, p)}

    # high + citation + mechanically checkable → HARD (proposed)
    assert facts["noise_curfew"].binding is FactBinding.HARD
    assert facts["fireworks_rules"].binding is FactBinding.HARD
    # high + citation but prose we cannot check → SOFT, never HARD
    assert facts["permit_authority"].binding is FactBinding.SOFT
    # high *without* a citation → SOFT even though it is a clean activity ban
    assert facts["drone_rules"].binding is FactBinding.SOFT
    # medium → SOFT; low → ADVISORY
    assert facts["restrictions1"].binding is FactBinding.SOFT
    assert facts["restrictions2"].binding is FactBinding.ADVISORY
    # empty values are dropped rather than invented
    assert "nearest_hospital" not in facts


def test_non_answers_are_dropped_but_real_restrictions_starting_with_no_survive():
    """A live run showed the model answers "No information found on X" instead of returning "".

    Those must be dropped: otherwise they are graded SOFT and carry the run's general citation, which
    reads as if a source supported the *absence* of a rule. The filter has to stay narrow, though —
    plenty of genuine restrictions start with the word "No".
    """
    from scenepilot.services.dossier import is_non_answer

    for absent in [
        "No information found on drone regulations for filming at this location",
        "No information found on monsoon flooding history for this area.",
        "Not specified", "unknown", "N/A", "none", "Unable to determine the permit authority",
        "No records available for this location", "Insufficient information to determine noise limits",
    ]:
        assert is_non_answer(absent) is True, absent

    for real in [
        "No vehicle access after 21:00", "No drones permitted", "No parking on the north side",
        "Drone flights are prohibited over this area.", "22:00-06:00", "Police NOC required",
        "Nearest hospital: KEM, 3 km",
    ]:
        assert is_non_answer(real) is False, real

    p = build_project()
    tr = TaskRun(project_id=p.id, resource_id="loc_rooftop", input="x", status="OK", output={
        "drone_rules": "No information found on drone regulations for filming at this location",
        "restrictions": ["No vehicle access after 21:00", "Not available"],
    })
    keys = [(f.key, f.value) for f in map_facts(tr, p)]
    assert keys == [("restrictions", "No vehicle access after 21:00")]


def test_only_two_shapes_are_machine_checkable():
    p, tr, _ = _run_dossier()
    rules = {f.key: f.rule for f in map_facts(tr, p) if f.rule}
    assert rules["noise_curfew"].kind == "TIME_WINDOW_BAN" and (rules["noise_curfew"].window_start, rules["noise_curfew"].window_end) == ("22:00", "06:00")
    assert rules["fireworks_rules"].kind == "ACTIVITY_BAN" and rules["fireworks_rules"].activity == "fireworks"
    assert "permit_authority" not in rules and "restrictions" not in rules

    assert parse_time_range("quiet hours 10 pm to 6 am") == ("22:00", "06:00")
    assert parse_time_range("no fixed hours") is None
    assert prohibits("Drones are prohibited") is True
    assert prohibits("Drones are prohibited without prior permission") is False  # a permission regime, not a ban
    assert prohibits("Drones are permitted") is False


def test_an_unaccepted_hard_fact_changes_nothing_and_an_accepted_one_rejects_with_its_citation():
    p, tr, _ = _run_dossier()
    merge_facts(p, "loc_rooftop", map_facts(tr, p))
    day = p.shoot_day(DAY4_ID)
    items = [i for i in day.items if i.location_id == "loc_rooftop"]
    assert items, "the hero day schedules the rooftop"
    # push the rooftop scene into the discovered curfew (22:00–06:00)
    item = items[0]
    item.start, item.end = "22:30", "23:30"

    curfew = next(f for f in p.location_facts if f.key == "noise_curfew")
    assert curfew.binding is FactBinding.HARD and curfew.binds is False  # proposed, not yet accepted

    def violations():
        ctx = ValidationContext(project=p, day=day, location_facts=[f for f in p.location_facts if f.binds])
        return [v for v in validate_schedule(ctx, day.items) if v.kind == ConstraintKind.EXTERNAL_RULE]

    assert violations() == []  # nothing the web said has touched the schedule

    curfew.accepted = True
    v = violations()
    assert len(v) == 1 and v[0].hard and v[0].minutes == 60
    assert v[0].fact_id == curfew.id
    assert v[0].evidence_url == "https://mumbaipolice.gov.in/noise"  # the rejection is traceable
    assert "noise curfew" in v[0].message.lower()

    # rejecting it again neutralises the rule
    curfew.rejected = True
    assert violations() == []


def test_an_accepted_activity_ban_rejects_the_equipment_that_triggers_it():
    p, tr, _ = _run_dossier()
    merge_facts(p, "loc_rooftop", map_facts(tr, p))
    for f in p.location_facts:
        if f.key == "fireworks_rules":
            f.accepted = True
        if f.key == "noise_curfew":
            f.rejected = True  # isolate the activity ban
    day = p.shoot_day(DAY4_ID)
    ctx = ValidationContext(project=p, day=day, location_facts=[f for f in p.location_facts if f.binds])
    v = [x for x in validate_schedule(ctx, day.items) if x.kind == ConstraintKind.EXTERNAL_RULE]
    assert len(v) == 1 and v[0].resource_id == "eq_fireworks" and v[0].hard
    assert "Fireworks rig" in v[0].message


def test_re_running_a_dossier_cannot_overwrite_a_rule_that_is_holding_the_schedule_up():
    p, tr, _ = _run_dossier()
    merge_facts(p, "loc_rooftop", map_facts(tr, p))
    for f in p.location_facts:
        f.accepted = True

    changed = dict(CONTENT, noise_curfew="23:00-05:00")  # the rule moved
    _, tr2, _ = _run_dossier(content=changed)
    tr2.resource_id = "loc_rooftop"
    withheld = merge_facts(p, "loc_rooftop", map_facts(tr2, p))

    # A new value needs a new decision — and until it gets one, the rule the producer signed off is
    # the one the scheduler is held to. This used to replace the accepted curfew with an unaccepted
    # one, which does not mean "awaiting a decision": `binds` is false either way, so the rule simply
    # stopped constraining anything, and every option the validator had rejected for breaching it
    # quietly went feasible again with nothing re-verdicting them.
    curfew = next(f for f in p.location_facts if f.key == "noise_curfew")
    assert curfew.value == "22:00-06:00" and curfew.binds, "the accepted rule stays in force"
    assert withheld and withheld[0].key == "noise_curfew" and withheld[0].value == "23:00-05:00"
    assert next(f for f in p.location_facts if f.key == "permit_authority").accepted is True  # unchanged fact keeps it
    assert len([f for f in p.location_facts if f.resource_id == "loc_rooftop" and f.key == "noise_curfew"]) == 1


def test_a_re_research_still_replaces_an_accepted_fact_that_is_not_holding_anything_up():
    """The refusal above is narrow on purpose, and this is the line it draws.

    `permit_authority` is prose: graded SOFT, never machine-checkable, so `binds` is false however
    accepted it is. Refusing to update it would turn "the producer signed this off" into "the
    producer froze it", and a re-research is the producer asking for the newest answer. Only a rule
    that is actually holding a schedule up is worth withholding.
    """
    p, tr, _ = _run_dossier()
    merge_facts(p, "loc_rooftop", map_facts(tr, p))
    for f in p.location_facts:
        f.accepted = True

    moved = dict(CONTENT, permit_authority="Maharashtra Film Cell")
    _, tr2, _ = _run_dossier(content=moved)
    tr2.resource_id = "loc_rooftop"
    withheld = merge_facts(p, "loc_rooftop", map_facts(tr2, p))

    authority = next(f for f in p.location_facts if f.key == "permit_authority")
    assert authority.value == "Maharashtra Film Cell"
    assert authority.accepted is False, "a different value is a different claim, so it needs deciding again"
    assert not [w for w in withheld if w.key == "permit_authority"]


def test_budget_and_missing_key_produce_records_not_exceptions():
    from scenepilot.tools.parallel_task import ParallelTaskTool

    p = build_project()
    tool = ParallelTaskTool(p, settings=_live_settings(parallel_task_max_runs=1), client=FakeClient(FakeTaskRuns(CONTENT, BASIS)))
    assert tool.dossier(p.resource("loc_rooftop")).status == "OK"
    second = tool.dossier(p.resource("loc_alley"))
    assert second.status == "ERROR" and "budget" in second.error
    assert map_facts(second, p) == []  # an errored run never produces facts

    keyless = ParallelTaskTool(p, settings=_live_settings(parallel_api_key=None), client=FakeClient(FakeTaskRuns(CONTENT, BASIS)))
    assert keyless.dossier(p.resource("loc_rooftop")).status == "ERROR"


def test_response_payload_survives_a_text_output():
    run = _Run()
    result = type("R", (), {"output": type("O", (), {"type": "text", "content": "plain prose", "basis": []})(), "run": run})()
    assert response_payload(result)["content"] == {"text": "plain prose"}


# --------------------------------------------------------------------------- #
# The gate again, at the HTTP layer
# --------------------------------------------------------------------------- #


def test_dossier_route_is_disabled_by_default_then_grades_and_accepts(monkeypatch):
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    monkeypatch.setattr(app_module, "repo", repo)

    with TestClient(app_module.app) as c:
        r = c.post("/api/projects/proj_nightfall/resources/loc_rooftop/dossier")
        assert r.status_code == 501 and r.json()["detail"]["env"] == "SCENEPILOT_PARALLEL_TASK=1"

        monkeypatch.setattr(app_module, "settings", _live_settings())
        monkeypatch.setattr(app_module.ParallelTaskTool, "client", property(lambda self: FakeClient(FakeTaskRuns(CONTENT, BASIS))))

        body = c.post("/api/projects/proj_nightfall/resources/loc_rooftop/dossier").json()
        assert body["task_run"]["status"] == "OK"
        facts = {f["key"]: f for f in body["facts"]}
        assert facts["noise_curfew"]["binding"] == "HARD" and facts["noise_curfew"]["accepted"] is False
        assert body["locations"][0]["binding_count"] == 0  # nothing binds until a producer accepts

        after = c.post(f"/api/projects/proj_nightfall/facts/{facts['noise_curfew']['id']}/accept", json={"accepted_by": "producer"}).json()
        assert after["fact"]["accepted"] is True
        assert next(l for l in after["locations"] if l["id"] == "loc_rooftop")["binding_count"] == 1

        # a dossier only makes sense for a location
        assert c.post("/api/projects/proj_nightfall/resources/cast_aarav/dossier").status_code == 400
        assert c.post(f"/api/projects/proj_nightfall/facts/{facts['noise_curfew']['id']}/maybe").status_code == 404


def test_accepted_facts_are_persisted_and_reset_clears_them(monkeypatch):
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    p = build_project()
    p.location_facts = [LocationFact(project_id=p.id, resource_id="loc_rooftop", task_run_id="task_1", key="noise_curfew", label="Noise curfew", value="22:00-06:00")]
    repo.save_project(p)
    repo.save_task_run(TaskRun(project_id=p.id, resource_id="loc_rooftop", input="x"))

    assert repo.get_project(p.id).location_facts[0].value == "22:00-06:00"  # survives the JSON round-trip
    repo.delete_project_data(p.id)
    assert repo.list_task_runs(project_id=p.id) == []


def test_an_accepted_fact_reaches_the_rejected_option_with_its_provenance():
    """The chain the UI draws: TaskRun → citation → accepted fact → a rejected recovery option.

    `build_option` reads accepted facts off the project, so this also pins that wiring: nothing in
    the rescue path has to remember to pass them.
    """
    from scenepilot.seed.nightfall import make_fixture_disruption
    from scenepilot.services.impact import analyze_impact
    from scenepilot.services.recovery import generate_candidates

    p, tr, _ = _run_dossier()
    merge_facts(p, "loc_rooftop", map_facts(tr, p))
    ban = next(f for f in p.location_facts if f.key == "fireworks_rules")
    ban.accepted = True  # the producer accepts what Parallel found
    for f in p.location_facts:
        if f.key == "noise_curfew":
            f.rejected = True  # isolate the activity ban

    day = p.shoot_day(DAY4_ID)
    d = make_fixture_disruption(p.id, day.id, "rain_pm")
    options = generate_candidates(p, day, d, analyze_impact(p, day, d), verification_confidence=0.8)

    # every option that still shoots the rooftop scene is now rejected by the discovered rule
    rooftop = [o for o in options if any(i.scene_id == "sc_42" for i in o.schedule)]
    assert rooftop, "the enumerator still considers keeping the rooftop scene"
    for o in rooftop:
        ext = [v for v in o.violations if v.kind == ConstraintKind.EXTERNAL_RULE]
        assert ext and not o.feasible
        assert ext[0].fact_id == ban.id
        assert ext[0].evidence_url == ban.citations[0].url  # traceable to the page Parallel cited
        check = next(c for c in o.checks if c["label"] == "external rules (permits, curfews)")
        assert check["ok"] is False and check["hard"] is True and check["detail"] == ext[0].message

    # and an option that carries the rooftop scene over passes that check
    without = [o for o in options if all(i.scene_id != "sc_42" for i in o.schedule)]
    assert without and all(
        next(c for c in o.checks if c["label"] == "external rules (permits, curfews)")["ok"] for o in without
    )


def test_the_recorded_night_curfew_rejects_the_night_units_rooftop_scene():
    """Day 6 is the day the 22:00–06:00 curfew stops being trivia.

    Day 4 hard-wraps at 22:00, so the curfew Parallel actually found can never bind there. The night
    unit runs the rooftop exterior to 23:30 — so accepting the fact rejects it, with the citation.
    """
    from scenepilot.seed.nightfall import DAY6_ID

    p, tr, _ = _run_dossier()
    merge_facts(p, "loc_rooftop", map_facts(tr, p))
    curfew = next(f for f in p.location_facts if f.key == "noise_curfew")
    day6 = p.shoot_day(DAY6_ID)

    def externals():
        ctx = ValidationContext(project=p, day=day6, location_facts=[f for f in p.location_facts if f.binds])
        return [v for v in validate_schedule(ctx, day6.items) if v.kind == ConstraintKind.EXTERNAL_RULE]

    assert externals() == []  # proposed, not accepted — the night shoot stands
    curfew.accepted = True
    v = externals()
    assert len(v) == 1 and v[0].hard
    assert v[0].scene_id == "sc_58" and v[0].minutes == 90  # 22:00 → 23:30
    assert v[0].fact_id == curfew.id and v[0].evidence_url == curfew.citations[0].url


def test_a_night_cover_set_is_not_offered_to_a_day_unit():
    """Cover has to be lightable in the gap it fills, or it is not cover."""
    from scenepilot.seed.nightfall import DAY6_ID, make_fixture_disruption
    from scenepilot.services.impact import analyze_impact

    from scenepilot.domain.enums import IntExt, TimeOfDay
    from scenepilot.domain.models import Scene

    p = build_project()
    day4, day6 = p.shoot_day(DAY4_ID), p.shoot_day(DAY6_ID)
    # a practical interior that genuinely needs darkness (unlike a stage, which shoots night any time)
    p.scenes.append(Scene(
        id="sc_71", number="71", heading="INT. PRACTICAL FLAT — NIGHT", int_ext=IntExt.INT, time_of_day=TimeOfDay.NIGHT,
        location_id="loc_apartment", cast_ids=["cast_meera"], equipment_ids=["eq_lighting"], estimated_minutes=90, is_cover=True,
    ))

    rain = make_fixture_disruption(p.id, day4.id, "rain_pm")  # 13:00-17:00
    covers_day = analyze_impact(p, day4, rain).cover_scene_ids
    assert "sc_27" in covers_day  # ANY interior - lightable in an afternoon gap
    assert "sc_71" not in covers_day  # needs darkness at 13:00, so it is not cover for a day unit

    night_rain = make_fixture_disruption(p.id, day6.id, "rain_pm")
    night_rain.window_start, night_rain.window_end = "21:00", "23:00"
    covers_night = analyze_impact(p, day6, night_rain).cover_scene_ids
    assert {"sc_27", "sc_71"} <= set(covers_night)  # after dark both interiors are usable


def test_a_rule_that_invalidates_the_baseline_gives_the_enumerator_something_to_move():
    """An accepted curfew makes the night unit's rooftop scene illegal even with no disruption hitting it.

    Deferrals used to be drawn only from disruption-affected scenes, so the day had no feasible
    recovery at all — just two rejected options. A scene the baseline already cannot legally shoot
    belongs in the pool too.
    """
    from scenepilot.domain.enums import DisruptionType
    from scenepilot.domain.models import Disruption
    from scenepilot.seed.nightfall import DAY6_ID
    from scenepilot.services.impact import analyze_impact
    from scenepilot.services.recovery import generate_candidates

    p, tr, _ = _run_dossier()
    merge_facts(p, "loc_rooftop", map_facts(tr, p))
    next(f for f in p.location_facts if f.key == "noise_curfew").accepted = True
    day6 = p.shoot_day(DAY6_ID)

    # a failure that is genuinely over before the rooftop scene starts: it affects nothing
    d = Disruption(project_id=p.id, shoot_day_id=day6.id, type=DisruptionType.EQUIPMENT_FAILURE,
                   title="rig down", description="", window_start="16:00", window_end="20:00",
                   affects_exteriors=False, affects_resource_ids=["eq_bike"])
    impact = analyze_impact(p, day6, d)
    assert impact.directly_affected_item_ids == []  # the disruption really does not touch Sc 58

    options = generate_candidates(p, day6, d, impact, verification_confidence=0.8)
    feasible = [o for o in options if o.feasible]
    assert feasible, "the curfew-blocked scene must still be movable"
    assert all("sc_58" in o.deferred_scene_ids for o in feasible)
    # and the rejected ones still explain themselves with the citation
    rejected = [o for o in options if not o.feasible]
    ext = [v for o in rejected for v in o.violations if v.kind == ConstraintKind.EXTERNAL_RULE]
    assert ext and all(v.evidence_url for v in ext)


def test_re_researching_continues_the_previous_interaction():
    """Parallel returns an interaction_id; passing it back continues that research instead of restarting.

    It is per-run state, so it must not reach the record/replay key — otherwise a re-research would
    miss the recording of the identical question.
    """
    from scenepilot.tools.parallel_task import ParallelTaskTool, build_task_request

    p = build_project()
    fake = FakeTaskRuns(CONTENT, BASIS)
    tool = ParallelTaskTool(p, settings=_live_settings(), client=FakeClient(fake))
    rooftop = p.resource("loc_rooftop")

    first = tool.dossier(rooftop)
    assert first.interaction_id == "int_1"
    assert "previous_interaction_id" not in fake.created[0]  # nothing to continue yet

    tool.dossier(rooftop, None, first.interaction_id)
    assert fake.created[1]["previous_interaction_id"] == "int_1"
    # the recorded request is unchanged, so the replay key is identical for both runs
    assert build_task_request(fake.created[0]["input"], "core-fast") == build_task_request(fake.created[1]["input"], "core-fast")
