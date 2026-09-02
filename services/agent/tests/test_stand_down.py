"""Ending a rescue without taking any of it.

This was the only state in the product a producer could enter and not leave. Reporting a disruption
drives the run to AWAITING_APPROVAL; `approve` refuses an option that is not feasible; and the day
page hides the fixture picker and the manual entry form for as long as a disruption is live. A
disruption no legal schedule survives therefore left the day holding a recommendation it could not
take, with one button reading "Option D cannot be approved" and no other way forward. The only exit
was `POST /reset`, which discards the whole production.

Standing down is not a revert. A revert un-applies a change already on the schedule; nothing is
applied here, and what ends is the asking.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from scenepilot.domain.enums import ScheduleItemStatus, ShootDayStatus
from scenepilot.seed.nightfall import DAY4_ID, PROJECT_ID

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


def _awaiting(client, fixture: str = "rain_pm") -> dict:
    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": fixture})
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    for _ in range(60):
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in ("AWAITING_APPROVAL", "FAILED", "COMPLETED"):
            break
        time.sleep(0.25)
    assert run["status"] == "AWAITING_APPROVAL", run.get("error")
    return run


def _day(client) -> dict:
    return client.get(f"{P}/shoot-days/{DAY4_ID}").json()


def test_standing_down_hands_the_day_back_whole(client):
    before = _day(client)["day"]
    run = _awaiting(client)
    held = _day(client)["day"]
    assert held["status"] == ShootDayStatus.RECOVERY_PROPOSED.value
    assert held["active_disruption_id"]
    assert any(i["status"] == ScheduleItemStatus.AT_RISK.value for i in held["items"]), "the rain marks strips"

    r = client.post(f"/api/runs/{run['id']}/stand-down", json={"reason": "the cell passed north of us"})
    assert r.status_code == 200, r.text

    after = _day(client)["day"]
    assert after["status"] == before["status"]
    assert after["active_disruption_id"] is None
    # The half the workflow's own release never had: a strip left orange is a day that reads healthy
    # at the top and alarmed on every row.
    assert not [i for i in after["items"] if i["status"] == ScheduleItemStatus.AT_RISK.value]
    assert [(i["scene_id"], i["start"], i["end"]) for i in after["items"]] == [(i["scene_id"], i["start"], i["end"]) for i in before["items"]]


def test_the_run_ends_completed_and_says_why(client):
    run = _awaiting(client)
    body = client.post(f"/api/runs/{run['id']}/stand-down", json={"reason": "we are shooting through it"}).json()
    assert body["run"]["status"] == "COMPLETED"
    rescue = body["run"]["rescue"]
    assert rescue["stood_down_reason"] == "we are shooting through it"
    assert rescue["stood_down_by"] == "producer" and rescue["stood_down_at"]
    assert rescue["no_impact_reason"] is None, "the engine found plenty; a person declined it"


def test_the_options_are_kept_as_the_record_of_what_was_declined(client):
    run = _awaiting(client)
    offered = [o["id"] for o in run["rescue"]["options"]]
    body = client.post(f"/api/runs/{run['id']}/stand-down", json={"reason": "no"}).json()
    assert [o["id"] for o in body["run"]["rescue"]["options"]] == offered


def test_the_day_can_be_worked_again_afterwards(client):
    """The picker comes back, which is the whole point — the day is no longer held."""
    run = _awaiting(client)
    client.post(f"/api/runs/{run['id']}/stand-down", json={"reason": "cleared"})
    view = _day(client)
    assert view["fixtures"], "a day nobody is rescuing is offered disruptions again"
    again = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "vikram_late"})
    assert again.status_code == 200


def test_the_decision_is_on_the_record(client):
    run = _awaiting(client)
    client.post(f"/api/runs/{run['id']}/stand-down", json={"reason": "the cell passed north of us"})
    log = client.get(f"{P}/activity").json()
    events = log if isinstance(log, list) else log["events"]
    entry = next(e for e in events if "stood down" in e["message"])
    assert entry["kind"] == "decision", "nothing was approved"
    assert "the cell passed north of us" in entry["message"]
    assert entry["meta"]["options_offered"] > 0


def test_an_applied_recovery_is_pointed_at_revert_instead(client):
    run = _awaiting(client)
    approved = client.post(f"/api/runs/{run['id']}/approve", json={"option_id": run["rescue"]["recommended_option_id"], "approved_by": "producer"})
    assert approved.status_code == 200

    r = client.post(f"/api/runs/{run['id']}/stand-down", json={"reason": "changed my mind"})
    assert r.status_code == 409
    assert "roll it back first" in r.json()["detail"]


def test_revert_then_stand_down_is_the_composed_path(client):
    run = _awaiting(client)
    client.post(f"/api/runs/{run['id']}/approve", json={"option_id": run["rescue"]["recommended_option_id"], "approved_by": "producer"})
    client.post(f"/api/runs/{run['id']}/revert", json={"reason": "rolled back"})

    r = client.post(f"/api/runs/{run['id']}/stand-down", json={"reason": "and we are not re-deciding it"})
    assert r.status_code == 200
    assert _day(client)["day"]["active_disruption_id"] is None


def test_a_finished_run_is_not_holding_anything(client):
    """`crane_failure` reaches `nothing_to_recover`, which already handed the day back."""
    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "crane_failure"})
    run_id = started.json()["run_id"]
    for _ in range(60):
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.25)
    r = client.post(f"/api/runs/{run_id}/stand-down", json={"reason": "tidying up"})
    assert r.status_code == 409 and "already ended" in r.json()["detail"]


def test_an_unknown_run_is_a_404(client):
    assert client.post("/api/runs/run_nope/stand-down", json={"reason": "x"}).status_code == 404
