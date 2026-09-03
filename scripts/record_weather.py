"""Record the hourly weather timelines the demo replays, and nothing else.

The P0.3 paid session, reduced to the one call it still owed. This deliberately does **not** go
through the API: the endpoint is correct, but a running service also serves polls, warm-seeds on
boot and shares one `call_budget`, and every one of those is a way for a recording session to write
a file nobody asked for. Two Task calls, made directly, with the recordings directory diffed either
side.

Run it with the environment stated explicitly, never from the ambient `.env`:

    SCENEPILOT_MODE=live SCENEPILOT_RECORD=1 SCENEPILOT_PARALLEL_TASK=1 \
      uv run python ../../scripts/record_weather.py

Key stability is what makes the resulting fixture worth committing: `build_weather_input` prints the
date only as `YYYY-MM-DD`, which `normalize` masks, so the key does not rot when the seed re-anchors
the hero day onto today. `test_weather.py` pins both that and the fact that the prompt carries
`production day {n}` — without it every day hashes identically and the second recording silently
overwrites the first.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "agent"))

RECORDINGS = REPO_ROOT / "services/agent/scenepilot/seed/fixtures/recordings"
DAYS = ("day_4", "day_6")


def _dirty() -> set[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain", str(RECORDINGS)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return {line[3:].strip() for line in out.splitlines() if line.strip()}


def main() -> int:
    from scenepilot.config import settings as base
    from scenepilot.seed.nightfall import PROJECT_ID, build_project
    from scenepilot.tools.parallel_task import ParallelTaskTool

    before = _dirty()
    if before:
        print("REFUSING: the recordings directory already has uncommitted changes:")
        for f in sorted(before):
            print(f"  {f}")
        print("Commit or restore them first, so what this session writes is unambiguous.")
        return 1

    settings = replace(base, mode="live", record=True, parallel_task_enabled=True)
    if not settings.parallel_configured:
        print("REFUSING: PARALLEL_API_KEY is not set.")
        return 1
    print(f"mode={settings.mode} record={settings.record} processor={settings.parallel_task_processor}")

    project = build_project()
    assert project.id == PROJECT_ID
    tool = ParallelTaskTool(project, settings=settings, on_event=lambda k, m, meta: print(f"  [{k}] {m}"))

    for day_id in DAYS:
        day = project.shoot_day(day_id)
        print(f"\n--- Day {day.day_number} ({day.date}) ---")
        run = tool.weather_timeline(day)
        hours = len(getattr(run, "output", {}) or {}) if isinstance(getattr(run, "output", None), dict) else "?"
        print(f"  status={run.status} purpose={run.purpose} shoot_day={run.shoot_day_id} fields={hours}")
        if run.status not in {"OK", "REPLAY"}:
            print(f"  FAILED: {getattr(run, 'error', None)}")
            return 1

    after = _dirty() - before
    print(f"\nnew or changed recordings ({len(after)}):")
    for f in sorted(after):
        print(f"  {f}")
    if len(after) != len(DAYS):
        print(f"WARNING: expected exactly {len(DAYS)} new files, got {len(after)}. Inspect before committing.")
        return 1
    print("\nExactly the two expected fixtures were written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
