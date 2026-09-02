"""Parallel FindAll — the rescue stops reporting and starts fixing.

When a crane vendor cancels, reshuffling the day is not a recovery; finding another crane is. FindAll
turns a plain-language need ("equipment rental houses in Mumbai that stock a 30-ft telescopic camera
crane") into a citation-backed list of real companies, which become `VendorCandidate`s a producer can
select — and a selected vendor writes an equipment call and a contact onto the regenerated call sheet.

Two paths, deliberately:
  * **entity_search** (default) — synchronous, ~$5/1k, returns ranked entities in one call. This is
    what the demo clicks, so the video never stalls on an async job.
  * **findall** — `ingest` → `create` → poll/webhook → `result` (+ optional `enrich`). Deeper and
    citation-rich, but minutes long. Polling is bounded and works without a public webhook URL,
    because `PUBLIC_BASE_URL` is unset in local development.

Both are feature-gated and cost money, so neither ever fires implicitly.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from ..config import Settings, settings as default_settings
from ..domain.enums import ResourceType
from ..domain.models import BasisCitation, FindAllRun, ParallelWarning, Project, Resource, VendorCandidate, utcnow
from .normalize import normalize
from .recorder import Recorder, ReplayMiss

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 5
TERMINAL = {"completed", "failed", "cancelled"}
# Verified live 2026-08-29: FindAll rejects a match_limit outside this range with a 422.
# Entity Search has no such floor, so the clamp applies to the deep path only.
FINDALL_MIN_MATCHES, FINDALL_MAX_MATCHES = 5, 1000

# What we ask for, per resource type. Kept in code (not model-written) so the spend is predictable.
NEED_BY_TYPE = {
    ResourceType.EQUIPMENT: "equipment rental houses",
    ResourceType.LOCATION: "filming locations or sound stages available at short notice",
    ResourceType.VEHICLE: "production vehicle and transport suppliers",
    ResourceType.CREW: "crew agencies and freelancers",
}

ENRICH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "phone": {"type": "string", "description": "Public contact phone number, empty if not published."},
        "address": {"type": "string", "description": "Street address."},
        "distance_km": {"type": "string", "description": "Approximate distance in km from the city centre."},
        "day_rate_band": {"type": "string", "description": "Typical day-rate band in INR, empty if not published."},
    },
}


# Entity Search wants a short noun phrase naming the *kind of company*, not a request sentence.
# Verified live on 2026-08-29: "grip and camera crane rental vendors serving film productions in
# Mumbai" returns 8 companies, while "equipment rental houses in and around Mumbai, IN, that can
# supply a replacement for '30 ft telescopic crane' at short notice for a film shoot" returns zero.
# So the two paths get different phrasing: entity_search gets the category, FindAll gets the full
# sentence plus match_conditions that carry the specificity.
ENTITY_CATEGORIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("crane", "jib", "grip", "dolly"), "grip and camera crane rental vendors"),
    (("drone", "aerial", "uav"), "aerial cinematography and drone service providers"),
    (("firework", "pyro", "explosive", "sfx"), "pyrotechnics and special effects vendors"),
    (("light", "electric", "genset", "generator"), "film lighting and power equipment rental companies"),
    (("camera", "lens", "rig"), "camera equipment rental companies"),
    (("motorcycle", "car", "vehicle", "van", "truck"), "picture vehicle and transport suppliers for film shoots"),
)
CATEGORY_BY_TYPE = {
    ResourceType.LOCATION: "film shooting locations and sound stages",
    ResourceType.VEHICLE: "production vehicle and transport companies",
    ResourceType.CREW: "film crew staffing agencies",
    ResourceType.EQUIPMENT: "film equipment rental companies",
}


def clamp_match_limit(value: int) -> int:
    """FindAll 422s outside 5..1000, so a misconfigured env var must not reach the API."""
    return max(FINDALL_MIN_MATCHES, min(FINDALL_MAX_MATCHES, int(value)))


def entity_category(resource: Resource) -> str:
    """The kind of company that could replace this resource, in Entity Search's preferred shape."""
    low = resource.name.lower()
    for words, category in ENTITY_CATEGORIES:
        if any(w in low for w in words):
            return category
    return CATEGORY_BY_TYPE.get(resource.type, "film equipment rental companies")


