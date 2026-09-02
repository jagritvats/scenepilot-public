"""Tests for Phase 4 extensions: Movie Magic MMSX XML export, Force Majeure Insurance Dossier, and Staggered Prep."""

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scenepilot.domain.enums import FactBinding, RunKind, RunStatus, VerificationStatus
from scenepilot.domain.models import (
    BasisCitation,
    Evidence,
    ExternalRule,
    LocationFact,
    Project,
    RescueState,
    Resource,
    Scene,
    ScheduleItem,
    SearchResultItem,
    SearchRun,
    ShootDay,
    WorkflowRun,
    utcnow,
)
from scenepilot.seed.nightfall import DAY4_ID, DAY6_ID, build_project, make_fixture_disruption
from scenepilot.services import insurance_dossier as insurance_dossier_module
from scenepilot.services.callsheet import build_call_sheet
from scenepilot.services.changeset import apply_changeset, build_changeset
from scenepilot.services.export_mmsx import generate_mmsx_xml
from scenepilot.services.impact import analyze_impact
from scenepilot.services.insurance_dossier import compile_insurance_dossier
from scenepilot.services.recovery import generate_candidates


def test_staggered_cast_prep_lead_times():
    """Verify that cast entries contain staggered prep timestamps (pickup, hmu, wardrobe, ready, on_set)."""
    p = build_project()
    day = p.shoot_day("day_4")
    cs = build_call_sheet(p, day)

    assert "cast" in cs
    assert len(cs["cast"]) > 0

    aarav = next((c for c in cs["cast"] if "aarav" in c["name"].lower()), None)
    assert aarav is not None
    # Aarav is lead stunt -> total lead = 30 travel + 60 hmu + 30 wardrobe = 120 mins
    assert "pickup" in aarav
    assert "hmu" in aarav
    assert "wardrobe" in aarav
    assert "ready" in aarav
    assert "on_set" in aarav
    assert "call" in aarav

    # Check temporal ordering: pickup <= hmu <= wardrobe <= ready <= on_set
    from scenepilot.services.timeutil import to_minutes
    p_min = to_minutes(aarav["pickup"])
    h_min = to_minutes(aarav["hmu"])
    w_min = to_minutes(aarav["wardrobe"])
    r_min = to_minutes(aarav["ready"])
    os_min = to_minutes(aarav["on_set"])

    assert p_min <= h_min
    assert h_min <= w_min
    assert w_min <= r_min
    assert r_min <= os_min


def test_stripboard_xml_generation():
    """The stripboard XML is well-formed, complete — and says on its face that it is unofficial."""
    p = build_project()
    day = p.shoot_day("day_4")
    xml_output = generate_mmsx_xml(p, day)

    assert xml_output.startswith("<?xml")
    assert "<ScenePilotStripboard" in xml_output
    assert "<Stripboard>" in xml_output
    assert "<BreakdownSheets>" in xml_output

    # Parse XML to verify valid document structure
    root = ET.fromstring(xml_output)
    assert root.tag == "ScenePilotStripboard"
    assert root.attrib.get("official") == "false"
    assert "Not written or validated by Movie Magic Scheduling" in root.attrib.get("note", "")
    # No product or schema version: the file must not assert conformance to a spec nobody checked it against.
    assert "version" not in root.attrib and "schemaVersion" not in root.attrib

    project_node = root.find("Project")
    assert project_node is not None
    assert project_node.find("Title").text == p.title

    strips = root.findall(".//Strip")
    assert len(strips) == len(day.items)
    for strip in strips:
        assert strip.find("SceneNumber") is not None
        assert strip.find("DurationMinutes") is not None
        assert strip.find("Location") is not None


def test_every_strip_exports_its_scenes_own_page_count():
    """`PagesEighths` used to read a field `Scene` does not have, so it was always the literal 8.

    The export button sits in the day-page header, directly above a board printing the real count
    for the same scene — and the four strips on the hero day are 10, 12, 9 and 8 eighths, not 8, 8,
    8 and 8.
    """
    p = build_project()
    day = p.shoot_day("day_4")
    root = ET.fromstring(generate_mmsx_xml(p, day))

    exported = {s.find("SceneNumber").text: s.find("PagesEighths").text for s in root.findall(".//Strip")}
    assert exported == {str(p.scene(i.scene_id).number): str(p.scene(i.scene_id).eighths) for i in day.items}
    assert len(set(exported.values())) > 1, "one page count on every strip is the old constant"


