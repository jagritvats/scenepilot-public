"""Booking pressure — and the difference between unconstrained and uncleared.

The load-bearing property is that availability is three-valued. A resource with no rows anywhere is
genuinely unrestricted; a resource with rows elsewhere but none on this day is what the validator
calls unavailable. A grid that painted those the same colour would report the crew as maximally
constrained and an unbooked lead as free — the exact inverse of the truth in both cases.
"""

from scenepilot.domain.models import Availability
from scenepilot.seed.nightfall import DAY4_ID, build_project
from scenepilot.services.heatmap import build_heatmap


def _row(heatmap, name_fragment: str):
    return next(r for r in heatmap["rows"] if name_fragment.lower() in r["name"].lower())


def _cell(heatmap, name_fragment: str, day_number: int):
    row = _row(heatmap, name_fragment)
    index = next(i for i, d in enumerate(heatmap["days"]) if d["day_number"] == day_number)
    return row["cells"][index]


def test_the_grid_covers_every_day_and_every_called_resource():
    p = build_project()
    h = build_heatmap(p)
    assert [d["day_number"] for d in h["days"]] == [3, 4, 5, 6]
    assert h["rows"], "the seeded schedule calls somebody"
    for row in h["rows"]:
        assert len(row["cells"]) == len(h["days"])
        assert row["days_booked"] >= 1, "a resource this schedule never calls is not a row"


def test_a_resource_with_no_availability_anywhere_reads_as_unconstrained_not_as_tight():
    p = build_project()
    lead = next(r for r in p.resources if "Aarav" in r.name)
    lead.availability = []  # the production has stated no restriction at all

    cell = _cell(build_heatmap(p), "Aarav", 4)
    assert cell["booked"] is True
    assert cell["availability"] == "unconstrained"
    assert cell["pressure"] is None, "no window means no ratio to state, not a full one"


def test_a_resource_booked_on_a_day_it_was_never_cleared_for_reads_as_a_conflict():
    p = build_project()
    lead = next(r for r in p.resources if "Aarav" in r.name)
    # Rows exist, but none covers Day 4 — which is what the validator reads as unavailable.
    lead.availability = [Availability(shoot_day_id="day_5", start="06:00", end="20:00")]

    cell = _cell(build_heatmap(p), "Aarav", 4)
    assert cell["availability"] == "not_booked"
    assert cell["pressure"] == 1.0
    assert cell["conflicts"], "the validator rejects the day, and the cell cites it"


def test_pressure_is_measured_against_the_span_a_resource_is_held_for():
    """A lead called at 10:00 and again at 18:00 is held all day, whatever happens between."""
    p = build_project()
    cell = _cell(build_heatmap(p), "Aarav", 4)
    assert cell["span_minutes"] >= cell["booked_minutes"]
    assert 0 < cell["pressure"] <= 1.0
    assert cell["held_from"] and cell["held_to"]


def test_an_uncalled_day_is_empty_rather_than_slack():
    p = build_project()
    cell = _cell(build_heatmap(p), "Aarav", 3)  # the day-3 splinter unit is an aerial plate, no cast
    assert cell["booked"] is False and cell["pressure"] is None
    assert "not called" in cell["detail"]


def test_conflicts_come_from_the_validator_not_from_this_view():
    p = build_project()
    lead = next(r for r in p.resources if "Aarav" in r.name)
    lead.availability = [Availability(shoot_day_id=DAY4_ID, start="06:00", end="07:00")]

    cell = _cell(build_heatmap(p), "Aarav", 4)
    assert cell["conflicts"], "an hour-long window against a full day is a hard rejection"
    assert any("unavailable" in c.lower() for c in cell["conflicts"])


def test_rows_lead_with_the_resources_in_trouble():
    p = build_project()
    lead = next(r for r in p.resources if "Aarav" in r.name)
    lead.availability = [Availability(shoot_day_id=DAY4_ID, start="06:00", end="07:00")]

    rows = build_heatmap(p)["rows"]
    assert "Aarav" in rows[0]["name"], "a resource in conflict sorts above one that is merely tight"


def test_crew_and_vehicles_are_not_graded():
    """They carry no availability rows by design and would render as uniformly slack."""
    p = build_project()
    graded = {r["type"] for r in build_heatmap(p)["rows"]}
    assert graded <= {"CAST", "LOCATION", "EQUIPMENT"}


def test_the_endpoint_serves_the_grid(monkeypatch):
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    with TestClient(app_module.app) as c:
        body = c.get("/api/projects/proj_nightfall/conflict-heatmap").json()
        assert len(body["days"]) == 4 and body["rows"]
        assert set(body["legend"]) == {"unconstrained", "windowed", "not_booked"}
        assert c.get("/api/projects/nope/conflict-heatmap").status_code == 404
