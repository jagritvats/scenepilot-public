"""Tests for Scheduling API: Ephemeris, Labor Rules, and Strip Move Simulation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from scenepilot.api.app import app
from scenepilot.seed.nightfall import PROJECT_ID


def test_shoot_day_ephemeris_api():
    client = TestClient(app)
    resp = client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_4/ephemeris")
    assert resp.status_code == 200
    data = resp.json()
    assert data["day_id"] == "day_4"
    assert "profile" in data
    prof = data["profile"]
    assert "sunrise" in prof
    assert "sunset" in prof
    assert "golden_hour_dusk" in prof


def test_labor_rules_api():
    client = TestClient(app)
    resp = client.get(f"/api/projects/{PROJECT_ID}/labor-rules")
    assert resp.status_code == 200
    data = resp.json()
    # Project Nightfall shoots in Mumbai, so the agreement in force is FWICE/CINTAA — not the
    # North American pack the route used to hardcode while the engine enforced Indian norms.
    assert data["active_preset"] == "FWICE_CINTAA"
    assert "DGA_SAG" in data["presets"]
    assert "FWICE_CINTAA" in data["presets"]


def test_simulate_strip_move_api():
    client = TestClient(app)
    # Test valid items
    items_valid = [
        {"id": "i1", "scene_id": "sc_31", "start": "06:30", "end": "08:30", "unit": "MAIN"},
        {"id": "i2", "scene_id": "sc_19", "start": "09:00", "end": "11:00", "unit": "MAIN"},
    ]
    resp = client.post(
        f"/api/projects/{PROJECT_ID}/shoot-days/day_4/simulate-strip-move",
        json={"items": items_valid, "labor_preset": "DGA_SAG"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True

    # Test overlapping items on the same unit (hard violation)
    items_invalid = [
        {"id": "i1", "scene_id": "sc_31", "start": "06:30", "end": "09:00", "unit": "MAIN"},
        {"id": "i2", "scene_id": "sc_19", "start": "08:00", "end": "10:30", "unit": "MAIN"},
    ]
    resp_inv = client.post(
        f"/api/projects/{PROJECT_ID}/shoot-days/day_4/simulate-strip-move",
        json={"items": items_invalid, "labor_preset": "DGA_SAG"},
    )
    assert resp_inv.status_code == 200
    data_inv = resp_inv.json()
    assert data_inv["valid"] is False
    assert len(data_inv["hard_violations"]) >= 1


def test_dragging_a_strip_past_an_accepted_curfew_is_rejected_with_its_citation(monkeypatch):
    """The board captions a red strip "the live validation rejects this" — so it has to.

    This endpoint built its `ValidationContext` without `location_facts`, while the recovery engine
    passes `[f for f in project.location_facts if f.binds]`. The gap only opened once a producer
    accepted a fact, which is the demo's marquee click: the same 21:30–23:30 rooftop strip came back
    green from the drag and rejected by the rescue, on the same project, seconds apart.
    """
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    with TestClient(app_module.app) as client:
        facts = client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_6").json()["location_facts"]
        curfew = next(f for f in facts if f["resource_id"] == "loc_rooftop" and f["binding"] == "HARD" and f["rule"])
        # Sc 58 on the roof, pushed 90 minutes into the 22:00–06:00 ban.
        strip = [{"id": "it_58", "scene_id": "sc_58", "start": "21:30", "end": "23:30", "location_id": "loc_rooftop", "unit": "MAIN"}]

        def simulate():
            return client.post(f"/api/projects/{PROJECT_ID}/shoot-days/day_6/simulate-strip-move", json={"items": strip}).json()

        # Unaccepted, the fact is a research finding and constrains nothing — the drag is clean.
        assert not any(v["fact_id"] == curfew["id"] for v in simulate()["hard_violations"])

        accepted = client.post(f"/api/projects/{PROJECT_ID}/facts/{curfew['id']}/accept", json={"accepted_by": "Priya Nair (Line Producer)"})
        assert accepted.status_code == 200

        after = simulate()
        assert after["valid"] is False
        breach = next(v for v in after["hard_violations"] if v["fact_id"] == curfew["id"])
        assert breach["scene_id"] == "sc_58" and "indiacode.nic.in" in breach["evidence_url"]
