"""The Parallel integration, checked against Parallel's documented best practices.

A fake SDK client (returning real `parallel.types` models) captures exactly what would be sent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from parallel.types import ExtractResponse, ExtractResult, SearchResult, UsageItem, WebSearchResult
from parallel.types.extract_error import ExtractError
from parallel.types.shared import Warning as ParallelWarningModel

from scenepilot.config import Settings, settings as real_settings
from scenepilot.services.parallel_usage import summarize
from scenepilot.tools.parallel_extract import ParallelExtractTool, build_extract_request
from scenepilot.tools.parallel_search import ParallelSearchTool, build_search_request, clean_queries
from scenepilot.tools.parallel_session import ParallelSession, new_session_id
from scenepilot.tools.recorder import Recorder, ReplayMiss


class FakeClient:
    def __init__(self, warn: bool = False):
        self.search_calls: list[dict] = []
        self.extract_calls: list[dict] = []
        self.warn = warn

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return SearchResult(
            search_id="search_cad0a6d2dec046bd95ae900527d880e7",
            session_id="server-echo",
            results=[WebSearchResult(url="https://digitalsky.dgca.gov.in/airspace-map", title="Digital Sky", publish_date="2026-07-01", excerpts=["Red zones need permission", "Yellow zones need ATC clearance", "third", "fourth"])],
            usage=[UsageItem(name="search_fast", count=1)],
            warnings=[ParallelWarningModel(type="warning", message="location narrowed results", detail=None)] if self.warn else None,
        )

    def extract(self, **kwargs):
        self.extract_calls.append(kwargs)
        return ExtractResponse(
            extract_id="extract_cad0a6d2dec046bd95ae900527d880e7",
            session_id="server-echo",
            results=[ExtractResult(url=kwargs["urls"][0], title="Policy", publish_date="2026-01-10", excerpts=["Relevant passage"], full_content="# Policy\n\nFull text of the policy page.")],
            errors=[ExtractError(url="https://broken.example", error_type="fetch_error", http_status_code=404)] if len(kwargs["urls"]) > 1 else [],
            usage=[UsageItem(name="extract_url", count=1)],
            warnings=None,
        )


def _settings(tmp_path: Path, mode: str = "live", record: bool = False, max_chars: int | None = None, fallback: bool = True) -> Settings:
    return Settings(
        gemini_model="gemini-3.5-flash", google_api_key="x", use_vertex=False, parallel_api_key="pk", parallel_search_mode="advanced",
        parallel_max_chars_total=max_chars, mode=mode, record=record, fallback_to_recording=fallback, database_url="sqlite:///:memory:", data_dir=tmp_path, recordings_dir=tmp_path / "rec", port=8000, public_base_url=None,
    )


def _tools(tmp_path: Path, **kw):
    st = _settings(tmp_path, **kw)
    rec = Recorder(st.recordings_dir, st.mode, st.record)
    session = ParallelSession(st, rec, session_id=new_session_id("planning", "run_0123456789"), client_model=st.gemini_model)
    client = FakeClient()
    session._client = client
    events: list[tuple[str, str]] = []
    search = ParallelSearchTool("run_0123456789", "proj", session=session, on_event=lambda k, m, meta: events.append((k, m)), recorder=rec, settings=st)
    extract = ParallelExtractTool("run_0123456789", "proj", session=session, on_event=lambda k, m, meta: events.append((k, m)), recorder=rec, settings=st)
    return st, rec, session, client, search, extract, events


# 1. Planning-style search: exactly the documented core fields, no advanced settings, client_model + session on every call
def test_search_sends_core_fields_only_with_client_model_and_session(tmp_path):
    st, rec, session, client, search, extract, events = _tools(tmp_path)
    sr = search.search("Drone permission over Lower Parel rooftops; prefer official DGCA documentation", ["Mumbai drone permission filming", "DGCA Digital Sky red zone", "Mumbai Police aerial NOC"], mode="fast")
    kw = client.search_calls[0]
    assert set(kw) == {"objective", "search_queries", "mode", "session_id", "client_model"}
    assert kw["client_model"] == "gemini-3.5-flash" and kw["session_id"] == "scenepilot_planning_run_0123456789"
    assert "advanced_settings" not in kw and "max_chars_total" not in kw
    assert sr.status == "OK" and sr.client_model == "gemini-3.5-flash" and sr.session_id == "scenepilot_planning_run_0123456789"
    assert sr.advanced_settings is None
    assert len(sr.results[0].excerpts) == 4  # no truncation on the run itself
    assert sr.usage[0].name == "search_fast" and sr.usage[0].count == 1


# 2. Verification-style call: exact nested advanced settings
def test_verification_search_builds_exact_advanced_settings(tmp_path):
    st, rec, session, client, search, extract, events = _tools(tmp_path)
    search.search("IMD warnings for Mumbai", ["IMD Mumbai nowcast warning", "Mumbai district forecast", "Mumbai rainfall warning"], mode="fast", location="IN", max_results=6, max_chars_per_result=1200, include_domains=["mausam.imd.gov.in"], after_date="2026-07-27", max_age_seconds=120)
    adv = client.search_calls[0]["advanced_settings"]
    assert adv == {"location": "in", "max_results": 6, "excerpt_settings": {"max_chars_per_result": 1200}, "source_policy": {"include_domains": ["mausam.imd.gov.in"], "after_date": "2026-07-27"}, "fetch_policy": {"max_age_seconds": 600}}


# 3. max_chars_total only when configured
def test_max_chars_total_env_override(tmp_path):
    st, rec, session, client, search, extract, events = _tools(tmp_path, max_chars=9000)
    search.search("o", ["a b c"], mode="fast")
    assert client.search_calls[0]["max_chars_total"] == 9000
    assert build_search_request("o", ["a b c"], "fast") == {"objective": "o", "search_queries": ["a b c"], "mode": "fast"}


# 4. Same session across search and extract; server echo never overwrites it
def test_session_shared_across_search_and_extract(tmp_path):
    st, rec, session, client, search, extract, events = _tools(tmp_path)
    search.search("o", ["a b c"], mode="fast")
    xr = extract.extract(["https://digitalsky.dgca.gov.in/airspace-map"], "What does the red-zone rule say?")
    assert client.search_calls[0]["session_id"] == client.extract_calls[0]["session_id"] == "scenepilot_planning_run_0123456789"
    assert client.extract_calls[0]["client_model"] == "gemini-3.5-flash"
    assert client.extract_calls[0]["advanced_settings"] == {"full_content": {"max_chars_per_result": 20000}}
    assert session.session_id == "scenepilot_planning_run_0123456789"
    assert xr.status == "OK" and xr.results[0].full_content.startswith("# Policy") and xr.usage[0].name == "extract_url"
    assert [c.id for c in session.calls] == [search.calls[0].id, xr.id]


# 5. usage/warnings persisted, warning event emitted, restored on replay
def test_usage_and_warnings_recorded_and_replayed(tmp_path):
    st, rec, session, client, search, extract, events = _tools(tmp_path, record=True)
    client.warn = True
    sr = search.search("o", ["a b c"], mode="fast")
    assert sr.warnings[0].message == "location narrowed results"
    assert any(k == "warning" and "Parallel warning" in m for k, m in events)
    files = list((st.recordings_dir / "parallel_search").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["response"]["usage"] == [{"name": "search_fast", "count": 1}] and payload["request"]["client_model"] == "gemini-3.5-flash"
    # replay with a different session/model still hits (keys exclude per-run metadata)
    st2 = _settings(tmp_path, mode="replay")
    rec2 = Recorder(st2.recordings_dir, "replay", False)
    session2 = ParallelSession(st2, rec2, session_id="scenepilot_planning_run_ffffffffff", client_model="gemini-3.7-flash")
    search2 = ParallelSearchTool("run_ffffffffff", "proj", session=session2, recorder=rec2, settings=st2)
    sr2 = search2.search("o", ["a b c"], mode="fast")
    assert sr2.replayed and sr2.status == "REPLAY" and sr2.usage[0].count == 1 and sr2.warnings[0].type == "warning"
    assert sr2.results[0].excerpts == sr.results[0].excerpts


# 6. ReplayMiss propagates loudly in replay mode (never a silent ERROR)
def test_replay_miss_is_loud(tmp_path):
    st = _settings(tmp_path, mode="replay")
    rec = Recorder(st.recordings_dir, "replay", False)
    search = ParallelSearchTool("run_x", "proj", session=ParallelSession(st, rec), recorder=rec, settings=st)
    with pytest.raises(ReplayMiss):
        search.search("never recorded", ["x y z"], mode="fast")
    assert search.calls[0].status == "ERROR"


# 7. Query hygiene per Parallel's tool rules
def test_clean_queries_enforces_parallel_rules():
    q, notes = clean_queries(['site:mcgm.gov.in "FilmShootingPolicy.pdf" structural', "Mumbai drone permission", "DGCA red zone rules", "extra fourth query", "Mumbai drone permission"], "objective")
    assert q == ["FilmShootingPolicy.pdf structural", "Mumbai drone permission", "DGCA red zone rules"]
    assert any("rewrote" in n for n in notes) and any("dropped" in n for n in notes)
    assert clean_queries(["", "  "], "fallback objective text")[0] == ["fallback objective text"]
    q2, _ = clean_queries(["Mumbai police NOC OR permission"], "")
    assert q2 == ["Mumbai police NOC permission"]


# 8. Extract budget and tool declaration
def test_extract_tool_budget_and_declaration(tmp_path):
    from google.adk.tools import FunctionTool

    st, rec, session, client, search, extract, events = _tools(tmp_path)
    fn = extract.make_adk_tool(default_question_id="rq_abc123_1", max_calls=1)
    first = fn("https://digitalsky.dgca.gov.in/airspace-map", "red zone rule")
    second = fn("https://digitalsky.dgca.gov.in/airspace-map", "again")
    assert first["status"] == "OK" and first["results"][0]["content"].startswith("# Policy")
    assert second["status"] == "LIMIT" and len(client.extract_calls) == 1
    decl = FunctionTool(fn)._get_declaration()
    props = decl.parameters.properties if decl.parameters else (decl.parameters_json_schema or {}).get("properties", {})
    assert {"url", "objective"} <= set(props)
    assert "Parallel Extract API" in (decl.description or "")


# 9. Search tool docstring carries Parallel's recommended wording
def test_search_tool_declaration_wording(tmp_path):
    from google.adk.tools import FunctionTool

    st, rec, session, client, search, extract, events = _tools(tmp_path)
    decl = FunctionTool(search.make_adk_tool()).  _get_declaration()
    assert "Exactly 3 keyword search queries" in (decl.description or "") and "site:" in (decl.description or "")


# 10. Usage summary arithmetic
def test_usage_summary_costs(tmp_path):
    st, rec, session, client, search, extract, events = _tools(tmp_path)
    for mode in ("fast", "fast", "advanced"):
        search.search("o", ["a b c"], mode=mode)
    extract.extract(["https://a.example", "https://broken.example"], "obj")
    u = summarize(search.calls, extract.calls)
    assert u["searches"] == 3 and u["by_mode"] == {"advanced": 1, "fast": 2} and u["extracts"] == 1 and u["urls"] == 2
    assert u["usage"] == [{"name": "extract_url", "count": 1}, {"name": "search_fast", "count": 3}]
    assert u["est_cost_usd"] == pytest.approx((2 * 1 + 1 * 5) / 1000 + 2 * 1 / 1000)
    assert u["session_ids"] == ["scenepilot_planning_run_0123456789"] and u["client_model"] == "gemini-3.5-flash"


# 11. Extract errors surface without breaking the run
def test_extract_records_per_url_errors(tmp_path):
    st, rec, session, client, search, extract, events = _tools(tmp_path)
    xr = extract.extract(["https://a.example", "https://broken.example"], "obj")
    assert xr.status == "OK" and xr.errors == [{"url": "https://broken.example", "error_type": "fetch_error", "http_status_code": 404}]
    assert build_extract_request(["u"], "o", search_queries=["a b", "c d", "e f", "g h"])["search_queries"] == ["a b", "c d", "e f"]



class BrokenClient(FakeClient):
    def search(self, **kwargs):
        raise ConnectionError("Parallel down")

    def extract(self, **kwargs):
        raise ConnectionError("Parallel down")


# 12. Demo hardening: a live failure serves the recording of the same request, labelled replayed
def test_live_failure_falls_back_to_recording(tmp_path):
    st, rec, session, client, search, extract, events = _tools(tmp_path, record=True)
    search.search("o", ["a b c"], mode="fast")
    extract.extract(["https://digitalsky.dgca.gov.in/airspace-map"], "rule")
    session._client = BrokenClient()
    events.clear()
    sr = search.search("o", ["a b c"], mode="fast")
    assert sr.replayed and sr.status == "REPLAY" and sr.error is None and sr.results[0].title == "Digital Sky"
    xr = extract.extract(["https://digitalsky.dgca.gov.in/airspace-map"], "rule")
    assert xr.replayed and xr.status == "REPLAY" and xr.results[0].full_content.startswith("# Policy")
    assert sum(1 for k, m in events if k == "warning" and "served the recording" in m) == 2
    # a request that was never recorded still fails honestly
    sr2 = search.search("never recorded", ["x y z"], mode="fast")
    assert sr2.status == "ERROR" and not sr2.replayed


def test_fallback_can_be_disabled(tmp_path):
    st, rec, session, client, search, extract, events = _tools(tmp_path, record=True, fallback=False)
    search.search("o", ["a b c"], mode="fast")
    session._client = BrokenClient()
    sr = search.search("o", ["a b c"], mode="fast")
    assert sr.status == "ERROR" and not sr.replayed


# 12. A replayed call spent nothing, and the ledger has to say so
def test_replayed_calls_are_not_charged(tmp_path):
    """`budget.costs_money` refuses to book a recorded call; the cost estimate must agree with it.

    Before this, every price was charged against every run including the replayed ones — so the
    hosted demo, which runs entirely on committed recordings, reported dollars nobody spent.
    """
    from scenepilot.domain.models import FindAllRun, TaskRun

    st, rec, session, client, search, extract, events = _tools(tmp_path)
    for mode in ("fast", "advanced"):
        search.search("o", ["a b c"], mode=mode)
    live_tasks = [TaskRun(processor="core-fast", status="OK", replayed=False)]
    replayed_tasks = [TaskRun(processor="core-fast", status="REPLAY", replayed=True)]

    live = summarize(search.calls, [], live_tasks, [])
    assert live["est_cost_usd"] == pytest.approx((1 + 5) / 1000 + 0.025)
    assert live["replayed_cost_usd"] == 0
    assert live["cost_by_api"]["task"] == {"spent_usd": pytest.approx(0.025), "replayed_usd": 0}

    # The same walk, replayed: the searches still happened live here, the Task did not.
    mixed = summarize(search.calls, [], replayed_tasks, [])
    assert mixed["est_cost_usd"] == pytest.approx((1 + 5) / 1000), "a replayed Task is not spend"
    assert mixed["replayed_cost_usd"] == pytest.approx(0.025), "but it is quotable as what it would cost"
    assert mixed["cost_by_api"]["task"] == {"spent_usd": 0, "replayed_usd": pytest.approx(0.025)}
    assert mixed["tasks"] == 1 and mixed["replayed"] == 1  # still counted, just not billed
