"""Spend containment: the priced endpoints can refuse, and a discarded monitor stops billing.

The hosted demo is a public URL with no auth, up for weeks. `ParallelTaskTool.max_runs` reads like
a budget but cannot be one — the tool is constructed per request, so `self.calls` is zero every
time — and a Parallel monitor keeps executing on Parallel's schedule long after the project that
created it has been reset away. Both are money leaving the account with nothing in the product able
to say no, which is a strange position for an app whose whole thesis is that no expensive call fires
implicitly. A refusal here is shaped exactly like a disabled feature's, because it says the same
thing: a priced capability, named and costed, that this deployment is not spending on right now.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from scenepilot.config import settings as default_settings
from scenepilot.domain.models import MonitorRecord
from scenepilot.seed.nightfall import PROJECT_ID
from scenepilot.services.budget import CallBudget


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _settings(**over):
    # `mode="live"` because that is the only mode where any of this spends money: outside it every
    # recorded endpoint is answered from the repository, so there is nothing to book and nothing to
    # refuse. The replay exemption has its own tests at the bottom of this file.
    base = {"mode": "live", "paid_call_budget": 5, "paid_call_window_s": 3600, "paid_call_cooldown_s": 60}
    return replace(default_settings, **{**base, **over})


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #


def test_the_same_priced_call_on_the_same_subject_is_refused_until_the_cooldown_passes():
    clock, budget, s = _Clock(), None, _settings()
    budget = CallBudget(clock)

    assert budget.charge("dossier", "loc_rooftop", settings=s) is None
    refusal = budget.charge("dossier", "loc_rooftop", settings=s)
    assert refusal is not None and refusal["reason"] == "cooldown"
    assert 0 < refusal["retry_after_s"] <= 61

    clock.advance(61)
    assert budget.charge("dossier", "loc_rooftop", settings=s) is None


def test_a_cooldown_is_per_subject_not_per_endpoint():
    budget, s = CallBudget(_Clock()), _settings()
    assert budget.charge("dossier", "loc_rooftop", settings=s) is None
    assert budget.charge("dossier", "loc_alley", settings=s) is None
    assert budget.charge("substitutes", "loc_rooftop", settings=s) is None


def test_the_cap_counts_what_a_call_actually_costs_and_then_refuses_everything():
    budget, s = CallBudget(_Clock()), _settings(paid_call_cooldown_s=0)
    assert budget.charge("preflight", "day_6", units=4, settings=s) is None  # four locations, four Task runs
    refusal = budget.charge("preflight", "day_4", units=2, settings=s)
    assert refusal is not None and refusal["reason"] == "cap" and refusal["spent"] == 4
    assert budget.charge("dossier", "loc_rooftop", settings=s) is None  # one slot left, and it fits
    assert budget.charge("dossier", "loc_alley", settings=s)["reason"] == "cap"


def test_the_cap_is_a_rolling_window_not_a_lifetime():
    clock = _Clock()
    budget, s = CallBudget(clock), _settings(paid_call_budget=1, paid_call_cooldown_s=0)
    assert budget.charge("dossier", "loc_rooftop", settings=s) is None
    assert budget.charge("dossier", "loc_alley", settings=s)["reason"] == "cap"
    clock.advance(3_601)
    assert budget.charge("dossier", "loc_alley", settings=s) is None


def test_a_refusal_carries_the_same_fields_a_disabled_feature_does():
    budget, s = CallBudget(_Clock()), _settings(paid_call_budget=1)
    budget.charge("dossier", "loc_rooftop", settings=s)
    refusal = budget.charge("dossier", "loc_alley", settings=s)
    assert {"feature", "env", "cost", "message"} <= set(refusal)  # exactly what `require_feature` raises
    assert refusal["env"].startswith("SCENEPILOT_PAID_CALL_BUDGET=")
    assert "$0.025" in refusal["cost"] and "priced Parallel calls" in refusal["message"]


def test_a_budget_of_zero_or_less_means_uncapped_and_a_cooldown_of_zero_means_no_wait():
    budget, s = CallBudget(_Clock()), _settings(paid_call_budget=0, paid_call_cooldown_s=0)
    for _ in range(50):
        assert budget.charge("dossier", "loc_rooftop", settings=s) is None
    assert budget.state(s)["spent"] == 50 and budget.state(s)["remaining"] is None


def test_the_ledger_reports_what_is_left_and_can_be_reset():
    budget, s = CallBudget(_Clock()), _settings()
    budget.charge("preflight", "day_6", units=2, settings=s)
    assert budget.state(s)["spent"] == 2 and budget.state(s)["remaining"] == 3 and budget.state(s)["budget"] == 5
    budget.reset()
    assert budget.state(s)["spent"] == 0 and budget.state(s)["remaining"] == 5


# --------------------------------------------------------------------------- #
# Through the service
# --------------------------------------------------------------------------- #


def _api(monkeypatch, **over):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    monkeypatch.setattr(app_module, "repo", repo)
    monkeypatch.setattr(app_module, "settings", _settings(warm_demo=False, parallel_task_enabled=True, parallel_api_key="test-key", **over))
    return app_module, repo


def test_a_second_dossier_click_is_refused_as_a_priced_call_not_an_error(monkeypatch):
    app_module, _ = _api(monkeypatch)
    calls: list[str] = []

    def _dossier(self, resource, date=None, prior=None):
        calls.append(resource.id)
        return _ok_task_run(resource)

    monkeypatch.setattr(app_module.ParallelTaskTool, "dossier", _dossier)

    with TestClient(app_module.app) as c:
        assert c.post(f"/api/projects/{PROJECT_ID}/resources/loc_rooftop/dossier").status_code == 200
        refused = c.post(f"/api/projects/{PROJECT_ID}/resources/loc_rooftop/dossier")

    assert refused.status_code == 501
    detail = refused.json()["detail"]
    assert detail["feature"] == "dossier" and detail["reason"] == "cooldown"
    assert "$0.025" in detail["cost"] and detail["env"].startswith("SCENEPILOT_PAID_CALL_COOLDOWN_S=")
    assert calls == ["loc_rooftop"]  # the refused click never reached Parallel


def test_a_request_that_was_going_to_fail_anyway_never_costs_a_slot(monkeypatch):
    app_module, _ = _api(monkeypatch)
    with TestClient(app_module.app) as c:
        assert c.post(f"/api/projects/{PROJECT_ID}/resources/cast_aarav/dossier").status_code == 400
        assert c.post(f"/api/projects/{PROJECT_ID}/resources/nope/dossier").status_code == 404
        assert c.get("/api/features").json()["budget"]["spent"] == 0


def test_the_features_route_publishes_what_is_left_to_spend(monkeypatch):
    app_module, _ = _api(monkeypatch)
    with TestClient(app_module.app) as c:
        budget = c.get("/api/features").json()["budget"]
    assert budget["budget"] == 5 and budget["remaining"] == 5 and budget["cooldown_s"] == 60


def test_the_budget_starts_full_on_a_fresh_process(monkeypatch):
    app_module, _ = _api(monkeypatch)
    monkeypatch.setattr(app_module.ParallelTaskTool, "dossier", lambda self, resource, date=None, prior=None: _ok_task_run(resource))
    with TestClient(app_module.app) as c:
        c.post(f"/api/projects/{PROJECT_ID}/resources/loc_rooftop/dossier")
        assert c.get("/api/features").json()["budget"]["spent"] == 1
    with TestClient(app_module.app) as c:
        assert c.get("/api/features").json()["budget"]["spent"] == 0


def _ok_task_run(resource):
    from scenepilot.domain.models import TaskRun, utcnow

    return TaskRun(project_id=PROJECT_ID, resource_id=resource.id, processor="core-fast", input="", status="OK", output={}, finished_at=utcnow())


# --------------------------------------------------------------------------- #
# Monitors stop billing when the project that created them is thrown away
# --------------------------------------------------------------------------- #


def _watched(repo, *, status: str = "active") -> None:
    p = repo.get_project(PROJECT_ID)
    p.monitors.append(MonitorRecord(id="mon_weather_1", project_id=p.id, shoot_day_id="day_4", kind="WEATHER", status=status))
    p.monitors.append(MonitorRecord(id="mon_dossier_1", project_id=p.id, kind="DOSSIER", monitor_type="snapshot", resource_id="loc_rooftop", status=status))
    repo.save_project(p)


def test_reset_cancels_every_live_monitor_before_it_forgets_them(monkeypatch):
    app_module, repo = _api(monkeypatch)
    cancelled: list[str] = []
    monkeypatch.setattr(app_module.ParallelMonitorTool, "cancel", lambda self, monitor_id: cancelled.append(monitor_id))

    with TestClient(app_module.app) as c:
        _watched(repo)
        body = c.post(f"/api/projects/{PROJECT_ID}/reset").json()

    assert cancelled == ["mon_weather_1", "mon_dossier_1"]
    assert body["cancelled_monitors"] == cancelled and body["uncancelled_monitors"] == []
    assert repo.get_project(PROJECT_ID).monitors == []
    assert any("Cancelled 2 live Parallel monitor" in e.message for e in repo.list_activity(project_id=PROJECT_ID))


def test_a_monitor_parallel_will_not_cancel_is_reported_by_id_and_never_blocks_the_reset(monkeypatch):
    app_module, repo = _api(monkeypatch)

    def _boom(self, monitor_id):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(app_module.ParallelMonitorTool, "cancel", _boom)

    with TestClient(app_module.app) as c:
        _watched(repo)
        body = c.post(f"/api/projects/{PROJECT_ID}/reset").json()

    assert body["ok"] is True and body["cancelled_monitors"] == []
    assert body["uncancelled_monitors"] == ["mon_weather_1", "mon_dossier_1"]
    assert any("may still be billing" in e.message for e in repo.list_activity(project_id=PROJECT_ID))


def test_a_simulated_monitor_is_not_a_parallel_object_and_is_not_cancelled(monkeypatch):
    app_module, repo = _api(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(app_module.ParallelMonitorTool, "cancel", lambda self, monitor_id: calls.append(monitor_id))

    with TestClient(app_module.app) as c:
        _watched(repo, status="simulated")
        assert c.post(f"/api/projects/{PROJECT_ID}/reset").json()["cancelled_monitors"] == []

    assert calls == []


# --------------------------------------------------------------------------- #
# ...and cancelling them cannot hold the safety button open
#
# "Reset demo state" is what a presenter presses when the demo has gone sideways, so it is the one
# button that may never wait on somebody else's API. Cancelling is still attempted, because a
# monitor nobody cancels keeps billing — but inside a bounded slice of time, with whatever was not
# reached reported by id rather than waited for.
# --------------------------------------------------------------------------- #


def test_a_slow_parallel_cannot_hold_the_reset_open(monkeypatch):
    import time

    app_module, repo = _api(monkeypatch)
    attempted: list[str] = []

    def _slow(self, monitor_id):
        attempted.append(monitor_id)
        time.sleep(0.05)

    monkeypatch.setattr(app_module.ParallelMonitorTool, "cancel", _slow)
    monkeypatch.setattr(app_module, "RESET_CANCEL_BUDGET_S", 0.01)

    with TestClient(app_module.app) as c:
        _watched(repo)
        started = time.perf_counter()
        body = c.post(f"/api/projects/{PROJECT_ID}/reset").json()
        elapsed = time.perf_counter() - started

    assert attempted == ["mon_weather_1"]  # the second one is past the budget and is never called
    assert body["ok"] is True and body["cancelled_monitors"] == ["mon_weather_1"]
    assert body["uncancelled_monitors"] == ["mon_dossier_1"]
    assert elapsed < 1.0
    assert repo.get_project(PROJECT_ID).monitors == []
    assert any("may still be billing" in e.message for e in repo.list_activity(project_id=PROJECT_ID))


def test_each_cancel_on_the_reset_path_is_given_a_short_timeout_and_no_retries(monkeypatch):
    """The wall clock alone is not a bound: one hung request could still outlive it."""
    app_module, repo = _api(monkeypatch)
    built: list[dict] = []

    class _Tool:
        def __init__(self, settings=None, timeout=None, max_retries=None):
            built.append({"timeout": timeout, "max_retries": max_retries})

        def cancel(self, monitor_id):
            return None

    monkeypatch.setattr(app_module, "ParallelMonitorTool", _Tool)

    with TestClient(app_module.app) as c:
        _watched(repo)
        assert c.post(f"/api/projects/{PROJECT_ID}/reset").json()["uncancelled_monitors"] == []

    assert built == [{"timeout": app_module.RESET_CANCEL_CALL_TIMEOUT_S, "max_retries": 0}]
    assert 0 < app_module.RESET_CANCEL_CALL_TIMEOUT_S < app_module.RESET_CANCEL_BUDGET_S


def test_the_default_parallel_client_still_gets_the_patient_timeout():
    from scenepilot.tools.parallel_monitor import ParallelMonitorTool

    assert (ParallelMonitorTool().session.timeout, ParallelMonitorTool().session.max_retries) == (60.0, 2)
    brisk = ParallelMonitorTool(timeout=3.0, max_retries=0)
    assert (brisk.session.timeout, brisk.session.max_retries) == (3.0, 0)


# --------------------------------------------------------------------------- #
# What the refusal tells you to change
# --------------------------------------------------------------------------- #


def test_the_features_route_names_the_budget_it_is_actually_running(monkeypatch):
    """The refusals carry the configured number; the advertised setting must not be an ellipsis."""
    app_module, _ = _api(monkeypatch)
    with TestClient(app_module.app) as c:
        env = c.get("/api/features").json()["budget"]["env"]
    assert env == "SCENEPILOT_PAID_CALL_BUDGET=5"


# --------------------------------------------------------------------------- #
# A guard on spend may not fire where there is no spend
#
# Outside `SCENEPILOT_MODE=live` a rescue, a plan and a dossier are all answered from a committed
# recording: no request leaves the process. A cooldown there books money nobody spent, tells a judge
# that running the rain fixture again "would ask the same question again at full price", and blocks
# the one path a demo is asked to repeat more than any other.
# --------------------------------------------------------------------------- #


def test_a_recorded_call_is_free_outside_live_mode_and_is_never_booked_or_refused():
    budget, s = CallBudget(_Clock()), _settings(mode="replay", paid_call_budget=1)
    for _ in range(20):
        assert budget.charge("disruption", "day_4", settings=s) is None  # no cooldown, no cap
    assert budget.state(s)["spent"] == 0
    assert budget.state(s)["mode"] == "replay"


def test_replay_does_not_exempt_a_monitor_because_replay_does_not_make_one_free():
    """A monitor is created on Parallel's side and bills daily until cancelled; nothing replays it."""
    budget, s = CallBudget(_Clock()), _settings(mode="replay")
    assert budget.charge("monitors", "day_4", settings=s) is None
    refusal = budget.charge("monitors", "day_4", settings=s)
    assert refusal is not None and refusal["reason"] == "cooldown"
    assert budget.state(s)["spent"] == 1
    assert budget.state(s)["charged_in_this_mode"] == ["monitors"]


