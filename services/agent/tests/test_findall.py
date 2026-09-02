"""Substitute suppliers: the rescue stops reporting and starts fixing.

The properties worth pinning: the feature makes no SDK call when it is off; the fast synchronous path
and the deep async path both produce the same `VendorCandidate` shape; polling is bounded so a run
that never finishes still returns what it found; and a vendor changes nothing about production state
until a producer selects it *and* the recovery is approved.
"""

from dataclasses import replace

from scenepilot.config import settings as default_settings
from scenepilot.domain.enums import CoordinationKind
from scenepilot.domain.models import ChangeSet, FindAllRun, VendorCandidate
from scenepilot.seed.nightfall import DAY4_ID, build_project
from scenepilot.services.coordination import derive_actions
from scenepilot.tools.parallel_findall import ParallelFindAllTool, build_entity_objective, build_match_conditions, build_objective, entity_category
from scenepilot.tools.recorder import ReplayMiss


def _live_settings(**over):
    base = {"mode": "live", "record": False, "parallel_api_key": "test-key", "parallel_findall_enabled": True}
    return replace(default_settings, **{**base, **over})


# --------------------------------------------------------------------------- #
# Fakes shaped like parallel.types.beta.*
# --------------------------------------------------------------------------- #


class _Entity:
    def __init__(self, name, url, description=""):
        self.name, self.url, self.description = name, url, description


class _Citation:
    def __init__(self, url, title=None):
        self.url, self.title, self.excerpts = url, title, []


class _Basis:
    def __init__(self, field, reasoning, citations):
        self.field, self.reasoning, self.citations, self.confidence = field, reasoning, citations, "high"


class _Candidate:
    def __init__(self, name, url, match_status="matched", output=None, basis=None, description=""):
        self.candidate_id = f"cand_{name[:4]}"
        self.name, self.url, self.match_status, self.output, self.basis, self.description = name, url, match_status, output, basis, description


class _Status:
    def __init__(self, status="completed", is_active=False, reason="match_limit_met"):
        self.status, self.is_active, self.termination_reason = status, is_active, reason


class _Run:
    def __init__(self, status):
        self.findall_id = "findall_abc"
        self.status = status


class FakeFindAll:
    """Serves entity_search plus a create/result sequence for the async path."""

    def __init__(self, entities=None, result_sequence=None, enrich_raises=None, after_enrich=None):
        self._entities = entities or []
        self._results = list(result_sequence or [])
        self._enrich_raises = enrich_raises
        self._after_enrich = after_enrich
        self.created: list[dict] = []
        self.searched: list[dict] = []
        self.enriched: list[dict] = []
        self.result_calls = 0

    def entity_search(self, **kwargs):
        self.searched.append(kwargs)
        return type("R", (), {"entities": list(self._entities), "entity_set_id": "entity_set_1"})()

    def create(self, **kwargs):
        self.created.append(kwargs)
        return _Run(_Status("running", True, None))

    def enrich(self, findall_id, **kwargs):
        self.enriched.append({"findall_id": findall_id, **kwargs})
        if self._enrich_raises:
            raise self._enrich_raises
        if self._after_enrich is not None:
            self._results.append(self._after_enrich)
        return None

    def result(self, findall_id):
        self.result_calls += 1
        return self._results[min(self.result_calls - 1, len(self._results) - 1)]


class FakeClient:
    def __init__(self, findall):
        self.beta = type("Beta", (), {"findall": findall})()


def _tool(fake, settings=None, project=None, **kw):
    return ParallelFindAllTool(project or build_project(), settings=settings or _live_settings(), client=FakeClient(fake), sleep=lambda s: None, **kw)


# --------------------------------------------------------------------------- #


def test_entity_search_is_the_fast_synchronous_path():
    p = build_project()
    fake = FakeFindAll(entities=[_Entity("Mumbai Grip House", "https://mumbaigrip.example", "Cranes and dollies"), _Entity("Western Camera Rentals", "https://wcr.example")])
    run = _tool(fake, project=p).find_substitutes(p.resource("eq_crane"), shoot_day_id=DAY4_ID)

    assert run.status == "OK" and run.mode == "entity_search" and len(run.candidates) == 2
    assert fake.searched[0]["entity_type"] == "companies" and fake.searched[0]["match_limit"] == 8
    assert fake.created == []  # the sync path never starts an async run
    first = run.candidates[0]
    assert first.name == "Mumbai Grip House" and first.citations[0].url == "https://mumbaigrip.example"
    assert run.provider_findall_id == "entity_set_1"


