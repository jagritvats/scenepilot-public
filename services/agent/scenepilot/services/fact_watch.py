"""Detecting that a fact this production relies on is no longer true — and doing nothing about it.

The interesting case is not "we learned something new" — that is what the event_stream monitors are
for — it is **"a rule you accepted three weeks ago is no longer the rule"**: the noise curfew moved
an hour earlier, the permit authority was renamed, drones stopped being allowed. A schedule built on
the old value is now quietly wrong.

Two ways in, for two different moments:

- **A snapshot monitor** re-runs a location dossier on Parallel's schedule and pushes only the fields
  that moved, with fresh citations. Right for the weeks between planning and shooting.
- **A pre-flight re-check** is a producer asking on purpose, the night before a day locks. A monitor
  cannot fire on cue, and the night before is when anyone actually looks.

They converge immediately: both produce the same graded, cited `FactChange`, so nothing downstream
needs to care which one found it.

Two principles are load-bearing here, and both are the same ones the rest of ScenePilot follows:

1. **Nothing changes the schedule on its own.** A detected change lands as a `FactChange` with
   status PENDING. The accepted value keeps constraining the schedule until a producer adopts the
   new one. Adopting a *binding* change also clears the acceptance, because acceptance was given to
   a specific value — so the producer sees the new window and accepts it, or does not.
2. **The change is graded exactly like the original fact.** We do not invent a second, weaker
   confidence path for updates: the changed fields are fed back through `dossier.map_facts`, so a
   new value only becomes HARD under the same rule (high confidence + a citation + mechanically
   checkable) that the first dossier had to satisfy.

We also compare values before recording anything. Parallel reports a field as changed when its
*content* moved, but a re-run can legitimately return the same fact in different words; recording a
"change" from "22:00-06:00" to "22:00-06:00" would train producers to ignore the feature.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import FactBinding
from ..domain.models import FactChange, LocationFact, MonitorRecord, Project, TaskRun, utcnow
from .dossier import map_facts


def changes_from_snapshot(project: Project, monitor: MonitorRecord, event: dict[str, Any], simulated: bool = False) -> list[FactChange]:
    """Grade a snapshot monitor's event into pending FactChanges. Pure — records nothing."""
    return _changes(
        project, resource_id=monitor.resource_id, event=event, simulated=simulated,
        detected_by="monitor", monitor_id=monitor.id, task_run_id=monitor.task_run_id,
    )


def changes_from_recheck(project: Project, resource_id: str, task_run: TaskRun, previous_output: dict[str, Any]) -> list[FactChange]:
    """The producer re-ran a dossier before the day locks. Same diff, no monitor in sight.

    A snapshot monitor cannot fire on cue, which makes it the wrong tool for the moment that
    actually matters — the night before, when someone finally asks "are these still the rules?".
    This is that question as a button. The comparison is identical, so a re-check and a monitor
    event are indistinguishable downstream: same grading, same pending decision, same audit trail.
    """
    event = {
        "event_id": f"recheck_{task_run.id}",
        "event_date": (task_run.finished_at or task_run.started_at).date().isoformat(),
        "changed": task_run.output,  # the whole output; unchanged fields drop out in the compare
        "previous": previous_output,
        "basis": [b.model_dump(mode="json") for b in task_run.basis],
    }
    return _changes(
        project, resource_id=resource_id, event=event, simulated=False,
        detected_by="preflight", monitor_id=None, task_run_id=task_run.id,
    )


