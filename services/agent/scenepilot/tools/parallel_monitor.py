"""Parallel Monitor API — the outside world pushes disruptions to ScenePilot.

Two monitor types, for the two ways the world moves under a production:

**`event_stream` — new information.** Per shoot day: "IMD warnings for Mumbai on <date>", "road
closures near the day's locations". Parallel runs them on a schedule and calls our webhook when it
detects a material change; we fetch the event and open a *draft* disruption.

**`snapshot` — known facts that changed.** A dossier Task run's output becomes the monitor's schema
and baseline, and Parallel re-runs it on a schedule. When a field moves — the noise curfew shifts an
hour earlier, the permit authority is renamed — the event carries only the *changed* fields with
fresh citations, plus the full previous output. That is the difference that matters: an event_stream
monitor tells you something happened today; a snapshot monitor tells you a rule you have been
planning against for three weeks is no longer the rule you accepted.

Both end in a producer decision, never an automatic schedule change — a draft disruption for the
first, a pending `FactChange` for the second. Monitors are stateful server-side objects, so they are
never recorded/replayed; a simulated event path exists so demos do not depend on the weather.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings, settings as default_settings
from ..domain.models import MonitorRecord, Project, Resource, ShootDay, TaskRun, utcnow
from .parallel_memory import scope_key
from .parallel_session import DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT_S, ParallelSession

log = logging.getLogger(__name__)

WEBHOOK_EVENT_TYPES = ["monitor.event.detected"]


def monitor_queries(project: Project, day: ShootDay) -> list[dict[str, str]]:
    """Deterministic monitor definitions for a shoot day."""
    city = project.base_city
    loc_names = sorted({project.resource(i.location_id).name.split(" — ")[-1] for i in day.items if i.location_id})
    near = ", ".join(loc_names[:3]) or city
    return [
        {"kind": "WEATHER", "query": f"India Meteorological Department warnings, nowcasts or orange/red alerts for {city} on {day.date} (heavy rain, thunderstorm, gusty wind)"},
        {"kind": "TRANSPORT", "query": f"Road closures, traffic police diversions, bandh or strike announcements affecting {near} in {city} on {day.date}"},
    ]


class ParallelMonitorTool:
    def __init__(self, session: ParallelSession | None = None, settings: Settings | None = None, timeout: float = DEFAULT_TIMEOUT_S, max_retries: int = DEFAULT_MAX_RETRIES):
        self.settings = settings or default_settings
        self.session = session or ParallelSession(self.settings, client_model=self.settings.gemini_model, timeout=timeout, max_retries=max_retries)

    def create_for_day(self, project: Project, day: ShootDay, webhook_url: str, frequency: str = "1h", processor: str = "lite") -> list[MonitorRecord]:
        if not self.settings.parallel_configured:
            raise RuntimeError("PARALLEL_API_KEY is not configured")
        records: list[MonitorRecord] = []
        # Monitors write into the project's Parallel memory scope when the Memory feature is on, so
        # what they detect is readable later alongside Task and FindAll research (see parallel_memory).
        scope = scope_key(project, self.settings) if self.settings.parallel_memory_enabled else None
        for spec in monitor_queries(project, day):
            m = self.session.client.monitor.create(
                type="event_stream",
                frequency=frequency,
                processor=processor,  # type: ignore[arg-type]
                settings={"query": spec["query"]},
                webhook={"url": webhook_url, "event_types": WEBHOOK_EVENT_TYPES},  # type: ignore[arg-type]
                metadata={"project_id": project.id, "shoot_day_id": day.id, "kind": spec["kind"]},
                **({"memory_scope_key": scope} if scope else {}),
            )
            records.append(MonitorRecord(id=m.monitor_id, project_id=project.id, shoot_day_id=day.id, kind=spec["kind"], query=spec["query"], frequency=m.frequency, processor=m.processor, status=m.status, webhook_url=webhook_url, created_at=utcnow()))
        return records

    def watch_dossier(self, project: Project, resource: Resource, task_run: TaskRun, webhook_url: str, frequency: str = "1d") -> MonitorRecord:
        """Watch a completed dossier for change: its output becomes the schema *and* the baseline.

        Daily by default — permit rules and curfews move on the timescale of council meetings, not
        weather, and every execution costs a task run.
        """
        if not self.settings.parallel_configured:
            raise RuntimeError("PARALLEL_API_KEY is not configured")
        if not task_run.provider_run_id:
            raise ValueError("only a dossier that actually ran at Parallel can be watched")
        scope = scope_key(project, self.settings) if self.settings.parallel_memory_enabled else None
        m = self.session.client.monitor.create(
            type="snapshot",
            frequency=frequency,
            settings={"task_run_id": task_run.provider_run_id},
            webhook={"url": webhook_url, "event_types": WEBHOOK_EVENT_TYPES},  # type: ignore[arg-type]
            metadata={"project_id": project.id, "resource_id": resource.id, "kind": "DOSSIER"},
            **({"memory_scope_key": scope} if scope else {}),
        )
        return MonitorRecord(
            id=m.monitor_id, project_id=project.id, kind="DOSSIER", monitor_type="snapshot",
            task_run_id=task_run.id, resource_id=resource.id,
            query=f"Changes to the filming rules for {resource.name}",
            frequency=m.frequency, processor=getattr(m, "processor", None) or self.settings.parallel_task_processor,
            status=m.status, webhook_url=webhook_url, created_at=utcnow(),
        )

    def cancel(self, monitor_id: str) -> None:
        self.session.client.monitor.cancel(monitor_id)

    def events(self, monitor_id: str, event_group_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Newest-first events, flattened to plain dicts.

        Both variants come back through this one call, tagged by `event_type`, so the webhook can
        dispatch on the monitor they belong to and the simulate paths can fabricate the same shapes.
        """
        page = self.session.client.monitor.events(monitor_id, event_group_id=event_group_id, limit=limit)
        out: list[dict[str, Any]] = []
        for ev in page.events:
            kind = getattr(ev, "event_type", None) or "event_stream"
            common = {"event_id": ev.event_id, "event_group_id": ev.event_group_id, "event_date": getattr(ev, "event_date", None), "event_type": kind}
            if kind == "snapshot":
                out.append({**common, **flatten_snapshot(ev)})
                continue
            output = getattr(ev, "output", None)
            text = getattr(output, "content", None)
            if not isinstance(text, str):
                text = str(text) if text is not None else ""
            basis = [b.model_dump(mode="json") if hasattr(b, "model_dump") else b for b in (getattr(output, "basis", None) or [])]
            out.append({**common, "text": text, "basis": basis})
        return out


def flatten_snapshot(ev: Any) -> dict[str, Any]:
    """A snapshot event as plain data: what changed, with its basis, and the full prior output.

    `changed_output.content` holds *only* the fields that moved since the last execution — precisely
    the diff a producer needs, with no client-side comparison to get wrong.
    """
    changed, previous = getattr(ev, "changed_output", None), getattr(ev, "previous_output", None)
    return {
        "changed": _content(changed),
        "basis": [b.model_dump(mode="json") if hasattr(b, "model_dump") else b for b in _raw_basis(changed)],
        "previous": _content(previous),
    }


def _content(output: Any) -> dict[str, Any]:
    """Task output arrives as JSON (`content` is a dict) or text; only JSON can be diffed by field."""
    content = getattr(output, "content", None) if output is not None else None
    return dict(content) if isinstance(content, dict) else {}


def _raw_basis(output: Any) -> list[Any]:
    return list(getattr(output, "basis", None) or []) if output is not None else []
