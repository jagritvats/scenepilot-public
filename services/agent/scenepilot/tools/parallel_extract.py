"""Parallel Extract API — full page content for a specific source.

Per docs.parallel.ai/extract/best-practices: "Search finds and ranks relevant URLs with
focused excerpts; Extract then pulls deeper content from specific pages" (JS-heavy pages
and PDFs included). Shares the task's `session_id` with Search. Every call is a persisted
`ExtractRun`, record/replay capable, and exposed to the Evidence Analyst as a budgeted tool.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..config import Settings, settings as default_settings
from ..domain.models import ExtractResultItem, ExtractRun, ParallelUsageItem, ParallelWarning, utcnow
from .parallel_search import request_key
from .parallel_session import ParallelSession
from .recorder import Recorder, ReplayMiss

log = logging.getLogger(__name__)

DEFAULT_FULL_CONTENT_CHARS = 20000
TOOL_CONTENT_CHARS = 6000

PARALLEL_EXTRACT_TOOL_DOC = """Fetch the full content of ONE specific URL through the Parallel Extract API.

Use it at most once, only for a URL you already have from search results, when the excerpt is
truncated and the exact wording matters (policy page, PDF, official notice, regulation). Do NOT
search again for the same document instead.

Args:
    url: The exact URL from a search result.
    objective: Concise, self-contained statement of what to find in the page (include the key entity).
    question_id: The research question id this extraction serves (if known).

Returns:
    dict with extract_run_id, status, and results (url, title, publish_date, excerpts, content).
    Results are labelled <extract_run_id>#<n> when cited.
