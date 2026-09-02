"""Shared orchestration context: persistence, activity log, tool/runtime wiring."""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings, settings as default_settings
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
        self.recorder = Recorder(self.settings.recordings_dir, self.settings.mode, self.settings.record)
        # One Parallel session per task (shared by Search and Extract), tagged with the consuming model.
        self.parallel_session = ParallelSession(self.settings, self.recorder, session_id=new_session_id(run.kind.value.lower(), run.id), client_model=self.settings.gemini_model)
        self.parallel = ParallelSearchTool(run_id=run.id, project_id=project.id, session=self.parallel_session, on_search_run=self._on_search_run, on_event=self.log, recorder=self.recorder, settings=self.settings)
        self.extract = ParallelExtractTool(run_id=run.id, project_id=project.id, session=self.parallel_session, on_extract_run=self._on_extract_run, on_event=self.log, recorder=self.recorder, settings=self.settings)
        self.gemini = GeminiRuntime(on_event=self.log, recorder=self.recorder, settings=self.settings)

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