def test_entity_search_gets_a_category_not_a_request_sentence():
    """Verified live on 2026-08-29: phrasing decides whether this returns anything at all.

    "grip and camera crane rental vendors serving film productions in Mumbai" → 7 real companies;
    the FindAll-style sentence naming the exact item → zero. So the two paths phrase differently.
    """
    p = build_project()
    assert build_entity_objective(p, p.resource("eq_crane")) == "grip and camera crane rental vendors serving film productions in Mumbai"
    assert build_entity_objective(p, p.resource("eq_drone")).startswith("aerial cinematography and drone service providers")
    assert build_entity_objective(p, p.resource("eq_fireworks")).startswith("pyrotechnics and special effects vendors")
    assert build_entity_objective(p, p.resource("loc_rooftop")).startswith("film shooting locations and sound stages")
    assert entity_category(p.resource("eq_lighting")) == "film lighting and power equipment rental companies"

    # the category must not leak the request-sentence shape that returned nothing
    obj = build_entity_objective(p, p.resource("eq_crane"))
    assert "replacement" not in obj and "'" not in obj and "short notice" not in obj

    fake = FakeFindAll(entities=[])
    _tool(fake, project=p).find_substitutes(p.resource("eq_crane"))
    assert fake.searched[0]["objective"] == obj


def test_the_same_company_under_several_profile_urls_is_listed_once():
    """Live results returned Paxton Equipments twice, as bo.linkedin and in.linkedin profiles."""
    p = build_project()
    fake = FakeFindAll(entities=[
        _Entity("Paxton Equipments", "https://bo.linkedin.com/company/paxton"),
        _Entity("Paxton Equipments", "https://in.linkedin.com/company/paxton"),
        _Entity("1 Stop Cine Digital", "https://in.linkedin.com/company/1scd"),
    ])
    run = _tool(fake, project=p).find_substitutes(p.resource("eq_crane"))
    assert [v.name for v in run.candidates] == ["Paxton Equipments", "1 Stop Cine Digital"]


def test_entity_search_respects_the_match_limit():
    p = build_project()
    fake = FakeFindAll(entities=[_Entity(f"Vendor {i}", f"https://v{i}.example") for i in range(20)])
    run = _tool(fake, project=p, settings=_live_settings(parallel_findall_match_limit=3)).find_substitutes(p.resource("eq_crane"))
    assert len(run.candidates) == 3


def test_findall_path_polls_until_terminal_and_keeps_only_matches():
    p = build_project()
    running = type("R", (), {"run": _Run(_Status("running", True, None)), "candidates": []})()
    done = type("R", (), {
        "run": _Run(_Status("completed", False, "match_limit_met")),
        "candidates": [
            _Candidate("Grip House", "https://grip.example", "matched",
                       output={"phone": "+91 22 1234 5678", "address": "Andheri East", "day_rate_band": "₹18,000–₹25,000"},
                       basis=[_Basis("capability", "Lists a 30 ft telescopic crane", [_Citation("https://grip.example/cranes", "Fleet")])]),
            _Candidate("Not A Match", "https://nope.example", "unmatched"),
        ],
    })()
    fake = FakeFindAll(result_sequence=[running, running, done])
    run = _tool(fake, project=p).find_substitutes(p.resource("eq_crane"), mode="findall")

    # three polls to reach a terminal state, then one more after enrichment
    assert run.status == "OK" and fake.result_calls == 4 and run.termination_reason == "match_limit_met"
    assert fake.enriched, "matched candidates are enriched for contact details"
    assert len(run.candidates) == 1  # unmatched candidates are dropped
    v = run.candidates[0]
    assert (v.phone, v.address, v.day_rate_band) == ("+91 22 1234 5678", "Andheri East", "₹18,000–₹25,000")
    assert v.citations[0].url == "https://grip.example/cranes"
    assert v.match_reasons == ["Lists a 30 ft telescopic crane"]
    sent = fake.created[0]
    assert sent["generator"] == "base" and sent["match_limit"] == 8
    assert [c["name"] for c in sent["match_conditions"]] == ["location", "capability", "contactable"]


