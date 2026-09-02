"""The daily production report: a receipt for a day that happened, and a refusal for one that has not.

The refusal is the load-bearing test. A DPR is the most authoritative-looking document this product
issues, so a DPR for a day still ahead would be its most convincing lie — and everything the page
needs to draw one exists days before the day is shot.
"""

from fastapi.testclient import TestClient

from scenepilot.domain.enums import ShootDayStatus
from scenepilot.seed.nightfall import DAY4_ID, build_project
from scenepilot.services.completion import day_completion
from scenepilot.services.day_cost import day_cost
from scenepilot.services.dpr import build_dpr


def _wrapped_day(project):
    return next(d for d in project.shoot_days if d.status == ShootDayStatus.WRAPPED)


def test_a_day_that_has_not_wrapped_has_no_report():
    p = build_project()
    for day in p.shoot_days:
        if day.status != ShootDayStatus.WRAPPED:
            assert build_dpr(p, day) is None


def test_the_report_states_what_the_day_delivered():
    p = build_project()
    day = _wrapped_day(p)
    dpr = build_dpr(p, day)
    record = day_completion(p, day)

    assert dpr["day_number"] == day.day_number and dpr["status"] == "WRAPPED"
    assert dpr["wrap"] == record["wrap"] and dpr["unit_call"] == day.unit_call
    assert [r["scene_number"] for r in dpr["scenes_completed"]] == [r["scene_number"] for r in record["scenes_completed"]]
    assert dpr["minutes_shot"] == record["minutes_shot"]
    assert dpr["crew_size"] == day.crew_size


def test_the_cost_is_the_same_figure_the_day_page_reports():
    """Two documents disagreeing about what one day cost is worse than neither existing."""
    p = build_project()
    day = _wrapped_day(p)
    assert build_dpr(p, day)["cost"] == day_cost(p, day)
    assert build_dpr(p, day)["cost"]["basis"] == "record"


def test_pages_are_withheld_rather_than_part_summed():
    p = build_project()
    day = _wrapped_day(p)
    shot = day.items[0]
    p.scene(shot.scene_id).eighths = None  # one scene the screenplay never paginated

    pages = build_dpr(p, day)["pages"]
    assert pages["shot_eighths"] is None and pages["shot_label"] is None
    assert pages["reason"] and "withheld" in pages["reason"]


def test_cast_worked_is_read_from_completed_scenes_only():
    p = build_project()
    day = _wrapped_day(p)
    dpr = build_dpr(p, day)
    shot_scene_ids = {r["scene_id"] for r in dpr["scenes_completed"]}
    expected = {cid for sid in shot_scene_ids for cid in p.scene(sid).cast_ids}
    assert {c["cast_id"] for c in dpr["cast_worked"]} == expected


def test_the_unit_still_has_to_write_what_the_schedule_cannot_know():
    p = build_project()
    fields = {f["field"] for f in build_dpr(p, _wrapped_day(p))["to_be_completed"]}
    assert "Accidents / incidents" in fields and "Director's notes" in fields


def test_the_endpoint_refuses_an_unwrapped_day_with_the_reason(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    with TestClient(app_module.app) as c:
        refused = c.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}/dpr")
        assert refused.status_code == 409
        assert "has not wrapped" in refused.json()["detail"]
        assert "call sheet" in refused.json()["detail"]  # it names the document that is right instead

        issued = c.get("/api/projects/proj_nightfall/shoot-days/day_3/dpr")
        assert issued.status_code == 200 and issued.json()["dpr"]["day_number"] == 3

        assert c.get("/api/projects/proj_nightfall/shoot-days/day_nope/dpr").status_code == 404
