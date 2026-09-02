"""Accepting a fact re-verdicts the recovery options that are already on screen.

This is the beat the product is built around: a producer accepts a statute Parallel cited, and the
schedule answers immediately. Before this existed, `decide_fact` flipped two booleans and nothing
re-validated — the option list kept the verdict it was born with until the whole rescue was re-run,
so the only way to *show* the mechanism was to accept the fact first and report the disruption
afterwards, in that order, off camera.

What is pinned here is therefore both halves: the verdict moves, and the identity of every option
(label, rank, title) does not — an option that jumps position while a producer is looking at it
reads as a different option rather than as this one turning red.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from scenepilot.seed.nightfall import DAY6_ID


# A lighting truck that misses the 16:00 call, which is Day 6's own kind of trouble and — this is the
# part the scenario needs — trouble that stops at Sc 62. It has to leave the rooftop alone: what this
# file measures is the curfew turning Sc 58 red, so Sc 58 must be legal and untouched until the fact
# is accepted, and a disruption that reached it would reject those options for a second reason.
#
# It was `rain_pm` until the pipeline learned to say "nothing to recover". Day 6 is a 16:00 night unit
# whose only exterior plays 21:00–23:30, so an afternoon rain forecast never reached it and the full
# option list this file drove on was itself the bug. `rain_night` is the fixture shaped for this unit,
# but it lands squarely on Sc 58 and so cannot be used here either.
_LIGHTING_LATE = {
    "type": "EQUIPMENT_FAILURE",
    "title": "Lighting truck held at Film City gate until 19:00",
    "description": "Grip and electric truck held at the Film City gate; the package cannot be on the apartment set before 19:00.",
    "window_start": "16:00",
    "window_end": "19:00",
    "affects_exteriors": False,
    "affects_resource_ids": ["eq_lighting"],
    "dry_out_minutes": 0,
}


def _drive_to_awaiting(client, day_id: str, body: dict | None = None) -> dict:
    started = client.post(f"/api/projects/proj_nightfall/shoot-days/{day_id}/disruptions", json=body or _LIGHTING_LATE)
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    for _ in range(40):
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in ("AWAITING_APPROVAL", "FAILED"):
            break
        time.sleep(0.25)
    assert run["status"] == "AWAITING_APPROVAL", run.get("error")
    return run


@pytest.fixture()
def client(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    with TestClient(app_module.app) as c:
        yield c


def _curfew_id(client) -> str:
    facts = client.get(f"/api/projects/proj_nightfall/shoot-days/{DAY6_ID}").json()["location_facts"]
    return next(f["id"] for f in facts if f["resource_id"] == "loc_rooftop" and f["binding"] == "HARD" and f["rule"])


def _options(client, run_id: str) -> list[dict]:
    return client.get(f"/api/runs/{run_id}").json()["run"]["rescue"]["options"]


def test_accepting_the_curfew_turns_the_option_red_where_it_stands(client):
    run = _drive_to_awaiting(client, DAY6_ID)
    before = _options(client, run["id"])
    # The feasible ones specifically: an option can also be rejected for having left the lighting
    # package on a scene that no longer has it, and one turning red for *that* would prove nothing
    # about the curfew. What is pinned is that a schedule the validator currently accepts, and which
    # plays the rooftop past 22:00, stops being accepted the moment the statute is accepted.
    exposed = [o for o in before if o["feasible"] and any(i["scene_id"] == "sc_58" and i["end"] > "22:00" for i in o["schedule"])]
    assert exposed, "the rooftop runs past 22:00 and is legal until the fact is accepted"

    accepted = client.post(f"/api/projects/proj_nightfall/facts/{_curfew_id(client)}/accept", json={"accepted_by": "producer"})
    assert accepted.status_code == 200

    after = {o["id"]: o for o in _options(client, run["id"])}
    for o in exposed:
        now = after[o["id"]]
        assert now["feasible"] is False
        assert "curfew" in (now["rejected_reason"] or "").lower()
        ext = [v for v in now["violations"] if v["kind"] == "EXTERNAL_RULE"]
        assert ext and ext[0]["evidence_url"], "the rejection carries the page the rule was read from"

    # identity is untouched: same order, same letters, same Gemini prose
    assert [(o["id"], o["label"], o["rank"], o["title"], o["explanation"]) for o in before] == [
        (o["id"], o["label"], o["rank"], o["title"], o["explanation"]) for o in _options(client, run["id"])
    ]


def test_the_re_validation_says_what_it_did_in_the_day_feed(client):
    run = _drive_to_awaiting(client, DAY6_ID)
    client.post(f"/api/projects/proj_nightfall/facts/{_curfew_id(client)}/accept", json={"accepted_by": "producer"})

    activity = client.get(f"/api/projects/proj_nightfall/shoot-days/{DAY6_ID}").json()["activity"]
    entry = next((e for e in activity if e["kind"] == "deterministic" and "Re-validated" in e["message"]), None)
    assert entry is not None, "the re-validation is run-scoped, so it belongs in the day's own feed"
    assert "option" in entry["message"] and entry["meta"]["flips"]


def test_withdrawing_the_acceptance_restores_what_it_rejected(client):
    run = _drive_to_awaiting(client, DAY6_ID)
    fact_id = _curfew_id(client)
    client.post(f"/api/projects/proj_nightfall/facts/{fact_id}/accept", json={"accepted_by": "producer"})
    rejected = [o["id"] for o in _options(client, run["id"]) if not o["feasible"]]
    assert rejected

    client.post(f"/api/projects/proj_nightfall/facts/{fact_id}/reject", json={"accepted_by": "producer"})
    after = {o["id"]: o for o in _options(client, run["id"])}
    assert any(after[oid]["feasible"] for oid in rejected)
    assert all(not [v for v in o["violations"] if v["kind"] == "EXTERNAL_RULE"] for o in after.values())


def test_an_approved_run_is_never_re_verdicted(client):
    """An applied option list is the record of what was approved, and on what grounds."""
    run = _drive_to_awaiting(client, DAY6_ID)
    approved = client.post(
        f"/api/runs/{run['id']}/approve",
        json={"option_id": run["rescue"]["recommended_option_id"], "approved_by": "producer"},
    )
    assert approved.status_code == 200
    frozen = _options(client, run["id"])

    client.post(f"/api/projects/proj_nightfall/facts/{_curfew_id(client)}/accept", json={"accepted_by": "producer"})
    assert _options(client, run["id"]) == frozen
