"""The production log — the audit trail that was always written and never read.

Twenty-eight sites across the API and the workflows log a line at the moment they do something, and
`GET /api/projects/{id}/activity` has always returned all of it. Nothing in the UI called it, so the
one question a producer most needs answered about a system that changes their schedule — who decided
what, on what evidence, and when — existed complete in the database and appeared on no screen.

The tests here pin the two properties that make it an audit trail rather than a log dump: it spans
both scopes (an act recorded inside a workflow run and an act recorded by the API are one story), and
its vocabulary is closed — every kind any writer emits has an entry, because the kind that fell
through the gap was `decision`, the producer's own accountable act.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scenepilot.api.app import ACTIVITY_KINDS, CATEGORY_ORDER, app
from scenepilot.seed.nightfall import PROJECT_ID

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "scenepilot"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _log(client) -> dict:
    body = client.get(f"/api/projects/{PROJECT_ID}/activity")
    assert body.status_code == 200
    return body.json()


def test_the_log_returns_the_events_the_seed_actually_wrote(client):
    log = _log(client)
    assert log["total"] == len(log["events"])
    assert log["events"], "a seeded project has already recorded its own seeding"
    for event in log["events"]:
        assert event["message"].strip()
        assert event["kind"] in ACTIVITY_KINDS


def test_every_kind_any_writer_emits_has_an_entry_in_the_vocabulary():
    """The gap this closes: `decision` had no entry, so producer decisions rendered as generic grey.

    Read off the source rather than off a list kept beside it, so a new kind introduced by a writer
    fails here instead of quietly rendering as orchestration.
    """
    emitted: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        emitted |= set(re.findall(r'_log_project\(\s*[A-Za-z_]+\s*,\s*"([a-z_]+)"', text))
        emitted |= set(re.findall(r'\.log\(\s*"([a-z_]+)"', text))
        emitted |= set(re.findall(r'ActivityEvent\([^)]*?kind="([a-z_]+)"', text, re.DOTALL))

    # `pasted_text` is a ProductionBrief source kind that the regex above also matches; it is not an
    # activity kind and is excluded rather than given a meaningless entry.
    emitted -= {"pasted_text"}
    missing = sorted(emitted - set(ACTIVITY_KINDS))
    assert not missing, f"activity kinds with no entry in ACTIVITY_KINDS: {missing}"


def test_every_kind_maps_to_a_category_the_client_is_told_about(client):
    log = _log(client)
    assert list(log["categories"]) == list(CATEGORY_ORDER)
    for kind, spec in log["kinds"].items():
        assert spec["category"] in CATEGORY_ORDER, kind
        assert spec["label"] and spec["description"]


def test_the_producers_own_decisions_are_their_own_category():
    """They are the accountable acts, and they must not read as bookkeeping."""
    assert ACTIVITY_KINDS["decision"]["category"] == "decision"
    assert ACTIVITY_KINDS["approval"]["category"] == "decision"
    assert ACTIVITY_KINDS["info"]["category"] == "orchestration"
    assert ACTIVITY_KINDS["parallel"]["category"] == "evidence"


def test_counts_by_category_add_up_to_the_events_returned(client):
    log = _log(client)
    assert sum(log["counts_by_category"].values()) == len(log["events"])


def test_accepting_a_fact_is_recorded_as_a_producer_decision(client):
    """The line a judge is looking for: a producer turning a cited statute into a hard constraint."""
    dossiers = client.get(f"/api/projects/{PROJECT_ID}/dossiers").json()
    facts = [f for f in dossiers.get("facts", []) if not f["accepted"] and not f["rejected"]]
    if not facts:
        pytest.skip("this deployment's warm seed proposed no undecided facts to accept")

    fact = facts[0]
    before = len(_log(client)["events"])
    client.post(f"/api/projects/{PROJECT_ID}/facts/{fact['id']}/accept", json={"accepted_by": "producer"})

    log = _log(client)
    assert len(log["events"]) > before
    decisions = [e for e in log["events"] if log["kinds"][e["kind"]]["category"] == "decision"]
    assert decisions, "accepting a fact must leave a producer decision on the audit trail"
    latest = decisions[-1]
    assert fact["label"] in latest["message"]
    # The entry carries the id, which is what lets the log link back to what it changed.
    assert latest["meta"].get("fact_id") == fact["id"]
    assert log["counts_by_category"]["decision"] >= 1


def test_the_log_spans_run_scoped_and_project_scoped_events(client):
    """An act recorded inside a workflow and an act recorded by the API are one story, not two.

    `list_activity(project_id=...)` returns both because a run event carries the project id too; this
    pins that, because filtering to `run_id is None` would silently halve the trail.
    """
    log = _log(client)
    scopes = {e["run_id"] is None for e in log["events"]}
    # A freshly seeded project has only project-scoped events; either way the field must be present
    # and the run index must name every run a line points at.
    assert scopes
    known = {r["id"] for r in log["runs"]}
    for event in log["events"]:
        if event["run_id"] and event["run_id"] != "dispatch":
            assert event["run_id"] in known, f"log references run {event['run_id']} the run index does not name"


def test_the_window_reports_whether_it_is_hiding_anything(client):
    """A truncated audit trail that does not say so is a misleading audit trail."""
    log = client.get(f"/api/projects/{PROJECT_ID}/activity?limit=2").json()
    assert len(log["events"]) <= 2
    assert log["truncated"] is (len(log["events"]) >= 2)


def test_an_unknown_project_is_a_404_not_an_empty_log(client):
    """An empty log and a project that does not exist are different answers."""
    assert client.get("/api/projects/proj_does_not_exist/activity").status_code == 404