def test_a_findall_that_never_finishes_returns_what_it_found_with_a_warning():
    p = build_project()
    stuck = type("R", (), {"run": _Run(_Status("running", True, None)), "candidates": [_Candidate("Partial Co", "https://partial.example")]})()
    fake = FakeFindAll(result_sequence=[stuck])
    run = _tool(fake, project=p, settings=_live_settings(parallel_findall_timeout_s=0)).find_substitutes(p.resource("eq_crane"), mode="findall")

    assert run.status == "OK" and len(run.candidates) == 1
    assert run.warnings and run.warnings[0].type == "timeout"


def test_memory_scope_reaches_findall_only_when_memory_is_on():
    p = build_project()
    fake = FakeFindAll(result_sequence=[type("R", (), {"run": _Run(_Status()), "candidates": []})()])
    _tool(fake, project=p, memory_scope_key="scenepilot_proj_nightfall").find_substitutes(p.resource("eq_crane"), mode="findall")
    assert fake.created[0]["memory_scope_key"] == "scenepilot_proj_nightfall"

    fake2 = FakeFindAll(result_sequence=[type("R", (), {"run": _Run(_Status()), "candidates": []})()])
    _tool(fake2, project=p).find_substitutes(p.resource("eq_crane"), mode="findall")
    assert "memory_scope_key" not in fake2.created[0]


def test_objective_and_conditions_adapt_to_the_resource_type():
    p = build_project()
    assert "equipment rental houses" in build_objective(p, p.resource("eq_crane"))
    assert "filming locations or sound stages" in build_objective(p, p.resource("loc_rooftop"))
    assert "urgently" in build_objective(p, p.resource("eq_crane"), "urgently")
    assert all(c["description"] for c in build_match_conditions(p, p.resource("eq_crane")))


def test_a_missing_key_or_a_failing_api_produces_a_record_not_an_exception():
    p = build_project()
    keyless = _tool(FakeFindAll(), settings=_live_settings(parallel_api_key=None), project=p)
    r = keyless.find_substitutes(p.resource("eq_crane"))
    assert r.status == "ERROR" and "PARALLEL_API_KEY" in r.error

    class Boom(FakeFindAll):
        def entity_search(self, **kwargs):
            raise RuntimeError("upstream 503")

    r2 = _tool(Boom(), project=p).find_substitutes(p.resource("eq_crane"))
    assert r2.status == "ERROR" and "upstream 503" in r2.error and r2.finished_at is not None


# --------------------------------------------------------------------------- #
# Nothing changes production state until a producer selects *and* approves
# --------------------------------------------------------------------------- #


def test_a_selected_vendor_becomes_a_coordination_action_and_an_unselected_one_does_not():
    p = build_project()
    day = p.shoot_day(DAY4_ID)
    cs = ChangeSet(id="cs_1", project_id=p.id, run_id="run_1", shoot_day_id=day.id)

    run = FindAllRun(project_id=p.id, resource_id="eq_crane", mode="findall", candidates=[
        VendorCandidate(findall_run_id="f1", name="Grip House", url="https://grip.example", description="Cranes", phone="+91 22 1234 5678", address="Andheri East", day_rate_band="₹18,000–₹25,000", citations=[]),
        VendorCandidate(findall_run_id="f1", name="Other Co", url="https://other.example"),
    ])

    assert [a for a in derive_actions(p, day, cs, substitutes=[run]) if a.kind == CoordinationKind.EQUIPMENT_SUBSTITUTE] == []

    run.candidates[0].selected = True
    actions = [a for a in derive_actions(p, day, cs, substitutes=[run]) if a.kind == CoordinationKind.EQUIPMENT_SUBSTITUTE]
    assert len(actions) == 1
    a = actions[0]
    assert a.title == "Book replacement — Grip House" and a.target == "Grip department"
    assert "Replacing 30 ft telescopic crane" in a.details
    assert any("+91 22 1234 5678" in d for d in a.details)
    assert any(d.startswith("Found by Parallel FindAll · source:") for d in a.details)  # the number is traceable
    assert a.payload["vendor_id"] == run.candidates[0].id


