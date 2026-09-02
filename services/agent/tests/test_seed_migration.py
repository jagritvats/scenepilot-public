"""What a persisted project does when the seed grows a field it was written without.

`_ensure_seed` builds the project only when it is missing. With a persistent `DATABASE_URL` — the
local `scenepilot.db`, or Cloud SQL behind the hosted demo — it is never missing, so a field added to
a seeded entity today is null on the deployment forever, while every test goes on passing because a
test builds the project fresh. That is exactly how four real Mumbai coordinates reached the code and
never reached the map: `resource loc_rooftop lat/long: None None`, every company move "distance
unknown", and a map that renders nothing.

These tests run against a project shaped like that stored one — the fields blanked back to what the
model would have given them — and then through the API the way a request does.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from scenepilot.domain.enums import FactBinding, ResourceType
from scenepilot.domain.models import LocationFact, Resource, ShootDay
from scenepilot.seed.migrate import migrate_seed_state
from scenepilot.seed.nightfall import DAY4_ID, LOCATION_COORDINATES, PROJECT_ID, SEED_CITY, build_project
from scenepilot.services.ephemeris import SOLAR_WINDOW_FIELDS, city_ephemeris
from scenepilot.services.geo import day_geography
from scenepilot.services.schedule import lighting_check

SEEDED_DAY_FIELDS = ("unit_call", "standard_hours", "hard_wrap", "crew_size")


def _as_persisted_before(project, *, resources=True, days=True, windows=True):
    """The stored document as an older seed wrote it: fields that did not exist yet, at their defaults."""
    if resources:
        for res in project.resources:
            if res.type == ResourceType.LOCATION:
                res.latitude = res.longitude = res.locality = None
    if days:
        for day in project.shoot_days:
            if day.id in {"day_3", "day_5"}:
                for name in SEEDED_DAY_FIELDS:
                    setattr(day, name, ShootDay.model_fields[name].default)
                day.notes = None
    if windows:
        for day in project.shoot_days:
            for name in SOLAR_WINDOW_FIELDS:
                setattr(day, name, ShootDay.model_fields[name].default)
    return project


def _stored():
    return _as_persisted_before(build_project())


# --------------------------------------------------------------------------- #
# The coordinates that never reached the map
# --------------------------------------------------------------------------- #


def test_a_location_persisted_without_coordinates_is_backfilled_from_the_seeds_own_table():
    project = _stored()
    assert [r.id for r in project.resources if r.type == ResourceType.LOCATION and not r.has_coordinates] == list(LOCATION_COORDINATES)

    notes = migrate_seed_state(project)

    rooftop = project.resource("loc_rooftop")
    assert (rooftop.latitude, rooftop.longitude) == (18.9977, 72.8298)
    assert rooftop.locality == "Lower Parel, Mumbai"
    assert all(project.resource(rid).has_coordinates for rid in LOCATION_COORDINATES)
    assert any("coordinates" in n for n in notes)


def test_the_backfill_turns_distance_unknown_back_into_a_measured_company_move():
    project = _stored()
    before = day_geography(project, project.shoot_day(DAY4_ID))
    assert before["total_straight_line_km"] is None
    assert [m["straight_line_km"] for m in before["moves"]] == [None, None, None]

    migrate_seed_state(project)

    after = day_geography(project, project.shoot_day(DAY4_ID))
    assert after["locations_missing_coordinates"] == []
    assert all(m["straight_line_km"] is not None for m in after["moves"])
    assert after["total_straight_line_km"] > 0


def test_a_coordinate_somebody_already_set_is_never_overwritten():
    project = _stored()
    project.resource("loc_rooftop").latitude = 19.0
    project.resource("loc_rooftop").longitude = 72.9

    migrate_seed_state(project)

    assert (project.resource("loc_rooftop").latitude, project.resource("loc_rooftop").longitude) == (19.0, 72.9)


def test_the_migration_backfills_and_never_rebuilds():
    """A producer's accepted facts are in the same document. They are not this seam's business."""
    project = _stored()
    fact = LocationFact(project_id=project.id, resource_id="loc_rooftop", task_run_id="trun_seed", key="noise_curfew",
                        label="Noise curfew", value="22:00-06:00", binding=FactBinding.HARD, accepted=True, accepted_by="producer")
    project.location_facts.append(fact)
    project.resources.append(Resource(id="loc_extra", type=ResourceType.LOCATION, name="A location a producer added"))
    items_before = [(i.id, i.scene_id, i.start, i.end) for i in project.shoot_day(DAY4_ID).items]

    migrate_seed_state(project)

    assert project.location_facts == [fact] and fact.accepted is True
    assert project.resource("loc_extra").latitude is None  # not a seeded location; nothing is invented for it
    assert [(i.id, i.scene_id, i.start, i.end) for i in project.shoot_day(DAY4_ID).items] == items_before


def test_running_it_twice_changes_nothing_the_second_time():
    project = _stored()
    assert migrate_seed_state(project)
    assert migrate_seed_state(project) == []


# --------------------------------------------------------------------------- #
# The seam has to fail loudly where it cannot decide, rather than skip
# --------------------------------------------------------------------------- #


def test_a_field_with_no_class_default_is_refused_instead_of_silently_skipped():
    """`default_factory` fields report `PydanticUndefined`, which equals nothing.

    `_adopt` compared against that and skipped the field with no error and no note — so listing
    `attributes` (which `geo.py` reads for a location's `kind`) would have looked like a working
    migration and shipped a permanently blank field.
    """
    import pytest

    from scenepilot.seed.migrate import SeedMigrationError, _adopt

    stored, seeded = _stored(), build_project()
    rooftop, seeded_rooftop = stored.resource("loc_rooftop"), build_project().resource("loc_rooftop")
    rooftop.attributes = {}

    with pytest.raises(SeedMigrationError, match="default_factory"):
        _adopt(rooftop, seeded_rooftop, ("attributes",))
    with pytest.raises(SeedMigrationError, match="no field"):
        _adopt(rooftop, seeded_rooftop, ("no_such_field",))
    assert _adopt(rooftop, seeded_rooftop, SEEDED_LOCATION_FIELDS_UNDER_TEST) == ["latitude", "longitude", "locality"]


SEEDED_LOCATION_FIELDS_UNDER_TEST = ("latitude", "longitude", "locality")


def test_every_field_this_seam_migrates_still_has_a_class_default_to_recognise():
    """A guard on the lists themselves, so the failure lands in CI rather than on a deployment."""
    from scenepilot.domain.models import Resource
    from scenepilot.seed.migrate import SEEDED_DAY_FIELDS as DAY_FIELDS, SEEDED_LOCATION_FIELDS as LOC_FIELDS
    from pydantic_core import PydanticUndefined

    for model, fields in ((Resource, LOC_FIELDS), (ShootDay, DAY_FIELDS)):
        for name in fields:
            assert name in model.model_fields, f"{model.__name__}.{name}"
            assert model.model_fields[name].default is not PydanticUndefined, f"{model.__name__}.{name}"


# --------------------------------------------------------------------------- #
# A pair the seed times, that a stored project has no row for
# --------------------------------------------------------------------------- #


def test_a_travel_time_the_seed_grew_reaches_a_project_written_before_it():
    from scenepilot.services.geo import seeded_travel_minutes

    project = _stored()
    project.travel_times = [t for t in project.travel_times if {t.from_location_id, t.to_location_id} != {"loc_street", "loc_rooftop"}]
    assert seeded_travel_minutes(project, "loc_street", "loc_rooftop") is None

    notes = migrate_seed_state(project)

    assert seeded_travel_minutes(project, "loc_street", "loc_rooftop") == 25
    assert any("travel time" in n for n in notes)
    assert migrate_seed_state(project) == []  # and it is idempotent


def test_a_booking_window_the_seed_grew_reaches_a_project_written_before_it():
    """Same shape as a travel time, and worse consequences: absence reads as *unavailable*.

    A stored project written before the night unit had bookings has no Day-5 row for the lead or
    the stage, so `availability_windows` reports them unavailable, the validator rejects Day 5's own
    seeded schedule, and the day's constraints panel comes up blank.
    """
    from scenepilot.services.schedule import ValidationContext, is_available, validate_schedule

    project = _stored()
    for rid in ("cast_aarav", "loc_apartment"):
        project.resource(rid).availability = [a for a in project.resource(rid).availability if a.shoot_day_id != "day_5"]
    day5 = project.shoot_day("day_5")
    assert [v.message for v in validate_schedule(ValidationContext(project=project, day=day5), day5.items) if v.hard]

    notes = migrate_seed_state(project)

    assert is_available(project.resource("cast_aarav"), day5, 19 * 60 + 30, 21 * 60 + 30)
    assert [v for v in validate_schedule(ValidationContext(project=project, day=day5), day5.items) if v.hard] == []
    assert any("booking window" in n for n in notes)
    assert migrate_seed_state(project) == []  # and it is idempotent


def test_a_booking_window_somebody_narrowed_is_never_widened_back():
    """A day the stored resource already holds any window for is a producer's decision, not a gap."""
    project = _stored()
    aarav = project.resource("cast_aarav")
    day5 = next(a for a in aarav.availability if a.shoot_day_id == "day_5")
    day5.end = "22:00"  # the lead was released early

    migrate_seed_state(project)

    kept = [a for a in aarav.availability if a.shoot_day_id == "day_5"]
    assert len(kept) == 1 and kept[0].end == "22:00"


def test_a_travel_time_somebody_re_measured_is_never_overwritten():
    from scenepilot.services.geo import seeded_travel_minutes

    project = _stored()
    pair = next(t for t in project.travel_times if {t.from_location_id, t.to_location_id} == {"loc_street", "loc_rooftop"})
    pair.minutes = 55  # the route was re-driven at rush hour

    migrate_seed_state(project)

    assert seeded_travel_minutes(project, "loc_street", "loc_rooftop") == 55
    assert len([t for t in project.travel_times if {t.from_location_id, t.to_location_id} == {"loc_street", "loc_rooftop"}]) == 1


def test_an_entity_the_seed_grew_is_reported_rather_than_created(caplog):
    """Creating a shoot day would change what the turnaround rule reads for its neighbours.

    So the seam does not — but it may not be quiet about it either, and it may not make the caller
    rewrite the document on every read to say so.
    """
    import logging

    project = build_project()
    project.shoot_days = [d for d in project.shoot_days if d.id != "day_6"]
    project.resources = [r for r in project.resources if r.id != "loc_alley"]

    with caplog.at_level(logging.WARNING, logger="scenepilot.seed.migrate"):
        notes = migrate_seed_state(project)

    assert notes == []  # nothing changed, so nothing is persisted
    assert any("day_6" in r.getMessage() and "loc_alley" in r.getMessage() for r in caplog.records)
    assert [d.id for d in project.shoot_days] == ["day_3", "day_4", "day_5"]


# --------------------------------------------------------------------------- #
# A page count measured off the draft excerpt, on the only database anybody looks at
# --------------------------------------------------------------------------- #

DRAFTED = ("sc_42", "sc_27", "sc_48", "sc_31", "sc_19")


def _paginated_from_the_excerpt(project):
    """The stored document as the old warm pass left it: the parser's count for a six-line scene."""
    for scene_id in DRAFTED:
        project.scene(scene_id).eighths = 1
    return project


def test_a_page_count_copied_off_the_draft_excerpt_is_replaced_by_the_producers_own():
    """The hero day used to board as "4 sc · 4/8 pgs" against 600 scheduled minutes.

    `warm.py` adopted the parser's `eighths`, and the Fountain file is a five-scene excerpt, so
    every drafted scene stored 1/8 of a page. A test built the project fresh and saw nothing.
    """
    project = _paginated_from_the_excerpt(build_project())
    day4 = project.shoot_day(DAY4_ID)
    assert sum(project.scene(i.scene_id).eighths for i in day4.items) == 4

    notes = migrate_seed_state(project)

    fresh = build_project()
    assert {s.id: s.eighths for s in project.scenes} == {s.id: s.eighths for s in fresh.scenes}
    assert sum(project.scene(i.scene_id).eighths for i in day4.items) == 39
    assert any("page count" in n for n in notes)


def test_a_page_count_that_is_not_the_excerpts_own_is_left_exactly_as_it_is():
    """The narrow signature: only a stored count the excerpt still produces is overwritten.

    A producer who uploaded a real draft in the Studio owns those numbers, and this seam does not
    get to second-guess them — it only reverses a copy the startup path made unattended.
    """
    project = build_project()
    project.scene("sc_48").eighths = 14  # a real draft, paginated on upload
    project.scene("sc_55").eighths = 1   # not in the draft at all, so not the excerpt's doing

    migrate_seed_state(project)

    assert project.scene("sc_48").eighths == 14
    assert project.scene("sc_55").eighths == 1


def test_migrating_a_page_count_twice_changes_nothing_the_second_time():
    project = _paginated_from_the_excerpt(build_project())
    assert any("page count" in n for n in migrate_seed_state(project))
    assert not any("page count" in n for n in migrate_seed_state(project))


def _stored_before_page_counts_existed(project):
    """The stored document as a seed that stated no page counts at all wrote it: every scene null.

    This is the case a fresh build hides completely, and the one on the machine the audit found —
    the board's headline page figure replaced by "pages not on file" on the hero day.
    """
    for scene in project.scenes:
        scene.eighths = None
    return project


def test_a_scene_stored_with_no_page_count_at_all_takes_the_seeds_own():
    """`None` is `Scene.eighths`'s class default, so this is `_adopt`'s rule, not the excerpt's.

    The old excerpt-signature test could never fire here: it only replaces a stored count the
    excerpt still reproduces, and a stored `None` reproduces nothing. So a database written before
    the seed stated any counts kept all five drafted scenes blank forever.
    """
    project = _stored_before_page_counts_existed(build_project())
    assert all(s.eighths is None for s in project.scenes)

    notes = migrate_seed_state(project)

    fresh = build_project()
    assert {s.id: s.eighths for s in project.scenes} == {s.id: s.eighths for s in fresh.scenes}
    assert any("page count" in n for n in notes)
    assert migrate_seed_state(project) == []  # and it is idempotent


def test_the_hero_days_page_total_comes_back_on_a_database_that_stored_none():
    """4 7/8 pages against 600 scheduled minutes — the figure the board headlines."""
    project = _stored_before_page_counts_existed(build_project())
    day4 = project.shoot_day(DAY4_ID)
    assert [project.scene(i.scene_id).eighths for i in day4.items] == [None, None, None, None]

    migrate_seed_state(project)

    assert sum(project.scene(i.scene_id).eighths for i in day4.items) == 39


def test_the_three_histories_of_a_stored_page_count_are_told_apart_in_one_document():
    """Never set, set by the old warm pass, set by a producer — one project, three outcomes.

    The middle case is the only one that overwrites, and it is recognised by the stored value being
    exactly what re-parsing the same excerpt still produces for that scene number. A producer's
    count fails both tests: it is not the class default, so it is never filled, and it is not the
    excerpt's own number, so it is never corrected.
    """
    project = build_project()
    project.scene("sc_31").eighths = None  # never set: a database older than the counts
    project.scene("sc_42").eighths = 1     # the old warm pass, measuring six lines of an excerpt
    project.scene("sc_48").eighths = 14    # a producer's real draft, paginated on upload
    project.scene("sc_55").eighths = 1     # a producer's 1/8, on a scene the excerpt never contained

    migrate_seed_state(project)

    assert project.scene("sc_31").eighths == 10  # filled from the seed
    assert project.scene("sc_42").eighths == 8   # corrected off the excerpt
    assert project.scene("sc_48").eighths == 14  # untouched
    assert project.scene("sc_55").eighths == 1   # untouched


def test_filling_a_blank_is_not_announced_as_correcting_a_measurement():
    """The two halves say different things, because they did different things.

    A blank that was never measured may not be reported as a count "measured off the draft excerpt
    rather than stated by the producer" — that sentence describes a correction that did not happen,
    on a value nobody ever wrote.
    """
    project = _stored_before_page_counts_existed(build_project())

    filled = [n for n in migrate_seed_state(project) if "page count" in n]

    assert len(filled) == 1
    assert "none on file" in filled[0]
    assert "measured off" not in filled[0]

    corrected = [n for n in migrate_seed_state(_paginated_from_the_excerpt(build_project())) if "page count" in n]
    assert len(corrected) == 1 and "measured off the five-scene draft excerpt" in corrected[0]


def test_a_scene_the_seed_states_no_page_count_for_is_left_blank_rather_than_invented():
    """Adoption is only ever from the seed. No count in the seed means no count on the board."""
    from scenepilot.domain.models import Scene

    project = _stored_before_page_counts_existed(build_project())
    model = project.scene("sc_42")
    project.scenes.append(Scene(id="sc_new", number="99", heading=model.heading, int_ext=model.int_ext,
                                time_of_day=model.time_of_day, synopsis="A scene a producer added after the seed"))

    migrate_seed_state(project)

    assert project.scene("sc_new").eighths is None


def test_a_day_read_from_a_database_written_before_the_page_counts_boards_them(monkeypatch):
    """Through the API, which is where "pages not on file" was actually visible."""
    app_module, repo = _api(monkeypatch, _stored_before_page_counts_existed(_stored()))

    with TestClient(app_module.app) as c:
        body = c.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}").json()

    scenes = body["scenes"]
    boarded = [scenes[i["scene_id"]]["eighths"] for i in body["day"]["items"]]
    assert None not in boarded and sum(boarded) == 39
    assert repo.get_project(PROJECT_ID).scene("sc_42").eighths == 8  # persisted, not just returned


