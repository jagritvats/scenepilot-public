"""Pluggable Labor Rule Packs: DGA / SAG-AFTRA, FWICE / CINTAA, and Custom.

Enforces industry labor standards:
- Compounding meal penalties (DGA/SAG-AFTRA 15/30-minute escalating tiers)
- Daily shift turnaround & forced call penalties (12-hour rest DGA/SAG, 10-hour FWICE)
- Golden Time triple-pay for continuous work past 16 elapsed hours
- Configurable crew and cast department rate multipliers

One pack governs a production at a time, and `active_pack` is the only place that decides which.
Every surface that prices or rejects a schedule reads it from here — the recovery validator, the
interactive stripboard, the call sheet's meal line and the panel that prints the rule in words — so
a producer cannot be shown a 12-hour turnaround in one panel and offered options validated against
a 10-hour one in the next.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ..domain.models import Resource, ScheduleItem
from .timeutil import to_hhmm, to_minutes

if TYPE_CHECKING:
    from ..domain.models import Project


class LaborPreset(StrEnum):
    DGA_SAG = "DGA_SAG"
    FWICE_CINTAA = "FWICE_CINTAA"
    CUSTOM = "CUSTOM"


class LaborRulePack(BaseModel):
    name: str = "DGA / SAG-AFTRA Standard"
    preset: LaborPreset = LaborPreset.DGA_SAG
    standard_shift_hours: float = 12.0
    lunch_due_hours: float = 6.0
    lunch_window_slack_minutes: int = 30
    minimum_lunch_minutes: int = 30
    compounding_meal_penalties: bool = True
    # Tiered penalty per worker: [1st 30m, 2nd 30m, 3rd 30m+]
    meal_penalty_tiers_inr: list[int] = Field(default_factory=lambda: [2000, 3000, 4500])
    minimum_turnaround_hours: float = 12.0  # rest before next call
    forced_call_penalty_enabled: bool = True
    forced_call_flat_penalty_inr: int = 25000  # per lead actor
    golden_time_threshold_hours: float = 16.0
    golden_time_multiplier: float = 3.0
    # How long a gap has to be before this agreement lets a production *release* a performer and
    # re-engage them, instead of paying to hold them through it. It is the single lever a UPM has
    # against hold-day cost, and it is what makes the DOOD a budget document rather than a grid.
    #
    # `None` means this pack models no such provision — which is not the same as a zero-day one, and
    # the difference is the whole point: under a pack with no drop-and-pickup, *every* day between a
    # performer's first and last call is a paid hold, and no amount of rescheduling changes that.
    drop_pickup_minimum_days: int | None = 10


# Standard Presets
DGA_SAG_PACK = LaborRulePack(
    name="DGA / SAG-AFTRA Standard (North America)",
    preset=LaborPreset.DGA_SAG,
    standard_shift_hours=12.0,
    lunch_due_hours=6.0,
    lunch_window_slack_minutes=15,
    minimum_lunch_minutes=30,
    compounding_meal_penalties=True,
    meal_penalty_tiers_inr=[2100, 2900, 4200],  # ~$25, $35, $50 USD
    minimum_turnaround_hours=12.0,
    forced_call_penalty_enabled=True,
    forced_call_flat_penalty_inr=35000,
    golden_time_threshold_hours=16.0,
    golden_time_multiplier=3.0,
    # SAG-AFTRA's day-performer drop-and-pickup: a performer released for at least ten intervening
    # days may be re-engaged without being paid for the gap.
    drop_pickup_minimum_days=10,
)

FWICE_CINTAA_PACK = LaborRulePack(
    name="FWICE / CINTAA Standard (India)",
    preset=LaborPreset.FWICE_CINTAA,
    standard_shift_hours=12.0,
    lunch_due_hours=6.0,
    lunch_window_slack_minutes=60,
    minimum_lunch_minutes=30,
    compounding_meal_penalties=False,
    meal_penalty_tiers_inr=[5000, 5000, 5000],  # Flat meal penalty
    minimum_turnaround_hours=10.0,
    forced_call_penalty_enabled=False,
    forced_call_flat_penalty_inr=0,
    golden_time_threshold_hours=18.0,
    golden_time_multiplier=2.0,
    # This pack models no drop-and-pickup provision, so a performer engaged across a gap is held and
    # paid through it. Stated as `None` rather than as some large number, because "the mechanism does
    # not exist here" and "the mechanism needs a very long gap" are different answers to a UPM asking
    # whether a hold run can be released.
    drop_pickup_minimum_days=None,
)


def get_rule_pack(preset: str = "DGA_SAG") -> LaborRulePack:
    if preset.upper() in {"FWICE", "FWICE_CINTAA", "CINTAA"}:
        return FWICE_CINTAA_PACK
    return DGA_SAG_PACK


# Which body's agreement a unit actually works under is a fact about where it shoots, so it is read
# from the production rather than configured: a Mumbai crew is FWICE/CINTAA, and quoting them
# DGA/SAG-AFTRA meal tiers would be wrong in both directions at once — the wrong rest norm on the
# board and the wrong currency of penalty in the message.
PRESET_BY_COUNTRY = {"IN": LaborPreset.FWICE_CINTAA}
DEFAULT_PRESET = LaborPreset.DGA_SAG
PACKS: dict[LaborPreset, LaborRulePack] = {
    LaborPreset.DGA_SAG: DGA_SAG_PACK,
    LaborPreset.FWICE_CINTAA: FWICE_CINTAA_PACK,
}


def active_preset(project: Project | None) -> LaborPreset:
    code = (getattr(project, "country_code", "") or "").strip().upper()
    return PRESET_BY_COUNTRY.get(code, DEFAULT_PRESET)


def active_pack(project: Project | None) -> LaborRulePack:
    """The one pack this production is validated, priced and described under."""
    return PACKS[active_preset(project)]


def evaluate_meal_penalties(
    pack: LaborRulePack,
    unit_call_min: int,
    ordered_items: list[ScheduleItem],
    crew_size: int = 45,
    cast_count: int = 0,
) -> tuple[int, list[str]]:
    """Evaluate lunch timing against labor rule pack.

    A compounding penalty is owed per person who was held through the meal window, so the multiplier
    is the day's own meal count — the crew on the call plus the cast working the scheduled scenes —
    and the message names both halves. It used to be `min(20, crew_size)`, a stand-in for "key crew"
    that no state supported: on a 45-crew day it silently discarded 25 people and then printed the
    result as the board's authoritative rupee figure.

    Returns: (total_penalty_cost_inr, messages).
    """
    if not ordered_items:
        return 0, []

    due = unit_call_min + int(pack.lunch_due_hours * 60)
    lo = due - pack.lunch_window_slack_minutes
    hi = due + pack.lunch_window_slack_minutes
    day_end = max(to_minutes(i.end) for i in ordered_items)

    if day_end <= due:
        return 0, []

    # Look for a lunch gap of at least minimum_lunch_minutes
    gaps = [
        (to_minutes(a.end), to_minutes(b.start))
        for a, b in zip(ordered_items, ordered_items[1:])
    ]
    has_lunch = any(
        min(ge, hi) - max(gs, lo) >= pack.minimum_lunch_minutes for gs, ge in gaps
    )

    if has_lunch:
        return 0, []

    # Compute penalty: how late is lunch pushed?
    # Find the earliest gap after 'due' or distance to day_end
    late_minutes = day_end - due
    tiers = pack.meal_penalty_tiers_inr

    if not pack.compounding_meal_penalties:
        # Flat penalty
        cost = tiers[0]
        msg = f"No {pack.minimum_lunch_minutes}-min meal break near {to_hhmm(due)} — flat meal penalty (≈₹{cost:,})"
        return cost, [msg]

    # Compounding DGA/SAG style: 1st 30m, 2nd 30m, 3rd+ 30m
    num_half_hours = math.ceil(late_minutes / 30.0)
    per_worker = 0
    for h in range(num_half_hours):
        idx = min(h, len(tiers) - 1)
        per_worker += tiers[idx]

    covered = max(1, crew_size + cast_count)
    basis = f"{crew_size} crew + {cast_count} cast" if cast_count else f"{crew_size} crew"
    total_cost = per_worker * covered

    msg = (
        f"No {pack.minimum_lunch_minutes}-min meal break in the window around {to_hhmm(due)}; "
        f"the day runs {late_minutes} min past it "
        f"({num_half_hours} penalty period(s) × {covered} on the day's meal count — {basis}) "
        f"under {pack.name} — compounding penalty: ₹{total_cost:,}"
    )
    return total_cost, [msg]


def evaluate_turnaround_rest(
    pack: LaborRulePack,
    last_end_min: int,
    next_call_min: int | None,
    affected_cast_count: int = 1,
) -> tuple[int, list[str]]:
    """Evaluate shift-to-shift turnaround rest.

    Returns: (penalty_cost_inr, messages).
    """
    if next_call_min is None:
        return 0, []

    rest_minutes = next_call_min - last_end_min
    required_minutes = int(pack.minimum_turnaround_hours * 60)

    if rest_minutes >= required_minutes:
        return 0, []

    deficit = required_minutes - rest_minutes
    cost = 0
    msgs = []

    hours_rest = rest_minutes // 60
    mins_rest = rest_minutes % 60
    norm_hours = int(pack.minimum_turnaround_hours)
    next_call = to_hhmm(next_call_min % (24 * 60))

    if pack.forced_call_penalty_enabled and deficit > 0:
        cost = pack.forced_call_flat_penalty_inr * max(1, affected_cast_count)
        msgs.append(
            f"Rest turnaround of {hours_rest}h{mins_rest:02d} breaches {norm_hours}h union rest "
            f"({deficit} min deficit) before tomorrow's {next_call} unit call under {pack.name} "
            f"— Forced Call penalty incurred: ₹{cost:,}"
        )
    else:
        msgs.append(
            f"Rest turnaround of {hours_rest}h{mins_rest:02d} is under the {norm_hours}h union norm "
            f"({deficit} min deficit) before tomorrow's {next_call} unit call under {pack.name}"
        )

    return cost, msgs


def evaluate_golden_time(
    pack: LaborRulePack,
    unit_call_min: int,
    last_end_min: int,
    hourly_ot_rate: int = 7500,
) -> tuple[int, list[str]]:
    """Evaluate Golden Time for shifts exceeding golden_time_threshold_hours."""
    elapsed = last_end_min - unit_call_min
    threshold = int(pack.golden_time_threshold_hours * 60)

    if elapsed <= threshold:
        return 0, []

    golden_minutes = elapsed - threshold
    hours = golden_minutes / 60.0
    # Additional surcharge beyond regular overtime: (multiplier - 1) * hourly_ot_rate
    surcharge = math.ceil(hours * (pack.golden_time_multiplier - 1.0) * hourly_ot_rate)

    msg = (
        f"Shift elapsed duration {elapsed // 60}h{elapsed % 60:02d} exceeds {int(pack.golden_time_threshold_hours)}h Golden Time limit "
        f"({golden_minutes} min at {pack.golden_time_multiplier}× rate) — Golden Time surcharge: ₹{surcharge:,}"
    )
    return surcharge, [msg]
