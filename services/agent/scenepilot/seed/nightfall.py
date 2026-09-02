"""Project Nightfall — an ORIGINAL, FICTIONAL production used as the hero demo.

Everything here is synthetic production data (people, locations, permits, prices).
"""

from __future__ import annotations

from ..domain.enums import DisruptionType, Importance, IntExt, RequirementCategory, ResourceType, ScheduleItemStatus, ShootDayStatus, TimeOfDay
from ..domain.models import (
    Availability,
    Disruption,
    EquipmentCall,
    Project,
    ProductionBrief,
    Requirement,
    Resource,
    ScheduleItem,
    Scene,
    ShootDay,
    TransportLeg,
    TravelTime,
)
from ..services.ephemeris import apply_solar_windows, city_today

PROJECT_ID = "proj_nightfall"
DAY4_ID = "day_4"
DAY6_ID = "day_6"
# The production's own city — every date here is a date in *its* timezone, never the server's.
SEED_CITY = "Mumbai"


def _today_offset(days: int) -> str:
    from datetime import timedelta

    return (city_today(SEED_CITY) + timedelta(days=days)).isoformat()


# Shoot Day 4 is always "today" so the live weather verification through Parallel is meaningful.
# Every date is read when `build_project()` runs, never bound at import: a module constant freezes
# the hero day on the date the process booted, and a reset would then re-date its siblings around a
# day that never moves — after one midnight the chronology runs backwards. "Today" is Mumbai's
# today: the container's is UTC, which is still yesterday here until 05:30 IST.
SHOOT_DAY_OFFSETS = {"day_3": -1, DAY4_ID: 0, "day_5": 1, DAY6_ID: 2}


def day4_date() -> str:
    return _today_offset(SHOOT_DAY_OFFSETS[DAY4_ID])


def __getattr__(name: str) -> str:
    """`DAY4_DATE` stays importable, but resolves to today every time it is read."""
    if name == "DAY4_DATE":
        return day4_date()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# The four sets are invented; the neighbourhoods they sit in are not. These are the real centres of
# those Mumbai localities (WGS84) — a locality, not a doorway — so a company move can be drawn on a
# map without a single fabricated address. Distances derived from them are straight lines between
# localities and are published under that name; no road distance is claimed anywhere.
LOCALITY_COORDINATES: dict[str, dict[str, object]] = {
    "lower_parel": {"latitude": 18.9977, "longitude": 72.8298, "locality": "Lower Parel, Mumbai"},
    "bhuleshwar": {"latitude": 18.9490, "longitude": 72.8300, "locality": "Bhuleshwar, Kalbadevi, Mumbai"},
    "kala_ghoda": {"latitude": 18.9276, "longitude": 72.8320, "locality": "Kala Ghoda, Fort, Mumbai"},
    "film_city": {"latitude": 19.1580, "longitude": 72.8660, "locality": "Film City, Goregaon East, Mumbai"},
    "worli": {"latitude": 19.0176, "longitude": 72.8118, "locality": "Worli, Mumbai"},
}

# Which set sits in which locality, in one place, so `build_project` and the forward-migration of an
# already-persisted project cannot disagree about where a location is.
LOCATION_COORDINATES: dict[str, dict[str, object]] = {
    "loc_rooftop": LOCALITY_COORDINATES["lower_parel"],
    "loc_alley": LOCALITY_COORDINATES["kala_ghoda"],
    "loc_street": LOCALITY_COORDINATES["bhuleshwar"],
    "loc_apartment": LOCALITY_COORDINATES["film_city"],
    "loc_sea_link": LOCALITY_COORDINATES["worli"],
}

HERO_SCENE_TEXT = """EXT. MUMBAI ROOFTOP — SUNSET

A motorcycle tears across adjoining rooftops.
A drone follows while fireworks explode over the skyline.
Rain begins as the rider jumps to an adjacent building."""


def _req(scene_id: str, n: int, category: RequirementCategory, description: str, importance: Importance, source_ref: str | None = None, weather_sensitive: bool = False, resource_ids: list[str] | None = None, depends_on: list[str] | None = None) -> Requirement:
    return Requirement(id=f"req_{scene_id}_{n}", scene_id=scene_id, category=category, description=description, importance=importance, source_ref=source_ref, weather_sensitive=weather_sensitive, resource_ids=resource_ids or [], depends_on=depends_on or [])