# --------------------------------------------------------------------------- #
# Operating facts a panel would otherwise print as this day's own
# --------------------------------------------------------------------------- #


def test_a_day_still_holding_the_models_defaults_adopts_what_the_seed_states():
    project = _stored()
    day5 = project.shoot_day("day_5")
    assert (day5.unit_call, day5.crew_size) == ("06:30", 45)  # the invented headline on a night unit

    migrate_seed_state(project)

    assert (day5.unit_call, day5.standard_hours, day5.crew_size) == ("18:00", 10.0, 30)
    assert project.shoot_day("day_3").unit_call == "05:15"


def test_a_day_the_seed_never_states_a_fact_for_keeps_the_default_it_has():
    """Adoption is only ever from the seed. A field the seed leaves alone is left alone."""
    project = _stored()
    day4 = project.shoot_day(DAY4_ID)
    day4.crew_size = 51  # a producer added six people

    migrate_seed_state(project)

    assert day4.crew_size == 51
    assert day4.unit_call == "06:30"  # the seed says 06:30 too; nothing to adopt


def test_a_night_unit_no_longer_reports_a_dawn_call_or_an_overtime_problem_it_does_not_have():
    project = _stored()
    migrate_seed_state(project)
    day5 = project.shoot_day("day_5")

    last_end = max(int(i.end[:2]) * 60 + int(i.end[3:]) for i in day5.items)
    overtime_at = int(day5.unit_call[:2]) * 60 + int(day5.unit_call[3:]) + int(day5.standard_hours * 60)
    assert last_end <= overtime_at


