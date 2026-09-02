"""Turn a Parallel Monitor event into a *draft* disruption (deterministic; producer confirms)."""

from __future__ import annotations

import re

from ..domain.enums import DisruptionType
from ..domain.models import Disruption, MonitorRecord, Project, ShootDay
from .timeutil import to_minutes

_TIME_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")
_WINDOW_RE = re.compile(r"\b(\d{1,2}[:.]\d{2})\s*(?:hrs?|h)?\s*(?:to|-|–|—|and|until|till)\s*(\d{1,2}[:.]\d{2})", re.IGNORECASE)
_RAIN_WORDS = ("rain", "shower", "thunderstorm", "downpour", "cloudburst", "orange alert", "red alert", "gusty", "squall")
_ROAD_WORDS = ("closure", "closed", "diversion", "bandh", "strike", "protest", "traffic", "blocked", "waterlogging")

KIND_TO_TYPE = {"WEATHER": DisruptionType.WEATHER, "TRANSPORT": DisruptionType.TRANSPORT, "REGULATORY": DisruptionType.REGULATORY}


def parse_window(text: str) -> tuple[str | None, str | None]:
    m = _WINDOW_RE.search(text)
    if not m:
        return None, None
    a, b = (x.replace(".", ":") for x in m.groups())
    try:
        if to_minutes(a) < to_minutes(b):
            return a.zfill(5), b.zfill(5)
    except ValueError:
        pass
    return None, None


def draft_from_event(project: Project, day: ShootDay, monitor: MonitorRecord, event: dict, simulated: bool = False) -> Disruption:
    text = (event.get("text") or "").strip()
    dtype = KIND_TO_TYPE.get(monitor.kind, DisruptionType.OTHER)
    low = text.lower()
    if dtype == DisruptionType.WEATHER and not any(w in low for w in _RAIN_WORDS):
        dtype = DisruptionType.OTHER
    if dtype == DisruptionType.TRANSPORT and not any(w in low for w in _ROAD_WORDS):
        dtype = DisruptionType.OTHER
    ws, we = parse_window(text)
    first_line = text.splitlines()[0] if text else f"{monitor.kind.title()} change detected"
    title = (first_line[:100] + "…") if len(first_line) > 100 else first_line
    # What the draft can actually reach, taken from what the monitor watches rather than from what the
    # text classifier managed to make of the wording. `scene_exposed` has four True branches and every
    # one needs exteriors, a named resource or a location: a REGULATORY event, and every WEATHER or
    # TRANSPORT event whose text failed the keyword check above and was downgraded to OTHER, used to
    # carry none of the three. Those drafts were guaranteed no-ops — confirmed, they ran a whole
    # rescue (a paid Parallel verification included, for the externally-verifiable types) over a
    # disruption that provably could not touch a scene. A monitor is attached to one shoot day, so an
    # event it raises is about that day: a weather monitor's is about the sky over its exterior work,
    # and every other monitor's is about where that unit is standing. The producer still confirms,
    # and can narrow it there.
    weather_watch = monitor.kind == "WEATHER"
    affected_locations = [] if weather_watch else list(dict.fromkeys(i.location_id for i in day.items if i.location_id))
    return Disruption(
        project_id=project.id, shoot_day_id=day.id, type=dtype, title=title or "Change detected by Parallel Monitor", description=text or "(no event text)",
        window_start=ws, window_end=we, affects_exteriors=weather_watch, affects_location_ids=affected_locations,
        dry_out_minutes=30 if dtype == DisruptionType.WEATHER else 0, source="parallel_monitor", synthetic=simulated, draft=True,
        monitor_id=monitor.id, monitor_event={"event_id": event.get("event_id"), "event_group_id": event.get("event_group_id"), "event_date": event.get("event_date"), "basis": event.get("basis", []), "simulated": simulated},
    )


SIMULATED_EVENTS = {
    "WEATHER": "IMD Mumbai nowcast: orange alert — moderate to heavy rain with thunderstorm and gusty winds (40-50 km/h) likely over Mumbai city and suburbs between 13:00 and 17:00 today. Rooftop surfaces will stay wet for about 30 minutes after.",
    "TRANSPORT": "Mumbai Traffic Police advisory: road closure and diversions on the approach to Lower Parel between 15:00 and 18:00 today due to a protest march; expect delays of 30-45 minutes.",
}