def test_a_scene_with_no_page_count_exports_no_page_count():
    """Absent, not defaulted: an importer cannot tell an invented page from a measured one."""
    p = build_project()
    day = p.shoot_day("day_4")
    p.scene(day.items[0].scene_id).eighths = None
    root = ET.fromstring(generate_mmsx_xml(p, day))

    strips = root.findall(".//Strip")
    assert strips[0].find("PagesEighths") is None
    assert all(s.find("PagesEighths") is not None for s in strips[1:])


# --------------------------------------------------------------------------- #
# Force Majeure dossier: every row traces to production state, or it is not printed
# --------------------------------------------------------------------------- #

CURFEW_CITATION = "http://indiacode.nic.in/ViewFileUploaded?file=noise_pollution_(regulation_and_control)_rules,_2000..pdf"


def _accepted_curfew(project: Project, resource_id: str = "loc_rooftop") -> LocationFact:
    """The fact the demo's marquee click accepts: a cited statutory curfew that binds the schedule."""
    fact = LocationFact(
        project_id=project.id,
        resource_id=resource_id,
        task_run_id="task_seeded",
        key="noise_curfew",
        label="Noise curfew",
        value="22:00-06:00",
        binding=FactBinding.HARD,
        confidence="high",
        reasoning="The Noise Pollution (Regulation and Control) Rules define night time as 22:00 to 06:00.",
        citations=[BasisCitation(url=CURFEW_CITATION, title=None, excerpts=["Night time shall mean from 10.00 p.m. to 6.00 a.m."])],
        rule=ExternalRule(kind="TIME_WINDOW_BAN", window_start="22:00", window_end="06:00"),
        accepted=True,
        accepted_at=utcnow(),
        accepted_by="Priya Nair (Line Producer)",
    )
    project.location_facts.append(fact)
    return fact


def _rescued_day_4():
    """Day 4's rain rescue taken all the way to an applied ChangeSet, deterministically and offline.

    The verification verdict, the search run and the analyst finding are set the way a completed
    Parallel-verified run persists them, so the packet is compiled from the same shapes the API sees.
    """
    project = build_project()
    day = project.shoot_day(DAY4_ID)
    disruption = make_fixture_disruption(project.id, day.id, "rain_pm")
    disruption.verification_status = VerificationStatus.PARTIALLY_CORROBORATED
    disruption.verification_summary = "IMD confirms intermittent moderate rainfall; the exact window is not stated."
    disruption.verification_confidence = 0.7
    project.disruptions.append(disruption)

    search = SearchRun(
        run_id="run_test", project_id=project.id, purpose="disruption_verification", mode="fast",
        objective=f"Official rain warnings for {project.base_city} on {day.date}.",
        queries=["IMD Mumbai rain warning"], status="REPLAY", replayed=True,
        advanced_settings={"location": "in", "max_results": 6},
        results=[SearchResultItem(url="https://mausam.imd.gov.in/mumbai/", title="RMC Mumbai", publish_date="2026-08-14", excerpts=["Heavy rainfall at isolated places."])],
    )
    disruption.search_run_ids.append(search.id)
    finding = Evidence(
        search_run_id=search.id, claim="Heavy rainfall at isolated places over Mumbai",
        source_url="https://mausam.imd.gov.in/mumbai/", source_title="RMC Mumbai",
        excerpt="Heavy rainfall at isolated places.", publish_date="2026-08-14",
    )
    disruption.evidence_ids.append(finding.id)

    baseline = [i.model_copy() for i in day.items]
    impact = analyze_impact(project, day, disruption)
    options = generate_candidates(project, day, disruption, impact, verification_confidence=0.7)
    chosen = next(o for o in options if o.feasible)
    changeset = build_changeset(project, day, chosen, disruption, "run_test")
    apply_changeset(project, changeset, approved_by="Priya Nair (Line Producer)")

    run = WorkflowRun(
        id="run_test", project_id=project.id, kind=RunKind.RESCUE, status=RunStatus.APPLIED,
        rescue=RescueState(
            shoot_day_id=day.id, disruption_id=disruption.id, stage="applied", baseline=baseline,
            evidence=[finding], options=options, recommended_option_id=chosen.id, changeset=changeset,
            recommendation_rationale=f"Option {chosen.label} is the highest-scoring feasible schedule.",
        ),
    )
    return project, day, disruption, run, chosen, [search]


