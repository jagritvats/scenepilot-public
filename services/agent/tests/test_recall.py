"""What a run recalled from Parallel Memory, joined back to the run that first learned it.

Memory's whole value is the second time, and the only trace of it was a count — "reused 3 remembered
runs" — which tells a producer that reuse happened and nothing about what was reused. The join is the
interesting half, because `MemoryEntry.ref_id` is *Parallel's* id, not ScenePilot's; every write path
stores the provider's id alongside its own record, so the chain back is recoverable.

The property that matters when it is not: an entry whose local run has been reset away is still a
real thing Parallel remembered, so it is reported with a note rather than dropped or linked nowhere.
"""

from scenepilot.domain.models import FindAllRun, MemoryEntry, MemoryRead, MonitorRecord, TaskRun
from scenepilot.seed.nightfall import build_project
from scenepilot.services.recall import recall_view


def _read(*entries: MemoryEntry, run_id: str = "run_1") -> MemoryRead:
    return MemoryRead(
        project_id="proj_nightfall",
        run_id=run_id,
        scope_key="scenepilot_proj_nightfall",
        query="EXT. MUMBAI ROOFTOP",
        status="OK",
        entries=list(entries),
    )


def test_a_recalled_dossier_names_the_location_it_was_about():
    p = build_project()
    task = TaskRun(project_id=p.id, resource_id="loc_rooftop", provider_run_id="trun_abc", status="OK", purpose="location_dossier")
    entry = MemoryEntry(kind="task", ref_id="trun_abc", input_excerpt="Filming location: Rooftop A", output_excerpt="Noise curfew 22:00-06:00")

    row = recall_view(p, [_read(entry)], [task], [])[0]

    assert row["kind_label"] == "location dossier"
    assert row["origin"]["kind"] == "task_run" and row["origin"]["id"] == task.id
    assert "Rooftop A" in row["origin"]["label"]
    assert row["origin_note"] is None
    assert row["excerpt"] == "Noise curfew 22:00-06:00"


def test_a_recalled_monitor_joins_on_its_own_provider_id():
    """A monitor's id *is* the provider id, so the join is direct."""
    p = build_project()
    p.monitors.append(
        MonitorRecord(id="monitor_xyz", project_id=p.id, kind="DOSSIER", monitor_type="snapshot", resource_id="loc_rooftop", query="rules", frequency="1d")
    )
    entry = MemoryEntry(kind="monitor", ref_id="monitor_xyz", input_excerpt="watching Rooftop A")

    row = recall_view(p, [_read(entry)], [], [])[0]
    assert row["origin"]["kind"] == "monitor" and row["origin"]["id"] == "monitor_xyz"


def test_a_recalled_entity_search_joins_on_the_findall_provider_id():
    p = build_project()
    findall = FindAllRun(project_id=p.id, provider_findall_id="fa_123", status="OK", objective="crane vendors")
    entry = MemoryEntry(kind="findall", ref_id="fa_123", input_excerpt="crane rental vendors", matched_count=7)

    row = recall_view(p, [_read(entry)], [], [findall])[0]
    assert row["origin"]["kind"] == "findall_run" and row["origin"]["id"] == findall.id
    assert row["kind_label"] == "entity search"


def test_an_entry_whose_local_run_is_gone_says_so_rather_than_linking_nowhere():
    """A reset clears local runs; Parallel still remembers. Dropping the entry would hide that."""
    p = build_project()
    entry = MemoryEntry(kind="task", ref_id="trun_forgotten", input_excerpt="a dossier this deployment no longer holds")

    row = recall_view(p, [_read(entry)], [], [])[0]
    assert row["origin"] is None
    assert row["origin_note"] and "no longer in this deployment" in row["origin_note"]
    assert row["ref_id"] == "trun_forgotten", "the provider's own id is still shown"


def test_every_row_carries_the_read_it_came_from():
    p = build_project()
    rows = recall_view(p, [_read(MemoryEntry(kind="task", ref_id="a"), MemoryEntry(kind="monitor", ref_id="b"))], [], [])
    assert len(rows) == 2
    for row in rows:
        assert row["run_id"] == "run_1" and row["scope_key"] == "scenepilot_proj_nightfall"
        assert row["query"] == "EXT. MUMBAI ROOFTOP"


def test_no_reads_is_no_rows():
    assert recall_view(build_project(), [], [], []) == []


def test_the_scene_payload_carries_what_the_run_recalled(monkeypatch):
    from fastapi.testclient import TestClient

    from scenepilot.api import app as app_module
    from scenepilot.store.db import make_engine
    from scenepilot.store.repo import Repo

    monkeypatch.setattr(app_module, "repo", Repo(make_engine("sqlite:///:memory:")))
    with TestClient(app_module.app) as c:
        body = c.get("/api/projects/proj_nightfall/scenes/sc_42").json()
        assert "recalled" in body and body["recalled"] == [], "no planning run has recalled anything yet"
