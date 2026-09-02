"""What the API refuses to accept as a disruption, and what it does when one turns out to be harmless.

Every case here used to be a 200. The pipeline's own arithmetic then produced an answer for it, and
the answer read exactly like a real one: an impact panel reporting "0 scheduled scene(s) directly
affected during 17:00–13:00" is a finding, not a rejected input, and a producer has no way to tell
the difference from the outside. The three shapes were

  * unreadable — `window_start='banana'` validated, and the first parse happened inside the spawned
    task, so the run went FAILED with the day left AT_RISK under a disruption and no way back;
  * impossible — a reversed window matched nothing because `overlaps` clamps at zero, and a negative
    dry-out drove the window end backwards past the start;
  * unreachable — the web form sends no `affects_resource_ids` and no `affects_location_ids` and
    overrides `affects_exteriors` to False for every non-weather type, so six of the seven types
    named nothing any scene could be exposed to.

And the fourth, which is not an input error at all: a disruption that is perfectly well formed and
simply does not touch the day. That one is answered rather than refused.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from scenepilot.domain.enums import ShootDayStatus
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


def _report(client, day_id: str, **body) -> tuple[int, str]:
    r = client.post(f"{P}/shoot-days/{day_id}/disruptions", json=body)
    return r.status_code, (r.json().get("detail", "") if r.status_code >= 400 else "")


def _settle(client, run_id: str) -> dict:
    for _ in range(60):
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in ("AWAITING_APPROVAL", "FAILED", "COMPLETED"):
            return run
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} never settled")


def _day(client, day_id: str) -> dict:
    return client.get(f"{P}/shoot-days/{day_id}").json()


# --------------------------------------------------------------------------- #
# Windows that cannot be read or cannot be true
# --------------------------------------------------------------------------- #


def test_an_unreadable_window_is_refused_at_the_edge(client):
    """Not 200-then-crash. The parse used to happen two nodes into a background task."""
    code, detail = _report(client, DAY4_ID, type="WEATHER", title="Rain", window_start="1300", window_end="17:00")
    assert code == 400
    assert "1300" in detail


def test_a_window_past_midnight_is_still_a_window(client):
    """`28:00` is how this production writes 04:00 the next morning, and Day 6 hard-wraps there.

    The obvious way to reject `banana` is an `HH:MM` pattern with hours 00-23, which would reject the
    night units this fixture set exists for. `to_minutes` is the parser instead, so the edge and the
    engine cannot disagree about what a time is.
    """
    code, _ = _report(
        client, DAY6_ID, type="WEATHER", title="Showers into the small hours",
        window_start="23:00", window_end="26:30", affects_exteriors=True,
    )
    assert code == 200


def test_a_reversed_window_is_refused_rather_than_answered(client):
    code, detail = _report(client, DAY4_ID, type="WEATHER", title="Storm", window_start="17:00", window_end="13:00", affects_exteriors=True)
    assert code == 400
    assert "ends before it starts" in detail


def test_a_window_outside_the_days_own_clock_is_refused(client):
    """Day 4 calls at 06:30 and wraps at 22:00; 00:00–01:00 cannot reach anything it shoots."""
    code, detail = _report(client, DAY4_ID, type="WEATHER", title="Overnight rain", window_start="00:00", window_end="01:00", affects_exteriors=True)
    assert code == 400
    assert "06:30" in detail and "22:00" in detail


def test_a_negative_dry_out_is_refused(client):
    """It ran the window end backwards: -100000 min turned 13:00–17:00 into (780, -98980)."""
    code, detail = _report(
        client, DAY4_ID, type="WEATHER", title="Rain", window_start="13:00", window_end="17:00",
        affects_exteriors=True, dry_out_minutes=-100000,
    )
    assert code == 400
    assert "dry_out_minutes" in detail


def test_half_a_window_is_refused(client):
    code, detail = _report(client, DAY4_ID, type="WEATHER", title="Rain", window_start="13:00", affects_exteriors=True)
    assert code == 400
    assert "both a start and an end" in detail


# --------------------------------------------------------------------------- #
# Disruptions that name nothing they affect
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("dtype", ["CAST_UNAVAILABLE", "LOCATION_UNAVAILABLE", "EQUIPMENT_FAILURE", "TRANSPORT", "REGULATORY", "OTHER"])
def test_a_disruption_that_affects_nothing_is_refused(client, dtype):
    """Exactly what the web form used to send for six of its seven types.

    `scene_exposed` has four True branches and every one needs exteriors, a named resource or a named
    location. Without one of the three the impact was empty by construction — and for TRANSPORT and
    REGULATORY the run spent a real Parallel search verifying it before finding that out.
    """
    code, detail = _report(client, DAY4_ID, type=dtype, title="Something happened", window_start="13:00", window_end="17:00", affects_exteriors=False)
    assert code == 400
    assert "affects no exteriors" in detail


def test_naming_the_resource_is_enough_to_be_accepted(client):
    code, _ = _report(
        client, DAY4_ID, type="EQUIPMENT_FAILURE", title="Crane down all day",
        window_start="13:00", window_end="19:00", affects_exteriors=False, affects_resource_ids=["eq_crane"],
    )
    assert code == 200


# --------------------------------------------------------------------------- #
# Well formed, and simply harmless
# --------------------------------------------------------------------------- #


def test_a_disruption_that_touches_nothing_ends_the_run_instead_of_recovering(client):
    """The seeded crane swap completes at 16:00; Sc 42 opens at 16:30. Nothing moves, and it says so.

    This is the outcome the pipeline had no way to reach. It reported "0 scheduled scene(s) directly
    affected" and then recommended "move Sc 48 13:30→13:10; move Sc 42 16:30→16:37" — a repack that
    outscored the untouched baseline 94 to 93 because `pack_day` restarts the cursor at unit call.
    """
    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "crane_failure"})
    assert started.status_code == 200
    run = _settle(client, started.json()["run_id"])

    assert run["status"] == "COMPLETED"
    assert run["rescue"]["options"] == []
    assert "nothing to recover" in run["rescue"]["no_impact_reason"].lower()


def test_a_harmless_disruption_hands_the_day_back(client):
    """The report is on the record; the day is not under it.

    `AT_RISK` used to be written two nodes before anything knew whether it was true, so a day that
    turned out to be fine kept an `active_disruption_id` — which is the state the day page renders as
    "under a disruption": no fixture picker, no manual entry, no options, nothing to do but reset.
    """
    before = _day(client, DAY4_ID)["day"]["status"]
    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "crane_failure"})
    _settle(client, started.json()["run_id"])

    view = _day(client, DAY4_ID)
    assert view["day"]["status"] == before
    assert view["day"]["active_disruption_id"] is None
    # …and the disruption itself is still findable: it happened, it was logged, it changed nothing.
    assert view["disruption"] is not None
    assert view["disruption"]["id"] == started.json()["disruption_id"]


def test_the_same_fault_on_a_day_that_can_feel_it_still_recovers(client):
    """The guard is about impact, not about the fixture: `vikram_late` takes Sc 19 out of Day 4."""
    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "vikram_late"})
    run = _settle(client, started.json()["run_id"])
    assert run["status"] == "AWAITING_APPROVAL"
    assert run["rescue"]["options"]
    # AT_RISK on the way through, RECOVERY_PROPOSED once there is something to propose. Either way
    # the day is held, which is the half `crane_failure` must not do.
    view = _day(client, DAY4_ID)
    assert view["day"]["status"] == ShootDayStatus.RECOVERY_PROPOSED.value
    assert view["day"]["active_disruption_id"] == started.json()["disruption_id"]


# --------------------------------------------------------------------------- #
# Which fixtures a day is offered
# --------------------------------------------------------------------------- #


def _cards(client, day_id: str) -> dict[str, dict]:
    return {f["id"]: f for f in _day(client, day_id)["fixtures"]}


def test_the_hero_day_is_offered_every_fixture_shaped_for_it(client):
    cards = _cards(client, DAY4_ID)
    for key in ("rain_pm", "vikram_late", "crane_failure"):
        assert cards[key]["applicable"] is True, key
        assert cards[key]["not_applicable_reason"] is None


def test_a_night_unit_is_told_which_fixtures_cannot_reach_it(client):
    """Day 6 calls at 16:00, carries no crane and no Vikram, and used to be offered all three anyway.

    It answered a crane hydraulic fault with "move Sc 62 17:00→16:30; pull cover Sc 27 into 19:00" —
    a scored, ranked recovery for a fault on a unit that has no crane. The cards stay in the list so
    a producer reads why they are disabled rather than wondering where they went.
    """
    cards = _cards(client, DAY6_ID)
    assert cards["crane_failure"]["applicable"] is False
    assert "crane" in cards["crane_failure"]["not_applicable_reason"].lower()
    assert cards["vikram_late"]["applicable"] is False
    assert cards["rain_night"]["applicable"] is True


def test_the_night_unit_has_a_fixture_that_actually_bites_it(client):
    """`rain_night` exists because the other three are Day 4's weather, cast and grip, not Day 6's.

    Until it did, the trailer runbook told the presenter to type a disruption by hand on Day 6.
    """
    started = client.post(f"{P}/shoot-days/{DAY6_ID}/disruptions", json={"fixture_id": "rain_night"})
    run = _settle(client, started.json()["run_id"])
    assert run["status"] == "AWAITING_APPROVAL"
    assert any(v["scene_id"] == "sc_58" for v in run["rescue"]["impact"]["violated_requirements"]) or run["rescue"]["impact"]["directly_affected_item_ids"]


def test_a_wrapped_day_is_offered_nothing(client):
    assert _day(client, "day_3")["fixtures"] == []


# --------------------------------------------------------------------------- #
# The states a run can leave a day in
# --------------------------------------------------------------------------- #


def test_a_failed_run_hands_the_day_back(client, monkeypatch):
    """A crash must not keep the day. It used to, and the only exit was POST /reset.

    `_step_disruption` had already written AT_RISK and `active_disruption_id` before anything could
    fail, so the day page rendered the banner branch — no fixture picker, no manual entry, no options
    — beside a red "Rescue run failed" card with no retry on it.
    """
    from scenepilot.workflows import rescue as rescue_module

    async def boom(ctx):
        raise RuntimeError("candidate generation exploded")

    monkeypatch.setattr(rescue_module, "_step_candidates", lambda ctx, rec: boom(ctx))
    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "rain_pm"})
    run = _settle(client, started.json()["run_id"])

    assert run["status"] == "FAILED"
    view = _day(client, DAY4_ID)
    assert view["day"]["active_disruption_id"] is None
    assert view["day"]["status"] != ShootDayStatus.AT_RISK.value


def test_a_reverted_recovery_is_marked_rescinded_and_the_revert_is_not(client):
    """Both change sets stay on the record; only one of them still stands.

    `revert_changeset` drops the original's id from `project.changeset_ids` and leaves the row exactly
    as approved, and the revert writes a second, inverted change set that also carries `approved_by`
    and `applied_at`. The day payload showed both as "applied - producer", underneath the option list
    asking the producer to choose again.
    """
    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "rain_pm"})
    run = _settle(client, started.json()["run_id"])
    run_id = run["id"]
    client.post(f"/api/runs/{run_id}/approve", json={"option_id": run["rescue"]["recommended_option_id"], "approved_by": "producer"})

    applied = {c["id"]: c for c in _day(client, DAY4_ID)["changesets"]}
    assert applied and all(c["rescinded"] is False for c in applied.values())

    body = client.post(f"/api/runs/{run_id}/revert", json={"reason": "the disruption cleared"}).json()
    after = {c["id"]: c for c in _day(client, DAY4_ID)["changesets"]}

    assert after[body["reverted_changeset_id"]]["rescinded"] is True
    assert after[body["changeset"]["id"]]["rescinded"] is False, "the revert record is not itself rescinded"


def test_a_draft_cannot_be_confirmed_out_from_under_a_live_decision(client):
    """Confirming a monitor draft mid-decision started a second run the day page would swap in.

    The day page reads `runs[0]`, so the option list a producer was weighing would be replaced by a
    fresh impact panel with no way back to it. `report_disruption` has refused this since it shipped;
    this endpoint only became reachable during a decision once the monitor panel stopped unmounting
    while a disruption was live.
    """
    drafted = client.post(f"{P}/shoot-days/{DAY4_ID}/monitors/simulate", params={"kind": "WEATHER"}).json()
    draft_id = drafted["disruption"]["id"]

    started = client.post(f"{P}/shoot-days/{DAY4_ID}/disruptions", json={"fixture_id": "rain_pm"})
    run = _settle(client, started.json()["run_id"])
    assert run["status"] == "AWAITING_APPROVAL"

    refused = client.post(f"{P}/disruptions/{draft_id}/confirm", json={"window_start": "13:00", "window_end": "17:00"})
    assert refused.status_code == 409
    assert run["id"] in refused.json()["detail"]
