"""Parallel Task API — structured research whose every field arrives with its own citation.

Search answers "is this true?". The Task API answers "what does the real world *require* here?",
in a shape the deterministic engine can consume: a JSON object where each field carries a `basis`
(citations, reasoning, confidence). That is what turns web evidence into a production constraint
instead of a paragraph — see `services/dossier.py` for the confidence gate that decides how much
authority a discovered fact is allowed to have.

Differences from Search/Extract worth remembering:
  * `task_run.create` takes **no `session_id` and no `client_model`**. Runs are linked by
    `metadata` and by the project's `memory_scope_key` (which also feeds the Production brain).
  * It is slow (1–5 min on `core-fast`) and costs ~$0.025 a run, so it is feature-gated and only
    ever reached from an explicit producer action.
  * `interaction_id` on the result can be passed back as `previous_interaction_id` to continue the
    same line of research rather than starting cold.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from ..config import Settings, settings as default_settings
from ..domain.models import BasisCitation, FieldBasis, ParallelWarning, Project, Resource, ShootDay, TaskRun, utcnow
from .normalize import normalize
from .recorder import Recorder, ReplayMiss

log = logging.getLogger(__name__)

# Per-element basis (a citation per list item, e.g. `restrictions.0`) went GA on 2026-08-24, but the
# pinned SDK's generated docs still describe it as header-gated. Sending the flag is harmless on the
# GA path and guarantees the behaviour on the older one.
FIELD_BASIS_BETA = "field-basis-2025-11-25"

DOSSIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "permit_authority": {"type": "string", "description": "The authority that issues filming permits for this location, by name."},
        "permit_lead_time_days": {"type": "string", "description": "Typical lead time to obtain a filming permit, in days. Empty if unknown."},
        "fee_band_inr": {"type": "string", "description": "Typical permit or location fee range in INR. Empty if unknown."},
        "noise_curfew": {"type": "string", "description": "Local night-time noise restriction as a plain time range, e.g. '22:00-06:00'. Empty string if there is no curfew."},
        "drone_rules": {"type": "string", "description": "Rules on flying camera drones here — say plainly whether drones are prohibited, permitted with permission, or unrestricted."},
        "fireworks_rules": {"type": "string", "description": "Rules on pyrotechnics or fireworks here — say plainly whether they are prohibited or permitted with a licence."},
        "restrictions": {"type": "array", "items": {"type": "string"}, "description": "Other filming restrictions that apply at this location, one per item."},
        "nearest_hospital": {"type": "string", "description": "Nearest hospital with an emergency department, and approximate distance."},
        "monsoon_flooding_history": {"type": "string", "description": "Known monsoon waterlogging or flooding history for this area."},
    },
    "required": ["permit_authority", "restrictions"],
}

# The shooting hours a board can hold: `services/schedule` bounds a day at 06:00 and the web
# scrubber's axis is 06:00–22:00, so an hour outside that range has nothing to be rendered onto.
WEATHER_HOURS: tuple[str, ...] = tuple(f"hour_{h:02d}" for h in range(6, 22))

# One flat string field per hour rather than an array of objects. Per-element basis is proven for
# arrays *of strings* (`restrictions.0`); for arrays of objects it is not, and a schema guess is
# unrecoverable once the paid recording is made. Flat named fields are exactly the shape
# DOSSIER_SCHEMA already proves returns one FieldBasis — citations, reasoning and confidence — per
# field, which is the whole point of rendering an hour: every bar carries its own source.
WEATHER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{
            f"hour_{h:02d}": {
                "type": "string",
                "description": (
                    f"Precipitation outlook for {h:02d}:00-{h + 1:02d}:00 local time on the shooting date: "
                    "the chance of precipitation as a percentage and a short condition, e.g. "
                    "'70% - moderate showers'. Empty string if no source states an hourly figure."
                ),
            }
            for h in range(6, 22)
        },
        "day_summary": {"type": "string", "description": "One-sentence overall precipitation outlook for the day, naming the peak window if there is one."},
    },
    # Only the summary is required: an hour no source covers must be answerable as empty rather than
    # guessed, because an invented bar is a bar a producer would schedule against.
    "required": ["day_summary"],
}


def build_task_input(project: Project, resource: Resource, date: str | None = None) -> str:
    """The semantic request — also what the record/replay key hashes."""
    attrs = ", ".join(f"{k}: {v}" for k, v in sorted(resource.attributes.items())) if resource.attributes else ""
    parts = [
        f"Filming location: {resource.name}.",
        f"City: {project.base_city}, {project.country_code}.",
        f"Location type: {attrs}." if attrs else "",
        f"Shooting date: {date}." if date else "",
        "Research the rules and practical constraints that apply to a film production shooting here: "
        "which authority issues the filming permit, the typical lead time and fee band, any night-time "
        "noise curfew as a time range, whether camera drones and pyrotechnics are allowed, other filming "
        "restrictions, the nearest emergency hospital, and any monsoon flooding history. "
        "Prefer official municipal, police and civil-aviation sources. Leave a field empty rather than guessing.",
    ]
    return " ".join(p for p in parts if p)


def build_weather_input(project: Project, day: ShootDay) -> str:
    """The semantic request for one shoot day's hourly precipitation — also what the key hashes.

    Two things here are load-bearing for record/replay and must not be reworded casually:

    * the date is printed as `day.date` (`YYYY-MM-DD`), the one form `normalize.DATE_RE` masks. Any
      other spelling ("4 September") escapes the mask, and since the seed re-anchors the shoot week
      to *today* on every boot, the key would then rot overnight and the paid recording with it; and
    * `production day {n}` is what keeps two days' keys apart. Every date collapses to the same
      `@DATE@` placeholder, so without the day number a second recorded day would hash identically
      to the first and silently overwrite its fixture.

    Deliberately absent: the day's schedule, its solar windows and any reported disruption. The first
    two are recomputed per date and would drift the key; the third would tell the researcher what we
    hope to hear.
    """
    return " ".join(
        [
            f"City: {project.base_city}, {project.country_code}.",
            f"Shooting date: {day.date} (production day {day.day_number}).",
            "Build an hour-by-hour precipitation outlook for a film crew shooting outdoors in this city on this "
            "date, covering every hour from 06:00 to 22:00 local time. For each hour give the chance of "
            "precipitation as a percentage and a short plain-language condition. Prefer official meteorological "
            "sources such as India Meteorological Department nowcasts and district forecasts. "
            "Leave an hour's field empty rather than guessing.",
        ]
    )


def build_task_request(input_text: str, processor: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """What is recorded/replayed. Excludes metadata and memory_scope_key, which vary per run."""
    return {"input": input_text, "processor": processor, "output_schema": DOSSIER_SCHEMA if schema is None else schema}


def recording_key(request: dict[str, Any]) -> str:
    """The record/replay key for a Task request. The seed replays by the same key the tool writes."""
    return Recorder.key("parallel_task", json.loads(normalize(json.dumps(request, ensure_ascii=False))))


def apply_payload(tr: TaskRun, payload: dict[str, Any], *, replayed: bool) -> TaskRun:
    """Fold a recorded or live Task response into a TaskRun. Shared by the tool and the demo seed."""
    tr.output = payload.get("content") or {}
    tr.basis = _basis(payload.get("basis"))
    tr.warnings = _warnings(payload.get("warnings"))
    tr.provider_run_id = tr.provider_run_id or payload.get("run_id")
    tr.interaction_id = payload.get("interaction_id")
    tr.replayed = replayed
    tr.status = "REPLAY" if replayed else "OK"
    return tr


def _citations(raw: Any) -> list[BasisCitation]:
    out: list[BasisCitation] = []
    for c in raw or []:
        url = c.get("url") if isinstance(c, dict) else getattr(c, "url", None)
        if not url:
            continue
        title = c.get("title") if isinstance(c, dict) else getattr(c, "title", None)
        excerpts = (c.get("excerpts") if isinstance(c, dict) else getattr(c, "excerpts", None)) or []
        out.append(BasisCitation(url=url, title=title, excerpts=[e for e in excerpts if isinstance(e, str)]))
    return out


def _basis(raw: Any) -> list[FieldBasis]:
    out: list[FieldBasis] = []
    for b in raw or []:
        get = (lambda k: b.get(k)) if isinstance(b, dict) else (lambda k: getattr(b, k, None))
        field = get("field")
        if not field:
            continue
        confidence = get("confidence")
        out.append(
            FieldBasis(
                field=str(field),
                reasoning=get("reasoning") or "",
                confidence=str(confidence).lower() if confidence else None,
                citations=_citations(get("citations")),
            )
        )
    return out


def _warnings(raw: Any) -> list[ParallelWarning]:
    out: list[ParallelWarning] = []
    for w in raw or []:
        get = (lambda k: w.get(k)) if isinstance(w, dict) else (lambda k: getattr(w, k, None))
        out.append(ParallelWarning(type=str(get("type") or "warning"), message=str(get("message") or ""), detail=get("detail") if isinstance(get("detail"), dict) else None))
    return out


def response_payload(result: Any) -> dict[str, Any]:
    """Flatten an SDK TaskRunResult into the plain dict we persist and replay."""
    output = getattr(result, "output", None)
    run = getattr(result, "run", None)
    content = getattr(output, "content", None)
    if not isinstance(content, dict):  # a text output, or an unexpected shape
        content = {"text": content} if content is not None else {}
    return {
        "content": content,
        "basis": [b.model_dump(mode="json") if hasattr(b, "model_dump") else b for b in (getattr(output, "basis", None) or [])],
        "run_id": getattr(run, "run_id", None),
        "interaction_id": getattr(run, "interaction_id", None),
        "processor": getattr(run, "processor", None),
        "warnings": [w.model_dump(mode="json") if hasattr(w, "model_dump") else w for w in (getattr(run, "warnings", None) or [])],
    }


class ParallelTaskTool:
    """Runs location dossiers and shoot-day weather timelines.

    One instance per request; `max_runs` caps a single request's spend.
    """

    def __init__(
        self,
        project: Project,
        *,
        settings: Settings | None = None,
        recorder: Recorder | None = None,
        client: Any | None = None,
        on_event: Callable[[str, str, dict[str, Any] | None], None] | None = None,
        on_task_run: Callable[[TaskRun], None] | None = None,
        run_id: str | None = None,
        memory_scope_key: str | None = None,
    ):
        self.settings = settings or default_settings
        self.project = project
        self.recorder = recorder or Recorder(self.settings.recordings_dir, self.settings.mode, self.settings.record)
        self._client = client
        self._on_event = on_event
        self._on_task_run = on_task_run
        self.run_id = run_id
        self.memory_scope_key = memory_scope_key
        self.calls = 0

    @property
    def client(self):
        if self._client is None:
            from parallel import Parallel  # lazy: tests and keyless runs never import the SDK

            self._client = Parallel(api_key=self.settings.parallel_api_key, max_retries=2, timeout=float(self.settings.parallel_task_timeout_s))
        return self._client

    def _log(self, kind: str, message: str, meta: dict[str, Any] | None = None) -> None:
        if self._on_event:
            self._on_event(kind, message, meta or {})

    def _finish(self, tr: TaskRun) -> TaskRun:
        tr.finished_at = utcnow()
        if self._on_task_run:
            self._on_task_run(tr)
        return tr

    def dossier(self, resource: Resource, date: str | None = None, previous_interaction_id: str | None = None) -> TaskRun:
        """One structured research run about one location. Always returns a record; never raises.

        `previous_interaction_id` chains a re-research onto the earlier run so Parallel continues that
        investigation instead of starting cold. It is per-run state, so it never enters the record key.
        """
        processor = self.settings.parallel_task_processor
        input_text = build_task_input(self.project, resource, date)
        request = build_task_request(input_text, processor)
        key = recording_key(request)

        tr = TaskRun(
            run_id=self.run_id,
            project_id=self.project.id,
            resource_id=resource.id,
            processor=processor,
            input=input_text,
            output_schema=DOSSIER_SCHEMA,
            memory_scope_key=self.memory_scope_key,
        )

        if self.calls >= self.settings.parallel_task_max_runs:
            tr.status = "ERROR"
            tr.error = f"task budget exhausted ({self.settings.parallel_task_max_runs} runs per request)"
            self._log("warning", f"Refused a location dossier: {tr.error}", {"resource_id": resource.id})
            return self._finish(tr)

        if self.recorder.replay:
            recorded = self.recorder.lookup("parallel_task", key)
            if recorded is None:
                self._log("warning", f"No recording for a dossier of {resource.name}", {"resource_id": resource.id, "key": key})
                raise ReplayMiss(f"no recorded Parallel task for {resource.name} ({key})")
            self._apply(tr, recorded, replayed=True)
            self._log("parallel", f"Parallel Task (replayed): dossier for {resource.name}", {"task_run_id": tr.id, "resource_id": resource.id})
            return self._finish(tr)

        if not self.settings.parallel_configured:
            tr.status = "ERROR"
            tr.error = "PARALLEL_API_KEY is not configured"
            self._log("warning", f"Cannot research {resource.name}: {tr.error}", {"resource_id": resource.id})
            return self._finish(tr)

        self.calls += 1
        continuing = " (continuing the earlier interaction)" if previous_interaction_id else ""
        self._log("parallel", f"Parallel Task ({processor}): researching {resource.name} — permits, curfew, drone and pyro rules{continuing}", {"task_run_id": tr.id, "resource_id": resource.id, "processor": processor, "previous_interaction_id": previous_interaction_id})
        try:
            created = self.client.task_run.create(
                input=input_text,
                processor=processor,
                task_spec={"output_schema": {"type": "json", "json_schema": DOSSIER_SCHEMA}},
                metadata={"project_id": self.project.id, "resource_id": resource.id, "kind": "location_dossier"},
                betas=[FIELD_BASIS_BETA],
                **({"memory_scope_key": self.memory_scope_key} if self.memory_scope_key else {}),
                **({"previous_interaction_id": previous_interaction_id} if previous_interaction_id else {}),
            )
            tr.provider_run_id = getattr(created, "run_id", None)
            result = self.client.task_run.result(created.run_id, api_timeout=self.settings.parallel_task_timeout_s)
            payload = response_payload(result)
            self.recorder.save("parallel_task", key, payload, request)
            self._apply(tr, payload, replayed=False)
            self._log(
                "parallel",
                f"Parallel Task complete: {len(tr.basis)} field{'' if len(tr.basis) == 1 else 's'} of the {resource.name} dossier came back with citations",
                {"task_run_id": tr.id, "resource_id": resource.id, "provider_run_id": tr.provider_run_id},
            )
        except Exception as exc:  # noqa: BLE001 — an errored run is a UI state, not a 500
            fallback = self.recorder.lookup("parallel_task", key) if self.settings.fallback_to_recording else None
            if fallback is not None:
                self._apply(tr, fallback, replayed=True)
                self._log("warning", f"Live Parallel Task failed ({exc}); served the recorded dossier for {resource.name}, labelled replayed", {"task_run_id": tr.id})
            else:
                tr.status = "ERROR"
                tr.error = f"{type(exc).__name__}: {exc}"
                log.warning("Parallel task failed: %s", tr.error)
                self._log("warning", f"Parallel Task failed for {resource.name}: {tr.error}", {"task_run_id": tr.id})
        return self._finish(tr)

    def weather_timeline(self, day: ShootDay) -> TaskRun:
        """One structured hourly-precipitation run for one shoot day. Always returns a record.

        Deliberately not chained with `previous_interaction_id`: a forecast asked again is a new
        question about a moved world, not a continuation of the earlier investigation.
        """
        processor = self.settings.parallel_task_processor
        input_text = build_weather_input(self.project, day)
        request = build_task_request(input_text, processor, WEATHER_SCHEMA)
        key = recording_key(request)
        label = f"Day {day.day_number}"

        tr = TaskRun(
            run_id=self.run_id,
            project_id=self.project.id,
            shoot_day_id=day.id,
            purpose="weather_timeline",
            processor=processor,
            input=input_text,
            output_schema=WEATHER_SCHEMA,
            memory_scope_key=self.memory_scope_key,
        )

        if self.calls >= self.settings.parallel_task_max_runs:
            tr.status = "ERROR"
            tr.error = f"task budget exhausted ({self.settings.parallel_task_max_runs} runs per request)"
            self._log("warning", f"Refused a weather timeline: {tr.error}", {"shoot_day_id": day.id})
            return self._finish(tr)

        if self.recorder.replay:
            recorded = self.recorder.lookup("parallel_task", key)
            if recorded is None:
                self._log("warning", f"No recording for a weather timeline of {label}", {"shoot_day_id": day.id, "key": key})
                raise ReplayMiss(f"no recorded Parallel task for the {label} weather timeline ({key})")
            self._apply(tr, recorded, replayed=True)
            self._log("parallel", f"Parallel Task (replayed): hourly weather for {label}", {"task_run_id": tr.id, "shoot_day_id": day.id})
            return self._finish(tr)

        if not self.settings.parallel_configured:
            tr.status = "ERROR"
            tr.error = "PARALLEL_API_KEY is not configured"
            self._log("warning", f"Cannot research the {label} weather: {tr.error}", {"shoot_day_id": day.id})
            return self._finish(tr)

        self.calls += 1
        self._log(
            "parallel",
            f"Parallel Task ({processor}): hour-by-hour precipitation for {label}, {day.date} — every hour cited separately",
            {"task_run_id": tr.id, "shoot_day_id": day.id, "processor": processor},
        )
        try:
            created = self.client.task_run.create(
                input=input_text,
                processor=processor,
                task_spec={"output_schema": {"type": "json", "json_schema": WEATHER_SCHEMA}},
                metadata={"project_id": self.project.id, "shoot_day_id": day.id, "kind": "weather_timeline"},
                betas=[FIELD_BASIS_BETA],
                **({"memory_scope_key": self.memory_scope_key} if self.memory_scope_key else {}),
            )
            tr.provider_run_id = getattr(created, "run_id", None)
            result = self.client.task_run.result(created.run_id, api_timeout=self.settings.parallel_task_timeout_s)
            payload = response_payload(result)
            self.recorder.save("parallel_task", key, payload, request)
            self._apply(tr, payload, replayed=False)
            self._log(
                "parallel",
                f"Parallel Task complete: {len(tr.basis)} field{'' if len(tr.basis) == 1 else 's'} of the {label} weather timeline came back with citations",
                {"task_run_id": tr.id, "shoot_day_id": day.id, "provider_run_id": tr.provider_run_id},
            )
        except Exception as exc:  # noqa: BLE001 — an errored run is a UI state, not a 500
            fallback = self.recorder.lookup("parallel_task", key) if self.settings.fallback_to_recording else None
            if fallback is not None:
                self._apply(tr, fallback, replayed=True)
                self._log("warning", f"Live Parallel Task failed ({exc}); served the recorded {label} weather timeline, labelled replayed", {"task_run_id": tr.id})
            else:
                tr.status = "ERROR"
                tr.error = f"{type(exc).__name__}: {exc}"
                log.warning("Parallel weather task failed: %s", tr.error)
                self._log("warning", f"Parallel Task failed for the {label} weather timeline: {tr.error}", {"task_run_id": tr.id})
        return self._finish(tr)

    def _apply(self, tr: TaskRun, payload: dict[str, Any], *, replayed: bool) -> None:
        apply_payload(tr, payload, replayed=replayed)
