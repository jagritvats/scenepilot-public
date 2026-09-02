"""Deterministic summary of Parallel usage for a run (calls, modes, SKUs, estimated cost).

Prices from docs.parallel.ai/getting-started/pricing (Aug 2026): Search turbo/fast $1 per 1k requests,
basic/advanced $5 per 1k; Extract $1 per 1k URLs; Task lite/base/core/pro $5/$10/$25/$100 per 1k runs;
Entity Search $5 per 1k; FindAll base $0.25 + $0.03/match
(`-fast` variants cost the same as their base processor). Usage SKU names/counts are shown verbatim when
Parallel returns them; the cost estimate is derived from modes and URL counts (transparent, not billing).

**A replayed call is free, and this used to say otherwise.** Every price here was charged against every
run, including the ones answered from a committed recording — so a demo in `SCENEPILOT_MODE=replay`,
which is how the hosted instance and every screenshot run, reported dollars nobody spent. That is the
same claim `services/budget.costs_money` already refuses to make when it declines to book a recorded
call against the spend cap, and the two must not disagree about the same call.

So the total splits in three, and each is a different sentence a producer can check:
  * `est_cost_usd`   — what this deployment actually spent: live calls only.
  * `replayed_cost_usd` — what the replayed calls would have cost had they run live. Not spent; the
    number that makes the recordings' value legible, and the honest one to quote for "a run like this".
  * `cost_by_api`    — the same split per API, because "where does the money go" is the question a
    judge pricing their own product actually asks.
"""

from __future__ import annotations

from collections import Counter

SEARCH_USD_PER_1K = {"turbo": 1.0, "fast": 1.0, "basic": 5.0, "advanced": 5.0}
EXTRACT_USD_PER_1K_URLS = 1.0
# Task processors, $ per 1k runs. "-fast" variants cost the same as their base processor.
TASK_USD_PER_1K = {"lite": 5.0, "base": 10.0, "core": 25.0, "core2x": 50.0, "pro": 100.0, "ultra": 300.0}
# Entity Search is priced per request; FindAll is a fixed fee plus a per-match fee.
ENTITY_SEARCH_USD_PER_1K = 5.0
FINDALL_USD = {"preview": (0.10, 0.0), "base": (0.25, 0.03), "core": (2.00, 0.15), "pro": (10.00, 1.00)}


def task_usd(processor: str) -> float:
    """Price one Task run. `core-fast` is priced as `core` (Parallel charges the same)."""
    return TASK_USD_PER_1K.get((processor or "").removesuffix("-fast").lower(), 25.0) / 1000


def findall_usd(run) -> float:
    """Price one FindAll / Entity Search run from its mode, generator and matched count."""
    if getattr(run, "mode", "") == "entity_search":
        return ENTITY_SEARCH_USD_PER_1K / 1000
    fixed, per_match = FINDALL_USD.get((getattr(run, "generator", None) or "base").lower(), FINDALL_USD["base"])
    return fixed + per_match * len(getattr(run, "candidates", []) or [])


def _replayed(run) -> bool:
    """True when this call was answered from a committed recording, and so cost nothing."""
    return bool(getattr(run, "replayed", False)) or getattr(run, "status", "") == "REPLAY"


def summarize(search_runs: list, extract_runs: list | None = None, task_runs: list | None = None, findall_runs: list | None = None) -> dict:
    extract_runs = extract_runs or []
    task_runs = task_runs or []
    findall_runs = findall_runs or []
    by_mode = Counter(sr.mode for sr in search_runs)
    skus: Counter = Counter()
    warnings = 0
    for r in [*search_runs, *extract_runs, *task_runs, *findall_runs]:
        for u in getattr(r, "usage", []) or []:
            skus[u.name] += u.count
        warnings += len(getattr(r, "warnings", []) or [])
    urls = sum(len(xr.urls) for xr in extract_runs)

    # Priced twice over: once for the calls that actually left the process, once for the calls a
    # recording answered. Same prices, different sentences — see the module docstring.
    def _split(runs: list, price) -> tuple[float, float]:
        spent = sum(price(r) for r in runs if not _replayed(r))
        replayed = sum(price(r) for r in runs if _replayed(r))
        return spent, replayed

    search_spent, search_replayed = _split(search_runs, lambda r: SEARCH_USD_PER_1K.get(getattr(r, "mode", ""), 5.0) / 1000)
    extract_spent, extract_replayed = _split(extract_runs, lambda r: EXTRACT_USD_PER_1K_URLS * len(getattr(r, "urls", []) or []) / 1000)
    priced_tasks = [t for t in task_runs if getattr(t, "status", "") in {"OK", "REPLAY"}]
    task_spent, task_replayed = _split(priced_tasks, lambda t: task_usd(t.processor))
    priced_findalls = [f for f in findall_runs if getattr(f, "status", "") in {"OK", "REPLAY"}]
    findall_spent, findall_replayed = _split(priced_findalls, findall_usd)

    cost = search_spent + extract_spent + task_spent + findall_spent
    replayed_cost = search_replayed + extract_replayed + task_replayed + findall_replayed
    sessions = sorted({r.session_id for r in [*search_runs, *extract_runs] if getattr(r, "session_id", None)})
    return {
        "cost_by_api": {
            "search": {"spent_usd": round(search_spent, 4), "replayed_usd": round(search_replayed, 4)},
            "extract": {"spent_usd": round(extract_spent, 4), "replayed_usd": round(extract_replayed, 4)},
            "task": {"spent_usd": round(task_spent, 4), "replayed_usd": round(task_replayed, 4)},
            "findall": {"spent_usd": round(findall_spent, 4), "replayed_usd": round(findall_replayed, 4)},
        },
        # What the replayed calls would have cost live. Never spent — quotable as "a run like this".
        "replayed_cost_usd": round(replayed_cost, 4),
        "searches": len(search_runs),
        "by_mode": dict(sorted(by_mode.items())),
        "extracts": len(extract_runs),
        "urls": urls,
        "tasks": len(task_runs),
        "task_processors": dict(sorted(Counter(t.processor for t in task_runs).items())),
        "findalls": len(findall_runs),
        "vendors": sum(len(f.candidates) for f in findall_runs),
        "usage": [{"name": k, "count": v} for k, v in sorted(skus.items())],
        "warnings": warnings,
        "replayed": sum(1 for r in [*search_runs, *extract_runs, *task_runs, *findall_runs] if getattr(r, "replayed", False)),
        "errors": sum(1 for r in [*search_runs, *extract_runs, *task_runs, *findall_runs] if getattr(r, "status", "") == "ERROR"),
        "session_ids": sessions,
        "client_model": next((r.client_model for r in [*search_runs, *extract_runs] if getattr(r, "client_model", None)), None),
        # Live calls only. A replayed demo reports $0 here, because it spent $0.
        "est_cost_usd": round(cost, 4),
    }
