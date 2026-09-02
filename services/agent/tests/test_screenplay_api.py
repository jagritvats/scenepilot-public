"""Tests for Screenplay Ingestion and Breakdown API endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from scenepilot.api.app import app
from scenepilot.seed.nightfall import PROJECT_ID

SAMPLE_FOUNTAIN = """Title: Test Film
Author: Test Author

EXT. LOWER PAREL MILL — SUNSET #101#

A motorcycle accelerates down the runway.
A drone zooms overhead.

AARAV
Hold on tight!

INT. WAREHOUSE — NIGHT #102#

ZOYA
We have five minutes.
"""


def test_upload_screenplay_endpoint():
    client = TestClient(app)
    resp = client.post(
        f"/api/projects/{PROJECT_ID}/screenplay/upload",
        json={"text": SAMPLE_FOUNTAIN, "format_hint": "fountain", "sync_scenes": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["scene_count"] == 2
    assert len(data["scenes"]) == 2
    assert data["scenes"][0]["scene_number"] == "101"
    assert data["scenes"][1]["scene_number"] == "102"
    assert data["scenes"][0]["eighths"] >= 1


def test_get_screenplay_scenes_and_dood():
    client = TestClient(app)
    # First upload
    client.post(
        f"/api/projects/{PROJECT_ID}/screenplay/upload",
        json={"text": SAMPLE_FOUNTAIN, "format_hint": "fountain", "sync_scenes": True},
    )

    # Get parsed scenes
    resp = client.get(f"/api/projects/{PROJECT_ID}/screenplay/scenes")
    assert resp.status_code == 200
    assert resp.json()["count"] >= 2

    # Get DOOD
    dood_resp = client.get(f"/api/projects/{PROJECT_ID}/dood")
    assert dood_resp.status_code == 200
    dood_data = dood_resp.json()
    assert "entries" in dood_data
    assert len(dood_data["shoot_days"]) >= 1


def test_scene_breakdown_elements_endpoint():
    client = TestClient(app)
    # Scene 42 exists in seeded project
    resp = client.post(f"/api/projects/{PROJECT_ID}/scenes/sc_42/breakdown-elements")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scene_id"] == "sc_42"
    assert "elements" in data
    assert len(data["elements"]) >= 1
    assert "stop_conditions" in data
