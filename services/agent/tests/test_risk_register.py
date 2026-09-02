"""The risk register — and the difference between "no risks" and "nobody has looked".

The engine has always written risks and weighted them into readiness; they were visible one scene at
a time, which is the one view in which a register is useless. The property that matters here is the
denominator: an unplanned scene must never be reported as carrying no risk, because that reads as
safe and means unexamined.
"""

from fastapi.testclient import TestClient

from scenepilot.domain.enums import ClaimKind, Severity
from scenepilot.domain.models import ProductionPlan, Risk
from scenepilot.seed.nightfall import build_project
from scenepilot.services.risk_register import build_risk_register


def _plan_scene(project, scene_id: str, *risks: Risk) -> None:
    project.plans[scene_id] = ProductionPlan(scene_id=scene_id, risks=list(risks))


def _risk(title: str, severity: Severity, likelihood: float, **kw) -> Risk:
    return Risk(title=title, description=f"{title} — as researched.", severity=severity, likelihood=likelihood, **kw)


def test_a_cold_production_has_no_register_and_says_why():
    reg = build_risk_register(build_project())
    assert reg["total"] == 0 and reg["scenes_planned"] == 0
    assert reg["empty_note"] and "no scene has been planned" in reg["empty_note"].lower()
    assert len(reg["unplanned_scenes"]) == reg["scenes_total"]


def test_an_unplanned_scene_is_never_reported_as_carrying_no_risk():
    """The sentence this document exists to avoid: '0 risks' for a scene nobody researched."""
    p = build_project()
    _plan_scene(p, "sc_42", _risk("Rooftop structural load", Severity.CRITICAL, 0.3))
    reg = build_risk_register(p)

    assert reg["scenes_planned"] == 1 and reg["scenes_total"] == len(p.scenes)
    unplanned = {s["scene_id"] for s in reg["unplanned_scenes"]}
    assert "sc_42" not in unplanned and len(unplanned) == len(p.scenes) - 1
    assert "not an empty one" in reg["coverage_note"]


def test_risks_are_ordered_by_the_same_exposure_the_readiness_score_sums():
    from scenepilot.services.readiness import SEVERITY_WEIGHT

    p = build_project()
    _plan_scene(
        p,
        "sc_42",
        _risk("Low but certain", Severity.LOW, 1.0),
        _risk("Critical and likely", Severity.CRITICAL, 0.8),
        _risk("High and even", Severity.HIGH, 0.5),
    )
    rows = build_risk_register(p)["risks"]
    assert [r["title"] for r in rows] == ["Critical and likely", "High and even", "Low but certain"]
    for row in rows:
        assert row["exposure"] == round(SEVERITY_WEIGHT[Severity(row["severity"])] * row["likelihood"], 4)


def test_each_risk_carries_the_scene_and_the_days_that_scene_shoots():
    p = build_project()
    _plan_scene(p, "sc_42", _risk("Monsoon exposure", Severity.HIGH, 0.6))
    row = build_risk_register(p)["risks"][0]
    assert row["scene_number"] == "42"
    assert [d["day_number"] for d in row["scheduled_on"]] == [4]


def test_a_planned_scene_with_no_risks_is_covered_not_missing():
    """Planned and clear is a real answer; it must not join the list of scenes nobody examined."""
    p = build_project()
    _plan_scene(p, "sc_42")  # planned, and the run found nothing
    reg = build_risk_register(p)
    assert reg["total"] == 0 and reg["scenes_planned"] == 1
    assert "sc_42" not in {s["scene_id"] for s in reg["unplanned_scenes"]}


def test_grouping_and_counts_agree_with_the_flat_list():
    p = build_project()
    _plan_scene(p, "sc_42", _risk("A", Severity.CRITICAL, 0.5), _risk("B", Severity.HIGH, 0.5))
    _plan_scene(p, "sc_48", _risk("C", Severity.CRITICAL, 0.2, kind=ClaimKind.FACT))
    reg = build_risk_register(p)
    assert reg["counts"] == {"CRITICAL": 2, "HIGH": 1, "MEDIUM": 0, "LOW": 0}
    assert sum(len(v) for v in reg["by_severity"].values()) == reg["total"] == 3


def test_the_endpoint_serves_the_register(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    with TestClient(app_module.app) as c:
        body = c.get("/api/projects/proj_nightfall/risk-register").json()
        assert body["total"] == 0 and body["scenes_total"] == 9
        assert c.get("/api/projects/nope/risk-register").status_code == 404
