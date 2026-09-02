"""Make record/replay keys stable across runs.

Prompts and requests embed per-run identifiers (search_…, ev_…, rq_…) and dates. For
recording keys we replace them with positional placeholders; for replayed Gemini outputs
we map the placeholders back to the *current* run's identifiers (same order of first
appearance), so evidence references still resolve.

The same problem, one step removed: a shoot day's lighting windows are *computed from its date*
(`services/ephemeris.apply_solar_windows`), so a prompt that prints them carries a number that moves
every night. Keyed raw, a recording made on Tuesday misses on Wednesday and a paid re-record is
worthless by the weekend. So the key also placeholders the sun.

The rule, and its limit: a clock time is placeholdered only when the text **names it as a reading of
the sun** — "golden hour 18:25–19:08", "outside usable daylight (06:23–18:54)", "before darkness
(19:17)", a bare sunrise/sunset/twilight. Anchoring on the label, never on the value, is what keeps
this from eating the schedule: 13:00–17:00 is a rain window, 06:30 is the unit call, 13:00–18:00 is
a permit — production decisions, semantically part of the question, and a recording that replayed
across a change in any of them would be answering a different one. Minute *counts* measured against
a solar window ("gets 35 min of golden hour instead of 43") drift for exactly the same reason and are
placeholdered on the same labels.

Not placeholdered, deliberately: clock times the scheduler *chose*, even where the sun influenced
the choice — a DAY scene pushed to sunrise moves its start time, and a season's worth of that is a
materially different schedule to reason about, not the same question re-asked.
"""

from __future__ import annotations

import re

ID_RE = re.compile(r"\b(search|extract|ev|opt|dis|run|cs|act|plan|evt|risk|cand|brief)_([0-9a-f]{10})\b")
RQ_RE = re.compile(r"\brq_([0-9a-f]{6})(?=_\d)")
DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
PLACEHOLDER_RE = re.compile(r"\b(search|extract|ev|opt|dis|run|cs|act|plan|evt|risk|cand|brief)_@(\d+)\b")
RQ_PLACEHOLDER = "rq_@@@@@@"
DATE_PLACEHOLDER = "@DATE@"
SOLAR_PLACEHOLDER = "@SOLAR@"

_HHMM = r"\d{1,2}:\d{2}"  # HH may exceed 23: a night window ends at 30:01
_DASH = r"[–—-]"
_SUN_NOUN = r"sunrise|sunset|solar noon|civil twilight(?: dawn| dusk)?|nautical twilight(?: dawn| dusk)?|first light|last light"

# Each pattern's *capture groups are the text to keep*; everything the match covers between and
# after them is the ephemeris reading, and becomes `@SOLAR@`. Every entry corresponds to a place the
# codebase prints a value derived from `SOLAR_WINDOW_FIELDS` — the specific ones first, so a general
# pattern cannot swallow the quantity a specific one still has to mask.
SOLAR_RES: tuple[re.Pattern[str], ...] = (
    # services/schedule.lighting_check, SUNSET/DAWN hard: "…overlaps golden hour (18:25–19:08) by only 4 min"
    re.compile(rf"(overlaps golden hour \(){_HHMM}({_DASH}){_HHMM}(\) by only )\d+( min)", re.IGNORECASE),
    # …SUNSET/DAWN soft: "…gets 35 min of golden hour instead of 43"
    re.compile(r"(gets )\d+( min of golden hour instead of )\d+", re.IGNORECASE),
    # …DAY hard: "…has 90 min outside usable daylight (06:23–18:54)"
    re.compile(rf"(has )\d+( min outside usable daylight \(){_HHMM}({_DASH}){_HHMM}(\))", re.IGNORECASE),
    # …DAY soft: "…runs 4 min past usable daylight"
    re.compile(r"(runs )\d+( min past usable daylight)", re.IGNORECASE),
    # …NIGHT hard: "…has 30 min before darkness (19:17)"
    re.compile(rf"(has )\d+( min before darkness \(){_HHMM}(\))", re.IGNORECASE),
    # …NIGHT soft: "…starts 12 min before full darkness"
    re.compile(r"(starts )\d+( min before full darkness)", re.IGNORECASE),
    # workflows/rescue._constraints_block and services/callsheet: "golden hour 18:25–19:08"
    re.compile(rf"(golden hour \(?){_HHMM}({_DASH}){_HHMM}", re.IGNORECASE),
    re.compile(rf"(usable daylight \(){_HHMM}({_DASH}){_HHMM}(\))", re.IGNORECASE),
    re.compile(rf"(before darkness \(){_HHMM}(\))", re.IGNORECASE),
    # Any ephemeris noun printed with its own time, wherever a future prompt puts one. The connector
    # is deliberately tiny so "SUNSET scene 42 … 17:30" — a scene's slot, not the sun's — cannot match.
    re.compile(rf"(\b(?:{_SUN_NOUN})\b[ :=]{{0,3}}(?:at |is )?){_HHMM}(?:({_DASH}){_HHMM})?", re.IGNORECASE),
)


def id_order(text: str) -> list[str]:
    """Run-specific ids in order of first appearance."""
    seen: list[str] = []
    for m in ID_RE.finditer(text):
        ident = m.group(0)
        if ident not in seen:
            seen.append(ident)
    return seen


def solar(text: str) -> str:
    """Replace labelled readings of the day's sun with `@SOLAR@`, leaving every other time alone."""

    def repl(m: re.Match) -> str:
        out: list[str] = []
        cursor = m.start()
        for i in range(1, (m.lastindex or 0) + 1):
            start, end = m.span(i)
            if start < 0:
                continue
            if start > cursor:
                out.append(SOLAR_PLACEHOLDER)
            out.append(m.group(i))
            cursor = end
        if m.end() > cursor:
            out.append(SOLAR_PLACEHOLDER)
        return "".join(out)

    for rx in SOLAR_RES:
        text = rx.sub(repl, text)
    return text


def normalize(text: str, order: list[str] | None = None, dates: bool = True) -> str:
    """Replace run-specific ids (and, unless `dates=False`, date-derived values) with placeholders.

    `dates=False` is how recorded *output* is stored: what a human reads back in the evidence drawer
    has to be the real date and the real sun, so only the ids are placeholdered there.
    """
    order = order if order is not None else id_order(text)
    index = {ident: i for i, ident in enumerate(order)}

    def repl(m: re.Match) -> str:
        return f"{m.group(1)}_@{index.get(m.group(0), 999)}"

    out = ID_RE.sub(repl, text)
    out = RQ_RE.sub(RQ_PLACEHOLDER, out)
    if dates:
        out = DATE_RE.sub(DATE_PLACEHOLDER, out)
        out = solar(out)
    return out


def denormalize(text: str, order: list[str], rq_run_id: str | None = None) -> str:
    """Map placeholders back to the current run's ids (positional)."""

    def repl(m: re.Match) -> str:
        i = int(m.group(2))
        return order[i] if i < len(order) else m.group(0)

    out = PLACEHOLDER_RE.sub(repl, text)
    if rq_run_id:
        out = out.replace(RQ_PLACEHOLDER, f"rq_{rq_run_id}")
    return out


def rq_run_id(text: str) -> str | None:
    m = RQ_RE.search(text)
    return m.group(1) if m else None
