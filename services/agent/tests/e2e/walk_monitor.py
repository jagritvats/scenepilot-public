"""Drive the Monitor flow: simulate an event → draft card → confirm → recovery options.

Usage: uv run python tests/e2e/walk_monitor.py <out_dir> [base_url]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:3000"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: print("PAGE ERROR:", e))
    page.goto(BASE + "/projects/proj_nightfall/days/day_4", wait_until="networkidle")
    page.wait_for_selector("text=Watching the outside world", timeout=60000)
    page.get_by_role("button", name="Simulate a monitor event").first.click()
    page.wait_for_selector("text=detected by Parallel Monitor", timeout=30000)
    page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / "14_monitor_draft.png"), full_page=False)
    print("shot 14_monitor_draft")
    page.get_by_role("button", name="Confirm & plan recovery").first.click()
    t0 = time.time()
    page.wait_for_selector("text=Recovery options", timeout=420000)
    print(f"recovery options after {time.time() - t0:.1f}s")
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / "15_monitor_confirmed_recovery.png"), full_page=False)
    print("shot 15_monitor_confirmed_recovery")
    browser.close()
