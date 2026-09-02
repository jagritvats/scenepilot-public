"""Snapshot monitors: a rule the production already accepted changes underneath it.

The event_stream monitors answer "what happened today". A snapshot monitor answers the harder
question — "is what I planned against still true?" — by re-running a location dossier and reporting
only the fields that moved.

The properties that matter, in order of how much damage getting them wrong would do:
  1. a detected change never edits the schedule by itself — it lands as PENDING;
  2. the accepted value keeps constraining the schedule for as long as the change is pending;
  3. adopting a *binding* change drops the acceptance, because acceptance was given to a value;
  4. a re-worded fact is not a change;
  5. a new value is graded by the same confidence gate the original had to pass.
"""

from dataclasses import replace

from scenepilot.config import settings as default_settings
from scenepilot.domain.enums import ConstraintKind, FactBinding
from scenepilot.domain.models import MonitorRecord, TaskRun
from scenepilot.seed.nightfall import DAY6_ID, build_project
from scenepilot.services.dossier import map_facts, merge_facts
from scenepilot.services.fact_watch import (
    SIMULATED_SNAPSHOT,
    adopt_change,
    changes_from_recheck,
    changes_from_snapshot,
    dismiss_change,
    pending_changes,
)
from scenepilot.services.schedule import ValidationContext, validate_schedule
from scenepilot.tools.parallel_monitor import ParallelMonitorTool, flatten_snapshot

from .test_dossier import BASIS, CONTENT, FakeClient, FakeTaskRuns, _live_settings


# --------------------------------------------------------------------------- #
# Fakes shaped like the SDK's MonitorSnapshotEvent / MonitorRunResult
# --------------------------------------------------------------------------- #


class _ModelBasis:
    """A FieldBasis as the SDK returns it — a pydantic model, so `model_dump` is what makes it JSON."""

    def __init__(self, field, confidence="high", reasoning="", citations=()):
        self._d = {"field": field, "confidence": confidence, "reasoning": reasoning, "citations": list(citations)}

    def model_dump(self, mode=None):
        return dict(self._d)


class _SnapOutput:
    type = "json"

    def __init__(self, content, basis=()):
        self.content, self.basis = content, list(basis)


class _SnapEvent:
    event_type = "snapshot"

    def __init__(self, changed, previous, basis=(), event_id="mevt_snap_1"):
        self.event_id, self.event_group_id, self.event_date = event_id, "mevtgrp_1", "2026-09-01"
        self.changed_output = _SnapOutput(changed, basis)
        self.previous_output = _SnapOutput(previous)


class FakeMonitors:
    def __init__(self, events=()):
        self.created: list[dict] = []
        self._events = list(events)

    def create(self, **kwargs):
        self.created.append(kwargs)
        return type("M", (), {"monitor_id": "mon_snap_1", "frequency": kwargs.get("frequency"), "processor": None, "status": "active"})()

    def events(self, monitor_id, event_group_id=None, limit=10):
        return type("P", (), {"events": self._events})()


def _project_with_curfew(accepted=True):
    """A project whose rooftop curfew came from a real dossier and has been signed off."""
    from scenepilot.tools.parallel_task import ParallelTaskTool

    p = build_project()
    tool = ParallelTaskTool(p, settings=_live_settings(), client=FakeClient(FakeTaskRuns(CONTENT, BASIS)))
    tr = tool.dossier(p.resource("loc_rooftop"))
    merge_facts(p, "loc_rooftop", map_facts(tr, p))
    curfew = next(f for f in p.location_facts if f.key == "noise_curfew")
    curfew.accepted = accepted
    return p, tr, curfew


def _monitor(p, task_run_id="task_1"):
    return MonitorRecord(id="mon_snap_1", project_id=p.id, kind="DOSSIER", monitor_type="snapshot", task_run_id=task_run_id, resource_id="loc_rooftop")


def _event(changed, previous, basis=None, event_id="mevt_snap_1"):
    return {"event_id": event_id, "event_group_id": "g", "event_date": "2026-09-01", "event_type": "snapshot", "changed": changed, "previous": previous, "basis": basis or SIMULATED_SNAPSHOT["basis"]}


# --------------------------------------------------------------------------- #


