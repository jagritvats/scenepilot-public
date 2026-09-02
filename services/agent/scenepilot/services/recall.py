"""What a run recalled from Parallel Memory, joined back to the run that first learned it.

Memory is the one Parallel surface whose whole value is *the second time*: a planning run that starts
from prior research is standing on a dossier, a monitor or an entity search this production already
paid for. Until now the only trace of that was a count — "reused 3 remembered runs" — which tells a
producer that reuse happened and nothing about what was reused.

The join is the interesting half. `MemoryEntry.ref_id` is **Parallel's** id, not ScenePilot's, so a
recalled entry does not know which local run produced it. But every write path stores the provider's
id alongside its own record (`TaskRun.provider_run_id`, `FindAllRun.provider_findall_id`, and a
monitor's id *is* the provider id), so the chain back is recoverable — and where it is not, the entry
says so rather than linking to a run it cannot name.
"""

from __future__ import annotations

from typing import Any

from ..domain.models import MemoryRead, Project

KIND_LABEL = {"task": "location dossier", "monitor": "monitor", "findall": "entity search"}


def _origin(project: Project, entry, task_runs: list, findall_runs: list) -> dict[str, Any] | None:
    """The local run that first produced what memory returned, when it can be identified."""
    if entry.kind == "monitor":
        monitor = next((m for m in project.monitors if m.id == entry.ref_id), None)
        if monitor:
            return {"kind": "monitor", "id": monitor.id, "label": f"monitor on {monitor.kind.lower()}", "resource_id": monitor.resource_id}
        return None
    if entry.kind == "task":
        run = next((t for t in task_runs if t.provider_run_id and t.provider_run_id == entry.ref_id), None)
        if run:
            resource = next((r for r in project.resources if r.id == run.resource_id), None)
            return {"kind": "task_run", "id": run.id, "label": f"dossier for {resource.name}" if resource else run.purpose, "resource_id": run.resource_id}
        return None
    if entry.kind == "findall":
        run = next((f for f in findall_runs if getattr(f, "provider_findall_id", None) == entry.ref_id), None)
        if run:
            return {"kind": "findall_run", "id": run.id, "label": "vendor search", "resource_id": getattr(run, "resource_id", None)}
        return None
    return None


def recall_view(project: Project, reads: list[MemoryRead], task_runs: list, findall_runs: list) -> list[dict[str, Any]]:
    """One row per recalled entry, newest read first, each linked to its origin where known."""
    rows: list[dict[str, Any]] = []
    for read in reads:
        for entry in read.entries:
            origin = _origin(project, entry, task_runs, findall_runs)
            rows.append({
                "memory_read_id": read.id,
                "run_id": read.run_id,
                "scope_key": read.scope_key,
                "query": read.query,
                "kind": entry.kind,
                "kind_label": KIND_LABEL.get(entry.kind, entry.kind),
                "ref_id": entry.ref_id,
                "excerpt": (entry.output_excerpt or entry.input_excerpt or "").replace("\n", " ").strip(),
                "input_excerpt": entry.input_excerpt,
                "updated_at": entry.updated_at,
                "origin": origin,
                # Said rather than hidden: a recalled entry whose local run has been reset away is
                # still a real thing Parallel remembered, and pretending otherwise would drop it.
                "origin_note": None if origin else "This was remembered by Parallel; the run that first produced it is no longer in this deployment's state.",
            })
    return rows
