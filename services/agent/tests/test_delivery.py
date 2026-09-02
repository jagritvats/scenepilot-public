"""Call-sheet dispatch: who it addresses, what it is allowed to claim, and when it may write.

Three failures got a panel deleted once already and all three live here. The log used to invent
seven crew heads and seven Mumbai mobile numbers that existed nowhere else in the production; it
used to stamp `READ` and `ACKNOWLEDGED` receipts, timestamped now, for messages nothing ever sent;
and it used to build itself on a GET, so opening the call sheet manufactured a delivery record
nobody asked for.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from scenepilot.api.app import app
from scenepilot.dispatch.delivery import (
    CHANNELS,
    acknowledge_dispatch,
    dispatch_roster,
    generate_crew_dispatches,
    get_dispatches_for_day,
    mark_dispatch_read,
    re_ping_unacknowledged,
)
from scenepilot.domain.enums import ResourceType
from scenepilot.seed.nightfall import DAY4_ID, PROJECT_ID, build_project
from scenepilot.services.callsheet import build_call_sheet
from scenepilot.services.coordination import DEPARTMENTS_BY_EQUIPMENT

# A number long enough to dial. Times ("06:30") and ISO dates ("2026-09-01") never reach five
# consecutive digits, so anything that does is a phone number somebody typed.
DIALABLE = re.compile(r"\d{5}")


@pytest.fixture()
def project():
    return build_project()


@pytest.fixture()
def day(project):
    return project.shoot_day(DAY4_ID)


@pytest.fixture()
def client():
    """Entered, so the lifespan seeds the demo project before a route goes looking for it."""
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Everybody on the distribution list is somebody the production actually models
# --------------------------------------------------------------------------- #


def test_every_recipient_is_a_resource_on_the_project(project, day):
    known = {r.id for r in project.resources}
    roster = dispatch_roster(project, day)
    assert roster
    assert {p.resource_id for p in roster} <= known
    for p in roster:
        assert project.resource(p.resource_id).name == p.name


def test_the_roster_is_the_day_s_cast_plus_every_seeded_department_head(project, day):
    roster = dispatch_roster(project, day)
    crew_ids = {r.id for r in project.resources if r.type == ResourceType.CREW}
    scheduled_cast = {c for i in day.items for c in project.scene(i.scene_id).cast_ids}
    assert {p.resource_id for p in roster if p.department == "Cast"} == scheduled_cast
    assert {p.resource_id for p in roster if p.department != "Cast"} == crew_ids


def test_every_department_the_coordination_engine_can_address_has_a_seeded_head(project):
    """A coordination action targets a department by name; the call sheet has to reach that name.

    `derive_actions` addresses departments as free strings — the equipment-to-department table plus
    four standing targets. If the seed and that table drift apart, an action goes to "Grip
    department" and the dispatch goes to nobody, and only a judge reading both panels would notice.
    """
    addressable = set(DEPARTMENTS_BY_EQUIPMENT.values()) | {"1st AD", "Transport captain", "Catering", "Production office"}
    seeded = {r.attributes.get("department") for r in project.resources if r.type == ResourceType.CREW}
    assert seeded == addressable


def test_the_seed_states_no_phone_number_anywhere(project):
    """India reserves no documentation number range, so a plausible +91 mobile would be a real one."""
    for r in project.resources:
        assert r.contact is None or not DIALABLE.search(r.contact), f"{r.id} carries a phone number"
        assert "+91" not in (r.contact or "")


def test_no_dispatch_invents_a_contact_for_somebody_who_has_none(project, day):
    for rec in generate_crew_dispatches(project, day):
        assert rec.contact is None or not DIALABLE.search(rec.contact)
        assert not DIALABLE.search(rec.payload_preview)
    # The cast carry no contact in the seed, and the deliverer does not fill one in.
    cast = [r for r in generate_crew_dispatches(project, day) if r.department == "Cast"]
    assert cast and all(r.contact is None for r in cast)


# --------------------------------------------------------------------------- #
# The numbers on a message are the numbers on the sheet it was built from
# --------------------------------------------------------------------------- #


def test_call_times_come_from_the_call_sheet_rather_than_being_recomputed(project, day):
    sheet = build_call_sheet(project, day)
    sheet_calls = {row["name"]: row["call"] for row in sheet["cast"]}
    for p in dispatch_roster(project, day):
        if p.department == "Cast":
            assert p.call_time == sheet_calls[p.name]
        else:
            assert p.call_time == sheet["unit_call"]


def test_a_department_head_message_quotes_the_derived_day_rather_than_a_typed_one(project, day):
    sheet = build_call_sheet(project, day)
    head = next(p for p in dispatch_roster(project, day) if p.resource_id == "crew_1st_ad")
    assert sheet["unit_call"] in head.payload_preview
    assert sheet["estimated_wrap"] in head.payload_preview
    # The golden hour is computed from the day's own date; the old copy typed "Sunrise: 06:22".
    assert sheet["sun"] in head.payload_preview


# --------------------------------------------------------------------------- #
# Nothing was sent, so nothing may claim it was read
# --------------------------------------------------------------------------- #


def test_generation_opens_every_row_queued_and_stamps_no_receipt(project, day):
    records = generate_crew_dispatches(project, day, channels=["WHATSAPP", "SMS"])
    assert len(records) == len(dispatch_roster(project, day)) * 2
    assert {r.status for r in records} == {"QUEUED"}
    assert all(r.read_at is None and r.acknowledged_at is None for r in records)
    assert all(r.simulated for r in records)


def test_read_and_acknowledged_are_reachable_only_by_an_explicit_call(project, day):
    records = generate_crew_dispatches(project, day, channels=["SMS"])
    first, second = records[0], records[1]

    assert mark_dispatch_read(project.id, day.id, first.id).status == "READ"
    assert first.read_at is not None and first.acknowledged_at is None

    acked = acknowledge_dispatch(project.id, day.id, second.id)
    assert acked.status == "ACKNOWLEDGED" and acked.acknowledged_at is not None
    # An acknowledgement implies it was read; it does not reopen as merely read afterwards.
    assert mark_dispatch_read(project.id, day.id, second.id).status == "ACKNOWLEDGED"

    assert acknowledge_dispatch(project.id, day.id, "disp_nope") is None


def test_re_ping_re_queues_the_unconfirmed_and_leaves_the_confirmed_alone(project, day):
    records = generate_crew_dispatches(project, day, channels=["SMS"])
    acknowledge_dispatch(project.id, day.id, records[0].id)
    mark_dispatch_read(project.id, day.id, records[1].id)

    repinged = re_ping_unacknowledged(project.id, day.id)
    assert records[0] not in repinged
    assert records[1] in repinged
    assert records[1].status == "READ", "re-sending a message does not un-read it"
    assert records[1].payload_preview.startswith("[RE-SEND] ")

    re_ping_unacknowledged(project.id, day.id)
    assert records[1].payload_preview.count("[RE-SEND]") == 1


def test_the_log_is_kept_per_project_not_per_day_id(project, day):
    generate_crew_dispatches(project, day, channels=["SMS"])
    assert get_dispatches_for_day(project.id, day.id)
    assert get_dispatches_for_day("proj_other", day.id) == []


# --------------------------------------------------------------------------- #
# Reading a page does not send a call sheet
# --------------------------------------------------------------------------- #


def test_opening_the_call_sheet_creates_no_delivery_record(client):
    base = f"/api/projects/{PROJECT_ID}/shoot-days/day_5/dispatch"

    empty = client.get(base)
    assert empty.status_code == 200
    body = empty.json()
    assert body["dispatches"] == [] and body["count"] == 0
    assert body["simulated"] is True and "nothing was transmitted" in body["note"]
    # Still nothing after a second read: a GET is not a side channel for the broadcast button.
    assert client.get(base).json()["count"] == 0

    # ...but it can still say who a broadcast would reach, without writing anything down.
    assert body["roster"] and client.get(base).json()["count"] == 0


def test_broadcasting_then_confirming_moves_the_row_and_only_the_row(client):
    base = f"/api/projects/{PROJECT_ID}/shoot-days/day_4/dispatch"

    posted = client.post(base, json={"channels": ["WHATSAPP", "SMS"]})
    assert posted.status_code == 200
    body = posted.json()
    assert body["count"] > 0 and body["simulated"] is True
    assert {d["status"] for d in body["dispatches"]} == {"QUEUED"}

    project = build_project()
    known = {r.id for r in project.resources}
    assert {d["recipient_id"] for d in body["dispatches"]} <= known

    target = body["dispatches"][0]["id"]
    assert client.post(f"{base}/{target}/read").json()["status"] == "READ"
    assert client.post(f"{base}/{target}/ack").json()["status"] == "ACKNOWLEDGED"
    assert client.post(f"{base}/disp_nope/ack").status_code == 404

    after = client.get(base).json()["dispatches"]
    assert [d["status"] for d in after].count("ACKNOWLEDGED") == 1
    assert client.post(f"{base}/re-ping").json()["repinged_count"] == len(after) - 1


def test_a_broadcast_refuses_a_channel_this_app_does_not_have(client):
    base = f"/api/projects/{PROJECT_ID}/shoot-days/day_4/dispatch"
    assert client.post(base, json={"channels": ["CARRIER_PIGEON"]}).status_code == 400
    ok = client.post(base, json={"channels": ["EMAIL", "CARRIER_PIGEON"]})
    assert ok.status_code == 200
    assert {d["channel"] for d in ok.json()["dispatches"]} == {"EMAIL"}
    assert set(CHANNELS) == {"WHATSAPP", "SMS", "EMAIL"}


def test_a_dispatch_route_on_a_day_that_does_not_exist_is_a_404(client):
    assert client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_99/dispatch").status_code == 404
    assert client.post(f"/api/projects/{PROJECT_ID}/shoot-days/day_99/dispatch").status_code == 404