def test_watch_request_is_a_snapshot_monitor_over_the_dossiers_own_run():
    """Parallel derives the schema and the baseline from the task run, so we send only its id."""
    p, tr, _ = _project_with_curfew()
    fake = FakeMonitors()
    tool = ParallelMonitorTool(settings=_live_settings())
    tool.session._client = type("C", (), {"monitor": fake})()

    record = tool.watch_dossier(p, p.resource("loc_rooftop"), tr, "https://example.test/api/webhooks/parallel")
    sent = fake.created[0]
    assert sent["type"] == "snapshot"
    assert sent["settings"] == {"task_run_id": tr.provider_run_id}  # the provider's id, not ours
    assert sent["frequency"] == "1d"  # rules move on council time, and each run costs a task
    assert sent["metadata"] == {"project_id": p.id, "resource_id": "loc_rooftop", "kind": "DOSSIER"}
    assert sent["webhook"]["event_types"] == ["monitor.event.detected"]
    assert "settings" in sent and "query" not in sent["settings"]  # a snapshot monitor has no query
    assert record.monitor_type == "snapshot" and record.resource_id == "loc_rooftop" and record.shoot_day_id is None
    assert record.task_run_id == tr.id

    # a dossier that never reached Parallel has nothing to watch
    try:
        tool.watch_dossier(p, p.resource("loc_rooftop"), TaskRun(project_id=p.id, resource_id="loc_rooftop"), "https://example.test/w")
        raise AssertionError("expected a refusal")
    except ValueError as exc:
        assert "actually ran" in str(exc)


def test_events_carries_both_variants_and_flattens_the_diff():
    ev = _SnapEvent({"noise_curfew": "21:00-06:00"}, {"noise_curfew": "22:00-06:00", "permit_authority": "BMC"}, [_ModelBasis("noise_curfew")])
    flat = flatten_snapshot(ev)
    assert flat["changed"] == {"noise_curfew": "21:00-06:00"}  # only what moved
    assert flat["previous"]["permit_authority"] == "BMC"  # the full prior output, for context
    assert flat["basis"][0]["field"] == "noise_curfew"

    tool = ParallelMonitorTool(settings=_live_settings())
    tool.session._client = type("C", (), {"monitor": FakeMonitors([ev])})()
    out = tool.events("mon_snap_1")
    assert out[0]["event_type"] == "snapshot" and out[0]["changed"] == {"noise_curfew": "21:00-06:00"}
    assert out[0]["event_id"] == "mevt_snap_1"


def test_a_changed_rule_is_graded_by_the_same_gate_as_the_original():
    p, _, curfew = _project_with_curfew()
    m = _monitor(p)

    changes = changes_from_snapshot(p, m, _event(SIMULATED_SNAPSHOT["changed"], SIMULATED_SNAPSHOT["previous"]))
    assert len(changes) == 1
    c = changes[0]
    assert c.key == "noise_curfew" and c.fact_id == curfew.id and c.status == "PENDING"
    assert c.old_value == curfew.value and "21:00-06:00" in c.new_value
    assert c.binding == FactBinding.HARD and c.rule.window_start == "21:00"  # high + cited + parseable
    assert c.old_accepted and c.old_binds  # the schedule is being enforced against the old value
    assert c.citations[0].url.startswith("https://www.indiacode.nic.in")

    # low confidence gets the same demotion a first-time fact would get. The window still parses —
    # `binding` is what decides whether it may ever be enforced, not whether we understood it.
    weak = changes_from_snapshot(p, m, _event({"noise_curfew": "20:00-06:00"}, {}, basis=[{"field": "noise_curfew", "confidence": "low", "reasoning": "a forum post", "citations": []}]))
    assert weak[0].binding == FactBinding.ADVISORY and weak[0].affects_schedule is False
    assert c.affects_schedule is True  # ...whereas the cited one would, once accepted


def test_a_rewording_is_not_a_change_and_a_non_answer_is_not_either():
    p, _, curfew = _project_with_curfew()
    m = _monitor(p)
    same = changes_from_snapshot(p, m, _event({"noise_curfew": f"  {curfew.value}  "}, {}))
    assert same == []  # identical once trimmed — reporting it would train producers to ignore this
    empty = changes_from_snapshot(p, m, _event({"noise_curfew": "No information found."}, {}))
    assert empty == []  # the dossier grader already drops non-answers; so does this