def test_substitute_routes_are_disabled_by_default_then_find_and_select(monkeypatch):
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    monkeypatch.setattr(app_module, "repo", repo)

    with TestClient(app_module.app) as c:
        r = c.post("/api/projects/proj_nightfall/resources/eq_crane/substitutes")
        assert r.status_code == 501 and r.json()["detail"]["env"] == "SCENEPILOT_PARALLEL_FINDALL=1"

        fake = FakeFindAll(entities=[_Entity("Mumbai Grip House", "https://mumbaigrip.example", "Cranes")])
        monkeypatch.setattr(app_module, "settings", _live_settings())
        monkeypatch.setattr(app_module.ParallelFindAllTool, "client", property(lambda self: FakeClient(fake)))

        body = c.post("/api/projects/proj_nightfall/resources/eq_crane/substitutes?shoot_day_id=day_4").json()
        fr = body["findall_run"]
        assert fr["status"] == "OK" and len(fr["candidates"]) == 1
        assert all(v["selected"] is False for v in fr["candidates"])

        vendor_id = fr["candidates"][0]["id"]
        picked = c.post(f"/api/findall-runs/{fr['id']}/select/{vendor_id}").json()["findall_run"]
        assert [v["selected"] for v in picked["candidates"]] == [True]
        assert repo.get_findall_run(fr["id"]).candidates[0].selected is True

        assert c.post(f"/api/findall-runs/{fr['id']}/select/vendor_nope").status_code == 404
        assert c.post("/api/projects/proj_nightfall/resources/nope/substitutes").status_code == 404


def test_the_webhook_routes_every_payload_type(monkeypatch):
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    monkeypatch.setattr(app_module, "repo", repo)

    with TestClient(app_module.app) as c:
        c.post("/api/projects/proj_nightfall/reset")

        r = c.post("/api/webhooks/parallel", json={"type": "task_run.status", "data": {"run_id": "trun_1", "status": "completed", "metadata": {"project_id": "proj_nightfall", "kind": "location_dossier"}}}).json()
        assert r == {"ok": True, "run_id": "trun_1", "status": "completed"}

        r = c.post("/api/webhooks/parallel", json={"type": "monitor.execution.completed", "data": {"monitor_id": "monitor_1", "metadata": {"project_id": "proj_nightfall"}}}).json()
        assert r["ok"] and r["monitor_id"] == "monitor_1"

        assert c.post("/api/webhooks/parallel", json={"type": "something.else", "data": {}}).json()["ignored"] == "something.else"
        # an unroutable run is acknowledged, not 500'd — webhooks must never fail loudly at Parallel
        assert c.post("/api/webhooks/parallel", json={"type": "task_run.status", "data": {"run_id": "x"}}).json()["ignored"] == "unknown run"

        # The activity route returns the production-log envelope, not a bare list: the events plus
        # the kind vocabulary and per-category counts the log renders from.
        messages = [e["message"] for e in c.get("/api/projects/proj_nightfall/activity").json()["events"]]
        assert any("trun_1 is completed" in m for m in messages)


# --------------------------------------------------------------------------- #
# Enrichment: what turns a company name into a phone number a 1st AD can ring
# --------------------------------------------------------------------------- #


def _matched(output=None):
    return type("R", (), {
        "run": _Run(_Status()),
        "candidates": [_Candidate("Grip House", "https://grip.example", "matched", output=output,
                                  basis=[_Basis("capability", "Rents a 30 ft crane", [_Citation("https://grip.example/fleet")])])],
    })()


def test_enrich_fetches_contact_details_for_matched_candidates_only():
    p = build_project()
    enriched = _matched({"phone": "+91 22 4000 1111", "address": "Andheri East, Mumbai", "distance_km": "about 12.5 km", "day_rate_band": "₹18,000–₹25,000"})
    fake = FakeFindAll(result_sequence=[_matched()], after_enrich=enriched)
    run = _tool(fake, project=p).find_substitutes(p.resource("eq_crane"), mode="findall")

    assert run.status == "OK" and run.enriched is True
    assert fake.enriched[0]["output_schema"]["type"] == "json"
    assert set(fake.enriched[0]["output_schema"]["json_schema"]["properties"]) == {"phone", "address", "distance_km", "day_rate_band"}
    v = run.candidates[0]
    assert (v.phone, v.address, v.day_rate_band) == ("+91 22 4000 1111", "Andheri East, Mumbai", "₹18,000–₹25,000")
    assert v.distance_km == 12.5  # "about 12.5 km" → a number the UI can sort on


def test_a_failing_enrich_still_returns_the_vendors():
    """Enrichment is a bonus. Losing it must not lose the search."""
    p = build_project()
    fake = FakeFindAll(result_sequence=[_matched()], enrich_raises=RuntimeError("enrich 500"))
    run = _tool(fake, project=p).find_substitutes(p.resource("eq_crane"), mode="findall")

    assert run.status == "OK" and len(run.candidates) == 1 and run.enriched is False
    assert any(w.type == "enrich_failed" and "enrich 500" in w.message for w in run.warnings)