# --------------------------------------------------------------------------- #
# One golden hour, and it is the one the sun makes
# --------------------------------------------------------------------------- #


def test_every_shoot_day_carries_the_windows_the_ephemeris_computes_for_its_own_date():
    project = _stored()
    migrate_seed_state(project)

    for day in project.shoot_days:
        profile = city_ephemeris(project.base_city, day.date)
        for name in SOLAR_WINDOW_FIELDS:
            assert tuple(getattr(day, name)) == tuple(getattr(profile, name)), f"{day.id}.{name}"


def test_the_window_the_validator_enforces_is_the_window_the_board_headlines():
    """Two golden hours on one page, 40 minutes apart, is the failure that deleted three panels."""
    project = build_project()
    day = project.shoot_day(DAY4_ID)
    headline = city_ephemeris(project.base_city, day.date).golden_hour_dusk

    assert tuple(day.golden_hour_dusk) == tuple(headline)
    assert tuple(day.golden_hour_dusk) != ("17:45", "19:15")  # the class default nobody measured

    sc42 = project.scene("sc_42")
    gs = int(headline[0][:2]) * 60 + int(headline[0][3:])
    assert lighting_check(day, sc42, gs, gs + sc42.estimated_minutes) is None
    outside = lighting_check(day, sc42, 9 * 60, 11 * 60 + 30)
    assert outside is not None and outside.hard and headline[0] in outside.message


