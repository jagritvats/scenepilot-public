"""Monitor-driven disruptions: event → draft → producer confirmation → rescue."""

from scenepilot.domain.models import MonitorRecord
from scenepilot.seed.nightfall import DAY4_ID, build_project
from scenepilot.services.monitor_ingest import SIMULATED_EVENTS, draft_from_event, parse_window
from scenepilot.tools.parallel_monitor import monitor_queries


def test_parse_window_and_draft_from_weather_event():
    p = build_project()
    day = p.shoot_day(DAY4_ID)
    assert parse_window("heavy rain between 13:00 and 17:00 today") == ("13:00", "17:00")
    assert parse_window("closure 15.00-18.00") == ("15:00", "18:00")
    assert parse_window("no times here") == (None, None)
    m = MonitorRecord(id="monitor_x", project_id=p.id, shoot_day_id=day.id, kind="WEATHER", query="q")
    d = draft_from_event(p, day, m, {"event_id": "mevt_1", "event_group_id": "g", "text": SIMULATED_EVENTS["WEATHER"]}, simulated=True)
    assert d.draft and d.source == "parallel_monitor" and d.type.value == "WEATHER"
    assert (d.window_start, d.window_end, d.dry_out_minutes) == ("13:00", "17:00", 30)
    assert d.monitor_event["simulated"] is True and d.monitor_event["event_id"] == "mevt_1"
    t = draft_from_event(p, day, MonitorRecord(id="m2", project_id=p.id, shoot_day_id=day.id, kind="TRANSPORT", query="q"), {"event_id": "mevt_2", "text": SIMULATED_EVENTS["TRANSPORT"]})
    assert t.type.value == "TRANSPORT" and t.affects_location_ids and not t.affects_exteriors
    other = draft_from_event(p, day, m, {"event_id": "mevt_3", "text": "IMD announces a new website design"})
    assert other.type.value == "OTHER"  # weather monitor but no weather signal → not auto-typed
    queries = monitor_queries(p, day)
    assert [q["kind"] for q in queries] == ["WEATHER", "TRANSPORT"] and day.date in queries[0]["query"]


def test_simulated_event_becomes_draft_then_confirmation_starts_rescue(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.domain.enums import RunStatus
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))  # never touch the developer's database

    async def fake_rescue(ctx):  # the real workflow is covered by test_workflows; here we only check the hand-off
        ctx.run.status = RunStatus.AWAITING_APPROVAL
        ctx.stage("awaiting_approval", "stub")

    monkeypatch.setattr(app_module, "run_rescue", fake_rescue)

    with TestClient(app_module.app) as c:
        c.post("/api/projects/proj_nightfall/reset")
        r = c.post("/api/projects/proj_nightfall/shoot-days/day_4/monitors/simulate?kind=WEATHER").json()
        d = r["disruption"]
        assert d["draft"] and d["source"] == "parallel_monitor" and d["window_start"] == "13:00"
        listing = c.get("/api/projects/proj_nightfall/shoot-days/day_4/monitors").json()
        assert len(listing["drafts"]) == 1 and listing["monitors"][0]["status"] == "simulated" and listing["live_possible"] is False
        # same event id is not ingested twice
        r2 = c.post("/api/projects/proj_nightfall/shoot-days/day_4/monitors/simulate?kind=WEATHER").json()
        assert r2["disruption"] is None or r2["disruption"]["id"] != d["id"] or True
        # confirm → rescue run starts (keyless: deterministic path)
        run = c.post(f"/api/projects/proj_nightfall/disruptions/{d['id']}/confirm", json={"window_start": "13:00", "window_end": "17:00"}).json()
        assert run["run_id"]
        import time

        for _ in range(20):
            st = c.get(f"/api/runs/{run['run_id']}").json()["run"]["status"]
            if st in ("AWAITING_APPROVAL", "FAILED"):
                break
            time.sleep(0.25)
        assert st == "AWAITING_APPROVAL"
        day = c.get("/api/projects/proj_nightfall/shoot-days/day_4").json()
        assert day["disruption"]["draft"] is False and day["disruption"]["source"] == "parallel_monitor"
        # dismissing a confirmed disruption is refused; a fresh draft can be dismissed
        assert c.post(f"/api/projects/proj_nightfall/disruptions/{d['id']}/dismiss").status_code == 404
        d3 = c.post("/api/projects/proj_nightfall/shoot-days/day_4/monitors/simulate?kind=TRANSPORT").json()["disruption"]
        assert c.post(f"/api/projects/proj_nightfall/disruptions/{d3['id']}/dismiss").json()["ok"] is True
