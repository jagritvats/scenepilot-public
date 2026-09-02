"""The snapshot-monitor beat: a rule the production accepted stops being the rule.

Drives: dossier (replayed) → accept the 22:00 noise curfew → a Parallel snapshot monitor reports it
has moved to 21:00 → the pending change → adopt → the fact waits to be signed off again.

Usage: uv run python tests/e2e/walk_fact_change.py <out_dir> [base_url]
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
    print("accepted curfew:", curfew["value"], "->", curfew["citations"][0]["url"][:60])

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.on("pageerror", lambda e: print("PAGE ERROR:", e))

    page.goto(f"{BASE}/projects/proj_nightfall/days/day_6", wait_until="networkidle")
    page.wait_for_selector("text=What the real world forbids", timeout=60000)
    page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / "18_rule_enforced.png"), full_page=False)
    print("shot 18_rule_enforced")

    # Parallel re-runs the dossier and reports only what moved
    page.get_by_role("button", name="Simulate a change").first.click()
    page.wait_for_selector("text=Your schedule is being enforced against the old value", timeout=30000)
    page.wait_for_timeout(600)
    card = page.get_by_text("Your schedule is being enforced against the old value").locator("..")
    card.screenshot(path=str(OUT / "19_change_detected.png"))
    page.screenshot(path=str(OUT / "19_change_detected_full.png"), full_page=True)
    print("shot 19_change_detected")

    # adopting takes the value but not the acceptance
    page.get_by_role("button", name="Adopt the new value").first.click()
    page.wait_for_timeout(1500)
    page.screenshot(path=str(OUT / "20_awaiting_re_acceptance.png"), full_page=True)
    print("shot 20_awaiting_re_acceptance")
    browser.close()
