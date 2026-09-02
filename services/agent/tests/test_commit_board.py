"""Keeping a board a producer re-timed by hand.

The interactive stripboard is the headline of this product's second phase and it was a pure what-if:
`/simulate-strip-move` validated and priced arbitrary times and handed them back, `Reset to baseline`
was the only other control, and every edit died on reload. It is also the surface a judge spends the
most unsupervised minutes on, so it was the one place where nothing they did persisted.

Two separations are load-bearing and both are pinned here: the commit is validated under the pack the
production is *held to* rather than the one the board is previewing, and it refuses a day whose
disruption is still live — because this validates the whole day with the disruption set aside, so it
would otherwise call a board legal that the panel beside it shows as exposed.
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from scenepilot.config import settings as default_settings
from scenepilot.seed.nightfall import DAY4_ID, PROJECT_ID

P = f"/api/projects/{PROJECT_ID}"


@pytest.fixture()
def client(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    # Off by default in code, so every test here has to open it deliberately.
    monkeypatch.setattr(app_module, "settings", replace(default_settings, allow_commit_board=True))
    monkeypatch.setattr(app_module, "repo", repo)
    with TestClient(app_module.app) as c:
        c.repo = repo  # type: ignore[attr-defined]
        yield c


def _items(client, day_id: str = DAY4_ID) -> list[dict]:
    day = client.get(f"{P}/shoot-days/{day_id}").json()["day"]
    return [{"item_id": i["id"], "start": i["start"], "end": i["end"]} for i in day["items"]]


def _commit(client, items, day_id: str = DAY4_ID, **kw):
    return client.post(f"{P}/shoot-days/{day_id}/commit-schedule", json={"items": items, **kw})


def test_a_nudged_strip_survives_a_reload(client):
    items = _items(client)
    items[0]["start"], items[0]["end"] = "06:45", "09:15"

    r = _commit(client, items, reason="pulled the alley earlier to catch the light")
    assert r.status_code == 200, r.text

    after = {i["id"]: i for i in client.get(f"{P}/shoot-days/{DAY4_ID}").json()["day"]["items"]}
    assert after[items[0]["item_id"]]["start"] == "06:45"
    assert r.json()["changeset"]["approved_by"] == "producer"
    assert any("Validated under" in n for n in r.json()["notes"])


def test_committing_re_derives_the_calls_that_follow_the_schedule(client):
    """Unlike a wrap, this is still a plan — the vendors have not turned up yet."""
    before = client.get(f"{P}/shoot-days/{DAY4_ID}").json()["day"]["equipment_calls"]
    items = _items(client)
    for i in items:
        i["start"], i["end"] = _shift(i["start"], 30), _shift(i["end"], 30)
    assert _commit(client, items).status_code == 200
    after = client.get(f"{P}/shoot-days/{DAY4_ID}").json()["day"]["equipment_calls"]
    assert after != before


def _shift(hhmm: str, minutes: int) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    total = h * 60 + m + minutes
    return f"{total // 60:02d}:{total % 60:02d}"


def test_a_board_that_breaks_a_hard_constraint_is_refused_and_rolled_back(client):
    items = _items(client)
    # Put every strip on top of the first one. Whatever else this breaks, it cannot be legal.
    for i in items:
        i["start"], i["end"] = "07:00", "09:30"

    before = client.get(f"{P}/shoot-days/{DAY4_ID}").json()["day"]["items"]
    r = _commit(client, items)
    assert r.status_code == 409
    assert "cannot be committed as edited" in r.json()["detail"]
    assert client.get(f"{P}/shoot-days/{DAY4_ID}").json()["day"]["items"] == before, "a refused commit leaves nothing behind"


def test_the_whole_day_or_none_of_it(client):
    r = _commit(client, _items(client)[:-1])
    assert r.status_code == 409 and "does not account for" in r.json()["detail"]


def test_an_unchanged_board_is_not_a_commit(client):
    r = _commit(client, _items(client))
    assert r.status_code == 409 and "nothing to commit" in r.json()["detail"]


def test_a_day_under_a_live_disruption_is_refused_and_told_where_to_go(client):
    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "rain_pm"})
    run_id = started.json()["run_id"]
    for _ in range(60):
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in ("AWAITING_APPROVAL", "FAILED", "COMPLETED"):
            break
        time.sleep(0.25)

    items = _items(client)
    items[0]["start"] = "07:15"
    r = _commit(client, items)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "stand it down" in detail or run_id in detail


def test_standing_the_rescue_down_makes_the_board_committable_again(client):
    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "rain_pm"})
    run_id = started.json()["run_id"]
    for _ in range(60):
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in ("AWAITING_APPROVAL", "FAILED", "COMPLETED"):
            break
        time.sleep(0.25)
    assert client.post(f"/api/runs/{run_id}/stand-down", json={"reason": "shooting through it"}).status_code == 200

    items = _items(client)
    items[0]["start"], items[0]["end"] = "06:45", "09:15"
    assert _commit(client, items).status_code == 200


def test_a_wrapped_day_cannot_be_re_timed(client):
    items = _items(client)
    wrap = [{"item_id": i["item_id"], "outcome": "SHOT", "actual_end": i["end"]} for i in items]
    assert client.post(f"{P}/shoot-days/{DAY4_ID}/wrap", json={"items": wrap, "camera_wrap": "19:00"}).status_code == 200

    items[0]["start"] = "07:15"
    r = _commit(client, items)
    assert r.status_code == 409 and "record of what was shot" in r.json()["detail"]


def test_the_commit_is_closed_where_the_deployment_says_so(client, monkeypatch):
    from scenepilot.api import app as app_module

    monkeypatch.setattr(app_module, "settings", replace(default_settings, allow_commit_board=False))
    items = _items(client)
    items[0]["start"] = "07:15"
    r = _commit(client, items)
    assert r.status_code == 501
    assert r.json()["detail"]["env"] == "SCENEPILOT_ALLOW_COMMIT_BOARD=1"
