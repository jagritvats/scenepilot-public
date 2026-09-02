"""Tests for labor rule packs (DGA/SAG-AFTRA, FWICE/CINTAA) and multi-unit scheduling."""

from __future__ import annotations

import pytest
from scenepilot.domain.enums import ConstraintKind, ResourceType, TimeOfDay
from scenepilot.domain.models import Project, Resource, Scene, ScheduleItem, ShootDay
from scenepilot.services.labor_rules import (
    DGA_SAG_PACK,
    FWICE_CINTAA_PACK,
    evaluate_golden_time,
    evaluate_meal_penalties,
    evaluate_turnaround_rest,
)
from scenepilot.services.schedule import ValidationContext, validate_schedule


def test_dga_compounding_meal_penalties():
    items = [
        ScheduleItem(id="i1", scene_id="s1", start="06:30", end="14:00"),  # 7.5 hours continuous without lunch
    ]
    # Unit call at 06:30 (390 min). Due at 12:30 (750 min). Day ends at 14:00 (840 min) -> 90 min late = 3 half-hours
    cost_dga, msgs_dga = evaluate_meal_penalties(DGA_SAG_PACK, 390, items, crew_size=20)
    assert cost_dga > 50000  # Compounding across union crew
    assert "compounding penalty" in msgs_dga[0].lower()

    # FWICE flat penalty
    cost_fwice, msgs_fwice = evaluate_meal_penalties(FWICE_CINTAA_PACK, 390, items, crew_size=20)
    assert cost_fwice == 5000
    assert "flat meal penalty" in msgs_fwice[0].lower()


def test_turnaround_rest_evaluation():
    # Wrap at 22:00 (1320 min), next call at 07:00 tomorrow (1440 + 420 = 1860 min)
    # Rest = 1860 - 1320 = 540 min = 9.0 hours
    cost_dga, msgs_dga = evaluate_turnaround_rest(DGA_SAG_PACK, 1320, 1860, affected_cast_count=1)
    assert cost_dga == 35000  # Forced call penalty
    assert "Forced Call penalty incurred" in msgs_dga[0]

    cost_fwice, msgs_fwice = evaluate_turnaround_rest(FWICE_CINTAA_PACK, 1320, 1860, affected_cast_count=1)
    assert cost_fwice == 0  # Advisory only under FWICE
    assert "under the 10h union norm" in msgs_fwice[0]


def test_golden_time_evaluation():
    # 06:00 call (360) to 23:00 wrap (1380) = 17 hours (1 hour golden time past 16h)
    cost, msgs = evaluate_golden_time(DGA_SAG_PACK, 360, 1380, hourly_ot_rate=10000)
    assert cost == 20000  # 1 hour * (3.0 - 1.0) * 10000
    assert "exceeds 16h Golden Time limit" in msgs[0]


def test_multi_unit_concurrent_resource_contention():
    # Two units running concurrently: MAIN and SECOND
    actor = Resource(id="cast_aarav", type=ResourceType.CAST, name="Aarav")
    s1 = Scene(id="s1", number="1", heading="EXT. STREET - DAY", int_ext="EXT", time_of_day=TimeOfDay.DAY, cast_ids=["cast_aarav"])
    s2 = Scene(id="s2", number="2", heading="INT. WAREHOUSE - DAY", int_ext="INT", time_of_day=TimeOfDay.DAY, cast_ids=["cast_aarav"])

    day = ShootDay(id="d1", project_id="p1", day_number=1, date="2026-09-01", unit_call="07:00", standard_hours=12.0)
    p = Project(id="p1", title="Contention Test", scenes=[s1, s2], resources=[actor], shoot_days=[day])

    # Items overlap in time: 08:00 to 11:00 on different units
    item_main = ScheduleItem(id="im", scene_id="s1", start="08:00", end="11:00", unit="MAIN")
    item_second = ScheduleItem(id="is", scene_id="s2", start="09:00", end="12:00", unit="SECOND")

    ctx = ValidationContext(project=p, day=day)
    violations = validate_schedule(ctx, [item_main, item_second])

    # Must detect hard CAST_UNAVAILABLE violation
    cast_viols = [v for v in violations if v.kind == ConstraintKind.CAST_UNAVAILABLE]
    assert len(cast_viols) == 1
    assert cast_viols[0].hard is True
    assert "Aarav cannot be on MAIN Unit and SECOND Unit simultaneously" in cast_viols[0].message
