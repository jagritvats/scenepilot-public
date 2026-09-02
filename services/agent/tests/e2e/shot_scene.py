"""Screenshot the Scene 42 readiness page once its planning run has completed.

Usage: uv run python tests/e2e/shot_scene.py <out_dir> [base_url]
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
    page.goto(BASE + "/projects/proj_nightfall/scenes/sc_42", wait_until="networkidle")
    page.wait_for_selector("text=Recommended approach", timeout=60000)
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / "10_scene42_planned.png"), full_page=True)
    print("shot 10_scene42_planned")
    page.get_by_role("button", name="Evidence (", exact=False).first.click()
    page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / "11_scene42_evidence.png"), full_page=False)
    print("shot 11_scene42_evidence")
    open_btn = page.get_by_role("button", name="open source", exact=True).first
    if open_btn.count():
        open_btn.click()
        try:
            page.wait_for_selector("text=parallel · extract", timeout=90000)
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / "11b_scene42_open_source.png"), full_page=False)
            print("shot 11b_scene42_open_source")
        except Exception as exc:  # noqa: BLE001
            print("open source did not render:", exc)
    page.keyboard.press("Escape")
    page.goto(BASE + "/projects/proj_nightfall", wait_until="networkidle")
    page.wait_for_selector("text=Shoot days", timeout=30000)
    page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / "12_project_after.png"), full_page=True)
    print("shot 12_project_after")
    browser.close()
