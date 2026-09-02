"""Tests for astronomical ephemeris and solar calculations."""

from __future__ import annotations

import pytest
from scenepilot.services.ephemeris import city_ephemeris, compute_solar_times
from scenepilot.services.timeutil import to_minutes


def test_mumbai_solar_times():
    profile = city_ephemeris("mumbai", "2026-08-29")
    sr = to_minutes(profile.sunrise)
    ss = to_minutes(profile.sunset)
    noon = to_minutes(profile.solar_noon)

    # Sunrise between 06:00 and 06:45
    assert 6 * 60 <= sr <= 6 * 60 + 45
    # Sunset between 18:30 and 19:30
    assert 18 * 60 + 30 <= ss <= 19 * 60 + 30
    # Solar noon between sunrise and sunset
    assert sr < noon < ss

    # Golden hour dusk duration should be roughly 45 to 65 minutes
    assert 40 <= profile.golden_hour_dusk_minutes <= 75

    # Golden hour dusk bounds
    gh_start = to_minutes(profile.golden_hour_dusk[0])
    gh_end = to_minutes(profile.golden_hour_dusk[1])
    assert gh_start < ss < gh_end


def test_london_and_la_ephemeris():
    london = city_ephemeris("london", "2026-06-21")  # Summer solstice
    la = city_ephemeris("los angeles", "2026-06-21")

    # London midsummer has long daylight (sunset > 21:00)
    assert to_minutes(london.sunset) > 20 * 60 + 30
    # LA sunset > 19:45
    assert to_minutes(la.sunset) > 19 * 60 + 30
