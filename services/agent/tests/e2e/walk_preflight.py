"""The pre-flight beat: the producer asks, the night before, whether the rules still hold.

Drives: dossier (replayed) → accept the noise curfew → the redesigned dossier panel → a pre-flight
re-check of the day's locations.

Usage: uv run python tests/e2e/walk_preflight.py <out_dir> [base_url]
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:3000"
API = "http://localhost:8000"
OUT.mkdir(parents=True, exist_ok=True)

with httpx.Client(base_url=API, timeout=300) as c:
    c.post("/api/projects/proj_nightfall/reset")
    c.post("/api/projects/proj_nightfall/resources/loc_rooftop/dossier")
    facts = c.get("/api/projects/proj_nightfall/dossiers").json()["facts"]
    curfew = next(f for f in facts if f["key"] == "noise_curfew")
    c.post(f"/api/projects/proj_nightfall/facts/{curfew['id']}/accept")
    print(f"{len(facts)} facts; accepted curfew {curfew['value']}")

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=2)
    page.on("pageerror", lambda e: print("PAGE ERROR:", e))

    page.goto(f"{BASE}/projects/proj_nightfall/days/day_6", wait_until="networkidle")
    page.wait_for_selector("text=What the real world forbids", timeout=60000)
    page.wait_for_timeout(1000)
    panel = page.locator("section").filter(has_text="What the real world forbids").first
    panel.screenshot(path=str(OUT / "21_panel_collapsed.png"))
    print("shot 21_panel_collapsed")

    # the advisory tier, on request
    page.get_by_role("button", name="Advisory").first.click()
    page.wait_for_timeout(500)
    panel.screenshot(path=str(OUT / "22_panel_advisory_open.png"))
    print("shot 22_panel_advisory_open")
    page.get_by_role("button", name="Advisory").first.click()
    page.wait_for_timeout(300)

    # pre-flight: re-ask Parallel whether the rules still hold
    page.get_by_role("button", name="Re-verify Day 6").first.click()
    page.wait_for_timeout(6000)
    panel.screenshot(path=str(OUT / "23_preflight.png"))
    print("shot 23_preflight")
    browser.close()
