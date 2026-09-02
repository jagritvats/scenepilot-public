"""Repository: typed access to persisted production state, runs, search runs, activity."""

from __future__ import annotations

import threading
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine

from ..domain.models import ActivityEvent, ChangeSet, ExtractRun, FindAllRun, MemoryRead, Project, SearchRun, TaskRun, WorkflowRun, utcnow
from . import db


class Repo:
    """Every read and every write goes through one lock, because one of them shares one connection.

    A `:memory:` engine is built on `StaticPool` with `check_same_thread=False` (see `db.make_engine`)
    — that is a *single* sqlite3 connection handed to every thread that asks. `check_same_thread`
    only removes Python's guard; it does not make concurrent use of one connection safe. A rescue run
    is an asyncio task that writes from `asyncio.to_thread` workers while the request thread polls
    the same run, and the collision surfaced as an intermittent
    `sqlite3.OperationalError: not an error` that failed roughly one rescue in twenty-four.

    The lock used to cover the nine writers and none of the eighteen readers, which is exactly the
    race: a read on the request thread against a write on the worker. SQLite serialises writes
    anyway, so making reads wait costs this application nothing measurable, and an `RLock` keeps the
    handful of methods that call each other re-entrant.
    """

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or db.make_engine()
        self._lock = threading.RLock()

    # ----- projects -----
    def list_projects(self) -> list[Project]:
        with self._lock, self.engine.begin() as conn:
            rows = conn.execute(select(db.projects.c.doc)).all()
        return [Project.model_validate(r[0]) for r in rows]

    def get_project(self, project_id: str) -> Project | None:
        with self._lock, self.engine.begin() as conn:
            row = conn.execute(select(db.projects.c.doc).where(db.projects.c.id == project_id)).first()
        return Project.model_validate(row[0]) if row else None

    def save_project(self, project: Project) -> None:
        project.updated_at = utcnow()
        doc = project.model_dump(mode="json")
        with self._lock, self.engine.begin() as conn:
            existing = conn.execute(select(db.projects.c.id).where(db.projects.c.id == project.id)).first()
            if existing:
                conn.execute(db.projects.update().where(db.projects.c.id == project.id).values(title=project.title, doc=doc, updated_at=project.updated_at))
            else:
                conn.execute(db.projects.insert().values(id=project.id, title=project.title, doc=doc, updated_at=project.updated_at))

    def delete_project_data(self, project_id: str) -> None:
        with self._lock, self.engine.begin() as conn:
            conn.execute(db.activity.delete().where(db.activity.c.project_id == project_id))
            conn.execute(db.search_runs.delete().where(db.search_runs.c.project_id == project_id))
            conn.execute(db.extract_runs.delete().where(db.extract_runs.c.project_id == project_id))
            conn.execute(db.memory_reads.delete().where(db.memory_reads.c.project_id == project_id))
            conn.execute(db.task_runs.delete().where(db.task_runs.c.project_id == project_id))
            conn.execute(db.findall_runs.delete().where(db.findall_runs.c.project_id == project_id))
            conn.execute(db.changesets.delete().where(db.changesets.c.project_id == project_id))
            conn.execute(db.runs.delete().where(db.runs.c.project_id == project_id))
            conn.execute(db.projects.delete().where(db.projects.c.id == project_id))

    # ----- runs -----
    def save_run(self, run: WorkflowRun) -> None:
        run.updated_at = utcnow()
        doc = run.model_dump(mode="json")
        with self._lock, self.engine.begin() as conn:
            existing = conn.execute(select(db.runs.c.id).where(db.runs.c.id == run.id)).first()
            if existing:
                conn.execute(db.runs.update().where(db.runs.c.id == run.id).values(status=run.status.value, doc=doc, updated_at=run.updated_at))
            else:
                conn.execute(db.runs.insert().values(id=run.id, project_id=run.project_id, kind=run.kind.value, status=run.status.value, doc=doc, created_at=run.created_at, updated_at=run.updated_at))

    def get_run(self, run_id: str) -> WorkflowRun | None:
        with self._lock, self.engine.begin() as conn:
            row = conn.execute(select(db.runs.c.doc).where(db.runs.c.id == run_id)).first()
        return WorkflowRun.model_validate(row[0]) if row else None

    def list_runs(self, project_id: str, kind: str | None = None) -> list[WorkflowRun]:
        stmt = select(db.runs.c.doc).where(db.runs.c.project_id == project_id).order_by(db.runs.c.created_at.desc())
        if kind:
            stmt = stmt.where(db.runs.c.kind == kind)
        with self._lock, self.engine.begin() as conn:
            rows = conn.execute(stmt).all()
        return [WorkflowRun.model_validate(r[0]) for r in rows]

    # ----- search runs -----
    def save_search_run(self, sr: SearchRun) -> None:
        doc = sr.model_dump(mode="json")
        with self._lock, self.engine.begin() as conn:
            existing = conn.execute(select(db.search_runs.c.id).where(db.search_runs.c.id == sr.id)).first()
            if existing:
                conn.execute(db.search_runs.update().where(db.search_runs.c.id == sr.id).values(doc=doc, question_id=sr.question_id))
            else:
                conn.execute(db.search_runs.insert().values(id=sr.id, run_id=sr.run_id, project_id=sr.project_id, question_id=sr.question_id, doc=doc, started_at=sr.started_at))

    def get_search_run(self, sr_id: str) -> SearchRun | None:
        with self._lock, self.engine.begin() as conn:
            row = conn.execute(select(db.search_runs.c.doc).where(db.search_runs.c.id == sr_id)).first()
        return SearchRun.model_validate(row[0]) if row else None

    def list_search_runs(self, run_id: str | None = None, project_id: str | None = None, ids: list[str] | None = None) -> list[SearchRun]:
        stmt = select(db.search_runs.c.doc).order_by(db.search_runs.c.started_at.asc())
        if run_id:
            stmt = stmt.where(db.search_runs.c.run_id == run_id)
        if project_id:
            stmt = stmt.where(db.search_runs.c.project_id == project_id)
        if ids is not None:
            if not ids:
                return []
            stmt = stmt.where(db.search_runs.c.id.in_(ids))
        with self._lock, self.engine.begin() as conn:
            rows = conn.execute(stmt).all()
        return [SearchRun.model_validate(r[0]) for r in rows]

    # ----- extract runs -----
    def save_extract_run(self, xr: ExtractRun) -> None:
        doc = xr.model_dump(mode="json")
        url = xr.urls[0] if xr.urls else None
        with self._lock, self.engine.begin() as conn:
            existing = conn.execute(select(db.extract_runs.c.id).where(db.extract_runs.c.id == xr.id)).first()
            if existing:
                conn.execute(db.extract_runs.update().where(db.extract_runs.c.id == xr.id).values(doc=doc, question_id=xr.question_id, url=url))
            else:
                conn.execute(db.extract_runs.insert().values(id=xr.id, run_id=xr.run_id, project_id=xr.project_id, question_id=xr.question_id, url=url, doc=doc, started_at=xr.started_at))

    # ----- Parallel task runs (location dossiers) -----
    def save_task_run(self, tr: TaskRun) -> None:
        doc = tr.model_dump(mode="json")
        with self._lock, self.engine.begin() as conn:
            existing = conn.execute(select(db.task_runs.c.id).where(db.task_runs.c.id == tr.id)).first()
            if existing:
                conn.execute(db.task_runs.update().where(db.task_runs.c.id == tr.id).values(doc=doc, resource_id=tr.resource_id))
            else:
                conn.execute(db.task_runs.insert().values(id=tr.id, run_id=tr.run_id, project_id=tr.project_id, resource_id=tr.resource_id, doc=doc, started_at=tr.started_at))

    def get_task_run(self, tr_id: str) -> TaskRun | None:
        with self._lock, self.engine.begin() as conn:
            row = conn.execute(select(db.task_runs.c.doc).where(db.task_runs.c.id == tr_id)).first()
        return TaskRun.model_validate(row[0]) if row else None

    def list_task_runs(self, project_id: str | None = None, resource_id: str | None = None) -> list[TaskRun]:
        stmt = select(db.task_runs.c.doc).order_by(db.task_runs.c.started_at.asc())
        if project_id:
            stmt = stmt.where(db.task_runs.c.project_id == project_id)
        if resource_id:
            stmt = stmt.where(db.task_runs.c.resource_id == resource_id)
        with self._lock, self.engine.begin() as conn:
            rows = conn.execute(stmt).all()
        return [TaskRun.model_validate(r[0]) for r in rows]

    # ----- Parallel FindAll runs (substitute vendors) -----
    def save_findall_run(self, fr: FindAllRun) -> None:
        doc = fr.model_dump(mode="json")
        with self._lock, self.engine.begin() as conn:
            existing = conn.execute(select(db.findall_runs.c.id).where(db.findall_runs.c.id == fr.id)).first()
            if existing:
                conn.execute(db.findall_runs.update().where(db.findall_runs.c.id == fr.id).values(doc=doc, resource_id=fr.resource_id))
            else:
                conn.execute(db.findall_runs.insert().values(id=fr.id, run_id=fr.run_id, project_id=fr.project_id, resource_id=fr.resource_id, doc=doc, started_at=fr.started_at))

    def get_findall_run(self, fr_id: str) -> FindAllRun | None:
        with self._lock, self.engine.begin() as conn:
            row = conn.execute(select(db.findall_runs.c.doc).where(db.findall_runs.c.id == fr_id)).first()
        return FindAllRun.model_validate(row[0]) if row else None

    def list_findall_runs(self, project_id: str | None = None, resource_id: str | None = None) -> list[FindAllRun]:
        stmt = select(db.findall_runs.c.doc).order_by(db.findall_runs.c.started_at.asc())
        if project_id:
            stmt = stmt.where(db.findall_runs.c.project_id == project_id)
        if resource_id:
            stmt = stmt.where(db.findall_runs.c.resource_id == resource_id)
        with self._lock, self.engine.begin() as conn:
            rows = conn.execute(stmt).all()
        return [FindAllRun.model_validate(r[0]) for r in rows]

    # ----- Parallel memory reads -----
    def save_memory_read(self, mr: MemoryRead) -> None:
        doc = mr.model_dump(mode="json")
        with self._lock, self.engine.begin() as conn:
            existing = conn.execute(select(db.memory_reads.c.id).where(db.memory_reads.c.id == mr.id)).first()
            if existing:
                conn.execute(db.memory_reads.update().where(db.memory_reads.c.id == mr.id).values(doc=doc))
            else:
                conn.execute(db.memory_reads.insert().values(id=mr.id, project_id=mr.project_id, run_id=mr.run_id, scope_key=mr.scope_key, doc=doc, started_at=mr.started_at))

    def list_memory_reads(self, project_id: str, limit: int = 20, run_id: str | None = None) -> list[MemoryRead]:
        stmt = select(db.memory_reads.c.doc).where(db.memory_reads.c.project_id == project_id)
        if run_id is not None:
            stmt = stmt.where(db.memory_reads.c.run_id == run_id)
        stmt = stmt.order_by(db.memory_reads.c.started_at.desc()).limit(limit)
        with self._lock, self.engine.begin() as conn:
            rows = conn.execute(stmt).all()
        return [MemoryRead.model_validate(r[0]) for r in rows]

    def get_extract_run(self, xr_id: str) -> ExtractRun | None:
        with self._lock, self.engine.begin() as conn:
            row = conn.execute(select(db.extract_runs.c.doc).where(db.extract_runs.c.id == xr_id)).first()
        return ExtractRun.model_validate(row[0]) if row else None

    def list_extract_runs(self, run_id: str | None = None, project_id: str | None = None, ids: list[str] | None = None) -> list[ExtractRun]:
        stmt = select(db.extract_runs.c.doc).order_by(db.extract_runs.c.started_at.asc())
        if run_id:
            stmt = stmt.where(db.extract_runs.c.run_id == run_id)
        if project_id:
            stmt = stmt.where(db.extract_runs.c.project_id == project_id)
        if ids is not None:
            if not ids:
                return []
            stmt = stmt.where(db.extract_runs.c.id.in_(ids))
        with self._lock, self.engine.begin() as conn:
            rows = conn.execute(stmt).all()
        return [ExtractRun.model_validate(r[0]) for r in rows]

    def find_extract_run(self, run_id: str, url: str) -> ExtractRun | None:
        """Cache lookup: an existing successful extract of this URL within the run."""
        with self._lock, self.engine.begin() as conn:
            rows = conn.execute(select(db.extract_runs.c.doc).where(db.extract_runs.c.run_id == run_id, db.extract_runs.c.url == url).order_by(db.extract_runs.c.started_at.desc())).all()
        for r in rows:
            xr = ExtractRun.model_validate(r[0])
            if xr.status in ("OK", "REPLAY") and xr.results:
                return xr
        return None

    # ----- activity -----
    def log(self, event: ActivityEvent) -> ActivityEvent:
        with self._lock, self.engine.begin() as conn:
            conn.execute(db.activity.insert().values(id=event.id, run_id=event.run_id, project_id=event.project_id, ts=event.ts, kind=event.kind, message=event.message, meta=event.meta))
        return event

    def list_activity(self, run_id: str | None = None, project_id: str | None = None, limit: int = 200) -> list[ActivityEvent]:
        stmt = select(db.activity).order_by(db.activity.c.seq.asc())
        if run_id:
            stmt = stmt.where(db.activity.c.run_id == run_id)
        elif project_id:
            stmt = stmt.where(db.activity.c.project_id == project_id)
        with self._lock, self.engine.begin() as conn:
            rows = conn.execute(stmt).mappings().all()
        events = [ActivityEvent(id=r["id"], run_id=r["run_id"], project_id=r["project_id"], ts=_aware(r["ts"]), kind=r["kind"], message=r["message"], meta=r["meta"] or {}) for r in rows]
        return events[-limit:]

    # ----- changesets -----
    def save_changeset(self, cs: ChangeSet) -> None:
        doc = cs.model_dump(mode="json")
        with self._lock, self.engine.begin() as conn:
            existing = conn.execute(select(db.changesets.c.id).where(db.changesets.c.id == cs.id)).first()
            if existing:
                conn.execute(db.changesets.update().where(db.changesets.c.id == cs.id).values(doc=doc))
            else:
                conn.execute(db.changesets.insert().values(id=cs.id, project_id=cs.project_id, run_id=cs.run_id, doc=doc, created_at=cs.created_at))

    def get_changeset(self, cs_id: str) -> ChangeSet | None:
        with self._lock, self.engine.begin() as conn:
            row = conn.execute(select(db.changesets.c.doc).where(db.changesets.c.id == cs_id)).first()
        return ChangeSet.model_validate(row[0]) if row else None

    def list_changesets(self, project_id: str) -> list[ChangeSet]:
        with self._lock, self.engine.begin() as conn:
            rows = conn.execute(select(db.changesets.c.doc).where(db.changesets.c.project_id == project_id).order_by(db.changesets.c.created_at.asc())).all()
        return [ChangeSet.model_validate(r[0]) for r in rows]


def _aware(ts: datetime | None) -> datetime:
    if ts is None:
        return utcnow()
    if ts.tzinfo is None:
        from datetime import timezone

        return ts.replace(tzinfo=timezone.utc)
    return ts
