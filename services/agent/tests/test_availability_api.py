"""Booking someone onto a day, and the blank that existed because nothing could.

`Resource.availability` is read by the validator, the heatmap, the ripple panel and the call sheet,
and it was written only by the seed. So the product could tell a producer *"no cast member, location
or equipment declares an availability window for Day N — that is a gap in the production data"* and
offer nothing to do about it, and a committed pickup day could name the three people nobody had
booked and leave the list in a React state that died on the next navigation.

The round-trip at the bottom is the point of the whole feature: `pending_clearance` shrinks when a
resource is cleared and comes back when it is released, which is what makes the blank and the write
two sides of one fact rather than two features.
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


def _day(client, day_id: str) -> dict:
    return client.get(f"{P}/shoot-days/{day_id}").json()


def _windows(client, resource_id: str, day_id: str) -> list[dict]:
    resources = _day(client, day_id)["resources"]
    return [a for a in resources[resource_id]["availability"] if a["shoot_day_id"] == day_id]


def test_every_seeded_day_is_fully_cleared(client):
    """A failure here is a finding about the seed, not about the endpoint.

    `pending_clearance` consults the same resource set `validate_schedule` does, and every seeded day
    validates — so a non-empty list would mean the panel cries wolf on the hero day.
    """
    for day_id in ("day_3", DAY4_ID, "day_5", DAY6_ID):
        assert _day(client, day_id)["pending_clearance"] == [], day_id


def test_clearing_a_resource_books_exactly_one_window(client):
    body = {"shoot_day_id": DAY6_ID, "start": "16:00", "end": "23:59"}
    assert client.post(f"{P}/resources/cast_vikram/availability", json=body).status_code == 200
    assert len(_windows(client, "cast_vikram", DAY6_ID)) == 1

    # Again, with a different window: it corrects the booking rather than widening it. Two rows would
    # both satisfy `is_available`, so an appending write would quietly keep the wider one alive.
    body["start"] = "18:00"
    assert client.post(f"{P}/resources/cast_vikram/availability", json=body).status_code == 200
    windows = _windows(client, "cast_vikram", DAY6_ID)
    assert len(windows) == 1 and windows[0]["start"] == "18:00"


def test_a_backwards_window_is_refused(client):
    r = client.post(f"{P}/resources/cast_vikram/availability", json={"shoot_day_id": DAY6_ID, "start": "19:00", "end": "17:00"})
    assert r.status_code == 400 and "ends before it starts" in r.json()["detail"]


def test_a_wrapped_day_cannot_be_booked_onto(client):
    r = client.post(f"{P}/resources/cast_vikram/availability", json={"shoot_day_id": "day_3", "start": "06:00", "end": "12:00"})
    assert r.status_code == 409


def test_releasing_a_day_nobody_named_says_so_instead_of_succeeding_quietly(client):
    r = client.request("DELETE", f"{P}/resources/cast_vikram/availability", params={"shoot_day_id": DAY6_ID})
    assert r.status_code == 404
    assert "no window naming Day 6" in r.json()["detail"]


def test_a_resource_booked_for_every_day_is_not_releasable_from_one(client):
    """`shoot_day_id=None` means every day, so there is no per-day row to take away.

    The state has to be built. `eq_lighting` is booked onto no day by the seed — which is why the
    migration will not fill one in underneath this — so a blanket row is the whole of its record.
    """
    from scenepilot.domain.models import Availability

    project = client.repo.get_project(PROJECT_ID)
    project.resource("eq_lighting").availability = [Availability(shoot_day_id=None, start="06:00", end="22:00")]
    client.repo.save_project(project)

    r = client.request("DELETE", f"{P}/resources/eq_lighting/availability", params={"shoot_day_id": DAY4_ID})
    assert r.status_code == 404
    assert "every day" in r.json()["detail"]


def test_a_clearance_that_does_not_cover_the_scene_says_so_on_the_record(client):
    """The booking succeeds and the day is still invalid — which looks exactly like a fix."""
    day = _day(client, DAY4_ID)["day"]
    item = day["items"][0]
    scene = _day(client, DAY4_ID)["scenes"][item["scene_id"]]
    cast_id = scene["cast_ids"][0]
    client.post(f"{P}/resources/{cast_id}/availability", json={"shoot_day_id": DAY4_ID, "start": "20:00", "end": "21:00"})

    log = client.get(f"{P}/activity").json()
    entry = next(e for e in (log if isinstance(log, list) else log["events"]) if "cleared" in e["message"])
    assert "does not cover" in entry["message"]
    assert entry["meta"]["uncovered_item_ids"]


def test_the_blank_and_the_write_are_two_sides_of_one_fact(client):
    """Release a resource the day needs and the blank appears; clear it and the blank goes away.

    Sc 58 calls Aarav on Day 6 and the seed books them for it, so the day starts clear. Taking the
    booking away is what the product could always *describe* — "no window on file names this day" —
    and never do anything about.
    """
    assert _day(client, DAY6_ID)["pending_clearance"] == []

    assert client.request("DELETE", f"{P}/resources/cast_aarav/availability", params={"shoot_day_id": DAY6_ID}).status_code == 200
    pending = {row["resource_id"] for row in _day(client, DAY6_ID)["pending_clearance"]}
    assert "cast_aarav" in pending, "they are called by Sc 58 and no window on file names Day 6"

    assert client.post(f"{P}/resources/cast_aarav/availability", json={"shoot_day_id": DAY6_ID, "start": "16:00", "end": "28:00"}).status_code == 200
    assert _day(client, DAY6_ID)["pending_clearance"] == []
