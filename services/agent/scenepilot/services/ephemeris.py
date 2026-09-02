"""Deterministic astronomical ephemeris engine for film production scheduling.

Calculates precise solar positions, sunrise/sunset, civil twilights, and
golden hour lighting windows (dawn and dusk) for any global coordinates
and shoot date, without requiring external network calls.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple
from pydantic import BaseModel, Field

from ..domain.models import ShootDay
from .timeutil import to_hhmm, to_minutes


# Well-known production hub default coordinates (latitude, longitude, timezone_offset_hours)
CITY_COORDINATES: dict[str, tuple[float, float, float]] = {
    "mumbai": (19.0760, 72.8777, 5.5),
    "london": (51.5074, -0.1278, 1.0),  # BST summer
    "los angeles": (34.0522, -118.2437, -7.0),  # PDT summer
    "new york": (40.7128, -74.0060, -4.0),  # EDT summer
    "vancouver": (49.2827, -123.1207, -7.0),
    "hyderabad": (17.3850, 78.4867, 5.5),
    "sydney": (-33.8688, 151.2093, 10.0),
}


class SolarLightingProfile(BaseModel):
    date: str
    latitude: float
    longitude: float
    timezone_offset: float
    sunrise: str
    sunset: str
    solar_noon: str
    civil_twilight_dawn: str
    civil_twilight_dusk: str
    nautical_twilight_dawn: str
    nautical_twilight_dusk: str
    golden_hour_dawn: tuple[str, str]
    golden_hour_dusk: tuple[str, str]
    day_window: tuple[str, str]
    night_window: tuple[str, str]
    golden_hour_dusk_minutes: int
    sun_azimuth_at_sunset: float


def _day_of_year(iso_date: str) -> int:
    try:
        d = date.fromisoformat(iso_date)
        return d.timetuple().tm_yday
    except ValueError:
        return 240  # late August default


def compute_solar_times(
    iso_date: str,
    latitude: float = 19.0760,
    longitude: float = 72.8777,
    tz_offset_hours: float = 5.5,
) -> SolarLightingProfile:
    """Compute exact solar lighting profile for a shoot date and coordinates.

    Uses standard NOAA solar position equations.
    """
    N = _day_of_year(iso_date)
    # Fractional year in radians
    gamma = (2.0 * math.pi / 365.0) * (N - 1)

    # Equation of time in minutes
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2.0 * gamma)
        - 0.040849 * math.sin(2.0 * gamma)
    )

    # Solar declination angle in radians
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2.0 * gamma)
        + 0.000907 * math.sin(2.0 * gamma)
        - 0.002697 * math.cos(3.0 * gamma)
        + 0.00148 * math.sin(3.0 * gamma)
    )

    lat_rad = math.radians(latitude)

    # Local solar noon in minutes from midnight
    time_offset = eqtime + 4.0 * longitude - 60.0 * tz_offset_hours
    t_solar_noon = 720.0 - time_offset

    def hour_angle_for_zenith(zenith_deg: float) -> float:
        zenith_rad = math.radians(zenith_deg)
        cos_ha = (math.cos(zenith_rad) - math.sin(lat_rad) * math.sin(decl)) / (
            math.cos(lat_rad) * math.cos(decl)
        )
        # Clamp for polar regions
        cos_ha = max(-1.0, min(1.0, cos_ha))
        return math.degrees(math.acos(cos_ha))

    # Standard zenith angles:
    # 90.833° = official sunrise/sunset (34' refraction + 16' solar radius)
    # 96.0° = civil twilight (sun 6° below horizon)
    # 102.0° = nautical twilight (sun 12° below horizon)
    # 84.0° = golden hour high edge (sun 6° above horizon)
    # 94.0° = golden hour low edge (sun 4° below horizon)
    ha_sunrise = hour_angle_for_zenith(90.833)
    ha_civil = hour_angle_for_zenith(96.0)
    ha_nautical = hour_angle_for_zenith(102.0)
    ha_golden_high = hour_angle_for_zenith(84.0)
    ha_golden_low = hour_angle_for_zenith(94.0)

    delta_sunrise = ha_sunrise * 4.0  # 4 minutes per degree
    delta_civil = ha_civil * 4.0
    delta_nautical = ha_nautical * 4.0
    delta_gh_high = ha_golden_high * 4.0
    delta_gh_low = ha_golden_low * 4.0

    sunrise_min = round(t_solar_noon - delta_sunrise)
    sunset_min = round(t_solar_noon + delta_sunrise)
    noon_min = round(t_solar_noon)

    civil_dawn_min = round(t_solar_noon - delta_civil)
    civil_dusk_min = round(t_solar_noon + delta_civil)

    nautical_dawn_min = round(t_solar_noon - delta_nautical)
    nautical_dusk_min = round(t_solar_noon + delta_nautical)

    # Golden hour dawn: from sun -4° (t_solar_noon - delta_gh_low) to sun +6° (t_solar_noon - delta_gh_high)
    gh_dawn_start = round(t_solar_noon - delta_gh_low)
    gh_dawn_end = round(t_solar_noon - delta_gh_high)

    # Golden hour dusk: from sun +6° (t_solar_noon + delta_gh_high) to sun -4° (t_solar_noon + delta_gh_low)
    gh_dusk_start = round(t_solar_noon + delta_gh_high)
    gh_dusk_end = round(t_solar_noon + delta_gh_low)

    # Approximate sunset azimuth
    cos_az = (math.sin(decl) - math.sin(lat_rad) * math.cos(math.radians(90.833))) / (
        math.cos(lat_rad) * math.sin(math.radians(90.833))
    )
    cos_az = max(-1.0, min(1.0, cos_az))
    azimuth_sunset = round(360.0 - math.degrees(math.acos(cos_az)), 1)

    return SolarLightingProfile(
        date=iso_date,
        latitude=latitude,
        longitude=longitude,
        timezone_offset=tz_offset_hours,
        sunrise=to_hhmm(sunrise_min),
        sunset=to_hhmm(sunset_min),
        solar_noon=to_hhmm(noon_min),
        civil_twilight_dawn=to_hhmm(civil_dawn_min),
        civil_twilight_dusk=to_hhmm(civil_dusk_min),
        nautical_twilight_dawn=to_hhmm(nautical_dawn_min),
        nautical_twilight_dusk=to_hhmm(nautical_dusk_min),
        golden_hour_dawn=(to_hhmm(gh_dawn_start), to_hhmm(gh_dawn_end)),
        golden_hour_dusk=(to_hhmm(gh_dusk_start), to_hhmm(gh_dusk_end)),
        day_window=(to_hhmm(sunrise_min), to_hhmm(sunset_min)),
        night_window=(to_hhmm(civil_dusk_min), to_hhmm(civil_dawn_min + 24 * 60)),
        golden_hour_dusk_minutes=gh_dusk_end - gh_dusk_start,
        sun_azimuth_at_sunset=azimuth_sunset,
    )


def city_ephemeris(city_name: str, iso_date: str) -> SolarLightingProfile:
    """Get solar profile for a named film production city."""
    clean = city_name.lower().strip()
    coords = CITY_COORDINATES.get(clean, CITY_COORDINATES["mumbai"])
    return compute_solar_times(iso_date, latitude=coords[0], longitude=coords[1], tz_offset_hours=coords[2])


SOLAR_WINDOW_FIELDS = ("golden_hour_dawn", "golden_hour_dusk", "day_window", "night_window")


def apply_solar_windows(day: ShootDay, city_name: str) -> bool:
    """Put this day's real sun on the day itself. Returns True if any window moved.

    `ShootDay` carries class defaults for these four windows, and the deterministic validator reads
    them: a SUNSET scene is accepted or rejected against `golden_hour_dusk`, a DAY scene against
    `day_window`. Left at their defaults they are an invented sun governing a real rejection, and the
    board ends up printing two different golden hours — the computed one in the headline and the
    hardcoded one in the rule. Deriving them here is what makes those the same number.

    Called when the project is built and again whenever a date moves, because the answer depends on
    the date: a window computed for a day that has since been re-anchored is as stale as a default.
    """
    profile = city_ephemeris(city_name, day.date)
    changed = False
    for name in SOLAR_WINDOW_FIELDS:
        value = getattr(profile, name)
        if tuple(getattr(day, name)) != tuple(value):
            setattr(day, name, value)
            changed = True
    return changed


def city_utc_offset_hours(city_name: str) -> float:
    """A production city's UTC offset, read from the same table the solar maths uses.

    Fixed offsets: exact for India, which has no daylight saving, and within the tolerance a lighting
    profile needs elsewhere. Not a substitute for a tz database on the day another city's clocks move.
    """
    clean = (city_name or "").lower().strip()
    return CITY_COORDINATES.get(clean, CITY_COORDINATES["mumbai"])[2]


def city_today(city_name: str, now: datetime | None = None) -> date:
    """Today where the production is, which is not where the server is.

    Cloud Run runs UTC. Between 00:00 and 05:30 IST the container's `date.today()` is still yesterday
    in Mumbai, which is a whole shoot day out: the hero day anchors onto the wrong date, and the
    Parallel monitor and search queries then name a date the production is not shooting.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment.astimezone(timezone.utc) + timedelta(hours=city_utc_offset_hours(city_name))).date()