def _changes(
    project: Project,
    *,
    resource_id: str | None,
    event: dict[str, Any],
    simulated: bool,
    detected_by: str,
    monitor_id: str | None,
    task_run_id: str | None,
) -> list[FactChange]:
    changed = event.get("changed") or {}
    if not resource_id or not changed:
        return []
    # Reuse the dossier grader verbatim: same confidence gate, same rule parsing, same drops for
    # non-answers. `map_facts` needs a TaskRun, so give it one holding just the changed fields.
    probe = TaskRun(
        id=task_run_id or monitor_id or "probe",
        project_id=project.id,
        resource_id=resource_id,
        purpose="snapshot_change",
        status="OK",
        output=changed,
        basis=_basis_models(event.get("basis") or []),
    )
    graded = map_facts(probe, project)
    current = {(f.key, f.label): f for f in project.location_facts if f.resource_id == resource_id}
    previous = event.get("previous") or {}
    seen = {(c.key, c.new_value) for c in project.fact_changes if c.resource_id == resource_id and c.pending}

    out: list[FactChange] = []
    for fact in graded:
        old = current.get((fact.key, fact.label))
        old_value = old.value if old is not None else _previous_value(previous, fact.key, fact.label)
        if old_value.strip() == fact.value.strip():
            continue  # re-worded, not changed
        if (fact.key, fact.value) in seen:
            continue  # already pending from an earlier execution
        out.append(
            FactChange(
                project_id=project.id,
                resource_id=resource_id,
                detected_by=detected_by,
                monitor_id=monitor_id,
                task_run_id=task_run_id,
                event_id=str(event.get("event_id") or ""),
                event_date=event.get("event_date"),
                key=fact.key,
                label=fact.label,
                fact_id=old.id if old is not None else None,
                old_value=old_value,
                new_value=fact.value,
                binding=fact.binding,
                confidence=fact.confidence,
                reasoning=fact.reasoning,
                citations=list(fact.citations),
                rule=fact.rule,
                old_accepted=bool(old is not None and old.accepted),
                old_binds=bool(old is not None and old.binds),
                simulated=simulated,
            )
        )
    return out


def adopt_change(project: Project, change: FactChange, decided_by: str = "producer") -> LocationFact:
    """Take the new value. A binding change loses its acceptance — it must be signed off again."""
    fact = next((f for f in project.location_facts if f.id == change.fact_id), None)
    if fact is None:
        fact = LocationFact(
            project_id=project.id, resource_id=change.resource_id, task_run_id=change.task_run_id or change.monitor_id or "",
            key=change.key, label=change.label, value=change.new_value,
        )
        project.location_facts.append(fact)
    fact.value = change.new_value
    fact.binding = change.binding
    fact.confidence = change.confidence
    fact.reasoning = change.reasoning
    fact.citations = list(change.citations)
    fact.rule = change.rule
    fact.rejected = False
    if fact.binding == FactBinding.HARD:
        # Acceptance was given to a value, not to a field. A new window has to be accepted anew.
        fact.accepted, fact.accepted_at, fact.accepted_by = False, None, None
    change.status, change.decided_at, change.decided_by = "ADOPTED", utcnow(), decided_by
    change.fact_id = fact.id
    return fact


def dismiss_change(change: FactChange, decided_by: str = "producer") -> None:
    """Keep the value the production already accepted; the fact is untouched."""
    change.status, change.decided_at, change.decided_by = "DISMISSED", utcnow(), decided_by


def pending_changes(project: Project, resource_id: str | None = None) -> list[FactChange]:
    return [c for c in project.fact_changes if c.pending and (resource_id is None or c.resource_id == resource_id)]


def _basis_models(raw: list[Any]):
    from ..tools.parallel_task import _basis

    return _basis(raw)


def _previous_value(previous: dict[str, Any], key: str, label: str) -> str:
    """The prior value straight from the event, for a fact we never graded (or have since dropped)."""
    raw = previous.get(key)
    if isinstance(raw, list):
        # `map_facts` labels list elements "Restriction 1", "Restriction 2" — recover the index.
        tail = label.rsplit(" ", 1)[-1]
        index = int(tail) - 1 if tail.isdigit() else 0
        raw = raw[index] if 0 <= index < len(raw) else ""
    return str(raw or "").strip()


# A fabricated snapshot event for the demo path, in the exact shape `flatten_snapshot` produces.
# Clearly labelled as simulated everywhere it surfaces. The value is the real Mumbai curfew moved
# one hour earlier — the smallest change that turns a legal night shoot into an illegal one.
SIMULATED_SNAPSHOT: dict[str, Any] = {
    "changed": {"noise_curfew": "Night time shall mean from 9.00 p.m. to 6.00 a.m. — 21:00-06:00 in residential zones, revised under the amended Noise Pollution (Regulation and Control) Rules."},
    "previous": {"noise_curfew": "Night time shall mean from 10.00 p.m. to 6.00 a.m. — 22:00-06:00 in residential zones."},
    "basis": [
        {
            "field": "noise_curfew",
            "reasoning": "The amended state notification advances the start of night hours in residential zones from 22:00 to 21:00.",
            "confidence": "high",
            "citations": [
                {
                    "url": "https://www.indiacode.nic.in/handle/123456789/1362",
                    "title": "Noise Pollution (Regulation and Control) Rules, 2000 — amendment",
                    "excerpts": ["Night time shall mean from 9.00 p.m. to 6.00 a.m. in residential areas."],
                }
            ],
        }
    ],
}