def test_a_window_computed_for_a_date_that_has_since_moved_is_recomputed():
    """The re-anchor slides the whole schedule; a window is a function of the date it slid to."""
    from scenepilot.seed.nightfall import reanchor_shoot_days

    project = build_project()

    reanchor_shoot_days(project, today="2026-12-21")
    winter = tuple(project.shoot_day(DAY4_ID).golden_hour_dusk)
    assert winter == tuple(city_ephemeris(SEED_CITY, "2026-12-21").golden_hour_dusk)

    reanchor_shoot_days(project, today="2026-06-21")
    summer = tuple(project.shoot_day(DAY4_ID).golden_hour_dusk)
    assert summer == tuple(city_ephemeris(SEED_CITY, "2026-06-21").golden_hour_dusk)
    assert summer != winter  # solstice to solstice is more than an hour of dusk


def test_a_note_may_not_restate_a_window_the_engine_derives():
    project = _stored()
    project.shoot_day(DAY4_ID).notes = "Standard 12.5 h day from 06:30 (wrap 19:00). Golden hour ≈17:45–19:15."

    migrate_seed_state(project)

    assert project.shoot_day(DAY4_ID).notes == "Standard 12.5 h day from 06:30 (wrap 19:00)."
    assert "17:45" not in (build_project().shoot_day(DAY4_ID).notes or "")