def test_insurance_dossier_is_compiled_from_the_rescue_run():
    """Peril → mitigation → cost, every value read back off the run that produced it."""
    project, day, disruption, run, chosen, searches = _rescued_day_4()
    dossier = compile_insurance_dossier(project, day, disruption, run, searches)

    assert dossier["claim_type"] == "WEATHER_FORCE_MAJEURE_AND_CIVIL_AUTHORITY"
    assert dossier["claim_status"] == "MITIGATION_APPLIED"
    assert dossier["production"]["fictional"] is True and "fictional production" in dossier["notice"]

    # Peril: the disruption's own fields, and the searches its verification actually fired
    peril = dossier["peril_evidence"]
    assert peril["peril"]["title"] == disruption.title
    assert (peril["peril"]["window_start"], peril["peril"]["window_end"]) == ("13:00", "17:00")
    assert peril["verification"]["status"] == "PARTIALLY_CORROBORATED" and peril["verification"]["confidence_pct"] == 70
    source = peril["certified_sources"][0]
    assert source["search_run_id"] == searches[0].id and source["queries"] == ["IMD Mumbai rain warning"]
    assert source["results"][0]["url"] == "https://mausam.imd.gov.in/mumbai/"
    assert peril["analyst_findings"][0]["source_url"] == "https://mausam.imd.gov.in/mumbai/"

    # Mitigation: the schedules the engine refused, with the constraint that refused each one
    mitigation = dossier["proof_of_mitigation"]
    rejected = mitigation["rejected_alternatives"]
    assert mitigation["alternatives_evaluated"] == len(run.rescue.options)
    assert rejected and all(r["violations"] for r in rejected)
    assert any("overlaps rain expected" in v["description"] for r in rejected for v in r["violations"])
    assert any("Bhuleshwar not available" in v["description"] for r in rejected for v in r["violations"])
    assert mitigation["selected_option"]["label"] == chosen.label
    assert mitigation["decision"]["approved_by"] == "Priya Nair (Line Producer)"
    assert mitigation["decision"]["approved_at_utc"] and mitigation["decision"]["changeset_id"] == run.rescue.changeset.id

    # Before the producer signs, the same option is a recommendation and the packet says so
    run.rescue.changeset = None
    pending = compile_insurance_dossier(project, day, disruption, run, searches)
    assert pending["claim_status"] == "AWAITING_PRODUCER_DECISION"
    assert pending["proof_of_mitigation"]["decision"] is None
    assert pending["cost_delta"]["basis"] == "recommended"


def test_insurance_dossier_costs_are_the_engines_own_arithmetic():
    """Every rupee in the packet is a rate on the day or a violation the chosen schedule broke."""
    project, day, disruption, run, chosen, searches = _rescued_day_4()
    cost = compile_insurance_dossier(project, day, disruption, run, searches)["cost_delta"]

    assert cost["rates"] == {
        "overtime_per_hour_inr": day.overtime_rate_per_hour,
        "carry_over_per_scene_inr": day.carry_over_cost,
        "company_move_inr": day.company_move_cost,
    }
    assert cost["basis"] == "approved"
    assert cost["mitigation_cost_inr"] == chosen.score.estimated_extra_cost_inr
    assert cost["overtime_minutes"] == chosen.score.overtime_minutes
    priced = [v for v in chosen.violations if not v.hard and v.cost_inr]
    assert [(li["kind"], li["amount_inr"]) for li in cost["line_items"]] == [(v.kind.value, v.cost_inr) for v in priced]
    assert sum(li["amount_inr"] for li in cost["line_items"]) == cost["mitigation_cost_inr"]
    assert {a["label"] for a in cost["alternatives_priced"]} == {o.label for o in run.rescue.options}

    # What is not in production state is named and left blank, never filled with a plausible number
    blanks = {row["field"]: row for row in cost["not_in_production_state"]}
    assert set(blanks) == {"policy_number", "insurer", "insured_daily_production_cost_inr", "deductible_inr", "notice_deadline"}
    assert all(row["value"] is None and row["why"] for row in blanks.values())


