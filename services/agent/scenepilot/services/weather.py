"""Turn a Parallel Task weather run into an hourly timeline the scrubber can draw.

The rule this module exists to enforce: **a bar is drawn only where a source said something.** The
deleted radar drew a picture of weather nobody had reported, and this is the honest replacement — so
every step here removes rather than fills:

* an hour the model left empty, or answered with "no information found", is dropped, not zeroed;
* a percentage is read out of the text the model returned, never inferred from a condition word
  ("showers" is not 60%); an hour that states a condition without a figure keeps the condition and
  reports no percentage, and the UI draws it as a marker rather than a height; and
* the hours that survive carry their own citations, reasoning and confidence — Parallel's Basis, per
  field, which is exactly why the schema is one field per hour.

The gaps are the point. A day the research could only answer for four hours *looks* like four
answered hours, and a producer can see the rest was never covered.
"""

from __future__ import annotations

import re
from typing import Any

from ..domain.models import TaskRun
from .dossier import find_basis, is_non_answer  # the same basis lookup and non-answer filter the dossier gate uses

HOUR_START, HOUR_END = 6, 22

# The first percentage in the value. Bounded to 0–100 so a stray "120%" is not read as a chance.
_PCT = re.compile(r"(\d{1,3})\s*%")


def parse_precip_pct(value: str) -> int | None:
    """The stated chance of precipitation, or None when the text does not carry one."""
    for m in _PCT.finditer(value):
        pct = int(m.group(1))
        if 0 <= pct <= 100:
            return pct
    return None


def _hour_label(hour: int) -> str:
    return f"{hour:02d}:00"


def map_timeline(task_run: TaskRun | None) -> dict[str, Any] | None:
    """The view an hourly strip is drawn from, or None when there is nothing honest to draw.

    None covers every "we do not know" case — no run, a run that errored, a run whose every hour was
    a non-answer — and the UI renders the research button and a sentence, never an empty axis
    pretending to be a forecast.
    """
    if task_run is None or task_run.status not in {"OK", "REPLAY"}:
        return None

    output = task_run.output or {}
    hours: list[dict[str, Any]] = []
    for hour in range(HOUR_START, HOUR_END):
        field = f"hour_{hour:02d}"
        raw = output.get(field)
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value or is_non_answer(value):
            continue
        basis = find_basis(task_run.basis, field)
        hours.append(
            {
                "field": field,
                "hour": hour,
                "start_min": hour * 60,
                "label": _hour_label(hour),
                "value": value,
                "precip_pct": parse_precip_pct(value),
                "confidence": basis.confidence if basis else None,
                "reasoning": basis.reasoning if basis else "",
                "citations": [c.model_dump(mode="json") for c in basis.citations] if basis else [],
            }
        )

    summary_raw = output.get("day_summary")
    summary: dict[str, Any] | None = None
    if isinstance(summary_raw, str) and summary_raw.strip() and not is_non_answer(summary_raw):
        basis = find_basis(task_run.basis, "day_summary")
        summary = {
            "value": summary_raw.strip(),
            "confidence": basis.confidence if basis else None,
            "reasoning": basis.reasoning if basis else "",
            "citations": [c.model_dump(mode="json") for c in basis.citations] if basis else [],
        }

    if not hours and summary is None:
        return None

    return {
        "task_run_id": task_run.id,
        "status": task_run.status,
        "replayed": task_run.replayed,
        "processor": task_run.processor,
        "researched_at": task_run.finished_at.isoformat() if task_run.finished_at else None,
        "shoot_day_id": task_run.shoot_day_id,
        "window": {"start_min": HOUR_START * 60, "end_min": HOUR_END * 60},
        "day_summary": summary,
        "hours": hours,
        "cited_hours": len([h for h in hours if h["citations"]]),
    }
