"""Capture a screenshot of every demo surface, to catch empty states.

The point is to catch shots that do not exist yet — an empty state where a demo expects a
populated one is a walkthrough wasted.

Usage: uv run python tests/e2e/capture_trailer_shots.py <out_dir> [base_url]

The API is reached through `base_url` rather than at a hardcoded localhost, so pointing this at the
hosted instance actually exercises the hosted instance — it used to reset and inspect a local service
while screenshotting a remote one, which is a green report about the wrong deployment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
BASE = (sys.argv[2] if len(sys.argv) > 2 else "http://localhost:3000").rstrip("/")
# The Next proxy forwards /api to the agent service, so the app's own origin is the API too.
API = BASE
OUT.mkdir(parents=True, exist_ok=True)

with httpx.Client(base_url=API, timeout=300) as c:
    c.post("/api/projects/proj_nightfall/reset")
    health = c.get("/api/health").json()
    print("mode:", health["mode"], "| features:", {k: v["enabled"] for k, v in health.get("parallel_features", {}).items()})
    # what a fresh instance actually has before anyone clicks anything
    scr = c.get("/api/projects/proj_nightfall/screenplay/scenes")
    print("screenplay scenes on a fresh instance:", scr.json().get("count") if scr.status_code == 200 else f"HTTP {scr.status_code}")
    dood = c.get("/api/projects/proj_nightfall/dood")
    print("DOOD entries:", len(dood.json().get("entries", [])) if dood.status_code == 200 else f"HTTP {dood.status_code}")

SHOTS = [
    ("A_home", "/"),
    ("B_project", "/projects/proj_nightfall"),
    ("C_screenplay_fresh", "/projects/proj_nightfall/screenplay"),
    ("D_day4", "/projects/proj_nightfall/days/day_4"),
    ("E_day6_night", "/projects/proj_nightfall/days/day_6"),
    ("F_callsheet", "/projects/proj_nightfall/days/day_4/call-sheet"),
    ("G_dood", "/projects/proj_nightfall/screenplay#dood"),
    ("H_production_log", "/projects/proj_nightfall/log"),
]

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page.on("pageerror", lambda e: print("  PAGE ERROR:", e))

    for name, path in SHOTS:
        page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
        text = page.inner_text("body")
        print(f"{name:<22} {len(text):>6} chars")

    # Buttons the script tells the recorder to click — do they exist?
    page.goto(f"{BASE}/projects/proj_nightfall/days/day_4", wait_until="networkidle")
    page.wait_for_timeout(1000)
    for label in [
        "Parallel Intelligence", "Parallel Console", "Broadcast Call Sheet", "Simulate a monitor event",
        "Create live monitors", "Research this location", "Re-verify Day 4", "Simulate a change",
        "Find replacements", "Recall", "Tour", "Day operations",
    ]:
        n = page.get_by_role("button", name=label).count() + page.get_by_text(label, exact=False).count()
        print(f"  button/text {label!r:<32} {'FOUND' if n else 'MISSING'}")

    # Controls that live on other surfaces. Probed where they actually are, because a label checked
    # on the wrong page reports MISSING for something that is present and working.
    for path, labels in [
        ("/projects/proj_nightfall", ["Production log", "DOOD Cast Matrix", "Screenplay Studio"]),
        ("/projects/proj_nightfall/days/day_4/call-sheet", ["Show one-liner", "Force Majeure claim packet", "Print / PDF"]),
        ("/projects/proj_nightfall/screenplay", ["Load Hero Screenplay"]),
    ]:
        page.goto(f"{BASE}{path}", wait_until="networkidle")
        page.wait_for_timeout(800)
        for label in labels:
            n = page.get_by_role("button", name=label).count() + page.get_by_text(label, exact=False).count()
            print(f"  {path.split('/')[-1] or 'home':<12} {label!r:<32} {'FOUND' if n else 'MISSING'}")
    browser.close()
