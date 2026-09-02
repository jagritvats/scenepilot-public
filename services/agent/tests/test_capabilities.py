"""Flags for the writes a producer cannot take back.

`require_feature` exists because a Parallel call costs money or takes minutes, so nothing expensive
may fire implicitly. This is the other reason to close something off: a wrapped day is a record and a
committed board replaces the engine's schedule with a human's typed times, and the hosted demo sits
on a public page with no auth for weeks. Same 501, same detail shape, same disabled-with-the-reason
rendering — a different question, so a different registry.

The defaults are deliberately the inverse of the Parallel ones, and that inversion is the thing most
likely to be "tidied up" by someone who reads only one of the two, so it is pinned here.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from scenepilot.api.deps import CAPABILITY_ENV, feature_state, require_capability
from scenepilot.config import settings as default_settings


@pytest.fixture()
def client(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    with TestClient(app_module.app) as c:
        yield c


def test_a_local_clone_may_wrap_a_day_and_may_not_commit_a_board():
    """On in code because a local database is the producer's own; off because it was asked to be."""
    assert default_settings.allow_wrap is True
    assert default_settings.allow_commit_board is False
    assert default_settings.write_capabilities == frozenset({"wrap"})


def test_a_closed_capability_names_the_variable_that_opens_it():
    with pytest.raises(HTTPException) as exc:
        require_capability("commit_board", replace(default_settings, allow_commit_board=False))
    assert exc.value.status_code == 501
    detail = exc.value.detail
    assert detail["env"] == "SCENEPILOT_ALLOW_COMMIT_BOARD=1"
    # The same four keys `require_feature` raises, so one renderer covers both.
    assert {"feature", "env", "cost", "message"} <= set(detail)


def test_an_open_capability_raises_nothing():
    require_capability("commit_board", replace(default_settings, allow_commit_board=True))
    require_capability("wrap", replace(default_settings, allow_wrap=True))


def test_the_features_endpoint_reports_both_kinds(client):
    features = client.get("/api/features").json()["features"]
    for name in CAPABILITY_ENV:
        assert features[name]["kind"] == "write"
        assert features[name]["requires_key"] is False, "a write capability calls nobody"
        assert features[name]["cost"], "the consequence is what a producer reads before an irreversible write"
    for name in ("memory", "task", "findall", "monitors"):
        assert features[name]["kind"] == "parallel"


def test_health_does_not_advertise_a_shoot_day_wrap_as_a_parallel_api(client):
    """The one payload a judge greps on an entry judged for its Parallel integration."""
    health = client.get("/api/health").json()
    assert set(health["parallel_features"]) == {"memory", "task", "findall", "monitors"}
    assert "wrap" not in health["parallel_apis"] and "commit_board" not in health["parallel_apis"]


def test_every_capability_is_reported_whether_it_is_open_or_closed():
    """Closed is rendered disabled with its reason, never hidden — same as a Parallel feature."""
    closed = feature_state(replace(default_settings, allow_wrap=False, allow_commit_board=False))
    assert closed["wrap"]["enabled"] is False and closed["commit_board"]["enabled"] is False
    opened = feature_state(replace(default_settings, allow_wrap=True, allow_commit_board=True))
    assert opened["wrap"]["enabled"] is True and opened["commit_board"]["enabled"] is True
