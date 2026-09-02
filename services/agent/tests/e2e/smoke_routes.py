"""Every page loads without a runtime error. Cheap insurance before a deploy.

Usage: uv run python tests/e2e/smoke_routes.py [base_url]

The API is reached *through* `base_url`, not at a hardcoded localhost:8000. That mattered the moment
this was pointed at the hosted instance: it went on resetting and reading a local service while
driving a remote browser, so it reported a green run against state the hosted app never had.
"""

from __future__ import annotations

import sys

import httpx
from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000").rstrip("/")
# The Next proxy forwards /api to the agent service, so the app's own origin is the API too.
API = BASE

with httpx.Client(base_url=API, timeout=120) as c:
    c.post("/api/projects/proj_nightfall/reset")
    scenes = c.get("/api/projects/proj_nightfall").json()["project"]["scenes"]
    scene_id = scenes[0]["id"]

ROUTES = [
    ("home", "/"),
    ("project", "/projects/proj_nightfall"),
    ("day 4", "/projects/proj_nightfall/days/day_4"),
    ("day 6", "/projects/proj_nightfall/days/day_6"),
    ("call sheet", "/projects/proj_nightfall/days/day_4/call-sheet"),
    ("screenplay", "/projects/proj_nightfall/screenplay"),
    ("dood", "/projects/proj_nightfall/screenplay#dood"),
    ("production log", "/projects/proj_nightfall/log"),
    ("fact drift inbox", "/projects/proj_nightfall/inbox"),
    ("risk register", "/projects/proj_nightfall/risks"),
    ("sides", "/projects/proj_nightfall/days/day_4/sides"),
    ("movement order", "/projects/proj_nightfall/days/day_4/movement-order"),
    # The DPR is issued only for a wrapped day; day 4 is meant to refuse it, and day 3 to serve it.
    ("daily production report", "/projects/proj_nightfall/days/day_3/dpr"),
    ("dpr refused on an unwrapped day", "/projects/proj_nightfall/days/day_4/dpr"),
    ("scene", f"/projects/proj_nightfall/scenes/{scene_id}"),
    ("not found", "/projects/proj_nightfall/days/does_not_exist"),
]

failures: list[str] = []
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for name, path in ROUTES:
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        try:
            resp = page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=60000)
            status = resp.status if resp else 0
            page.wait_for_timeout(1200)
            body = (page.inner_text("body") or "").strip()
            bad = [e for e in errors if "favicon" not in e.lower()]
            ok = status < 400 and len(body) > 40 and not bad
            print(f"{'OK  ' if ok else 'FAIL'} {name:<12} {status} chars={len(body):<6} {('| ' + bad[0][:110]) if bad else ''}")
            if not ok:
                failures.append(f"{name} ({path}): status={status} errors={bad[:2]}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {name:<12} threw {exc}")
            failures.append(f"{name} ({path}): {exc}")
        finally:
            page.close()
    browser.close()

print("\n" + ("ALL ROUTES OK" if not failures else f"{len(failures)} ROUTE(S) FAILED"))
for f in failures:
    print(" -", f)
sys.exit(1 if failures else 0)