def test_an_unclassified_endpoint_is_assumed_to_spend_real_money():
    budget, s = CallBudget(_Clock()), _settings(mode="replay")
    assert budget.charge("something_new", "x", settings=s) is None
    assert budget.charge("something_new", "x", settings=s)["reason"] == "cooldown"


def test_a_refusal_only_ever_names_a_price_that_was_really_going_to_be_charged():
    from scenepilot.services.budget import PRICED, costs_money

    live, replay = _settings(mode="live"), _settings(mode="replay")
    for name in PRICED:
        budget = CallBudget(_Clock())
        budget.charge(name, "subject", settings=replay)
        refusal = budget.charge(name, "subject", settings=replay)
        assert (refusal is not None) is costs_money(name, replay)
        assert costs_money(name, live)


def test_re_triggering_the_rain_fixture_in_replay_mode_is_not_refused(monkeypatch):
    """The 'run it again for the next judge' path: two rescues on the same day, seconds apart."""
    app_module, _ = _api(monkeypatch, mode="replay")
    with TestClient(app_module.app) as c:
        first = c.post(f"/api/projects/{PROJECT_ID}/shoot-days/day_4/disruptions", json={"fixture_id": "rain_pm"})
        second = c.post(f"/api/projects/{PROJECT_ID}/shoot-days/day_4/disruptions", json={"fixture_id": "rain_pm"})
        spent = c.get("/api/features").json()["budget"]["spent"]

    assert (first.status_code, second.status_code) == (200, 200)
    assert spent == 0


