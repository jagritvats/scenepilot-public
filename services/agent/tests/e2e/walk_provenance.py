"""The provenance beat: a cited web rule rejects a schedule option.

Drives: dossier (replayed) → accept the noise curfew → a night-unit disruption → the rejected
option's chain back to the page Parallel cited.

Usage: uv run python tests/e2e/walk_provenance.py <out_dir> [base_url]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:3000"
API = "http://localhost:8000"
OUT.mkdir(parents=True, exist_ok=True)

DISRUPTION = {
    "type": "EQUIPMENT_FAILURE",
    "title": "Camera rig hydraulics fault until 20:00",
    "description": "The bike camera rig is down for repair; the vendor can have it back on the roof by 20:00.",
    "window_start": "16:00",
    "window_end": "20:00",
    "affects_exteriors": False,
    "affects_resource_ids": ["eq_bike"],
}

with httpx.Client(base_url=API, timeout=300) as c:
    c.post("/api/projects/proj_nightfall/reset")
    c.post("/api/projects/proj_nightfall/resources/loc_rooftop/dossier")
    facts = c.get("/api/projects/proj_nightfall/dossiers").json()["facts"]
    curfew = next(f for f in facts if f["key"] == "noise_curfew")
    print("curfew fact:", curfew["id"], curfew["value"], "->", curfew["citations"][0]["url"][:60])

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("pageerror", lambda e: print("PAGE ERROR:", e))

    # the dossier panel, before the producer decides
    page.goto(f"{BASE}/projects/proj_nightfall/days/day_6", wait_until="networkidle")
    page.wait_for_selector("text=What the real world forbids", timeout=60000)
    page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / "16_dossier_facts.png"), full_page=False)
    print("shot 16_dossier_facts")

    # accept the curfew as a hard constraint
    page.get_by_role("button", name="Accept as a hard constraint").first.click()
    page.wait_for_timeout(1500)

    with httpx.Client(base_url=API, timeout=300) as c:
        run_id = c.post("/api/projects/proj_nightfall/shoot-days/day_6/disruptions", json=DISRUPTION).json()["run_id"]
        for _ in range(60):
            if c.get(f"/api/runs/{run_id}").json()["run"]["status"] in ("AWAITING_APPROVAL", "FAILED"):
                break
            time.sleep(2)
        print("run:", run_id, c.get(f"/api/runs/{run_id}").json()["run"]["status"])

    page.reload(wait_until="networkidle")
    page.wait_for_selector("text=Recovery options", timeout=120000)
    page.wait_for_timeout(1200)

    # select the rejected option so its chain renders
    rejected = page.locator("button[aria-pressed]").filter(has_text="infeasible").first
    if rejected.count():
        rejected.click()
        page.wait_for_selector("text=Why this was rejected", timeout=30000)
        page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / "17_provenance_chain.png"), full_page=True)
    print("shot 17_provenance_chain")
    browser.close()
