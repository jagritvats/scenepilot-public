"""What a day costs in consequences — composed from priced terms, and honest about what it withholds.

A roll-up is where a report starts lying, so the properties pinned here are mostly about *not*
counting: the total is exactly its own lines, a term never arrives from two sources, an estimate is
never printed under a record's heading, and a performer whose retention nobody can price is named
rather than counted as zero.
"""

from fastapi.testclient import TestClient

from scenepilot.domain.enums import ConstraintKind, ShootDayStatus
from scenepilot.seed.nightfall import DAY4_ID, build_project
from scenepilot.services.completion import day_completion
from scenepilot.services.day_cost import day_cost, production_cost_strip
from scenepilot.services.recovery import next_day_call
from scenepilot.services.schedule import ValidationContext, validate_schedule


def _card(day_id=DAY4_ID, **kw):
    p = build_project()
    return p, day_cost(p, p.shoot_day(day_id), **kw)


def test_the_total_is_exactly_the_lines_it_shows():
    for day in build_project().shoot_days:
        p = build_project()
        card = day_cost(p, p.shoot_day(day.id))
        assert card["total_inr"] == sum(line["cost_inr"] for line in card["lines"])
        assert all(line["cost_inr"] > 0 for line in card["lines"]), "a zero line is noise, not a cost"


def test_company_moves_are_counted_from_the_day_not_from_the_baseline_delta():
    """`EXTRA_COMPANY_MOVE` measures moves against a baseline, so for a day priced against itself it
    is always zero. Reading it here would report a day with three real moves as costing nothing."""
    p, card = _card()
    day = p.shoot_day(DAY4_ID)
    moves = next(line for line in card["lines"] if line["key"] == "company_moves")
    assert moves["cost_inr"] == 3 * day.company_move_cost
    assert not any(line["key"] == "extra_company_move" for line in card["lines"])


def test_overtime_and_meals_match_the_validator_the_options_are_priced_against():
    """The card and the recovery waterfall have to agree — they are the same violations."""
    p = build_project()
    day = p.shoot_day("day_6")
    ctx = ValidationContext(project=p, day=day, baseline_items=day.items, next_day_call=next_day_call(p, day))
    violations = validate_schedule(ctx, day.items)
    expected = sum(v.cost_inr for v in violations if not v.hard and v.kind == ConstraintKind.MEAL_BREAK)

    card = day_cost(p, day)
    assert expected > 0
    assert next(line["cost_inr"] for line in card["lines"] if line["key"] == "meal") == expected


def test_a_deferred_scene_adds_the_carry_over_at_the_days_own_rate():
    p = build_project()
    day = p.shoot_day(DAY4_ID)
    before = day_cost(p, day)["total_inr"]
    after = day_cost(p, day, deferred_scene_ids=["sc_48"])
    carry = next(line for line in after["lines"] if line["key"] == "carry_over")
    assert carry["cost_inr"] == day.carry_over_cost
    assert after["total_inr"] >= before + day.carry_over_cost  # re-rental may ride along, never less


def test_a_wrapped_day_reports_its_record_and_says_what_it_will_not_estimate():
    p = build_project()
    day = next(d for d in p.shoot_days if d.status == ShootDayStatus.WRAPPED)
    card = day_cost(p, day)
    record = day_completion(p, day)

    assert card["basis"] == "record"
    assert card["labor_pack"] is None  # a shot day is not "projected under" anything
    by_key = {line["key"]: line["cost_inr"] for line in card["lines"]}
    assert by_key.get("overtime", 0) == record["overtime_cost_inr"]
    assert by_key.get("carry_over", 0) == record["carry_over_cost_inr"]
    assert not any(k in by_key for k in ("meal", "company_moves", "rerental")), "never re-estimated after the fact"
    assert any(n["key"] == "as_shot" for n in card["not_priced"])


def test_a_forward_day_names_the_pack_its_numbers_were_priced_under():
    _, card = _card()
    assert card["basis"] == "projected" and card["labor_pack"]


def test_an_unpriced_hold_is_named_rather_than_counted_as_zero():
    p = build_project()
    day = p.shoot_day("day_5")
    held = [r for r in p.resources if r.day_rate_inr]
    assert held, "the seed prices at least one performer"
    for r in held:
        r.day_rate_inr = 0  # the production has stated no rate for anyone

    card = day_cost(p, day)
    assert not any(line["key"] == "cast_holds" for line in card["lines"]), "an unpriced hold is not a zero line"
    note = next((n for n in card["not_priced"] if n["key"] == "cast_holds_unpriced"), None)
    assert note is not None and "day rate" in note["reason"]


def test_the_pickup_day_is_never_charged_to_a_day():
    _, card = _card()
    assert any(n["key"] == "pickup_day" for n in card["not_priced"])
    assert not any(line["key"] == "pickup_day" for line in card["lines"])


def test_only_the_last_day_reports_having_no_next_call_to_measure_rest_against():
    p = build_project()
    last = max(p.shoot_days, key=lambda d: (d.day_number, d.date))
    for day in p.shoot_days:
        if day.status == ShootDayStatus.WRAPPED:
            continue
        withheld = {n["key"] for n in day_cost(p, day)["not_priced"]}
        assert ("turnaround" in withheld) == (day.id == last.id)


def test_the_production_total_is_the_sum_of_its_days():
    p = build_project()
    strip = production_cost_strip(p)
    assert strip["total_inr"] == sum(d["total_inr"] for d in strip["days"])
    assert [d["day_number"] for d in strip["days"]] == sorted(d.day_number for d in p.shoot_days)


def test_the_api_serves_the_card_the_strip_and_the_one_liner_figure(monkeypatch):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    with TestClient(app_module.app) as c:
        card = c.get(f"/api/projects/proj_nightfall/shoot-days/{DAY4_ID}").json()["day_cost"]
        assert card["total_inr"] == sum(line["cost_inr"] for line in card["lines"])

        strip = c.get("/api/projects/proj_nightfall/cost-strip").json()
        assert strip["total_inr"] == sum(d["total_inr"] for d in strip["days"])
        assert next(d for d in strip["days"] if d["shoot_day_id"] == DAY4_ID)["total_inr"] == card["total_inr"]

        one_liner = c.get("/api/projects/proj_nightfall/one-liner").json()
        assert all("cost" in d for d in one_liner["current"]["days"])
        assert one_liner["production_cost_inr"] == strip["total_inr"]
        # The baseline sheet is a historical document and carries no figure.
        assert one_liner["baseline"] is None or all("cost" not in d for d in one_liner["baseline"]["days"])