def test_the_guard_the_rescue_endpoint_calls_still_refuses_a_second_live_rescue():
    import pytest
    from fastapi import HTTPException

    from scenepilot.api.deps import require_budget
    from scenepilot.services.budget import call_budget

    call_budget.reset()
    live = _settings(mode="live")
    require_budget("disruption", "day_4", settings=live)
    with pytest.raises(HTTPException) as raised:
        require_budget("disruption", "day_4", settings=live)
    assert raised.value.status_code == 501 and raised.value.detail["reason"] == "cooldown"

    call_budget.reset()
    replay = _settings(mode="replay")
    for _ in range(5):
        require_budget("disruption", "day_4", settings=replay)  # nothing raised: nothing is being spent
    call_budget.reset()


def test_a_monitor_can_be_cancelled_on_its_own_wherever_it_is_listed(monkeypatch):
    app_module, repo = _api(monkeypatch)
    cancelled: list[str] = []
    monkeypatch.setattr(app_module.ParallelMonitorTool, "cancel", lambda self, monitor_id: cancelled.append(monitor_id))

    with TestClient(app_module.app) as c:
        _watched(repo)
        body = c.post(f"/api/projects/{PROJECT_ID}/monitors/mon_weather_1/cancel").json()
        assert c.post(f"/api/projects/{PROJECT_ID}/monitors/nope/cancel").status_code == 404

    assert cancelled == ["mon_weather_1"] and body["monitor"]["status"] == "cancelled"
    assert [m.status for m in repo.get_project(PROJECT_ID).monitors] == ["cancelled", "active"]