def build_project() -> Project:
    # ---------------- Resources ----------------
    # Every day the production actually books somebody for gets an `Availability` row for that day.
    # An empty list means "no booking window on file", which the scheduler reads as available all
    # day — so a day with no rows at all is not a permissive day, it is a day whose own seeded
    # schedule the validator rejects, and whose "Constraints on this day" panel is blank.
    d3, d4, d5, d6 = "day_3", DAY4_ID, "day_5", DAY6_ID
    resources = [
        # Cast (fictional). `cast_number` is the production's own billing order, stated here once:
        # the lead is 1, the two other principals follow, and the stunt double — who doubles 1 —
        # takes the last principal number. Every document that names a performer joins on it, so it
        # is typed rather than derived from this list's order: renumbering a cast mid-shoot is how a
        # DOOD stops matching the call sheets already sent out.
        #
        # `day_rate_inr` is what each performer is engaged for per day, in billing order, and it is
        # stated here because the DOOD prices *hold* days with it — the days a production pays a
        # performer and does not shoot them. The matrix used to fall back to a flat ₹25,000 for
        # everybody, an invented rate that made a lead's idle day and a stunt double's cost the same
        # money. Every rate below is fictional, like the people; what matters is that the number on
        # screen comes from the production rather than from the renderer.
        Resource(id="cast_aarav", type=ResourceType.CAST, cast_number=1, name="Aarav Mehta (Rider / lead)", day_rate_inr=180000, availability=[Availability(shoot_day_id=d4, start="06:00", end="21:00"), Availability(shoot_day_id=d5, start="18:00", end="28:00", note="night unit — stage interior only"), Availability(shoot_day_id=d6, start="16:00", end="28:00")], attributes={"role": "lead"}),
        Resource(id="cast_meera", type=ResourceType.CAST, cast_number=2, name="Meera Iyer (Zoya)", day_rate_inr=120000, availability=[Availability(shoot_day_id=d4, start="06:30", end="19:00", note="evening flight — hard out 19:00"), Availability(shoot_day_id=d6, start="16:00", end="23:00", note="night unit — released before the rooftop move")]),
        Resource(id="cast_vikram", type=ResourceType.CAST, cast_number=3, name="Vikram Rao (Inspector Dalvi)", day_rate_inr=95000, availability=[Availability(shoot_day_id=d4, start="09:00", end="20:00", note="arrives from second unit")]),
        Resource(id="cast_stunt", type=ResourceType.CAST, cast_number=4, name="Stunt double (Rider)", day_rate_inr=45000, availability=[Availability(shoot_day_id=d4, start="06:00", end="21:00"), Availability(shoot_day_id=d6, start="16:00", end="28:00")]),
        # Locations (fictional sets, real Mumbai localities — see LOCALITY_COORDINATES)
        Resource(id="loc_rooftop", type=ResourceType.LOCATION, name="Rooftop A — Sitara Mills, Lower Parel", **LOCATION_COORDINATES["loc_rooftop"], availability=[Availability(shoot_day_id=d4, start="06:00", end="20:30", note="owner permission + rooftop access"), Availability(shoot_day_id=d6, start="16:00", end="28:00", note="night access agreed with the mill estate")], contact="Mill estate office — R. Kulkarni", attributes={"kind": "rooftop", "surface": "concrete, slippery when wet"}),
        Resource(id="loc_alley", type=ResourceType.LOCATION, name="Service alley — Kala Ghoda", **LOCATION_COORDINATES["loc_alley"], availability=[Availability(shoot_day_id=d4, start="06:30", end="18:00")], contact="Ward office liaison", attributes={"kind": "street"}),
        Resource(id="loc_street", type=ResourceType.LOCATION, name="Market street — Bhuleshwar", **LOCATION_COORDINATES["loc_street"], availability=[Availability(shoot_day_id=d4, start="13:00", end="18:00", note="traffic police permit window only")], contact="Traffic police liaison — Insp. Sawant", attributes={"kind": "street", "permit": "afternoon closure 13:00–18:00"}),
        Resource(id="loc_apartment", type=ResourceType.LOCATION, name="Apartment set — Film City Stage 3", **LOCATION_COORDINATES["loc_apartment"], availability=[Availability(shoot_day_id=d4, start="06:00", end="23:00"), Availability(shoot_day_id=d5, start="17:00", end="28:00", note="stage held overnight — prelight from 17:00"), Availability(shoot_day_id=d6, start="14:00", end="28:00")], contact="Stage manager", attributes={"kind": "stage"}),
        # The Day-3 dawn splinter's only set: an aerial plate flown over the sea link from the Worli
        # approach. The unit never goes on the span, which is why the window is a police-agreed
        # slot before the morning peak rather than a location day.
        Resource(id="loc_sea_link", type=ResourceType.LOCATION, name="Sea link approach — Worli", **LOCATION_COORDINATES["loc_sea_link"], availability=[Availability(shoot_day_id=d3, start="04:45", end="08:00", note="aerial slot agreed before the morning peak — off the carriageway")], contact="Traffic police liaison — Insp. Sawant", attributes={"kind": "aerial plate", "access": "approach road only; no unit on the span"}),
        # Equipment (fictional bookings)
        Resource(id="eq_drone", type=ResourceType.EQUIPMENT, name="FPV drone unit", weather_sensitive=True, prep_minutes=60, rerental_cost=35000, availability=[Availability(shoot_day_id=d3, start="04:45", end="08:00", note="dawn charter for the sea-link plate"), Availability(shoot_day_id=d4, start="06:00", end="21:00")], contact="Aerial vendor"),
        Resource(id="eq_crane", type=ResourceType.EQUIPMENT, name="30 ft telescopic crane", prep_minutes=90, rerental_cost=45000, availability=[Availability(shoot_day_id=d4, start="06:00", end="21:00", note="one-day rental")], contact="Grip vendor"),
        Resource(id="eq_bike", type=ResourceType.EQUIPMENT, name="Hero motorcycle + camera rig", prep_minutes=45, rerental_cost=15000, availability=[Availability(shoot_day_id=d4, start="06:00", end="21:00"), Availability(shoot_day_id=d6, start="16:00", end="28:00")]),
        Resource(id="eq_fireworks", type=ResourceType.EQUIPMENT, name="Fireworks rig (licensed pyrotechnician)", weather_sensitive=True, prep_minutes=60, rerental_cost=25000, availability=[Availability(shoot_day_id=d4, start="14:00", end="21:00")]),
        # Deliberately no availability rows: the lighting package travels with the main unit and is
        # never booked by the day, so there is no window to state. Empty means "available all day"
        # to the validator, which is the truth here — inventing four windows would put a booking on
        # the constraints panel that nobody made.
        Resource(id="eq_lighting", type=ResourceType.EQUIPMENT, name="Lighting package", prep_minutes=45, availability=[]),
        # Crew (fictional people, real departments). One head per department the coordination engine
        # already addresses: `DEPARTMENTS_BY_EQUIPMENT` plus the four standing targets in
        # `services/coordination.py` ("1st AD", "Transport captain", "Catering", "Production
        # office"). `attributes["department"]` is that target string verbatim, which is what lets a
        # coordination action and a call-sheet dispatch name the same person instead of two
        # spellings of one department — `test_seed_coherence` pins the two lists together.
        #
        # A department the production does not model does not get a head: there is no sound package,
        # no sound call and no sound target anywhere in this seed, so there is no production sound
        # mixer here either. Inventing one would put a name on a call sheet backed by nothing.
        #
        # No phone numbers, here or anywhere else in this seed: `contact` is the desk somebody is
        # reached through, as it is for every location and vendor above. India has no reserved
        # documentation range, so a plausible +91 mobile would be a real person's number.
        #
        # No availability rows, for the same reason `eq_lighting` has none: the crew travel with the
        # unit and are called by the day's own unit call, so there is no per-day booking window to
        # state. Writing the day's hours here would put a negotiated constraint on the constraints
        # panel that nobody negotiated.
        #
        # `walkie_channel` is this unit's agreed radio plan, stated here because it is a decision
        # somebody made and not a fact anybody can derive: 1 is the production channel every
        # department can be raised on, and the two shares are the way this unit actually works —
        # the production office sits on the AD's channel, and SFX shares the stunt channel because
        # the rigging on this show is one conversation. Eight channels, which is the radio the unit
        # carries; a ninth department would share, not overflow.
        Resource(id="crew_1st_ad", type=ResourceType.CREW, name="Rhea Fernandes (1st AD)", walkie_channel=1, contact="Production office — AD desk", attributes={"role": "1st Assistant Director", "department": "1st AD"}),
        Resource(id="crew_camera", type=ResourceType.CREW, name="Kabir Shroff (DoP)", walkie_channel=2, contact="Camera department desk", attributes={"role": "Director of Photography", "department": "Camera department"}),
        Resource(id="crew_grip", type=ResourceType.CREW, name="Nikhil Barve (Key Grip)", walkie_channel=3, contact="Grip vendor", attributes={"role": "Key Grip", "department": "Grip department"}),
        Resource(id="crew_electric", type=ResourceType.CREW, name="Sunita Kale (Gaffer)", walkie_channel=4, contact="Electric department desk", attributes={"role": "Gaffer", "department": "Electric department"}),
        Resource(id="crew_aerial", type=ResourceType.CREW, name="Farhan Qureshi (drone pilot)", walkie_channel=6, contact="Aerial vendor", attributes={"role": "Drone pilot", "department": "Aerial / drone unit"}),
        Resource(id="crew_sfx", type=ResourceType.CREW, name="Devika Rane (pyrotechnician)", walkie_channel=5, contact="SFX / pyrotechnics desk", attributes={"role": "Licensed pyrotechnician", "department": "SFX / pyrotechnics"}),
        Resource(id="crew_stunts", type=ResourceType.CREW, name="Imran Shaikh (stunt coordinator)", walkie_channel=5, contact="Stunt & rigging desk", attributes={"role": "Stunt coordinator", "department": "Stunt & rigging"}),
        Resource(id="crew_transport", type=ResourceType.CREW, name="Prakash Gaikwad (transport captain)", walkie_channel=7, contact="Transport desk", attributes={"role": "Transport captain", "department": "Transport captain"}),
        Resource(id="crew_catering", type=ResourceType.CREW, name="Anjali Deshmukh (catering lead)", walkie_channel=8, contact="Catering desk", attributes={"role": "Catering lead", "department": "Catering"}),
        Resource(id="crew_production_office", type=ResourceType.CREW, name="Nandini Pillai (production coordinator)", walkie_channel=1, contact="Production office", attributes={"role": "Production coordinator", "department": "Production office"}),
        # Vehicles
        Resource(id="veh_1", type=ResourceType.VEHICLE, name="Unit truck 1", attributes={"role": "unit"}),
        Resource(id="veh_2", type=ResourceType.VEHICLE, name="Cast van 2", attributes={"role": "cast"}),
    ]

    travel = [
        TravelTime(from_location_id="loc_alley", to_location_id="loc_apartment", minutes=30),
        TravelTime(from_location_id="loc_apartment", to_location_id="loc_street", minutes=40),
        TravelTime(from_location_id="loc_street", to_location_id="loc_rooftop", minutes=25),
        TravelTime(from_location_id="loc_alley", to_location_id="loc_street", minutes=20),
        TravelTime(from_location_id="loc_alley", to_location_id="loc_rooftop", minutes=35),
        TravelTime(from_location_id="loc_apartment", to_location_id="loc_rooftop", minutes=45),
    ]

    # ---------------- Scenes ----------------
    sc42 = Scene(
        id="sc_42", number="42", heading="EXT. MUMBAI ROOFTOP — SUNSET", int_ext=IntExt.EXT, time_of_day=TimeOfDay.SUNSET,
        synopsis="Rider chases across adjoining mill rooftops; drone follows; fireworks over the skyline; rain begins as he jumps to the next building.",
        script_text=HERO_SCENE_TEXT, location_id="loc_rooftop", cast_ids=["cast_aarav", "cast_stunt"],
        equipment_ids=["eq_drone", "eq_crane", "eq_bike", "eq_fireworks"], estimated_minutes=150, continuity_group="rooftop_chase", eighths=8,
    )
    sc42.requirements = [
        _req("sc_42", 1, RequirementCategory.CREATIVE, "Sunset light on the skyline; fireworks readable against dusk sky", Importance.HIGH, "EXT. MUMBAI ROOFTOP — SUNSET"),
        _req("sc_42", 2, RequirementCategory.LOCATION, "Two adjoining rooftops with a jumpable gap (or a safe practical substitute) and clear skyline view", Importance.CRITICAL, "tears across adjoining rooftops", resource_ids=["loc_rooftop"]),
        _req("sc_42", 3, RequirementCategory.SAFETY, "Dry, non-slip rooftop surface for motorcycle and rider jump; wet surface is a stop condition", Importance.CRITICAL, "Rain begins as the rider jumps", weather_sensitive=True),
        _req("sc_42", 4, RequirementCategory.TECHNICAL, "Drone tracking flight over an urban rooftop — no flight in rain or gusts", Importance.HIGH, "A drone follows", weather_sensitive=True, resource_ids=["eq_drone"]),
        _req("sc_42", 5, RequirementCategory.REGULATORY, "Drone operation permission for the airspace and a licensed pyrotechnician + fireworks permission", Importance.CRITICAL, "A drone follows while fireworks explode", resource_ids=["eq_drone", "eq_fireworks"]),
        _req("sc_42", 6, RequirementCategory.WEATHER, "Controlled rain effect on cue; ambient rain must not be present before the jump", Importance.HIGH, "Rain begins as the rider jumps", weather_sensitive=True),
        _req("sc_42", 7, RequirementCategory.EQUIPMENT, "Telescopic crane for the jump reveal; motorcycle camera rig", Importance.HIGH, resource_ids=["eq_crane", "eq_bike"]),
        _req("sc_42", 8, RequirementCategory.SCHEDULE, "Golden hour window ≈17:45–19:15; stunt rehearsal earlier in the day", Importance.HIGH),
    ]
    sc31 = Scene(id="sc_31", number="31", heading="EXT. SERVICE ALLEY — DAY", int_ext=IntExt.EXT, time_of_day=TimeOfDay.DAY, synopsis="Rider and Zoya argue in the alley; a tail is spotted.", location_id="loc_alley", cast_ids=["cast_aarav", "cast_meera"], equipment_ids=["eq_lighting"], estimated_minutes=150, eighths=10)
    sc31.requirements = [
        _req("sc_31", 1, RequirementCategory.LOCATION, "Narrow alley with controllable foot traffic", Importance.MEDIUM, resource_ids=["loc_alley"]),
        _req("sc_31", 2, RequirementCategory.WEATHER, "Dry exterior; dialogue scene — light drizzle tolerable only with cover", Importance.MEDIUM, weather_sensitive=True),
    ]
    sc48 = Scene(id="sc_48", number="48", heading="EXT. MARKET STREET — DAY", int_ext=IntExt.EXT, time_of_day=TimeOfDay.DAY, synopsis="Inspector Dalvi corners the Rider in a crowded market.", location_id="loc_street", cast_ids=["cast_aarav", "cast_vikram"], equipment_ids=["eq_lighting"], estimated_minutes=150, eighths=9)
    sc48.requirements = [
        _req("sc_48", 1, RequirementCategory.REGULATORY, "Traffic police permit for street closure — granted 13:00–18:00 only", Importance.CRITICAL, resource_ids=["loc_street"]),
        _req("sc_48", 2, RequirementCategory.WEATHER, "Dry exterior; crowd of 60 background artists", Importance.HIGH, weather_sensitive=True),
        _req("sc_48", 3, RequirementCategory.CAST, "Inspector Dalvi (Vikram Rao) — available from 09:00", Importance.HIGH, resource_ids=["cast_vikram"]),
    ]
    sc19 = Scene(id="sc_19", number="19", heading="INT. APARTMENT — DAY", int_ext=IntExt.INT, time_of_day=TimeOfDay.ANY, synopsis="Zoya confronts Dalvi about the missing file.", location_id="loc_apartment", cast_ids=["cast_meera", "cast_vikram"], equipment_ids=["eq_lighting"], estimated_minutes=150, continuity_group="apartment", eighths=12)
    sc19.requirements = [
        _req("sc_19", 1, RequirementCategory.CAST, "Zoya and Dalvi", Importance.HIGH, resource_ids=["cast_meera", "cast_vikram"]),
        _req("sc_19", 2, RequirementCategory.CONTINUITY, "Day-for-day continuity with Sc 27 (same wardrobe)", Importance.MEDIUM),
    ]
    sc27 = Scene(id="sc_27", number="27", heading="INT. APARTMENT KITCHEN — MORNING", int_ext=IntExt.INT, time_of_day=TimeOfDay.ANY, synopsis="Zoya finds the burner phone. (Cover set.)", location_id="loc_apartment", cast_ids=["cast_meera"], equipment_ids=["eq_lighting"], estimated_minutes=75, continuity_group="apartment", is_cover=True, eighths=6)
    sc27.requirements = [_req("sc_27", 1, RequirementCategory.CAST, "Zoya only — ideal cover scene", Importance.MEDIUM, resource_ids=["cast_meera"])]
    sc55 = Scene(id="sc_55", number="55", heading="INT. STAIRWELL — NIGHT", int_ext=IntExt.INT, time_of_day=TimeOfDay.NIGHT, synopsis="Rider descends in the dark; footsteps above.", location_id="loc_apartment", cast_ids=["cast_aarav"], equipment_ids=["eq_lighting"], estimated_minutes=120, eighths=6)
    sc55.requirements = [
        _req("sc_55", 1, RequirementCategory.LOCATION, "Stairwell run with practical fittings that can be dimmed to a single working source", Importance.HIGH, resource_ids=["loc_apartment"]),
        _req("sc_55", 2, RequirementCategory.TECHNICAL, "Night interior built on stage — no daylight spill through the stairwell window at any hour", Importance.HIGH, resource_ids=["eq_lighting"]),
        _req("sc_55", 3, RequirementCategory.CAST, "Rider alone on camera; the footsteps above are off-screen and cut in post", Importance.MEDIUM, resource_ids=["cast_aarav"]),
    ]
    # Shot on the Day-3 dawn splinter. A second-unit aerial plate: a set, a drone charter and a page
    # count, and deliberately no cast — nobody was called, which is why the DOOD Day-3 column is
    # empty. Filling that column would mean inventing a performer onto a plate they never worked.
    sc12 = Scene(id="sc_12", number="12", heading="EXT. SEA LINK — DAWN", int_ext=IntExt.EXT, time_of_day=TimeOfDay.DAWN, synopsis="Opening: the city wakes — an aerial plate over the sea link, flown from the Worli approach. (Completed Day 3.)", location_id="loc_sea_link", cast_ids=[], equipment_ids=["eq_drone"], estimated_minutes=90, eighths=3)
    sc12.requirements = [
        _req("sc_12", 1, RequirementCategory.REGULATORY, "Drone operation permission over the sea link approach and a police-agreed slot before the morning peak", Importance.CRITICAL, resource_ids=["eq_drone", "loc_sea_link"]),
        _req("sc_12", 2, RequirementCategory.SCHEDULE, "Dawn light only — one usable pass window per morning, so a lost morning is a lost day", Importance.CRITICAL),
        _req("sc_12", 3, RequirementCategory.WEATHER, "No flight in rain or gusts; the plate is the film's first image and has no interior cover", Importance.HIGH, weather_sensitive=True, resource_ids=["eq_drone"]),
        _req("sc_12", 4, RequirementCategory.LOCATION, "Launch and recovery off the carriageway; the unit never stands on the span", Importance.HIGH, resource_ids=["loc_sea_link"]),
    ]

    sc58 = Scene(
        id="sc_58", number="58", heading="EXT. MUMBAI ROOFTOP — NIGHT", int_ext=IntExt.EXT, time_of_day=TimeOfDay.NIGHT,
        synopsis="The rider returns to the rooftop after dark; the city is lit below.",
        location_id="loc_rooftop", cast_ids=["cast_aarav", "cast_stunt"], equipment_ids=["eq_bike", "eq_lighting"], estimated_minutes=150, continuity_group="rooftop_chase", eighths=10,
    )
    sc58.requirements = [
        _req("sc_58", 1, RequirementCategory.LOCATION, "Rooftop access after dark with safe egress lighting", Importance.CRITICAL, resource_ids=["loc_rooftop"]),
        _req("sc_58", 2, RequirementCategory.REGULATORY, "Night work at an exterior city location — local noise limits apply", Importance.CRITICAL, resource_ids=["loc_rooftop"]),
        _req("sc_58", 3, RequirementCategory.TECHNICAL, "Lighting package rigged for a night exterior on a rooftop", Importance.HIGH, resource_ids=["eq_lighting"]),
    ]
    sc62 = Scene(
        # A stage interior: "night" is the story, not the lighting — so it can shoot at any hour (as Sc 19/27 do).
        id="sc_62", number="62", heading="INT. APARTMENT — NIGHT", int_ext=IntExt.INT, time_of_day=TimeOfDay.ANY,
        synopsis="Zoya waits by the window as the city goes quiet.",
        location_id="loc_apartment", cast_ids=["cast_meera"], equipment_ids=["eq_lighting"], estimated_minutes=120, continuity_group="apartment", eighths=5,
    )
    sc62.requirements = [_req("sc_62", 1, RequirementCategory.CAST, "Zoya only — opens the night unit before the company moves to the roof", Importance.MEDIUM, resource_ids=["cast_meera"])]

    # Every scene states its own `eighths`, because the board prints the page count and the
    # scheduled minutes side by side and they have to tell one story. Five of these used to be left
    # blank for the Fountain draft to paginate, and the draft in `fixtures/` is a five-scene
    # *excerpt* — five to eight lines a scene — so the parser correctly returned 1/8 of a page for
    # each. The board then read the hero day as "4 sc · 4/8 pgs" in a 12.5 h day that budgets 150
    # minutes a scene: half a page in twelve and a half hours. The parser was not wrong; measuring
    # an excerpt is not the same claim as a production's committed page count, and only the second
    # belongs on a board next to a call time.
    #
    # The numbers below are that committed count, and each is the production's own minutes divided
    # by a rate the scene's character supports: ~12.5 min per eighth for a stage two-hander
    # (19, 27), 15 for an exterior dialogue scene (31) and for a night exterior (58), ~17 for a
    # street scene carrying 60 background artists (48), ~19 for the stunt/pyro rooftop (42), 20–24
    # for the night-unit interiors (55, 62) and 30 for a dawn aerial plate with one usable pass a
    # morning (12). `test_seed_coherence` pins that ratio so a later edit cannot drift a page count
    # away from the minutes the same row prints.
    scenes = [sc12, sc19, sc27, sc31, sc42, sc48, sc55, sc58, sc62]

    # ---------------- Shoot days ----------------
    # Every day states its own operating facts. Left unset they inherit `ShootDay`'s class defaults
    # (06:30 / 12 h / 45 crew / 22:00 wrap), and a panel then reports a standard main-unit day for a
    # dawn splinter and a night unit — an invented headline nobody typed.
    day3 = ShootDay(
        id="day_3", project_id=PROJECT_ID, day_number=3, date=_today_offset(SHOOT_DAY_OFFSETS["day_3"]), status=ShootDayStatus.WRAPPED,
        unit_call="05:15", standard_hours=8.0, hard_wrap="16:00", crew_size=18, overtime_rate_per_hour=6000,
        # A day that already happened says so on its own row: `COMPLETED`, not `SCHEDULED`, and the
        # splinter unit it was rather than the main unit every other day carries.
        items=[ScheduleItem(id="it_12", scene_id="sc_12", start="05:45", end="07:15", location_id="loc_sea_link", status=ScheduleItemStatus.COMPLETED, unit="SPLINTER", note="Aerial plate completed in one dawn window; no pickups outstanding.")],
        # Call times match what `derive_equipment_calls` recomputes from the schedule (prep before
        # first shot, never before the unit call), so the day page and the call sheet cannot print
        # two different times for the same vendor.
        equipment_calls=[EquipmentCall(resource_id="eq_drone", call_time="05:15")],
        notes="Dawn aerial splinter unit: 8 h from 05:15 with an 18-crew call. One drone plate over the sea link; wrapped 07:15.",
    )
    day4 = ShootDay(
        id=DAY4_ID, project_id=PROJECT_ID, day_number=4, date=day4_date(), unit_call="06:30", standard_hours=12.5, hard_wrap="22:00",
        crew_size=45, overtime_rate_per_hour=7500, company_move_cost=12000, carry_over_cost=60000,
        items=[
            ScheduleItem(id="it_31", scene_id="sc_31", start="07:00", end="09:30", location_id="loc_alley"),
            ScheduleItem(id="it_19", scene_id="sc_19", start="10:00", end="12:30", location_id="loc_apartment"),
            ScheduleItem(id="it_48", scene_id="sc_48", start="13:30", end="16:00", location_id="loc_street"),
            ScheduleItem(id="it_42", scene_id="sc_42", start="16:30", end="19:00", location_id="loc_rooftop"),
        ],
        equipment_calls=[
            EquipmentCall(resource_id="eq_lighting", call_time="06:30"),
            EquipmentCall(resource_id="eq_crane", call_time="15:00"),
            EquipmentCall(resource_id="eq_drone", call_time="15:30"),
            EquipmentCall(resource_id="eq_fireworks", call_time="15:30"),
            EquipmentCall(resource_id="eq_bike", call_time="15:45"),
        ],
        transport=[
            TransportLeg(id="leg_1", vehicle_id="veh_2", from_location_id="loc_alley", to_location_id="loc_apartment", departure="09:30"),
            TransportLeg(id="leg_2", vehicle_id="veh_2", from_location_id="loc_apartment", to_location_id="loc_street", departure="12:35"),
            TransportLeg(id="leg_3", vehicle_id="veh_2", from_location_id="loc_street", to_location_id="loc_rooftop", departure="16:00"),
        ],
        notes="Standard 12.5 h day from 06:30 (wrap 19:00); overtime ₹7,500/h for the whole crew.",
    )
    day5 = ShootDay(
        id="day_5", project_id=PROJECT_ID, day_number=5, date=_today_offset(SHOOT_DAY_OFFSETS["day_5"]), status=ShootDayStatus.READY,
        unit_call="18:00", standard_hours=10.0, hard_wrap="28:00", crew_size=30, overtime_rate_per_hour=7500,
        items=[ScheduleItem(id="it_55", scene_id="sc_55", start="19:30", end="21:30", location_id="loc_apartment")],
        equipment_calls=[EquipmentCall(resource_id="eq_lighting", call_time="18:45")],
        notes="Night unit: 10 h from 18:00 (wrap 04:00). One stage interior at Film City — no exterior, no curfew exposure.",
    )
    # A night unit: the day when a city noise curfew stops being trivia and starts rejecting schedules.
    day6 = ShootDay(
        id=DAY6_ID, project_id=PROJECT_ID, day_number=6, date=_today_offset(SHOOT_DAY_OFFSETS[DAY6_ID]), unit_call="16:00", standard_hours=12.0, hard_wrap="28:00",
        crew_size=38, overtime_rate_per_hour=7500, status=ShootDayStatus.READY,
        items=[
            ScheduleItem(id="it_62", scene_id="sc_62", start="17:00", end="19:00", location_id="loc_apartment"),
            ScheduleItem(id="it_58", scene_id="sc_58", start="21:00", end="23:30", location_id="loc_rooftop"),
        ],
        equipment_calls=[EquipmentCall(resource_id="eq_lighting", call_time="16:15"), EquipmentCall(resource_id="eq_bike", call_time="20:15")],
        transport=[TransportLeg(id="leg_n1", vehicle_id="veh_2", from_location_id="loc_apartment", to_location_id="loc_rooftop", departure="20:00")],
        notes="Night unit: 12 h from 16:00 (wrap 04:00). The rooftop exterior runs past 22:00 — check the local noise curfew before locking this.",
    )

    project = Project(
        id=PROJECT_ID, title="Project Nightfall", synthetic=True,
        logline="A courier who moves things that should not exist has one night to get a file across Mumbai before the city's fireworks end.",
        base_city=SEED_CITY, country_code="IN",
        briefs=[ProductionBrief(id="brief_42", project_id=PROJECT_ID, source_kind="pasted_text", raw_text=HERO_SCENE_TEXT)],
        scenes=scenes, resources=resources, travel_times=travel, shoot_days=[day3, day4, day5, day6],
    )
    sc42.brief_id = "brief_42"
    # The sun is computed, never typed. `ShootDay` ships class defaults for the four lighting windows
    # and the deterministic validator enforces them, so a day that never overrides them rejects a
    # sunset scene against a window nobody measured.
    for day in project.shoot_days:
        apply_solar_windows(day, SEED_CITY)
    return project