def test_enrich_is_skipped_when_nothing_matched_or_when_disabled():
    p = build_project()
    empty = type("R", (), {"run": _Run(_Status()), "candidates": [_Candidate("No", "https://no.example", "unmatched")]})()
    fake = FakeFindAll(result_sequence=[empty])
    _tool(fake, project=p).find_substitutes(p.resource("eq_crane"), mode="findall")
    assert fake.enriched == []  # nothing matched → nothing to enrich, and no spend

    fake2 = FakeFindAll(result_sequence=[_matched()])
    _tool(fake2, project=p, settings=_live_settings(parallel_findall_enrich=False)).find_substitutes(p.resource("eq_crane"), mode="findall")
    assert fake2.enriched == []

    fake3 = FakeFindAll(entities=[_Entity("A", "https://a.example")])
    _tool(fake3, project=p).find_substitutes(p.resource("eq_crane"), mode="entity_search")
    assert fake3.enriched == []  # entity_search has nothing to enrich


# --------------------------------------------------------------------------- #
# Record / replay, so a demo survives an outage
# --------------------------------------------------------------------------- #


def test_a_recorded_search_replays_without_touching_the_sdk(tmp_path):
    p = build_project()
    rec = _live_settings(recordings_dir=tmp_path, record=True)
    fake = FakeFindAll(entities=[_Entity("Mumbai Grip House", "https://mumbaigrip.example", "Cranes")])
    live = _tool(fake, project=p, settings=rec).find_substitutes(p.resource("eq_crane"))
    assert live.status == "OK" and live.replayed is False

    class Exploding(FakeFindAll):
        def entity_search(self, **kwargs):
            raise AssertionError("replay must not call the SDK")

    replayed = _tool(Exploding(), project=p, settings=_live_settings(recordings_dir=tmp_path, mode="replay")).find_substitutes(p.resource("eq_crane"))
    assert replayed.status == "REPLAY" and replayed.replayed is True
    assert [v.name for v in replayed.candidates] == ["Mumbai Grip House"]
    assert replayed.candidates[0].citations[0].url == "https://mumbaigrip.example"


def test_replay_without_a_recording_fails_loudly(tmp_path):
    import pytest

    p = build_project()
    with pytest.raises(ReplayMiss):
        _tool(FakeFindAll(), project=p, settings=_live_settings(recordings_dir=tmp_path, mode="replay")).find_substitutes(p.resource("eq_crane"))


def test_a_live_failure_falls_back_to_a_recording_and_says_so(tmp_path):
    p = build_project()
    rec = _live_settings(recordings_dir=tmp_path, record=True)
    _tool(FakeFindAll(entities=[_Entity("Mumbai Grip House", "https://mumbaigrip.example")]), project=p, settings=rec).find_substitutes(p.resource("eq_crane"))

    class Down(FakeFindAll):
        def entity_search(self, **kwargs):
            raise RuntimeError("upstream 503")

    run = _tool(Down(), project=p, settings=_live_settings(recordings_dir=tmp_path, fallback_to_recording=True)).find_substitutes(p.resource("eq_crane"))
    assert run.status == "REPLAY" and run.replayed is True and len(run.candidates) == 1


def test_findall_match_limit_is_clamped_to_the_range_the_api_accepts():
    """Verified live 2026-08-29: FindAll 422s with "Match limit must be between 5 and 1000"."""
    from scenepilot.tools.parallel_findall import clamp_match_limit

    assert (clamp_match_limit(3), clamp_match_limit(5), clamp_match_limit(8), clamp_match_limit(5000)) == (5, 5, 8, 1000)

    p = build_project()
    fake = FakeFindAll(result_sequence=[type("R", (), {"run": _Run(_Status()), "candidates": []})()])
    _tool(fake, project=p, settings=_live_settings(parallel_findall_match_limit=3)).find_substitutes(p.resource("eq_crane"), mode="findall")
    assert fake.created[0]["match_limit"] == 5  # a misconfigured env var never reaches the API

    # Entity Search has no such floor — it must keep honouring a small limit
    fake2 = FakeFindAll(entities=[_Entity(f"V{i}", f"https://v{i}.example") for i in range(9)])
    run = _tool(fake2, project=p, settings=_live_settings(parallel_findall_match_limit=3)).find_substitutes(p.resource("eq_crane"))
    assert len(run.candidates) == 3
