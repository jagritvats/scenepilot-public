"""Time-of-day arithmetic. Times are 'HH:MM' strings; HH may exceed 23 for night shoots."""

from __future__ import annotations

import re

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def to_minutes(value: str) -> int:
    m = _TIME_RE.match(value.strip())
    if not m:
        raise ValueError(f"Invalid time {value!r}; expected HH:MM")
    hours, minutes = int(m.group(1)), int(m.group(2))
    if minutes >= 60:
        raise ValueError(f"Invalid minutes in {value!r}")
    return hours * 60 + minutes


def to_hhmm(minutes: int) -> str:
    minutes = max(0, int(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def overlap_minutes(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """Minutes of overlap between half-open intervals [a_start, a_end) and [b_start, b_end)."""
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return overlap_minutes(a_start, a_end, b_start, b_end) > 0


def covers(outer_start: int, outer_end: int, inner_start: int, inner_end: int) -> bool:
    return outer_start <= inner_start and inner_end <= outer_end


def duration(start: str, end: str) -> int:
    return to_minutes(end) - to_minutes(start)


def fmt_range(start: str, end: str) -> str:
    return f"{start}–{end}"
