"""Custom `adk eval` metric: did the agent use Parallel the way Parallel recommends?

Deterministic (no LLM judge), scored per invocation:
  0.4  called `parallel_search` at least once before answering
  0.3  every search call had exactly 3 keyword queries of 3-6 words, with no sentences,
       quotes, `OR` or `site:` operators (Parallel's tool-schema rules)
  0.3  the final answer cites at least one source a Parallel tool actually returned (URL or its [search_<id>#n] label)
Wired through eval_config.json → custom_metrics.parallel_tool_use.code_config.name.
"""

from __future__ import annotations

import re
from typing import Any

from google.adk.evaluation import evaluator as _ev
from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.evaluation.evaluator import EvaluationResult, PerInvocationResult

EvalStatus = _ev.EvalStatus
_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
_BAD_QUERY_RE = re.compile(r"\bsite:|\"|“|”|\bOR\b")
_LABEL_RE = re.compile(r"\b(?:search|extract)_[0-9a-f]{10}#\d+\b")


def _text(content: Any) -> str:
    if content is None:
        return ""
    parts = getattr(content, "parts", None) or []
    return " ".join(getattr(p, "text", "") or "" for p in parts)


def _tool_calls_and_responses(inv: Invocation) -> tuple[list[Any], list[Any]]:
    data = inv.intermediate_data
    calls: list[Any] = []
    responses: list[Any] = []
    if data is None:
        return calls, responses
    if getattr(data, "tool_uses", None) is not None:
        calls = list(data.tool_uses or [])
        responses = list(getattr(data, "tool_responses", None) or [])
        return calls, responses
    for ev in getattr(data, "invocation_events", None) or []:
        for p in (getattr(ev.content, "parts", None) or []) if ev.content else []:
            if getattr(p, "function_call", None):
                calls.append(p.function_call)
            if getattr(p, "function_response", None):
                responses.append(p.function_response)
    return calls, responses


def _query_ok(q: str) -> bool:
    words = q.split()
    return 3 <= len(words) <= 6 and not _BAD_QUERY_RE.search(q)


def score_invocation(inv: Invocation) -> tuple[float, str]:
    calls, responses = _tool_calls_and_responses(inv)
    searches = [c for c in calls if getattr(c, "name", "") == "parallel_search"]
    score = 0.0
    notes: list[str] = []
    if searches:
        score += 0.4
        notes.append(f"{len(searches)} parallel_search call(s)")
    else:
        notes.append("no parallel_search call")
    if searches:
        good = 0
        for c in searches:
            qs = list((getattr(c, "args", None) or {}).get("search_queries") or [])
            if len(qs) == 3 and all(_query_ok(q) for q in qs):
                good += 1
        share = good / len(searches)
        score += 0.3 * share
        notes.append(f"{good}/{len(searches)} call(s) follow the query rules")
    returned_urls: set[str] = set()
    returned_labels: set[str] = set()
    for r in responses:
        payload = getattr(r, "response", None) or {}
        if isinstance(payload, dict):
            run_id = payload.get("search_run_id") or payload.get("extract_run_id")
            for n, item in enumerate(payload.get("results", []) or [], 1):
                if isinstance(item, dict) and item.get("url"):
                    returned_urls.add(item["url"].rstrip("/"))
                    if run_id:
                        returned_labels.add(f"{run_id}#{n}")
    answer = _text(inv.final_response)
    cited_urls = {u.rstrip("/").rstrip(".,") for u in _URL_RE.findall(answer)}
    cited_labels = set(_LABEL_RE.findall(answer))
    hits = (cited_urls & returned_urls) | (cited_labels & returned_labels)
    if hits:
        score += 0.3
        notes.append(f"answer cites {len(hits)} returned source(s)")
    else:
        notes.append("answer cites no returned source")
    return round(score, 3), "; ".join(notes)


def parallel_tool_use(eval_metric: EvalMetric, actual: list[Invocation], expected: list[Invocation] | None = None, scenario: Any = None) -> EvaluationResult:
    threshold = float(getattr(eval_metric, "threshold", 0.8) or 0.8)
    per: list[PerInvocationResult] = []
    for i, inv in enumerate(actual):
        s, note = score_invocation(inv)
        per.append(PerInvocationResult(actual_invocation=inv, expected_invocation=expected[i] if expected and i < len(expected) else None, score=s, eval_status=EvalStatus.PASSED if s >= threshold else EvalStatus.FAILED))
    overall = sum(p.score or 0 for p in per) / len(per) if per else 0.0
    return EvaluationResult(overall_score=round(overall, 3), overall_eval_status=EvalStatus.PASSED if overall >= threshold else EvalStatus.FAILED, per_invocation_results=per)