def reanchor_shoot_days(project: Project, today: str | None = None) -> int:
    """Slide the whole shoot schedule so Day 4 is today again. Returns the shift in days (0 = none).

    A rebuild is not available where it matters: with a persistent `DATABASE_URL` the project is
    found rather than seeded, so its dates would age with the deployment until the hero day is a
    week in the past. Every day moves by the same delta, so the gaps that the turnaround and
    multi-day rules read stay exactly as they were, and the only things written are `ShootDay.date`
    and the lighting windows derived from it — accepted facts, runs, changesets and evidence are not
    this function's business. The windows move with the date because they are a function of it: a
    golden hour computed for a day that has since slid a week is as invented as a hardcoded one.

    "Today" is the production's own today (`project.base_city`), so a UTC server does not open the
    Mumbai hero day on yesterday's date for the first five and a half hours of every morning.
    """
    from datetime import date, timedelta

    anchor = next((d for d in project.shoot_days if d.id == DAY4_ID), None)
    if anchor is None:
        return 0
    try:
        current = date.fromisoformat(anchor.date)
        target = date.fromisoformat(today) if today else city_today(project.base_city) + timedelta(days=SHOOT_DAY_OFFSETS[DAY4_ID])
    except ValueError:
        return 0
    shift = (target - current).days
    if shift == 0:
        return 0
    for day in project.shoot_days:
        try:
            day.date = (date.fromisoformat(day.date) + timedelta(days=shift)).isoformat()
        except ValueError:
            continue
        apply_solar_windows(day, project.base_city)
    return shift


