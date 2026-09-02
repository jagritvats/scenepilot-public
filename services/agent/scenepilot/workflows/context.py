"""Shared orchestration context: persistence, activity log, tool/runtime wiring."""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings, degraded_reason, settings as default_settings
from ..domain.models import ActivityEvent, ExtractRun, Project, SearchRun, WorkflowRun
from ..store.repo import Repo
from ..tools.parallel_extract import ParallelExtractTool
from ..tools.parallel_search import ParallelSearchTool
from ..tools.parallel_session import ParallelSession, new_session_id
from ..tools.recorder import Recorder
from ..agents.runtime import GeminiRuntime

log = logging.getLogger(__name__)


class RunContext:
    def __init__(self, repo: Repo, run: WorkflowRun, project: Project, settings: Settings | None = None):
        self.repo = repo
        self.run = run
        self.project = project
        self.settings = settings or default_settings
        self.recorder = Recorder(self.settings.recordings_dir, self.settings.active_mode, self.settings.record)
        # One Parallel session per task (shared by Search and Extract), tagged with the consuming model.
        self.parallel_session = ParallelSession(self.settings, self.recorder, session_id=new_session_id(run.kind.value.lower(), run.id), client_model=self.settings.gemini_model)
        self.parallel = ParallelSearchTool(run_id=run.id, project_id=project.id, session=self.parallel_session, on_search_run=self._on_search_run, on_event=self.log, recorder=self.recorder, settings=self.settings)
        self.extract = ParallelExtractTool(run_id=run.id, project_id=project.id, session=self.parallel_session, on_extract_run=self._on_extract_run, on_event=self.log, recorder=self.recorder, settings=self.settings)
        self.gemini = GeminiRuntime(on_event=self.log, recorder=self.recorder, settings=self.settings)
        self._log_degradation()

    def _log_degradation(self) -> None:
        """Say, in the feed the producer is watching, why this run is not spending.

        The tools already stamp every row they write as `REPLAY` and append "(replayed)" to what
        they log, so a degraded run is never disguised. What they cannot say is *why* — a reader
        seeing only the labels would reasonably conclude the deployment was a recording all along.
        This is the one line that distinguishes "this deployment does not make live calls" from
        "this deployment does, and declined to spend on this particular click".
        """
        reason = degraded_reason()
        if reason is None:
            return
        self.log(
            "warning",
            f"Served from recordings, not live: {reason.get('message', 'a priced call was not made')}",
            {"degraded": True, "reason": reason.get("reason"), "feature": reason.get("feature"), "cost": reason.get("cost")},
        )

    # ----- observability -----
    def log(self, kind: str, message: str, meta: dict[str, Any] | None = None) -> None:
        evt = ActivityEvent(run_id=self.run.id, project_id=self.project.id, kind=kind, message=message, meta=meta or {})
        try:
            self.repo.log(evt)
        except Exception:  # noqa: BLE001
            log.exception("failed to persist activity event")
        log.info("[%s] %s", kind, message)

    def _on_search_run(self, sr: SearchRun) -> None:
        self.repo.save_search_run(sr)
        if self.run.planning is not None and sr.id not in self.run.planning.search_run_ids:
            self.run.planning.search_run_ids.append(sr.id)
        if self.run.rescue is not None and sr.id not in self.run.rescue.search_run_ids:
            self.run.rescue.search_run_ids.append(sr.id)

    def _on_extract_run(self, xr: ExtractRun) -> None:
        self.repo.save_extract_run(xr)
        if self.run.planning is not None and xr.id not in self.run.planning.extract_run_ids:
            self.run.planning.extract_run_ids.append(xr.id)
        if self.run.rescue is not None and xr.id not in self.run.rescue.extract_run_ids:
            self.run.rescue.extract_run_ids.append(xr.id)

    # ----- state -----
    def stage(self, name: str, message: str | None = None) -> None:
        self.run.stage = name
        if self.run.planning is not None:
            self.run.planning.stage = name
        if self.run.rescue is not None:
            self.run.rescue.stage = name
        self.repo.save_run(self.run)
        if message:
            self.log("info", message, {"stage": name})

    def save(self) -> None:
        self.repo.save_run(self.run)

    def save_project(self) -> None:
        self.repo.save_project(self.project)