def test_the_same_pending_change_is_not_recorded_twice():
    p, _, _ = _project_with_curfew()
    m = _monitor(p)
    first = changes_from_snapshot(p, m, _event(SIMULATED_SNAPSHOT["changed"], SIMULATED_SNAPSHOT["previous"]))
    p.fact_changes.extend(first)
    again = changes_from_snapshot(p, m, _event(SIMULATED_SNAPSHOT["changed"], SIMULATED_SNAPSHOT["previous"], event_id="mevt_snap_2"))
    assert again == []  # a second execution reporting the same value adds nothing


def test_the_schedule_follows_the_accepted_value_until_the_producer_adopts():
    """The whole point: Parallel noticing is not Parallel deciding.

    Day 6's night unit wraps the rooftop at 23:30. Under the accepted 22:00 curfew that is a 90
    minute violation. Parallel then reports the curfew has moved to 21:00 — which would make it 150
    — but nothing about the schedule may change until a producer says so, twice: adopt, then accept.
    """
    p, _, curfew = _project_with_curfew()
    day6 = p.shoot_day(DAY6_ID)

    def violation():
        ctx = ValidationContext(project=p, day=day6, location_facts=[f for f in p.location_facts if f.binds])
        return next((v for v in validate_schedule(ctx, day6.items) if v.kind == ConstraintKind.EXTERNAL_RULE), None)

    assert violation().minutes == 90  # 22:00 → 23:30, the rule as accepted

    change = changes_from_snapshot(p, _monitor(p), _event(SIMULATED_SNAPSHOT["changed"], SIMULATED_SNAPSHOT["previous"]))[0]
    p.fact_changes.append(change)
    assert violation().minutes == 90  # detected, pending — the schedule has not moved

    adopt_change(p, change)
    assert change.status == "ADOPTED"
    assert curfew.value == change.new_value and curfew.rule.window_start == "21:00"
    assert curfew.accepted is False  # acceptance was given to 22:00, not to "whatever this field says"
    assert violation() is None  # ...so for now nothing binds at all

    curfew.accepted = True
    assert violation().minutes == 150  # 21:00 → 23:30, the new rule, on the producer's word


def test_dismissing_keeps_the_value_the_production_signed_off():
    p, _, curfew = _project_with_curfew()
    change = changes_from_snapshot(p, _monitor(p), _event(SIMULATED_SNAPSHOT["changed"], SIMULATED_SNAPSHOT["previous"]))[0]
    p.fact_changes.append(change)
    dismiss_change(change, "line producer")
    assert change.status == "DISMISSED" and change.decided_by == "line producer" and change.decided_at
    assert curfew.value == "22:00-06:00" and curfew.accepted is True  # untouched
    assert pending_changes(p, "loc_rooftop") == []


def test_a_field_the_production_never_had_becomes_a_new_fact_on_adoption():
    """The dossier found nothing about drones; months later the DGCA publishes a red zone."""
    p, _, _ = _project_with_curfew()
    p.location_facts = [f for f in p.location_facts if f.key != "drone_rules"]
    change = changes_from_snapshot(
        p,
        _monitor(p),
        _event({"drone_rules": "Drone flights are prohibited over the mill estate."}, {"drone_rules": ""},
               basis=[{"field": "drone_rules", "confidence": "high", "reasoning": "DGCA red zone", "citations": [{"url": "https://digitalsky.dgca.gov.in/", "title": "DigitalSky"}]}]),
    )[0]
    assert change.old_value == "" and change.fact_id is None
    before = len(p.location_facts)
    fact = adopt_change(p, change)
    assert len(p.location_facts) == before + 1 and change.fact_id == fact.id
    assert fact.rule.kind == "ACTIVITY_BAN" and fact.accepted is False


# --------------------------------------------------------------------------- #
# Through the API, including the webhook the real monitor calls
# --------------------------------------------------------------------------- #