# ---------------- Deterministic disruption fixtures ----------------

DISRUPTION_FIXTURES: dict[str, dict] = {
    "rain_pm": dict(
        type=DisruptionType.WEATHER, title="Rain expected 13:00–17:00",
        description="Nowcast for Mumbai city: moderate to heavy showers with gusty winds (40–50 km/h) between 13:00 and 17:00 IST. Exterior rooftop surfaces will need ~30 min to dry.",
        window_start="13:00", window_end="17:00", affects_exteriors=True, dry_out_minutes=30,
    ),
    "vikram_late": dict(
        type=DisruptionType.CAST_UNAVAILABLE, title="Vikram Rao delayed until 15:00",
        description="Second-unit overrun: Vikram Rao (Inspector Dalvi) cannot reach set before 15:00.",
        window_start="06:30", window_end="15:00", affects_exteriors=False, affects_resource_ids=["cast_vikram"], dry_out_minutes=0,
    ),
    # Deliberately a fault the day absorbs, and the only fixture here that is. The crane is called at
    # 15:00 for a 16:30 rooftop scene, so a swap completed "by 16:00" costs the production nothing —
    # `test_resource_disruption_uses_affected_resources` pins that no exposure is raised for it.
    # It is kept because the honest answer to it is an answer: the pipeline used to report "0
    # scheduled scene(s) directly affected" and then recommend moving two scenes anyway, and this is
    # the fixture that reaches `nothing_to_recover` and says so instead. Widening the window until it
    # bit Sc 42 would have deleted the only route to that outcome.
    "crane_failure": dict(
        type=DisruptionType.EQUIPMENT_FAILURE, title="Crane hydraulic fault — vendor swap by 16:00",
        description="Grip vendor reports a hydraulic fault on the telescopic crane; a replacement unit can arrive by 16:00.",
        window_start="06:30", window_end="16:00", affects_exteriors=False, affects_resource_ids=["eq_crane"], dry_out_minutes=0,
    ),
    # The three above are shaped for Day 4's main unit, and for a long time they were the whole list —
    # so the night units were offered an afternoon cast delay and a crane they do not carry, and the
    # trailer runbook told the presenter to type a disruption by hand on Day 6 to get around it. A
    # night unit's exposure is its own: the rooftop exterior plays 21:00–23:30, which is where its
    # weather risk lives and nowhere near 13:00–17:00.
    "rain_night": dict(
        type=DisruptionType.WEATHER, title="Night showers over the rooftop 20:30–23:00",
        description="Nowcast for the island city: scattered showers rolling in off the sea between 20:30 and 23:00 IST, easing after midnight. Exterior rooftop surfaces will need ~30 min to dry.",
        window_start="20:30", window_end="23:00", affects_exteriors=True, dry_out_minutes=30,
    ),
}


def make_fixture_disruption(project_id: str, shoot_day_id: str, fixture_id: str) -> Disruption:
    spec = DISRUPTION_FIXTURES[fixture_id]
    return Disruption(project_id=project_id, shoot_day_id=shoot_day_id, source="fixture", fixture_id=fixture_id, synthetic=True, **spec)