# --------------------------------------------------------------------------- #
# Through the service, which is the only place the bug was ever visible
# --------------------------------------------------------------------------- #


def _api(monkeypatch, project):
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    repo = Repo(make_engine("sqlite:///:memory:"))
    repo.save_project(project)
    monkeypatch.setattr(app_module, "repo", repo)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, warm_demo=False))
    return app_module, repo


def test_a_day_read_against_a_project_stored_before_the_fields_existed_returns_a_drawable_map(monkeypatch):
    app_module, repo = _api(monkeypatch, _stored())

    with TestClient(app_module.app) as c:
        body = c.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}").json()

    geo = body["geography"]
    assert geo["locations_missing_coordinates"] == []
    assert geo["total_straight_line_km"] > 0 and geo["move_count"] == 3
    assert all(m["straight_line_km"] is not None for m in geo["moves"])
    assert body["day"]["golden_hour_dusk"] == list(city_ephemeris(SEED_CITY, body["day"]["date"]).golden_hour_dusk)
    assert repo.get_project(PROJECT_ID).resource("loc_rooftop").has_coordinates  # persisted, not just returned


def test_the_migration_is_announced_rather_than_done_silently(monkeypatch):
    app_module, repo = _api(monkeypatch, _stored())

    with TestClient(app_module.app) as c:
        c.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}")

    messages = [e.message for e in repo.list_activity(project_id=PROJECT_ID)]
    assert any("coordinates" in m for m in messages)
    assert any("ephemeris" in m for m in messages)