def test_simulate_adopt_and_the_webhook_all_go_through_one_ingestion(monkeypatch):
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    monkeypatch.setattr(app_module, "settings", _live_settings())
    monkeypatch.setattr(app_module.ParallelTaskTool, "client", property(lambda self: FakeClient(FakeTaskRuns(CONTENT, BASIS))))

    with TestClient(app_module.app) as c:
        c.post("/api/projects/proj_nightfall/reset")
        body = c.post("/api/projects/proj_nightfall/resources/loc_rooftop/dossier").json()
        curfew = next(f for f in body["facts"] if f["key"] == "noise_curfew")
        c.post(f"/api/projects/proj_nightfall/facts/{curfew['id']}/accept")

        # a location has to be researched before it can be watched
        assert c.post("/api/projects/proj_nightfall/resources/loc_apartment/watch").status_code == 400
        # ...and going live needs a webhook Parallel can reach
        assert c.post("/api/projects/proj_nightfall/resources/loc_rooftop/watch").status_code == 400

        r = c.post("/api/projects/proj_nightfall/resources/loc_rooftop/watch/simulate").json()
        assert len(r["changes"]) == 1 and r["changes"][0]["simulated"] is True
        change = r["changes"][0]
        assert change["old_accepted"] is True and change["binding"] == "HARD"
        assert next(l for l in r["locations"] if l["id"] == "loc_rooftop")["pending_changes"] == 1
        assert next(f for f in r["facts"] if f["id"] == curfew["id"])["value"] == "22:00-06:00"  # not yet touched

        adopted = c.post(f"/api/projects/proj_nightfall/fact-changes/{change['id']}/adopt", json={"decided_by": "producer"}).json()
        assert adopted["change"]["status"] == "ADOPTED"
        fact = next(f for f in adopted["facts"] if f["id"] == curfew["id"])
        assert "21:00-06:00" in fact["value"] and fact["accepted"] is False
        assert next(l for l in adopted["locations"] if l["id"] == "loc_rooftop")["pending_changes"] == 0

        assert c.post(f"/api/projects/proj_nightfall/fact-changes/{change['id']}/adopt").status_code == 409
        assert c.post(f"/api/projects/proj_nightfall/fact-changes/{change['id']}/maybe").status_code == 404

        # the webhook path: a real snapshot monitor's event reaches the same ingestion
        p = app_module.repo.get_project("proj_nightfall")
        p.monitors.append(MonitorRecord(id="mon_live_1", project_id=p.id, kind="DOSSIER", monitor_type="snapshot", resource_id="loc_rooftop", status="active"))
        app_module.repo.save_project(p)
        ev = _SnapEvent({"permit_authority": "Mumbai Film City Cell (single-window)"}, {"permit_authority": "Brihanmumbai Municipal Corporation (BMC)"},
                        [_ModelBasis("permit_authority", reasoning="portal notice")], event_id="mevt_live_1")
        monkeypatch.setattr(app_module.ParallelMonitorTool, "events", lambda self, mid, gid=None, limit=10: [{"event_id": "mevt_live_1", "event_group_id": "g", "event_date": "2026-09-01", "event_type": "snapshot", **flatten_snapshot(ev)}])
        hook = c.post("/api/webhooks/parallel", json={"type": "monitor.event.detected", "data": {"monitor_id": "mon_live_1", "metadata": {"project_id": "proj_nightfall"}, "event": {"event_group_id": "g"}}}).json()
        assert len(hook["fact_changes"]) == 1  # routed to the snapshot path, not the disruption path

        view = c.get("/api/projects/proj_nightfall/dossiers?resource_id=loc_rooftop").json()
        permit = next(ch for ch in view["fact_changes"] if ch["key"] == "permit_authority")
        assert permit["status"] == "PENDING" and permit["simulated"] is False
        assert [d for d in c.get("/api/projects/proj_nightfall/shoot-days/day_6").json().get("disruptions", []) if d.get("draft")] == []


# --------------------------------------------------------------------------- #
# Pre-flight: the same diff, asked on purpose the night before a day locks
# --------------------------------------------------------------------------- #


class FakeTaskRunsSeq:
    """A Parallel task_run that answers differently each call — a location whose rules moved."""

    def __init__(self, outputs, basis):
        self._outputs, self._basis, self._n = list(outputs), basis, 0
        self.created: list[dict] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return _SeqRun(len(self.created))

    def result(self, run_id, api_timeout=None):
        content = self._outputs[min(self._n, len(self._outputs) - 1)]
        self._n += 1
        return type("R", (), {"output": _SnapOutput(content, self._basis), "run": _SeqRun(self._n)})()


