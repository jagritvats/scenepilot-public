"""Rolling an approved recovery back — and doing it without editing history.

Two properties carry this feature. The day must come back *whole*, which is why the restore reads
`RescueState.baseline` rather than inverting the change list (a deferred scene is recorded there as
`start → None` and nothing else, so inverting it would restore half a scene). And the original
approval must survive on the record, because a producer asking "what did we do and when" is entitled
to see both the approval and the revert, not a schedule that claims nothing ever happened.
"""

import time

import pytest
from fastapi.testclient import TestClient

from scenepilot.domain.enums import RunStatus
from scenepilot.seed.nightfall import DAY4_ID


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


def _apply_recovery(client) -> tuple[str, list[dict]]:
    """Drive the hero rescue to APPLIED and return the run id and the pre-recovery schedule."""
    before = client.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}").json()["day"]["items"]
    started = client.post(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "rain_pm"})
    run_id = started.json()["run_id"]
    for _ in range(40):
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in ("AWAITING_APPROVAL", "FAILED"):
            break
        time.sleep(0.25)
    assert run["status"] == "AWAITING_APPROVAL", run.get("error")
    approved = client.post(f"/api/runs/{run_id}/approve", json={"option_id": run["rescue"]["recommended_option_id"], "approved_by": "producer"})
    assert approved.status_code == 200, approved.text
    return run_id, before


def test_reverting_restores_the_schedule_the_recovery_replaced(client):
    run_id, before = _apply_recovery(client)
    after_apply = client.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}").json()["day"]["items"]
    assert [(i["scene_id"], i["start"]) for i in after_apply] != [(i["scene_id"], i["start"]) for i in before]

    reverted = client.post(f"/api/runs/{run_id}/revert", json={"reason": "the rain passed"})
    assert reverted.status_code == 200, reverted.text

    restored = client.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}").json()["day"]["items"]
    assert [(i["scene_id"], i["start"], i["end"]) for i in restored] == [(i["scene_id"], i["start"], i["end"]) for i in before]


def test_the_carried_scene_comes_back_whole(client):
    """The change list records a deferral as `start → None` and nothing else; the baseline has it all."""
    run_id, before = _apply_recovery(client)
    after_apply = {i["scene_id"] for i in client.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}").json()["day"]["items"]}
    carried = {i["scene_id"] for i in before} - after_apply
    assert carried, "the hero recovery carries a scene off the day"

    client.post(f"/api/runs/{run_id}/revert", json={"reason": "rolled back"})

    restored = {i["scene_id"]: i for i in client.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}").json()["day"]["items"]}
    for scene_id in carried:
        assert scene_id in restored
        item = restored[scene_id]
        assert item["start"] and item["end"] and item["location_id"], "restored with every field, not just a start"


def test_the_run_awaits_a_decision_again_and_the_approval_stays_on_the_record(client):
    run_id, _ = _apply_recovery(client)
    original = client.get(f"/api/runs/{run_id}").json()["run"]["rescue"]["changeset"]["id"]

    body = client.post(f"/api/runs/{run_id}/revert", json={"reason": "producer changed their mind"}).json()

    run = client.get(f"/api/runs/{run_id}").json()["run"]
    assert run["status"] == RunStatus.AWAITING_APPROVAL.value
    assert run["rescue"]["changeset"] is None
    assert body["reverted_changeset_id"] == original

    # Both change sets are on the record: the approval and the revert.
    changesets = client.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}").json()["changesets"]
    ids = {c["id"] for c in changesets}
    assert original in ids and body["changeset"]["id"] in ids
    inverted = next(c for c in changesets if c["id"] == body["changeset"]["id"])
    assert "Reverted" in inverted["summary"] and all("Reverted by" in ch["reason"] for ch in inverted["changes"])


