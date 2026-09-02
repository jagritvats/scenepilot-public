"""What `POST /scenes/{id}/plan` does with the Scene input box.

The box is the one place a producer hands the breakdown agent text, and pressing the button spends
a real Gemini breakdown and several real Parallel searches. So the three states it can be in have to
reach the server as three different requests: untouched, rewritten, and emptied.

They did not. `api.ts` sent `text || null`, which turns an empty string into `null`, and the server
read `if body.text and body.text.strip()`, which treats `null` and `""` alike — so emptying the box
and pressing the button planned on the text that had just been deleted, the one input the producer
had explicitly said not to use.

The planning workflow itself is not under test here and is never allowed to start: `_spawn` is
stubbed, so a run is created and the project is saved exactly as in the real path, and nothing runs.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from scenepilot.api.app import app, repo
from scenepilot.domain.enums import RunKind, RunStatus
from scenepilot.seed.nightfall import PROJECT_ID

SCENE_ID = "sc_42"


@pytest.fixture
def client(monkeypatch):
    """A seeded client whose plan requests reach persistence but never start a workflow.

    `with TestClient(app)` rather than a bare one: the seed is built in the lifespan hook, and a
    bare client never runs it.
    """
    monkeypatch.setattr("scenepilot.api.app._spawn", lambda coro: coro.close())
    with TestClient(app) as c:
        yield c


def _scene(client: TestClient) -> dict:
    return client.get(f"/api/projects/{PROJECT_ID}/scenes/{SCENE_ID}").json()["scene"]


def _plan(client: TestClient, body: dict) -> None:
    """Send one plan request, then retire the run it opened so the next request is not deduplicated."""
    resp = client.post(f"/api/projects/{PROJECT_ID}/scenes/{SCENE_ID}/plan", json=body)
    assert resp.status_code == 200, resp.text
    for run in repo.list_runs(PROJECT_ID, RunKind.PLANNING.value):
        if run.planning and run.planning.scene_id == SCENE_ID and run.status in (RunStatus.PENDING, RunStatus.RUNNING):
            run.status = RunStatus.FAILED
            repo.save_run(run)


def test_an_untouched_box_leaves_the_scene_text_alone(client):
    before = _scene(client)["script_text"]
    assert before, "sc_42 is the seeded scene that carries script text; this test is empty without it"
    _plan(client, {"text": None})
    assert _scene(client)["script_text"] == before


def test_a_rewritten_box_replaces_the_scene_text(client):
    _plan(client, {"text": "  EXT. NEW SLUG — DAY\n\nA different scene entirely.  "})
    assert _scene(client)["script_text"] == "EXT. NEW SLUG — DAY\n\nA different scene entirely."


def test_an_emptied_box_is_not_read_as_an_untouched_one(client):
    """The bug: `""` and `None` arrived identically, so a cleared box planned on the deleted text."""
    _plan(client, {"text": "something the producer then deletes"})
    assert _scene(client)["script_text"]

    _plan(client, {"text": ""})
    assert _scene(client)["script_text"] == ""


def test_a_box_of_only_whitespace_counts_as_emptied(client):
    _plan(client, {"text": "pages that are about to go"})
    assert _scene(client)["script_text"]

    _plan(client, {"text": "   \n  "})
    assert _scene(client)["script_text"] == ""


def test_clearing_the_box_stops_the_scene_citing_a_brief_for_text_it_no_longer_has(client):
    _plan(client, {"text": "pasted pages that become a brief"})
    assert _scene(client)["brief_id"]

    _plan(client, {"text": ""})
    assert _scene(client)["brief_id"] is None
    # the brief itself is history and stays in the project — something really was pasted
    assert client.get(f"/api/projects/{PROJECT_ID}").json()["project"]["briefs"]