def test_insurance_dossier_survives_a_producer_accepting_a_hard_fact():
    """The regression: accepting a cited HARD fact used to make every later dossier 500.

    `LocationFact` has `value`, `citations` and `binding` — not `claim`, `source_url` or `authority` —
    and the old compiler read the three fields that do not exist, behind an `if fact.binds` guard that
    only opened once a producer clicked accept.
    """
    project, day, disruption, run, _chosen, searches = _rescued_day_4()
    fact = _accepted_curfew(project)
    assert fact.binds

    dossier = compile_insurance_dossier(project, day, disruption, run, searches)
    row = next(c for c in dossier["constraints_on_record"] if c["fact_id"] == fact.id)
    assert row["value"] == "22:00-06:00" and row["binding"] == "HARD"
    assert row["citations"][0]["url"] == CURFEW_CITATION
    assert row["citations"][0]["excerpt"].startswith("Night time shall mean")
    assert row["accepted_by"] == "Priya Nair (Line Producer)" and row["accepted_at_utc"]
    assert row["rule"] == {"kind": "TIME_WINDOW_BAN", "window_start": "22:00", "window_end": "06:00", "activity": None}
    assert row["location"] == project.resource("loc_rooftop").name


def test_insurance_dossier_computes_whether_an_accepted_curfew_actually_bites():
    """Day 6's night unit runs the rooftop past 22:00, so the accepted curfew rejects it — in minutes."""
    project = build_project()
    day = project.shoot_day(DAY6_ID)
    fact = _accepted_curfew(project)

    dossier = compile_insurance_dossier(project, day)
    assert dossier["claim_status"] == "NO_PERIL_ON_RECORD"
    assert dossier["peril_evidence"] is None and dossier["proof_of_mitigation"] is None

    row = next(c for c in dossier["constraints_on_record"] if c["fact_id"] == fact.id)
    violation = row["current_schedule_violations"][0]
    assert violation["scene_number"] == "58" and violation["minutes"] == 90
    assert violation["evidence_url"] == CURFEW_CITATION
    assert "1 of them broken by the committed schedule" in dossier["summary"]


def test_insurance_dossier_claims_nothing_on_a_day_with_no_peril():
    """No disruption, no recovery, no numbers — a packet that says so rather than one that invents."""
    project = build_project()
    dossier = compile_insurance_dossier(project, project.shoot_day(DAY6_ID))

    assert dossier["claim_status"] == "NO_PERIL_ON_RECORD"
    assert dossier["peril_evidence"] is None and dossier["proof_of_mitigation"] is None
    assert dossier["constraints_on_record"] == []  # nothing accepted yet
    cost = dossier["cost_delta"]
    assert cost["basis"] is None and cost["mitigation_cost_inr"] is None
    assert cost["line_items"] == [] and cost["alternatives_priced"] == []
    assert "No disruption is on record" in dossier["summary"]


def test_no_fabricated_constant_survives_in_the_dossier():
    """The literals the sweep found, gone from the source and absent from the compiled packet."""
    source = Path(insurance_dossier_module.__file__).read_text(encoding="utf-8")
    for literal in (
        "350000",  # UNMITIGATED_SHOOT_DAY_VALUE_INR
        "67500",  # the cost fallback
        "35–55 mm/hr",
        "92%",
        "mausam.imd.gov.in",
        "123456789/2263",
        "APPROVED_FOR_CLAIMS_FILING",
        "South Asia Hub",
        "zero false claims",
        "Cast & Crew Overtime",
        "Extended Equipment Rental",
        "Crew Dinner Catering",
        "Option D",
        "Option E",
    ):
        assert literal not in source, f"{literal!r} is a fabricated constant and must not be in the compiler"

    project, day, disruption, run, _chosen, searches = _rescued_day_4()
    _accepted_curfew(project)
    blob = json.dumps(compile_insurance_dossier(project, day, disruption, run, searches), ensure_ascii=False)
    for ghost in ("350000", "Precipitation probability", "35–55 mm/hr", "APPROVED_FOR_CLAIMS_FILING", "Global / South Asia Hub", "Crew Dinner Catering", "123456789/2263"):
        assert ghost not in blob, f"{ghost!r} is invented and reached the packet"