"""


def build_extract_request(urls: list[str], objective: str, *, search_queries: list[str] | None = None, full_content_chars: int | None = DEFAULT_FULL_CONTENT_CHARS, max_chars_total: int | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {"urls": list(urls), "objective": objective}
    if search_queries:
        request["search_queries"] = list(search_queries)[:3]
    if max_chars_total:
        request["max_chars_total"] = int(max_chars_total)
    if full_content_chars:
        request["advanced_settings"] = {"full_content": {"max_chars_per_result": int(full_content_chars)}}
    return request


class ParallelExtractTool:
    def __init__(
        self,
        run_id: str | None,
        project_id: str | None,
        session: ParallelSession | None = None,
        on_extract_run: Callable[[ExtractRun], None] | None = None,
        on_event: Callable[[str, str, dict], None] | None = None,
        recorder: Recorder | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or default_settings
        self.run_id = run_id
        self.project_id = project_id
        self.on_extract_run = on_extract_run or (lambda xr: None)
        self.on_event = on_event or (lambda kind, msg, meta: None)
        self.recorder = recorder or Recorder(self.settings.recordings_dir, self.settings.mode, self.settings.record)
        self.session = session or ParallelSession(self.settings, self.recorder, client_model=self.settings.gemini_model)
        self.calls: list[ExtractRun] = []

    def extract(
        self,
        urls: list[str],
        objective: str,
        *,
        question_id: str | None = None,
        search_run_id: str | None = None,
        purpose: str = "evidence_open_source",
        search_queries: list[str] | None = None,
        full_content_chars: int | None = DEFAULT_FULL_CONTENT_CHARS,
    ) -> ExtractRun:
        urls = [u.strip() for u in urls if u and u.strip()][:5]
        request = build_extract_request(urls, objective, search_queries=search_queries, full_content_chars=full_content_chars, max_chars_total=self.settings.parallel_max_chars_total)
        xr = ExtractRun(
            run_id=self.run_id, project_id=self.project_id, question_id=question_id, search_run_id=search_run_id, purpose=purpose, objective=objective, urls=urls,
            session_id=self.session.session_id, client_model=self.session.client_model, advanced_settings=request.get("advanced_settings"),
        )
        key = request_key("parallel_extract", request)
        self.on_event("parallel", f"Parallel Extract: {urls[0][:80] if urls else '?'}", {"extract_run_id": xr.id, "urls": urls, "purpose": purpose, "question_id": question_id})
        self.calls.append(xr)
        self.session.calls.append(xr)
        try:
            if self.recorder.replay:
                rec = self.recorder.lookup("parallel_extract", key)
                if rec is None:
                    raise ReplayMiss(f"No recording for Parallel extract of '{urls[0][:60] if urls else '?'}'")
                xr.replayed = True
                xr.status = "REPLAY"
                xr.provider_extract_id = rec.get("extract_id")
                xr.results = [ExtractResultItem.model_validate(r) for r in rec.get("results", [])]
                xr.errors = list(rec.get("errors", []))
                xr.usage = [ParallelUsageItem.model_validate(u) for u in rec.get("usage", [])]
                xr.warnings = [ParallelWarning.model_validate(w) for w in rec.get("warnings", [])]
            else:
                if not self.settings.parallel_configured:
                    raise RuntimeError("PARALLEL_API_KEY is not configured")
                resp = self.session.client.extract(**request, **self.session.meta())
                xr.provider_extract_id = resp.extract_id
                xr.results = [ExtractResultItem(url=r.url, title=r.title, publish_date=r.publish_date, excerpts=list(r.excerpts or []), full_content=r.full_content) for r in resp.results]
                xr.errors = [{"url": e.url, "error_type": e.error_type, "http_status_code": e.http_status_code} for e in (resp.errors or [])]
                xr.usage = [ParallelUsageItem(name=u.name, count=u.count) for u in (resp.usage or [])]
                xr.warnings = [ParallelWarning(type=w.type, message=w.message, detail=w.detail) for w in (resp.warnings or [])]
                xr.status = "OK" if xr.results else "ERROR"
                if not xr.results:
                    xr.error = "; ".join(f"{e['url']}: {e['error_type']} {e.get('http_status_code') or ''}".strip() for e in xr.errors)[:500] or "no content returned"
                self.recorder.save(
                    "parallel_extract", key,
                    {"extract_id": resp.extract_id, "results": [r.model_dump(mode="json") for r in xr.results], "errors": xr.errors, "usage": [u.model_dump() for u in xr.usage], "warnings": [w.model_dump() for w in xr.warnings]},
                    request={**request, **self.session.meta()},
                )
        except ReplayMiss:
            xr.status = "ERROR"
            xr.error = "This source was not captured in the demo recording (replay mode)."
            xr.finished_at = utcnow()
            self.on_extract_run(xr)
            if purpose == "evidence_open_source":
                self.on_event("warning", "Parallel Extract: no recording for this source (replay mode)", {"extract_run_id": xr.id})
                return xr  # a UI click must not crash a replayed demo
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Parallel extract failed: %s", exc)
            xr.status = "ERROR"
            xr.error = f"{type(exc).__name__}: {exc}"[:500]
            rec = self.recorder.lookup("parallel_extract", key) if self.settings.fallback_to_recording else None
            if rec is not None:
                xr.replayed, xr.status, xr.error = True, "REPLAY", None
                xr.provider_extract_id = rec.get("extract_id")
                xr.results = [ExtractResultItem.model_validate(r) for r in rec.get("results", [])]
                xr.errors = list(rec.get("errors", []))
                xr.usage = [ParallelUsageItem.model_validate(u) for u in rec.get("usage", [])]
                xr.warnings = [ParallelWarning.model_validate(w) for w in rec.get("warnings", [])]
                self.on_event("warning", f"Parallel Extract unavailable ({type(exc).__name__}) — served the recording of this request (replayed)", {"extract_run_id": xr.id, "fallback": True})
        xr.finished_at = utcnow()
        self.on_extract_run(xr)
        for w in xr.warnings:
            self.on_event("warning", f"Parallel warning ({w.type}): {w.message}"[:300], {"extract_run_id": xr.id})
        if xr.status == "ERROR":
            self.on_event("warning", f"Parallel Extract failed ({xr.error})", {"extract_run_id": xr.id})
        else:
            chars = sum(len(r.full_content or "") for r in xr.results)
            self.on_event("parallel", f"Parallel Extract returned {len(xr.results)} page(s), {chars:,} chars{' (replayed)' if xr.replayed else ''}", {"extract_run_id": xr.id, "extract_id": xr.provider_extract_id, "usage": [u.model_dump() for u in xr.usage]})
        return xr

    # ----- ADK tool surface -----
    def make_adk_tool(self, default_question_id: str | None = None, purpose: str = "agent_extract", max_calls: int = 1):
        tool = self
        budget = {"used": 0}

        def parallel_extract(url: str, objective: str, question_id: str = "") -> dict:
            if budget["used"] >= max_calls:
                tool.on_event("warning", f"Extract limit ({max_calls}) reached — model asked to grade with existing evidence", {"question_id": question_id or default_question_id})
                return {"extract_run_id": None, "status": "LIMIT", "error": f"Extract limit of {max_calls} reached. Do not call this tool again; grade the question using what you already have.", "results": []}
            budget["used"] += 1
            xr = tool.extract([url], objective, question_id=question_id or default_question_id, purpose=purpose)
            return {
                "extract_run_id": xr.id,
                "status": xr.status,
                "error": xr.error,
                "results": [
                    {"url": r.url, "title": r.title, "publish_date": r.publish_date, "excerpts": [e[:600] for e in r.excerpts[:3]], "content": (r.full_content or "")[:TOOL_CONTENT_CHARS]}
                    for r in xr.results
                ],
            }

        parallel_extract.__doc__ = PARALLEL_EXTRACT_TOOL_DOC
        return parallel_extract