class _SeqRun:
    def __init__(self, n):
        self.run_id, self.interaction_id, self.processor, self.warnings = f"trun_seq{n}", f"int_{n}", "core-fast", []


CHANGED_CONTENT = {**CONTENT, "noise_curfew": "21:00-06:00"}


def test_a_recheck_reports_what_moved_without_touching_what_was_accepted():
    """The property that separates a re-check from 'Re-research': it never rewrites a decision."""
    from scenepilot.tools.parallel_task import ParallelTaskTool

    p, first, curfew = _project_with_curfew()
    assert curfew.accepted and curfew.binds

    tool = ParallelTaskTool(p, settings=_live_settings(), client=FakeClient(FakeTaskRunsSeq([CHANGED_CONTENT], BASIS)))
    second = tool.dossier(p.resource("loc_rooftop"))
    found = changes_from_recheck(p, "loc_rooftop", second, first.output)

    assert len(found) == 1 and found[0].key == "noise_curfew"
    c = found[0]
    assert c.detected_by == "preflight" and c.monitor_id is None and c.task_run_id == second.id
    assert c.old_value == "22:00-06:00" and c.new_value == "21:00-06:00"
    assert c.old_accepted and c.old_binds and c.simulated is False
    # ...and the accepted fact is completely untouched until someone adopts
    assert curfew.value == "22:00-06:00" and curfew.accepted and curfew.rule.window_start == "22:00"


def test_a_recheck_that_finds_nothing_says_so():
    from scenepilot.tools.parallel_task import ParallelTaskTool

    p, first, _ = _project_with_curfew()
    tool = ParallelTaskTool(p, settings=_live_settings(), client=FakeClient(FakeTaskRunsSeq([CONTENT], BASIS)))
    again = tool.dossier(p.resource("loc_rooftop"))
    assert changes_from_recheck(p, "loc_rooftop", again, first.output) == []


def test_preflight_route_checks_the_days_locations_and_prices_the_unresearched(monkeypatch):
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    # Cold on purpose: this is the *billing* property — a location nobody researched is never
    # re-checked — so the demo seed's pre-loaded dossiers would answer a different question.
    monkeypatch.setattr(app_module, "settings", _live_settings(warm_demo=False))

    seq = FakeTaskRunsSeq([CONTENT, CHANGED_CONTENT], BASIS)
    monkeypatch.setattr(app_module.ParallelTaskTool, "client", property(lambda self: FakeClient(seq)))

    with TestClient(app_module.app) as c:
        c.post("/api/projects/proj_nightfall/reset")
        body = c.post("/api/projects/proj_nightfall/resources/loc_rooftop/dossier").json()
        curfew = next(f for f in body["facts"] if f["key"] == "noise_curfew")
        c.post(f"/api/projects/proj_nightfall/facts/{curfew['id']}/accept")

        r = c.post("/api/projects/proj_nightfall/shoot-days/day_6/preflight").json()
        # the rooftop was researched and re-checked; the apartment never was, so it is reported not run
        assert [x["resource_id"] for x in r["checked"]] == ["loc_rooftop"]
        assert [x["id"] for x in r["unresearched"]] == ["loc_apartment"]  # never researched → never billed
        assert len(r["changes"]) == 1 and r["urgent"] == 1
        change = r["changes"][0]
        assert change["detected_by"] == "preflight" and change["status"] == "PENDING"
        assert change["old_value"] == "22:00-06:00" and change["new_value"] == "21:00-06:00"

        # the accepted rule still governs the schedule until the producer decides
        fact = next(f for f in r["facts"] if f["id"] == curfew["id"])
        assert fact["value"] == "22:00-06:00" and fact["accepted"] is True
        assert next(l for l in r["locations"] if l["id"] == "loc_rooftop")["binding_count"] == 1

        # and it lands in the same queue a monitor would have filled
        adopted = c.post(f"/api/projects/proj_nightfall/fact-changes/{change['id']}/adopt").json()
        assert adopted["change"]["status"] == "ADOPTED"
        assert next(f for f in adopted["facts"] if f["id"] == curfew["id"])["value"] == "21:00-06:00"
