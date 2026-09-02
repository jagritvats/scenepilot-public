"""Two reads a producer had no way to make, and one decision they had no way to unmake.

A monitor firing on Day 6 while the producer reads Day 4 announced itself to nobody: drafts were
reachable only through the day page's monitor panel, which renders one day at a time. And a chosen
replacement vendor was a chip on a card forever — `select_vendor` set one and cleared the rest, so
there was no route back to *none chosen*.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scenepilot.seed.nightfall import DAY4_ID, DAY6_ID, PROJECT_ID

P = f"/api/projects/{PROJECT_ID}"


@pytest.fixture()
def client(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    monkeypatch.setattr(app_module, "repo", repo)
    with TestClient(app_module.app) as c:
        c.repo = repo  # type: ignore[attr-defined]
        yield c


def test_a_production_with_no_monitor_events_has_an_empty_feed(client):
    assert client.get(f"{P}/draft-disruptions").json()["drafts"] == []


def test_a_draft_raised_on_one_day_is_visible_from_the_production(client):
    """The whole point: it is found without already being on the day it belongs to."""
    client.post(f"{P}/shoot-days/{DAY6_ID}/monitors/simulate", params={"kind": "WEATHER"})

    drafts = client.get(f"{P}/draft-disruptions").json()["drafts"]
    assert len(drafts) == 1
    row = drafts[0]
    assert row["shoot_day_id"] == DAY6_ID and row["day_number"] == 6
    assert row["date"] and row["monitor_id"]
    assert row["detected_at"], "when we ingested it, not the date the event is about"
    assert row["disruption"]["draft"] is True


def test_a_confirmed_draft_leaves_the_feed(client):
    client.post(f"{P}/shoot-days/{DAY4_ID}/monitors/simulate", params={"kind": "WEATHER"})
    draft_id = client.get(f"{P}/draft-disruptions").json()["drafts"][0]["disruption"]["id"]

    confirmed = client.post(f"{P}/disruptions/{draft_id}/confirm", json={"window_start": "13:00", "window_end": "17:00"})
    assert confirmed.status_code == 200
    assert client.get(f"{P}/draft-disruptions").json()["drafts"] == []


def test_a_dismissed_draft_leaves_the_feed(client):
    client.post(f"{P}/shoot-days/{DAY4_ID}/monitors/simulate", params={"kind": "WEATHER"})
    draft_id = client.get(f"{P}/draft-disruptions").json()["drafts"][0]["disruption"]["id"]

    assert client.post(f"{P}/disruptions/{draft_id}/dismiss").status_code == 200
    assert client.get(f"{P}/draft-disruptions").json()["drafts"] == []


def _findall_with_a_selection(client):
    """A recorded FindAll run with one vendor chosen, built directly — the search itself is paid."""
    from scenepilot.domain.models import FindAllRun, VendorCandidate

    fr = FindAllRun(project_id=PROJECT_ID, resource_id="eq_crane", status="OK")
    fr.candidates = [
        VendorCandidate(findall_run_id=fr.id, name="Mumbai Grip House", url="https://example.test/a"),
        VendorCandidate(findall_run_id=fr.id, name="Andheri Cranes", url="https://example.test/b"),
    ]
    client.repo.save_findall_run(fr)
    chosen = fr.candidates[0].id
    assert client.post(f"/api/findall-runs/{fr.id}/select/{chosen}").status_code == 200
    return fr.id, chosen


def test_a_vendor_choice_can_be_taken_back(client):
    run_id, chosen = _findall_with_a_selection(client)
    assert any(v["selected"] for v in client.repo.get_findall_run(run_id).model_dump()["candidates"])

    body = client.request("DELETE", f"/api/findall-runs/{run_id}/select").json()
    assert not any(v["selected"] for v in body["findall_run"]["candidates"])

    log = client.get(f"{P}/activity").json()
    events = log if isinstance(log, list) else log["events"]
    assert any("withdrew" in e["message"] for e in events)


def test_withdrawing_nothing_is_refused_rather_than_silently_fine(client):
    from scenepilot.domain.models import FindAllRun, VendorCandidate

    fr = FindAllRun(project_id=PROJECT_ID, resource_id="eq_crane", status="OK")
    fr.candidates = [VendorCandidate(findall_run_id=fr.id, name="Nobody Chosen", url="https://example.test/c")]
    client.repo.save_findall_run(fr)
    r = client.request("DELETE", f"/api/findall-runs/{fr.id}/select")
    assert r.status_code == 409 and "nothing to take back" in r.json()["detail"]
