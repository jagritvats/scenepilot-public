"""Closing a day out — the write three shipped features were waiting on.

`day_completion` has computed a per-scene record on every day payload since it was written, and
nothing could produce a day for it to describe. `day_cost` has carried a record branch it could never
take. `build_dpr` refused every day but the one the seed ships wrapped. All three were reachable only
by editing the database by hand.

The carried-scene case is the one worth reading closely. Everywhere else in this codebase a
`DEFERRED` item leaves the day; here it stays, because the record of what a day did *not* deliver is
half of what a wrapped day is for.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from scenepilot.config import settings as default_settings
from scenepilot.domain.enums import ScheduleItemStatus, ShootDayStatus
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


def _day(client, day_id: str = DAY4_ID) -> dict:
    return client.get(f"{P}/shoot-days/{day_id}").json()


def _all_shot(client, day_id: str = DAY4_ID) -> list[dict]:
    return [{"item_id": i["id"], "outcome": "SHOT", "actual_end": i["end"]} for i in _day(client, day_id)["day"]["items"]]


def _wrap(client, items, day_id: str = DAY4_ID, **kw):
    return client.post(f"{P}/shoot-days/{day_id}/wrap", json={"items": items, **kw})


def test_a_wrapped_day_reports_what_it_delivered(client):
    r = _wrap(client, _all_shot(client), camera_wrap="19:00")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["day"]["status"] == ShootDayStatus.WRAPPED.value
    assert body["day"]["camera_wrap"] == "19:00"
    completion = body["completion"]
    assert completion is not None and completion["wrapped"] is True
    assert len(completion["scenes_completed"]) == 4 and completion["scenes_carried"] == []
    assert completion["wrap"] == "19:00"
    assert all(i["status"] == ScheduleItemStatus.COMPLETED.value for i in body["day"]["items"])


def test_a_carried_scene_stays_on_the_day_so_the_record_can_report_it(client):
    items = _all_shot(client)
    items[-1]["outcome"] = "CARRIED"
    items[-1]["note"] = "lost the light on the rooftop"
    carried_item = items[-1]["item_id"]

    body = _wrap(client, items, camera_wrap="18:00").json()
    day_items = {i["id"]: i for i in body["day"]["items"]}
    assert carried_item in day_items, "filtering it off the day would make the DPR say nothing carried"
    assert day_items[carried_item]["status"] == ScheduleItemStatus.DEFERRED.value
    assert day_items[carried_item]["note"] == "lost the light on the rooftop"

    completion = body["completion"]
    assert len(completion["scenes_completed"]) == 3
    assert len(completion["scenes_carried"]) == 1
    assert completion["carry_over_cost_inr"] > 0, "a carried scene costs this production to carry"


def test_the_daily_production_report_finally_issues(client):
    """It refused every day but the seeded one, because nothing else could ever be wrapped."""
    assert client.get(f"{P}/shoot-days/{DAY4_ID}/dpr").status_code == 409
    _wrap(client, _all_shot(client), camera_wrap="19:00")
    issued = client.get(f"{P}/shoot-days/{DAY4_ID}/dpr")
    assert issued.status_code == 200
    assert issued.json()["dpr"]["day_number"] == 4


def test_the_cost_card_flips_from_estimate_to_record(client):
    assert _day(client)["day_cost"]["basis"] == "projected"
    _wrap(client, _all_shot(client), camera_wrap="19:00")
    assert _day(client)["day_cost"]["basis"] == "record"


def test_the_call_sheet_counts_what_was_carried(client):
    """It printed "N scene(s) completed; nothing outstanding" over every row on the day."""
    items = _all_shot(client)
    items[-1]["outcome"] = "CARRIED"
    _wrap(client, items, camera_wrap="18:00")

    sheet = client.get(f"{P}/shoot-days/{DAY4_ID}/call-sheet").json()["current"]
    line = next(a for a in sheet["advisories"] if "wrapped at" in a)
    assert "3 scene(s) completed" in line
    assert "1 carried" in line and "nothing outstanding" not in line


def test_a_carried_scene_can_still_be_placed_on_another_day(client):
    """A record of a carry is not a booking — `_scene_is_unscheduled` had to learn the difference."""
    items = _all_shot(client)
    items[-1]["outcome"] = "CARRIED"
    carried_scene = next(i["scene_id"] for i in _day(client)["day"]["items"] if i["id"] == items[-1]["item_id"])
    _wrap(client, items, camera_wrap="18:00")

    placed = client.post(f"{P}/shoot-days/{DAY6_ID}/commit-placement", json={"scene_id": carried_scene, "committed_by": "producer"})
    assert placed.status_code in (200, 409), placed.text
    if placed.status_code == 409:
        assert "already scheduled" not in placed.json()["detail"].lower()


def test_every_strip_has_to_be_accounted_for(client):
    partial = _all_shot(client)[:-1]
    r = _wrap(client, partial)
    assert r.status_code == 409
    assert "not accounted for" in r.json()["detail"]


def test_a_strip_that_is_not_on_the_day_is_refused(client):
    items = _all_shot(client) + [{"item_id": "it_nope", "outcome": "SHOT"}]
    r = _wrap(client, items)
    assert r.status_code == 409 and "it_nope" in r.json()["detail"]


def test_a_day_cannot_be_wrapped_twice(client):
    assert _wrap(client, _all_shot(client), camera_wrap="19:00").status_code == 200
    r = _wrap(client, [{"item_id": "it_31", "outcome": "SHOT"}])
    assert r.status_code == 409 and "rewrite a record" in r.json()["detail"]


def test_a_wrap_earlier_than_the_last_scene_shot_is_refused(client):
    r = _wrap(client, _all_shot(client), camera_wrap="10:00")
    assert r.status_code == 409 and "cannot have wrapped" in r.json()["detail"]


def test_a_scene_cannot_have_ended_before_it_started(client):
    items = _all_shot(client)
    items[0]["actual_end"] = "06:00"
    r = _wrap(client, items)
    assert r.status_code == 409 and "cannot have ended" in r.json()["detail"]


def test_a_day_under_a_live_rescue_cannot_be_wrapped(client):
    """Otherwise a recovery approved afterwards applies a change set to a day already shot."""
    import time

    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "rain_pm"})
    run_id = started.json()["run_id"]
    for _ in range(60):
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in ("AWAITING_APPROVAL", "FAILED", "COMPLETED"):
            break
        time.sleep(0.25)

    r = _wrap(client, _all_shot(client), camera_wrap="19:00")
    assert r.status_code == 409 and run_id in r.json()["detail"]


def test_an_actual_end_is_recorded_on_the_change_set(client):
    items = _all_shot(client)
    items[0]["actual_end"] = "10:15"
    body = _wrap(client, items, camera_wrap="19:00").json()

    ends = [c for c in body["changeset"]["changes"] if c["field"] == "end"]
    assert len(ends) == 1 and ends[0]["after"] == "10:15"
    assert body["changeset"]["approved_by"] == "producer" and body["changeset"]["applied_at"]
    # A shot strip that ran long is still shot. `apply_changeset` would have marked it MOVED, and
    # `day_completion` counts anything short of COMPLETED as outstanding.
    assert all(i["status"] == ScheduleItemStatus.COMPLETED.value for i in body["day"]["items"])


def test_wrapping_is_refused_where_the_deployment_does_not_allow_it(client, monkeypatch):
    from scenepilot.api import app as app_module

    monkeypatch.setattr(app_module, "settings", replace(default_settings, allow_wrap=False))
    r = _wrap(client, _all_shot(client), camera_wrap="19:00")
    assert r.status_code == 501
    assert r.json()["detail"]["env"] == "SCENEPILOT_ALLOW_WRAP=1"