def build_entity_objective(project: Project, resource: Resource) -> str:
    """Short and categorical — what Entity Search actually responds to."""
    return f"{entity_category(resource)} serving film productions in {project.base_city}"


def build_objective(project: Project, resource: Resource, note: str | None = None) -> str:
    """The fuller FindAll objective; its match_conditions carry the specificity."""
    need = NEED_BY_TYPE.get(resource.type, "suppliers")
    parts = [
        f"{need} in and around {project.base_city}, {project.country_code},",
        f"that can supply a replacement for '{resource.name}' at short notice for a film shoot.",
    ]
    if note:
        parts.append(note)
    return " ".join(parts)


def build_match_conditions(project: Project, resource: Resource) -> list[dict[str, str]]:
    return [
        {"name": "location", "description": f"The company operates in or delivers to {project.base_city}, {project.country_code}."},
        {"name": "capability", "description": f"The company supplies or rents equipment equivalent to '{resource.name}' for film or television production."},
        {"name": "contactable", "description": "The company publishes a phone number or enquiry contact on its website."},
    ]


def _field(output: dict, key: str) -> str | None:
    """Enrichment fields come back as strings and may be empty or a non-answer."""
    value = str(output.get(key) or "").strip()
    return value or None


def _number(output: dict, key: str) -> float | None:
    raw = _field(output, key)
    if raw is None:
        return None
    import re as _re

    m = _re.search(r"\d+(?:\.\d+)?", raw)
    return float(m.group(0)) if m else None


def _citations(basis: Any) -> tuple[list[BasisCitation], list[str]]:
    """Flatten a candidate's per-condition basis into citations plus the reasons it matched."""
    citations: list[BasisCitation] = []
    reasons: list[str] = []
    seen: set[str] = set()
    for b in basis or []:
        get = (lambda k: b.get(k)) if isinstance(b, dict) else (lambda k: getattr(b, k, None))
        if get("reasoning"):
            reasons.append(str(get("reasoning")))
        for c in get("citations") or []:
            cg = (lambda k: c.get(k)) if isinstance(c, dict) else (lambda k: getattr(c, k, None))
            url = cg("url")
            if url and url not in seen:
                seen.add(url)
                citations.append(BasisCitation(url=url, title=cg("title"), excerpts=[e for e in (cg("excerpts") or []) if isinstance(e, str)]))
    return citations, reasons


