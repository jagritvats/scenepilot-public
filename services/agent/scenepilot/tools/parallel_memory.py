"""Parallel Memory (beta) — what the web has already taught this production.

Task, Monitor and FindAll runs created with the same `memory_scope_key` accumulate into one scope;
`client.beta.memory.retrieve` reads back across all three. ScenePilot gives every project a scope
(`scenepilot_<project_id>`) so a shoot's research is one body of knowledge rather than a pile of
one-shot runs — and gives the producer an `evict` button, because a permit rule that changed should
be forgettable. Human curation of machine memory: the same approval philosophy the ChangeSet uses,
applied to knowledge.

Deliberate non-goals:
  * **Never fired implicitly.** Every read comes from an explicit UI action (see `api/deps.py`).
  * **Not record/replayed.** Memory is server-side state, like Monitors. In replay mode a read
    returns UNAVAILABLE instead of inventing entries.
  * **Never raises.** A beta API whose shape drifts must surface as an errored `MemoryRead` in the
    UI, exactly like an errored SearchRun — not as a 500 in the middle of a demo.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from ..config import Settings, settings as default_settings
from ..domain.models import MemoryEntry, MemoryRead, Project, utcnow

log = logging.getLogger(__name__)

# Parallel's rule for a scope key: letters, digits, underscores or hyphens.
SCOPE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
KINDS = {"task", "monitor", "findall"}


def scope_key(project: Project, settings: Settings | None = None) -> str:
    """`<prefix>_<project_id>`, sanitised to Parallel's allowed character set."""
    s = settings or default_settings
    raw = f"{s.parallel_memory_scope_prefix}_{project.id}"
    key = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
    if not SCOPE_KEY_RE.match(key):  # pragma: no cover — the sub above guarantees it
        raise ValueError(f"cannot build a valid Parallel memory scope key from {raw!r}")
    return key


def _entry(result: Any) -> MemoryEntry:
    """Flatten the Task/Monitor/FindAll memory union into one row type."""
    kind = getattr(result, "kind", None) or "task"
    events = list(getattr(result, "matched_events", None) or [])
    output = getattr(result, "output_excerpt", None)
    if output is None and events:
        output = "\n".join(e.excerpt for e in events if getattr(e, "excerpt", None))
    return MemoryEntry(
        kind=str(kind),
        ref_id=getattr(result, "id", ""),
        input_excerpt=getattr(result, "input_excerpt", "") or "",
        output_excerpt=output or "",
        updated_at=getattr(result, "updated_at", None),
        status=getattr(result, "status", None),
        matched_count=getattr(result, "matched_count", None),
        event_ids=[e.event_id for e in events if getattr(e, "event_id", None)],
    )


class ParallelMemoryTool:
    def __init__(
        self,
        project: Project,
        *,
        settings: Settings | None = None,
        client: Any | None = None,
        on_event: Callable[[str, str, dict[str, Any] | None], None] | None = None,
        run_id: str | None = None,
    ):
        self.settings = settings or default_settings
        self.project = project
        self.run_id = run_id
        self.scope = scope_key(project, self.settings)
        self._client = client
        self._on_event = on_event

    # ----- plumbing -----
    @property
    def client(self):
        if self._client is None:
            from parallel import Parallel  # lazy: tests and keyless runs never import the SDK

            self._client = Parallel(api_key=self.settings.parallel_api_key, max_retries=2, timeout=60.0)
        return self._client

    def _log(self, kind: str, message: str, meta: dict[str, Any] | None = None) -> None:
        if self._on_event:
            self._on_event(kind, message, meta or {})

    @property
    def available(self) -> bool:
        """Live mode + a key. Replay has no server-side memory to read."""
        return self.settings.live and self.settings.parallel_configured

    # ----- operations -----
    def retrieve(self, query: str = "", limit: int = 10, kind: str | None = None) -> MemoryRead:
        """Read this production's Parallel memory. Always returns a record — never raises."""
        if kind is not None and kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        read = MemoryRead(project_id=self.project.id, run_id=self.run_id, scope_key=self.scope, query=query or "", kind=kind, limit=limit)
        if not self.available:
            read.status = "UNAVAILABLE"
            read.error = "replay mode has no server-side Parallel memory" if not self.settings.live else "PARALLEL_API_KEY is not configured"
            read.finished_at = utcnow()
            self._log("parallel", f"Parallel memory unavailable ({read.error})", {"scope_key": self.scope})
            return read
        try:
            response = self.client.beta.memory.retrieve(
                query=query or None,
                limit=limit,
                kind=kind,
                memory_scope_key=self.scope,
            )
            read.entries = [_entry(r) for r in (getattr(response, "results", None) or [])]
            read.status = "OK"
            self._log(
                "parallel",
                f"Parallel memory: {len(read.entries)} entr{'y' if len(read.entries) == 1 else 'ies'} in scope {self.scope}" + (f" for “{query}”" if query else " (most recent)"),
                {"scope_key": self.scope, "query": query, "count": len(read.entries), "memory_read_id": read.id},
            )
        except Exception as exc:  # noqa: BLE001 — a beta API must not break the page
            read.status = "ERROR"
            read.error = f"{type(exc).__name__}: {exc}"
            log.warning("Parallel memory retrieve failed: %s", read.error)
            self._log("warning", f"Parallel memory read failed: {read.error}", {"scope_key": self.scope})
        read.finished_at = utcnow()
        return read

    def evict(self, kind: str, ref_id: str) -> None:
        """Forget one run without deleting the underlying Task/Monitor/FindAll resource."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        self.client.beta.memory.evict(id=ref_id, kind=kind, memory_scope_key=self.scope)
        self._log("parallel", f"Producer marked a memory stale — evicted {kind} {ref_id} from scope {self.scope}", {"scope_key": self.scope, "kind": kind, "ref_id": ref_id})

    def clear(self) -> None:
        """Forget everything in this project's scope. Underlying runs are untouched."""
        self.client.beta.memory.clear(memory_scope_key=self.scope)
        self._log("parallel", f"Cleared the Parallel memory scope {self.scope}", {"scope_key": self.scope})