def test_the_revert_is_recorded_as_a_producer_decision(client):
    run_id, _ = _apply_recovery(client)
    client.post(f"/api/runs/{run_id}/revert", json={"reason": "the rain passed"})
    log = client.get("/api/projects/proj_nightfall/activity").json()["events"]
    assert any(e["kind"] == "decision" and "reverted" in e["message"].lower() and "the rain passed" in e["message"] for e in log)


def test_a_recovery_can_be_approved_again_after_a_revert(client):
    """The re-apply guard keys on `project.changeset_ids`; a revert has to release it."""
    run_id, _ = _apply_recovery(client)
    client.post(f"/api/runs/{run_id}/revert", json={"reason": "second thoughts"})

    run = client.get(f"/api/runs/{run_id}").json()["run"]
    again = client.post(f"/api/runs/{run_id}/approve", json={"option_id": run["rescue"]["recommended_option_id"], "approved_by": "producer"})
    assert again.status_code == 200, again.text
    assert client.get(f"/api/runs/{run_id}").json()["run"]["status"] == "APPLIED"


def test_a_run_that_was_never_applied_has_nothing_to_revert(client):
    started = client.post(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "rain_pm"})
    run_id = started.json()["run_id"]
    for _ in range(40):
        if client.get(f"/api/runs/{run_id}").json()["run"]["status"] in ("AWAITING_APPROVAL", "FAILED"):
            break
        time.sleep(0.25)
    refused = client.post(f"/api/runs/{run_id}/revert", json={"reason": "nothing to undo"})
    assert refused.status_code == 409 and "nothing to roll back" in refused.json()["detail"]


def test_an_unknown_run_is_a_404(client):
    assert client.post("/api/runs/run_nope/revert", json={"reason": "x"}).status_code == 404


def test_a_revert_is_refused_while_the_carried_scene_is_booked_on_another_day():
    """A revert puts the carried scenes back. It must not put one back on top of a real booking.

    Both halves are advertised buttons — README offers "commit a downstream placement or materialise
    the synthesised pickup day" and "revert an applied recovery" — so this is those two pressed in
    the order a producer would press them. The restore reads `RescueState.baseline` wholesale and had
    no idea the scene had since been given a home, so it booked it on both days and returned 200 with
    a reassuring note.

    Refused rather than reconciled: un-committing the other day is a second decision and it belongs
    to the producer. The message has to name the day so it can be undone there first.
    """
    from scenepilot.domain.models import ChangeSet, RescueState, RunKind, WorkflowRun
    from scenepilot.seed.nightfall import build_project
    from scenepilot.services.revert import RevertRefused, revert_changeset

    p = build_project()
    day4 = p.shoot_day(DAY4_ID)
    baseline = [i.model_copy(deep=True) for i in day4.items]
    carried = next(i for i in baseline if i.scene_id == "sc_42")

    # An approved recovery that carried sc_42 off Day 4 …
    day4.items = [i for i in day4.items if i.scene_id != carried.scene_id]
    changeset = ChangeSet(project_id=p.id, shoot_day_id=DAY4_ID, changes=[])
    p.changeset_ids.append(changeset.id)
    run = WorkflowRun(
        project_id=p.id, kind=RunKind.RESCUE, mode="replay", status=RunStatus.APPLIED,
        rescue=RescueState(shoot_day_id=DAY4_ID, disruption_id="dis_test", baseline=baseline,
                           changeset=changeset),
    )

    # … and a producer who then gave that scene a home on another day. Written straight onto the day
    # rather than through `commit_placement`, which rightly refuses sc_42 on Day 5 (no rooftop, no
    # crane, no golden hour). How the booking got there is not what this guard is about; that the
    # scene is *on* another day is.
    other = p.shoot_day("day_5")
    other.items.append(carried.model_copy(deep=True))

    with pytest.raises(RevertRefused, match="booked elsewhere"):
        revert_changeset(p, run, changeset, reverted_by="producer", reason="test")

    booked = [(d.id, i.scene_id) for d in p.shoot_days for i in d.items if i.scene_id == "sc_42"]
    assert len(booked) == 1, f"sc_42 ended up on more than one day: {booked}"
