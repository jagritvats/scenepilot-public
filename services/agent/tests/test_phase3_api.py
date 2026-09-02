"""Tests for Phase 3 API: Multi-Day Ripple Solver and Field Dispatch."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from scenepilot.api.app import app
from scenepilot.seed.nightfall import PROJECT_ID


def test_multiday_ripple_plan_api():
    client = TestClient(app)
    resp = client.get(
        f"/api/projects/{PROJECT_ID}/shoot-days/day_4/multiday-plan?deferred_scene_ids=sc_42"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "recovery_option_id" in data
    assert "summary" in data
    assert "total_ripple_cost_inr" in data
    assert len(data["placements"]) > 0 or data["synthesized_pickup_day"] is not None


def test_dispatch_and_acknowledgement_api():
    client = TestClient(app)
    # Trigger dispatch
    resp = client.post(
        f"/api/projects/{PROJECT_ID}/shoot-days/day_4/dispatch",
        json={"channels": ["WHATSAPP", "SMS"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 5
    dispatches = data["dispatches"]
    assert len(dispatches) >= 5

    # Get dispatches
    get_resp = client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_4/dispatch")
    assert get_resp.status_code == 200
    assert get_resp.json()["count"] >= 5

    # Acknowledge one dispatch
    target_id = dispatches[0]["id"]
    ack_resp = client.post(
        f"/api/projects/{PROJECT_ID}/shoot-days/day_4/dispatch/{target_id}/ack"
    )
    assert ack_resp.status_code == 200
    assert ack_resp.json()["status"] == "ACKNOWLEDGED"