def test_api_endpoints_phase4():
    """Verify stripboard XML export, insurance dossier, and dispatch reping HTTP endpoints."""
    from fastapi.testclient import TestClient
    from scenepilot.api.app import app

    # `with`, so the lifespan seeds the project: without it this module only passed when some earlier
    # module happened to have seeded the shared repository first.
    with TestClient(app) as client:
        # Test stripboard XML export endpoint
        res_mmsx = client.get("/api/projects/proj_nightfall/shoot-days/day_4/export/mmsx")
        assert res_mmsx.status_code == 200
        assert "ScenePilotStripboard" in res_mmsx.text
        assert res_mmsx.headers["content-type"] == "application/xml"
        # served as .xml — .mmsx/.sex are MMS's own exchange extensions and would promise an untested import
        assert res_mmsx.headers["content-disposition"].endswith('_stripboard.xml"')

        # Test Insurance Dossier endpoint
        res_ins = client.get("/api/projects/proj_nightfall/shoot-days/day_4/insurance-dossier")
        assert res_ins.status_code == 200
        data = res_ins.json()
        assert "dossier_id" in data
        assert data["claim_type"] == "WEATHER_FORCE_MAJEURE_AND_CIVIL_AUTHORITY"

        # Test Re-ping endpoint
        res_reping = client.post("/api/projects/proj_nightfall/shoot-days/day_4/dispatch/re-ping")
        assert res_reping.status_code == 200
        assert "repinged_count" in res_reping.json()


def test_the_demo_sequence_that_used_to_500(monkeypatch):
    """Rescue Day 4 → approve → accept the Rooftop A curfew → both dossiers still answer 200.

    The exact click order the demo runs, against the real routes: before the acceptance the endpoint
    answered fine, and the moment a producer accepted a HARD fact every later open of it crashed.
    """
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))

    with TestClient(app_module.app) as client:
        started = client.post("/api/projects/proj_nightfall/shoot-days/day_4/disruptions", json={"fixture_id": "rain_pm"})
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        for _ in range(40):
            run = client.get(f"/api/runs/{run_id}").json()["run"]
            if run["status"] in ("AWAITING_APPROVAL", "FAILED"):
                break
            time.sleep(0.25)
        assert run["status"] == "AWAITING_APPROVAL", run.get("error")

        approved = client.post(f"/api/runs/{run_id}/approve", json={"option_id": run["rescue"]["recommended_option_id"], "approved_by": "Priya Nair (Line Producer)"})
        assert approved.status_code == 200

        facts = client.get("/api/projects/proj_nightfall/shoot-days/day_6").json()["location_facts"]
        curfew = next(f for f in facts if f["resource_id"] == "loc_rooftop" and f["binding"] == "HARD" and f["rule"])
        assert client.post(f"/api/projects/proj_nightfall/facts/{curfew['id']}/accept", json={"accepted_by": "Priya Nair (Line Producer)"}).status_code == 200

        day4 = client.get("/api/projects/proj_nightfall/shoot-days/day_4/insurance-dossier")
        day6 = client.get("/api/projects/proj_nightfall/shoot-days/day_6/insurance-dossier")

    assert (day4.status_code, day6.status_code) == (200, 200)
    d4, d6 = day4.json(), day6.json()
    assert d4["claim_status"] == "MITIGATION_APPLIED"
    assert d4["proof_of_mitigation"]["decision"]["approved_by"] == "Priya Nair (Line Producer)"
    assert d4["peril_evidence"]["certified_sources"], "the verification searches never reached the packet"
    assert d4["cost_delta"]["mitigation_cost_inr"] > 0
    # the accepted curfew is on both days' records, and on Day 6 it rejects the rooftop night scene
    for dossier in (d4, d6):
        row = next(c for c in dossier["constraints_on_record"] if c["fact_id"] == curfew["id"])
        assert row["accepted_by"] == "Priya Nair (Line Producer)" and row["citations"]
    assert d6["constraints_on_record"][0]["current_schedule_violations"][0]["scene_number"] == "58"
