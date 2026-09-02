"""Parallel Search API — ScenePilot's external-world intelligence layer.

Every call is an observable, persisted `SearchRun` (request as sent, results, usage,
warnings, session). The same tool is exposed to ADK agents as a FunctionTool so Gemini
can autonomously run budgeted follow-up searches.

Request shape follows docs.parallel.ai/search/best-practices:
  * `objective` + 2–3 concise keyword queries (we cap at 3, strip `site:`/quotes);
  * `client_model` and `session_id` on every call;
  * `max_chars_total` left to Parallel's dynamic default unless configured;
  * advanced settings only when a caller explicitly opts in.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from ..config import Settings, settings as default_settings
from ..domain.models import ParallelUsageItem, ParallelWarning, SearchResultItem, SearchRun, utcnow
from .normalize import normalize
from .parallel_session import ParallelSession
from .recorder import Recorder, ReplayMiss

log = logging.getLogger(__name__)

MAX_QUERIES = 3
MAX_QUERY_CHARS = 200
PROMPT_EXCERPTS_PER_RESULT = 2
PROMPT_EXCERPT_CHARS = 600

# Parallel's recommended tool-schema wording (docs.parallel.ai/search/best-practices, "Tool exposure for agents").
PARALLEL_SEARCH_TOOL_DOC = """Search the live web through the Parallel Search API.

Use it when the evidence you already have is weak, conflicting or missing.

Args:
    objective: A concise, self-contained search query. Must include the key entity or topic,
        plus any source or freshness preference in words (e.g. "prefer official DGCA documentation").
    search_queries: Exactly 3 keyword search queries, each 3-6 words. Must be diverse — vary entity
        names, synonyms, and angles. NEVER write sentences, instructions, or use site: operators.
    question_id: The research question id this search serves (if known).

Returns:
    dict with search_run_id, status and results (url, title, publish_date, excerpts). Results are
    labelled <search_run_id>#<n> when cited.
"""

_SITE_RE = re.compile(r"\bsite:\S+", re.IGNORECASE)
_OR_RE = re.compile(r"\s+OR\s+")


def clean_queries(queries: list[str], objective: str = "") -> tuple[list[str], list[str]]:
    """Apply Parallel's query rules deterministically. Returns (queries, notes about rewrites)."""
    notes: list[str] = []
    out: list[str] = []
    for raw in queries:
        q = (raw or "").strip()
        if not q:
            continue
        original = q
        q = _SITE_RE.sub("", q)
        q = _OR_RE.sub(" ", q)
        q = q.replace('"', "").replace("“", "").replace("”", "")
        q = re.sub(r"\s+", " ", q).strip(" -:")
        if q != original:
            notes.append(f"rewrote {original!r} → {q!r}")
        if q and q.lower() not in {x.lower() for x in out}:
            out.append(q[:MAX_QUERY_CHARS])
    if len(out) > MAX_QUERIES:
        notes.append(f"dropped {len(out) - MAX_QUERIES} extra quer{'y' if len(out) - MAX_QUERIES == 1 else 'ies'} (max {MAX_QUERIES})")
        out = out[:MAX_QUERIES]
    if not out:
        out = [objective[:80]] if objective else []
    return out, notes


def build_search_request(
    objective: str,
    queries: list[str],
    mode: str,
    *,
    max_chars_total: int | None = None,
    location: str | None = None,
    max_results: int | None = None,
    max_chars_per_result: int | None = None,
    include_domains: list[str] | None = None,
    after_date: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, Any]:
    """The semantic request (what record/replay keys hash). Advanced settings only when asked for."""
    request: dict[str, Any] = {"objective": objective, "search_queries": list(queries), "mode": mode}
    if max_chars_total:
        request["max_chars_total"] = int(max_chars_total)
    adv: dict[str, Any] = {}
    if location:
        adv["location"] = location.lower()
    if max_results:
        adv["max_results"] = int(max_results)
    if max_chars_per_result:
        adv["excerpt_settings"] = {"max_chars_per_result": int(max_chars_per_result)}
    policy: dict[str, Any] = {}
    if include_domains:
        policy["include_domains"] = list(include_domains)
    if after_date:
        policy["after_date"] = after_date
    if policy:
        adv["source_policy"] = policy
    if max_age_seconds:
        adv["fetch_policy"] = {"max_age_seconds": max(600, int(max_age_seconds))}
    if adv:
        request["advanced_settings"] = adv
    return request


def request_key(namespace: str, request: dict[str, Any]) -> str:
    return Recorder.key(namespace, json.loads(normalize(json.dumps(request, ensure_ascii=False))))