class ParallelFindAllTool:
    def __init__(
        self,
        project: Project,
        *,
        settings: Settings | None = None,
        client: Any | None = None,
        on_event: Callable[[str, str, dict[str, Any] | None], None] | None = None,
        run_id: str | None = None,
        memory_scope_key: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        recorder: Recorder | None = None,
    ):
        self.settings = settings or default_settings
        self.project = project
        self.recorder = recorder or Recorder(self.settings.recordings_dir, self.settings.mode, self.settings.record)
        self._client = client
        self._on_event = on_event
        self.run_id = run_id
        self.memory_scope_key = memory_scope_key
        self._sleep = sleep

    @property
    def client(self):
        if self._client is None:
            from parallel import Parallel  # lazy: tests and keyless runs never import the SDK

            self._client = Parallel(api_key=self.settings.parallel_api_key, max_retries=2, timeout=120.0)
        return self._client

    def _log(self, kind: str, message: str, meta: dict[str, Any] | None = None) -> None:
        if self._on_event:
            self._on_event(kind, message, meta or {})

    def find_substitutes(self, resource: Resource, *, shoot_day_id: str | None = None, note: str | None = None, mode: str | None = None) -> FindAllRun:
        """Find real replacements for a resource. Always returns a record; never raises."""
        mode = (mode or self.settings.parallel_findall_mode).lower()
        objective = build_entity_objective(self.project, resource) if mode == "entity_search" else build_objective(self.project, resource, note)
        run = FindAllRun(
            run_id=self.run_id,
            project_id=self.project.id,
            resource_id=resource.id,
            shoot_day_id=shoot_day_id,
            mode=mode,
            objective=objective,
            match_limit=self.settings.parallel_findall_match_limit,
            memory_scope_key=self.memory_scope_key,
            generator=self.settings.parallel_findall_generator if mode == "findall" else None,
            match_conditions=build_match_conditions(self.project, resource) if mode == "findall" else [],
        )
        request = {
            "mode": mode,
            "objective": objective,
            "entity_type": run.entity_type,
            "match_limit": clamp_match_limit(run.match_limit) if mode == "findall" else run.match_limit,
            "generator": run.generator,
            "match_conditions": run.match_conditions,
            "enrich": bool(self.settings.parallel_findall_enrich) if mode == "findall" else False,
        }
        key = Recorder.key("parallel_findall", json.loads(normalize(json.dumps(request, ensure_ascii=False))))

        if self.recorder.replay:
            recorded = self.recorder.lookup("parallel_findall", key)
            if recorded is None:
                self._log("warning", f"No recording for a substitute search for {resource.name}", {"resource_id": resource.id, "key": key})
                raise ReplayMiss(f"no recorded Parallel findall for {resource.name} ({key})")
            self._apply(run, recorded, replayed=True)
            self._log("parallel", f"Parallel {'Entity Search' if mode == 'entity_search' else 'FindAll'} (replayed): {len(run.candidates)} replacement(s) for {resource.name}", {"findall_run_id": run.id, "resource_id": resource.id})
            run.finished_at = utcnow()
            return run

        if not self.settings.parallel_configured:
            run.status, run.error = "ERROR", "PARALLEL_API_KEY is not configured"
            run.finished_at = utcnow()
            self._log("warning", f"Cannot look for a replacement for {resource.name}: {run.error}", {"resource_id": resource.id})
            return run

        self._log("parallel", f"Parallel {'Entity Search' if mode == 'entity_search' else 'FindAll'}: looking for a replacement for {resource.name}", {"findall_run_id": run.id, "resource_id": resource.id, "mode": mode})
        try:
            payload = self._entity_search(run) if mode == "entity_search" else self._findall(run)
            self.recorder.save("parallel_findall", key, payload, request)
            self._apply(run, payload, replayed=False)
            self._log(
                "parallel",
                f"Parallel found {len(run.candidates)} possible replacement{'' if len(run.candidates) == 1 else 's'} for {resource.name}",
                {"findall_run_id": run.id, "resource_id": resource.id, "count": len(run.candidates)},
            )
        except Exception as exc:  # noqa: BLE001 — an errored run is a UI state, not a 500
            fallback = self.recorder.lookup("parallel_findall", key) if self.settings.fallback_to_recording else None
            if fallback is not None:
                self._apply(run, fallback, replayed=True)
                self._log("warning", f"Live Parallel findall failed ({exc}); served the recorded search for {resource.name}, labelled replayed", {"findall_run_id": run.id})
            else:
                run.status, run.error = "ERROR", f"{type(exc).__name__}: {exc}"
                log.warning("Parallel findall failed: %s", run.error)
                self._log("warning", f"Parallel could not look for replacements for {resource.name}: {run.error}", {"findall_run_id": run.id})
        run.finished_at = utcnow()
        return run

    def _apply(self, run: FindAllRun, payload: dict[str, Any], *, replayed: bool) -> None:
        run.candidates = [VendorCandidate(findall_run_id=run.id, **c) for c in payload.get("candidates") or []]
        run.provider_findall_id = payload.get("provider_findall_id")
        run.termination_reason = payload.get("termination_reason")
        run.warnings = [ParallelWarning(**w) if isinstance(w, dict) else w for w in payload.get("warnings") or []]
        run.enriched = bool(payload.get("enriched"))
        run.replayed = replayed
        run.status = "REPLAY" if replayed else "OK"

    # ----- the two paths, each returning a recordable payload -----
    def _entity_search(self, run: FindAllRun) -> dict[str, Any]:
        """Synchronous. No per-entity citations — the entity URL is the source, and we say so."""
        response = self.client.beta.findall.entity_search(entity_type=run.entity_type, objective=run.objective, match_limit=run.match_limit)
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for e in getattr(response, "entities", None) or []:
            # The same company can come back under several profile URLs (in./bo. LinkedIn); one row each.
            name = (getattr(e, "name", "") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            if len(candidates) >= run.match_limit:
                break
            url = getattr(e, "url", "") or ""
            candidates.append({
                "name": name,
                "url": url,
                "description": getattr(e, "description", "") or "",
                "citations": [{"url": url, "title": None, "excerpts": []}] if url else [],
            })
        return {"candidates": candidates, "provider_findall_id": getattr(response, "entity_set_id", None), "warnings": [], "enriched": False}

    def _poll(self, findall_id: str, deadline: float) -> tuple[Any, bool]:
        """Poll a FindAll run to a terminal state. Returns (result, timed_out)."""
        while True:
            result = self.client.beta.findall.result(findall_id)
            status = getattr(getattr(result, "run", None), "status", None)
            if getattr(status, "status", None) in TERMINAL or not getattr(status, "is_active", False):
                return result, False
            if time.monotonic() >= deadline:
                return result, True
            self._sleep(POLL_INTERVAL_S)

    def _findall(self, run: FindAllRun) -> dict[str, Any]:
        """Asynchronous and deeper. Polls with a bound, so it works without a public webhook URL."""
        created = self.client.beta.findall.create(
            objective=run.objective,
            entity_type=run.entity_type,
            match_conditions=run.match_conditions,
            match_limit=clamp_match_limit(run.match_limit),
            generator=run.generator or "base",
            **({"memory_scope_key": self.memory_scope_key} if self.memory_scope_key else {}),
        )
        findall_id = getattr(created, "findall_id", None)
        deadline = time.monotonic() + self.settings.parallel_findall_timeout_s
        warnings: list[dict[str, Any]] = []

        result, timed_out = self._poll(findall_id, deadline)
        if timed_out:
            warnings.append({"type": "timeout", "message": f"FindAll still running after {self.settings.parallel_findall_timeout_s}s; showing candidates found so far", "detail": None})

        # Enrichment runs only on candidates that already matched, so it is the cheap half of the
        # deep path — it is what turns a company name into a phone number a 1st AD can actually ring.
        enriched = False
        if self.settings.parallel_findall_enrich and not timed_out and any(getattr(c, "match_status", None) == "matched" for c in getattr(result, "candidates", None) or []):
            try:
                self.client.beta.findall.enrich(findall_id, output_schema={"type": "json", "json_schema": ENRICH_SCHEMA})
                result, enrich_timed_out = self._poll(findall_id, time.monotonic() + self.settings.parallel_findall_timeout_s)
                enriched = not enrich_timed_out
                if enrich_timed_out:
                    warnings.append({"type": "timeout", "message": "enrichment did not finish in time; contact details may be missing", "detail": None})
            except Exception as exc:  # noqa: BLE001 — enrichment is a bonus, never the reason a search fails
                log.warning("FindAll enrich failed: %s", exc)
                warnings.append({"type": "enrich_failed", "message": f"contact enrichment failed: {exc}", "detail": None})

        status = getattr(getattr(result, "run", None), "status", None)
        candidates: list[dict[str, Any]] = []
        for c in getattr(result, "candidates", None) or []:
            if getattr(c, "match_status", None) != "matched":
                continue
            citations, reasons = _citations(getattr(c, "basis", None))
            out = getattr(c, "output", None) or {}
            candidates.append({
                "name": getattr(c, "name", "") or "",
                "url": getattr(c, "url", "") or "",
                "description": getattr(c, "description", "") or "",
                "match_status": "matched",
                "match_reasons": reasons,
                "citations": [x.model_dump(mode="json") for x in citations],
                "phone": _field(out, "phone"),
                "address": _field(out, "address"),
                "distance_km": _number(out, "distance_km"),
                "day_rate_band": _field(out, "day_rate_band"),
            })
        return {
            "candidates": candidates,
            "provider_findall_id": findall_id,
            "termination_reason": getattr(status, "termination_reason", None),
            "warnings": warnings,
            "enriched": enriched,
        }
