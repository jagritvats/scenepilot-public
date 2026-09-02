"""Tests for LocationScoutAgent and confidence gating."""

from __future__ import annotations

import pytest
from scenepilot.domain.enums import FactBinding
from scenepilot.services.location_scout import scout_location


@pytest.mark.asyncio
async def test_scout_lower_parel_curfew():
    result = await scout_location("Lower Parel Mill Rooftop", "Mumbai")
    assert result.location_name == "Lower Parel Mill Rooftop"
    assert result.verified_curfew == ("22:00", "06:00")
    assert result.binding == FactBinding.HARD
    assert result.confidence == "HIGH"
    assert len(result.citations) >= 1
    assert "mumbaipolice.gov.in" in result.citations[0]


@pytest.mark.asyncio
async def test_scout_bandra_sea_link_drone_ban():
    result = await scout_location("Bandra-Worli Sea Link", "Mumbai")
    assert result.drone_airspace_prohibited is True
    assert result.binding == FactBinding.HARD
    assert "dgca.gov.in" in result.citations[0]