class ParallelSearchTool:
    """Thin, observable wrapper around `parallel.Parallel().search`."""

    def __init__(
        self,
        run_id: str | None,
        project_id: str | None,
        session: ParallelSession | None = None,
        on_search_run: Callable[[SearchRun], None] | None = None,
        on_event: Callable[[str, str, dict], None] | None = None,
        recorder: Recorder | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or default_settings
        self.run_id = run_id
        self.project_id = project_id
        self.on_search_run = on_search_run or (lambda sr: None)
        self.on_event = on_event or (lambda kind, msg, meta: None)
        self.recorder = recorder or Recorder(self.settings.recordings_dir, self.settings.mode, self.settings.record)
        self.session = session or ParallelSession(self.settings, self.recorder, client_model=self.settings.gemini_model)
        self.calls: list[SearchRun] = []

    # ----- core call -----
    def search(
        self,
        objective: str,
        queries: list[str],
        *,
        question_id: str | None = None,
        purpose: str = "research",
        round: int = 1,
        mode: str | None = None,
        location: str | None = None,
        max_results: int | None = None,
        max_chars_per_result: int | None = None,
        after_date: str | None = None,
        include_domains: list[str] | None = None,
        max_age_seconds: int | None = None,
    ) -> SearchRun:
        queries, notes = clean_queries(queries, objective)
        if notes:
            self.on_event("warning", "Parallel query hygiene: " + "; ".join(notes)[:300], {"question_id": question_id})
        mode = mode or self.settings.parallel_search_mode
        request = build_search_request(
            objective, queries, mode, max_chars_total=self.settings.parallel_max_chars_total, location=location, max_results=max_results,
            max_chars_per_result=max_chars_per_result, include_domains=include_domains, after_date=after_date, max_age_seconds=max_age_seconds,
        )
        sr = SearchRun(
            run_id=self.run_id, project_id=self.project_id, question_id=question_id, purpose=purpose, round=round, objective=objective,
            queries=queries, mode=mode, session_id=self.session.session_id, client_model=self.session.client_model, advanced_settings=request.get("advanced_settings"),
        )
        key = request_key("parallel_search", request)
        self.on_event("parallel", f"Parallel Search ({mode}): {objective[:90]}", {"search_run_id": sr.id, "queries": queries, "mode": mode, "purpose": purpose, "question_id": question_id})
        self.calls.append(sr)
        self.session.calls.append(sr)
        try:
            if self.recorder.replay:
                rec = self.recorder.lookup("parallel_search", key)
                if rec is None:
                    raise ReplayMiss(f"No recording for Parallel search '{objective[:60]}'")
                sr.replayed = True
                sr.status = "REPLAY"
                sr.provider_search_id = rec.get("search_id")
                sr.results = [SearchResultItem.model_validate(r) for r in rec.get("results", [])]
                sr.usage = [ParallelUsageItem.model_validate(u) for u in rec.get("usage", [])]
                sr.warnings = [ParallelWarning.model_validate(w) for w in rec.get("warnings", [])]
            else:
                if not self.settings.parallel_configured:
                    raise RuntimeError("PARALLEL_API_KEY is not configured")
                resp = self.session.client.search(**request, **self.session.meta())
                if resp.session_id and resp.session_id != self.session.session_id:
                    log.warning("Parallel echoed a different session id (%s != %s)", resp.session_id, self.session.session_id)
                sr.provider_search_id = resp.search_id
                sr.results = [SearchResultItem(url=r.url, title=r.title, publish_date=r.publish_date, excerpts=list(r.excerpts or [])) for r in resp.results]
                sr.usage = [ParallelUsageItem(name=u.name, count=u.count) for u in (resp.usage or [])]
                sr.warnings = [ParallelWarning(type=w.type, message=w.message, detail=w.detail) for w in (resp.warnings or [])]
                sr.status = "OK"
                self.recorder.save(
                    "parallel_search", key,
                    {"search_id": resp.search_id, "results": [r.model_dump(mode="json") for r in sr.results], "usage": [u.model_dump() for u in sr.usage], "warnings": [w.model_dump() for w in sr.warnings]},
                    request={**request, **self.session.meta()},
                )
        except ReplayMiss:
            sr.status = "ERROR"
            sr.error = "replay miss"
            sr.finished_at = utcnow()
            self.on_search_run(sr)
            raise
        except Exception as exc:  # noqa: BLE001 — external call; we record and continue
            log.warning("Parallel search failed: %s", exc)
            sr.status = "ERROR"
            sr.error = f"{type(exc).__name__}: {exc}"[:500]
            rec = self.recorder.lookup("parallel_search", key) if self.settings.fallback_to_recording else None
            if rec is not None:
                # demo hardening: serve the recording of this exact request, labelled as replayed
                sr.replayed, sr.status, sr.error = True, "REPLAY", None
                sr.provider_search_id = rec.get("search_id")
                sr.results = [SearchResultItem.model_validate(r) for r in rec.get("results", [])]
                sr.usage = [ParallelUsageItem.model_validate(u) for u in rec.get("usage", [])]
                sr.warnings = [ParallelWarning.model_validate(w) for w in rec.get("warnings", [])]
                self.on_event("warning", f"Parallel Search unavailable ({type(exc).__name__}) — served the recording of this request from an earlier live run (replayed)", {"search_run_id": sr.id, "fallback": True})
        sr.finished_at = utcnow()
        self.on_search_run(sr)
        for w in sr.warnings:
            self.on_event("warning", f"Parallel warning ({w.type}): {w.message}"[:300], {"search_run_id": sr.id})
        n = len(sr.results)
        if sr.status == "ERROR":
            self.on_event("warning", f"Parallel Search failed ({sr.error})", {"search_run_id": sr.id})
        else:
            self.on_event("parallel", f"Parallel returned {n} source(s){' (replayed)' if sr.replayed else ''}", {"search_run_id": sr.id, "count": n, "search_id": sr.provider_search_id, "usage": [u.model_dump() for u in sr.usage]})
        return sr

    # ----- ADK tool surface -----
    def make_adk_tool(self, default_question_id: str | None = None, purpose: str = "agent_follow_up", round: int = 2, max_calls: int = 2):
        """Return a plain function suitable for `LlmAgent(tools=[...])`.

        `max_calls` is enforced here, not just in the prompt: once reached, the tool returns a
        LIMIT status so the model must grade with the evidence it already has.
        """
        tool = self
        budget = {"used": 0}

        def parallel_search(objective: str, search_queries: list[str], question_id: str = "") -> dict:
            if budget["used"] >= max_calls:
                tool.on_event("warning", f"Follow-up search limit ({max_calls}) reached — model asked to grade with existing evidence", {"question_id": question_id or default_question_id})
                return {"search_run_id": None, "status": "LIMIT", "error": f"Follow-up search limit of {max_calls} reached. Do not call this tool again; grade the question using ALL results you already have.", "results": []}
            budget["used"] += 1
            sr = tool.search(objective, list(search_queries), question_id=question_id or default_question_id, purpose=purpose, round=round)
            return {
                "search_run_id": sr.id,
                "status": sr.status,
                "error": sr.error,
                "results": [
                    {"url": r.url, "title": r.title, "publish_date": r.publish_date, "excerpts": [e[:PROMPT_EXCERPT_CHARS] for e in r.excerpts[:PROMPT_EXCERPTS_PER_RESULT]]}
                    for r in sr.results[:6]
                ],
            }

        parallel_search.__doc__ = PARALLEL_SEARCH_TOOL_DOC
        return parallel_search


def format_results_for_prompt(search_runs: list[SearchRun], max_results_per_run: int = 8) -> str:
    """Render search runs as a compact, citable block for a Gemini prompt.

    No status, error text, usage or replay flags: prompt text must be byte-identical in live and replay.
    """
    lines: list[str] = []
    for sr in search_runs:
        lines.append(f"### SearchRun {sr.id} — objective: {sr.objective}")
        lines.append(f"queries: {sr.queries}")
        if not sr.results:
            lines.append("(no results)")
        for i, r in enumerate(sr.results[:max_results_per_run], 1):
            lines.append(f"[{sr.id}#{i}] {r.title or '(untitled)'} — {r.url} — published: {r.publish_date or 'unknown'}")
            for e in r.excerpts[:PROMPT_EXCERPTS_PER_RESULT]:
                lines.append("    > " + e.replace("\n", " ")[:PROMPT_EXCERPT_CHARS])
        lines.append("")
    return "\n".join(lines)


def format_extracts_for_prompt(extract_runs, max_content_chars: int = 4000) -> str:
    """Render extract runs (full page content) as a citable block: labels are <extract_run_id>#<n>."""
    lines: list[str] = []
    for xr in extract_runs:
        lines.append(f"### ExtractRun {xr.id} — objective: {xr.objective}")
        if not xr.results:
            lines.append("(no content)")
        for i, r in enumerate(xr.results, 1):
            lines.append(f"[{xr.id}#{i}] {r.title or '(untitled)'} — {r.url} — published: {r.publish_date or 'unknown'}")
            for e in r.excerpts[:3]:
                lines.append("    > " + e.replace("\n", " ")[:PROMPT_EXCERPT_CHARS])
            if r.full_content:
                lines.append("    --- page content (truncated) ---")
                lines.append("    " + r.full_content[:max_content_chars].replace("\n", "\n    "))
        lines.append("")
    return "\n".join(lines)
