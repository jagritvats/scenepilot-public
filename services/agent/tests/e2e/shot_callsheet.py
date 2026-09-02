"""Screenshot the regenerated call sheet (before/after) for Day 4.

Usage: uv run python tests/e2e/shot_callsheet.py <out_dir> [base_url]
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:3000"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: print("PAGE ERROR:", e))
    page.goto(BASE + "/projects/proj_nightfall/days/day_4/call-sheet", wait_until="networkidle")
    page.wait_for_selector("text=Shooting schedule", timeout=60000)
    page.wait_for_timeout(1000)
    page.screenshot(path=str(OUT / "13_call_sheet.png"), full_page=True)
    print("shot 13_call_sheet")
    browser.close()
