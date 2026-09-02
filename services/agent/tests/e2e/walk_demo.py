"""Drive the hero demo in headless Chromium and capture screenshots.

Usage:  uv run python tests/e2e/walk_demo.py [out_dir] [base_url]
Requires the web app (3000) and agent service (8000) to be running.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data/screens")
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:3000"
OUT.mkdir(parents=True, exist_ok=True)


def shot(page, name: str, full: bool = True) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    print("shot", name)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page.on("pageerror", lambda e: print("PAGE ERROR:", e))
    page.on("console", lambda m: print("console:", m.type, m.text) if m.type in ("error", "warning") else None)

    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_selector("text=Project Nightfall", timeout=30000)
    shot(page, "01_projects")

    page.goto(BASE + "/projects/proj_nightfall", wait_until="networkidle")
    page.wait_for_selector("text=Shoot days", timeout=30000)
    shot(page, "02_project")

    page.goto(BASE + "/projects/proj_nightfall/scenes/sc_42", wait_until="networkidle")
    page.wait_for_selector("text=Requirements", timeout=30000)
    shot(page, "03_scene42")

    page.goto(BASE + "/projects/proj_nightfall/days/day_4", wait_until="networkidle")
    page.wait_for_selector("text=SHOOT DAY 4", timeout=30000)
    page.wait_for_selector("text=Rain expected 13:00", timeout=30000)
    shot(page, "04_day4_before")

    # trigger the hero disruption
    page.get_by_role("button", name="Rain expected 13:00–17:00").first.click()
    t0 = time.time()
    page.wait_for_selector("text=Recovery options", timeout=420000)
    print(f"recovery options after {time.time() - t0:.1f}s")
    page.wait_for_timeout(1500)
    shot(page, "05_day4_options")

    # open evidence drawer
    page.get_by_role("button", name="Evidence (", exact=False).first.click()
    page.wait_for_timeout(600)
    shot(page, "06_evidence_drawer", full=False)
    # Parallel Extract: open the first source in full
    open_btn = page.get_by_role("button", name="open source", exact=True).first
    if open_btn.count():
        open_btn.click()
        try:
            page.wait_for_selector("text=parallel · extract", timeout=90000)
            page.wait_for_timeout(800)
            shot(page, "06b_open_source", full=False)
        except Exception as exc:  # noqa: BLE001
            print("open source did not render:", exc)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # select a rejected option to show the rejection reason
    rejected = page.locator("button[aria-pressed]").filter(has_text="infeasible").first
    if rejected.count():
        rejected.click()
        page.wait_for_timeout(500)
        shot(page, "07_day4_rejected_option", full=False)
    # back to recommended and approve
    page.locator("button[aria-pressed]").filter(has_text="recommended").first.click()
    page.wait_for_timeout(400)
    approve = page.get_by_role("button", name="Approve recovery", exact=False).first
    approve.click()
    page.wait_for_selector("text=Coordinated actions", timeout=60000)
    page.wait_for_timeout(1500)
    shot(page, "08_day4_applied")
    page.get_by_role("button", name="before", exact=True).click()
    page.wait_for_timeout(1200)
    shot(page, "09_day4_before_toggle", full=False)

    browser.close()
print("done ->", OUT.resolve())
