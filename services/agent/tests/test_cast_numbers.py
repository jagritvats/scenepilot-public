"""The cast number: one join key, shared by every document that names a performer.

In production every credited performer is assigned a number once — 1 is the lead — and from then on
the stripboard's cast column, the DOOD rows, the call sheet's leading **Cast #** column and the
dispatch all address them by it. It is what makes those four read as one system rather than three
spreadsheets and a board, and it is production state: a number a frontend computes for a column is a
production identifier that a real backend field will later silently contradict.

So the guarantees under test are the ones a producer actually relies on. The number is *unique*,
*stable* across a rebuild and a re-seed, *ordered* by billing with the lead at 1, *carried* by every
payload the UI reads — and *absent* from crew, locations, equipment and vehicles, because a call
sheet does not number them.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from scenepilot.domain.enums import ResourceType
from scenepilot.ingestion.dood import build_dood_matrix
from scenepilot.seed.migrate import migrate_seed_state
from scenepilot.seed.nightfall import DAY4_ID, PROJECT_ID, build_project
from scenepilot.services.callsheet import build_call_sheet
from scenepilot.services.export_mmsx import generate_mmsx_xml
from scenepilot.dispatch.delivery import dispatch_roster


def _cast(project):
    return [r for r in project.resources if r.type == ResourceType.CAST]


# --------------------------------------------------------------------------- #
# The number itself
# --------------------------------------------------------------------------- #


def test_every_performer_has_a_number_and_no_two_share_one():
    project = build_project()
    numbers = [r.cast_number for r in _cast(project)]
    assert all(n is not None for n in numbers), "a performer with no number cannot be joined on"
    assert len(set(numbers)) == len(numbers), f"two performers share a cast number: {numbers}"


def test_the_numbers_run_in_billing_order_from_the_lead_at_one():
    project = build_project()
    billing = [(r.cast_number, r.id) for r in sorted(_cast(project), key=lambda r: r.cast_number)]
    assert billing == [(1, "cast_aarav"), (2, "cast_meera"), (3, "cast_vikram"), (4, "cast_stunt")]
    # 1 is the lead because the seed says so, not because the list happens to start with them
    assert project.resource("cast_aarav").attributes.get("role") == "lead"


def test_nothing_that_is_not_cast_carries_a_number():
    """Crew, sets, vendors and vehicles: a call sheet numbers none of them."""
    project = build_project()
    unnumbered = [r for r in project.resources if r.type != ResourceType.CAST]
    assert [r.id for r in unnumbered if r.cast_number is not None] == []


def test_a_rebuild_and_a_re_seed_hand_out_the_same_numbers():
    """A number that moves between deployments is worse than no number at all."""
    first = {r.id: r.cast_number for r in _cast(build_project())}
    for _ in range(3):
        assert {r.id: r.cast_number for r in _cast(build_project())} == first


def test_a_performer_persisted_before_the_field_existed_is_numbered_by_migration():
    """The stored-database case: without this the hosted demo joins on nothing but a name."""
    project = build_project()
    for r in _cast(project):
        r.cast_number = None

    notes = migrate_seed_state(project)
    assert {r.id: r.cast_number for r in _cast(project)} == {r.id: r.cast_number for r in _cast(build_project())}
    assert any("cast number" in n for n in notes)
    # and a number already on file is never reassigned by a later read
    assert not any("cast number" in n for n in migrate_seed_state(project))


# --------------------------------------------------------------------------- #
# Every payload the UI reads
# --------------------------------------------------------------------------- #


def test_the_dood_is_ordered_by_cast_number_not_by_declaration_order():
    """Convention: a DOOD reads 1 down the billing. Reversing the roster must not reorder it."""
    project = build_project()
    project.resources.reverse()

    entries = build_dood_matrix(project)
    assert [e.cast_number for e in entries] == [1, 2, 3, 4]
    assert [e.cast_id for e in entries] == ["cast_aarav", "cast_meera", "cast_vikram", "cast_stunt"]
    assert all(e.cast_number == project.resource(e.cast_id).cast_number for e in entries)


def test_the_call_sheet_carries_the_number_on_every_cast_row():
    """The sheet's rows stay in first-shot order; the number is what joins them to the board."""
    project = build_project()
    sheet = build_call_sheet(project, project.shoot_day(DAY4_ID))

    by_name = {r.name: r.cast_number for r in _cast(project)}
    assert sheet["cast"], "the hero day has cast"
    assert all(row["cast_number"] == by_name[row["name"]] for row in sheet["cast"])

    # The night unit is where the two orders come apart: Zoya opens the day on the stage at 17:00
    # and the Rider is not on camera until the roof at 21:00. The rows stay in first-shot order —
    # that is what makes the staggered calls beside them readable — so the number is the only thing
    # tying a row to the same performer's strip, and it is not the row's position.
    night = build_call_sheet(project, project.shoot_day("day_6"))
    assert [row["cast_number"] for row in night["cast"]] == [2, 1, 4]


def test_the_dispatch_addresses_cast_by_number_and_crew_by_none():
    project = build_project()
    roster = dispatch_roster(project, project.shoot_day(DAY4_ID))

    cast_rows = [r for r in roster if r.department == "Cast"]
    assert cast_rows and all(r.cast_number == project.resource(r.resource_id).cast_number for r in cast_rows)
    assert [r.name for r in roster if r.department != "Cast" and r.cast_number is not None] == []


def test_the_stripboard_export_stamps_the_number_on_every_performer():
    import xml.etree.ElementTree as ET

    project = build_project()
    root = ET.fromstring(generate_mmsx_xml(project, project.shoot_day(DAY4_ID)))

    performers = root.findall(".//Performer")
    assert performers
    assert all(p.attrib["number"] == str(project.resource(p.attrib["id"]).cast_number) for p in performers)


def test_the_day_payload_and_the_dood_endpoint_both_serve_the_number(monkeypatch):
    """The two reads the day page makes. A column the UI can build has to arrive in both."""
    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    with TestClient(app_module.app) as client:
        resources = client.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}").json()["resources"]
        assert resources["cast_aarav"]["cast_number"] == 1
        assert {rid: r["cast_number"] for rid, r in resources.items() if r["type"] == "CAST"} == {
            "cast_aarav": 1, "cast_meera": 2, "cast_vikram": 3, "cast_stunt": 4,
        }
        assert all(r["cast_number"] is None for r in resources.values() if r["type"] != "CAST")

        entries = client.get(f"/api/projects/{PROJECT_ID}/dood").json()["entries"]
        assert [e["cast_number"] for e in entries] == [1, 2, 3, 4]

        sheet = client.get(f"/api/projects/{PROJECT_ID}/shoot-days/{DAY4_ID}/call-sheet").json()["current"]
        assert all(isinstance(row["cast_number"], int) for row in sheet["cast"])