def test_a_read_of_an_already_migrated_project_writes_nothing(monkeypatch):
    app_module, repo = _api(monkeypatch, build_project())
    saves: list[str] = []
    saved = repo.save_project
    monkeypatch.setattr(repo, "save_project", lambda p: (saves.append(p.id), saved(p))[1])

    with TestClient(app_module.app) as c:
        for _ in range(3):
            assert c.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}").status_code == 200

    assert saves == []


# --------------------------------------------------------------------------- #
# The radio plan a database written before the field existed has none of
# --------------------------------------------------------------------------- #


def test_a_crew_head_persisted_without_a_channel_gets_the_seeds_radio_plan():
    """Same failure as the coordinates: the field is new, the database is not, and the column is blank.

    A call sheet whose channel column is empty is a call sheet the unit cannot use to raise anybody,
    and no test would have caught it — every test builds the project fresh.
    """
    project = build_project()
    for res in project.resources:
        if res.type == ResourceType.CREW:
            res.walkie_channel = None

    notes = migrate_seed_state(project)

    assert project.resource("crew_1st_ad").walkie_channel == 1
    assert project.resource("crew_camera").walkie_channel == 2
    assert all(r.walkie_channel is not None for r in project.resources if r.type == ResourceType.CREW)
    assert any("radio plan" in n for n in notes)


def test_a_channel_somebody_has_already_changed_is_left_alone():
    """`_adopt`'s rule, unchanged: only a stored class default is treated as never having been set."""
    project = build_project()
    project.resource("crew_camera").walkie_channel = 7

    migrate_seed_state(project)

    assert project.resource("crew_camera").walkie_channel == 7


def test_nothing_that_is_not_crew_is_given_a_channel():
    project = build_project()
    for res in project.resources:
        res.walkie_channel = None

    migrate_seed_state(project)

    assert all(r.walkie_channel is None for r in project.resources if r.type != ResourceType.CREW)


def test_a_performer_persisted_without_a_day_rate_is_priced_from_the_production():
    """The DOOD prices hold days with this; a stored 0 is how a matrix ends up reporting no cost."""
    project = build_project()
    for res in project.resources:
        if res.type == ResourceType.CAST:
            res.day_rate_inr = 0

    notes = migrate_seed_state(project)

    assert project.resource("cast_aarav").day_rate_inr == 180000
    assert project.resource("cast_stunt").day_rate_inr == 45000
    assert any("day rate" in n for n in notes)


def test_a_day_rate_somebody_has_already_negotiated_is_left_alone():
    project = build_project()
    project.resource("cast_meera").day_rate_inr = 250000

    migrate_seed_state(project)

    assert project.resource("cast_meera").day_rate_inr == 250000
