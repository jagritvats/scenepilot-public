"""SQLAlchemy Core persistence. SQLite by default; Postgres when DATABASE_URL is set."""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from ..config import settings

metadata = MetaData()

projects = Table(
    "projects", metadata,
    Column("id", String, primary_key=True),
    Column("title", String, nullable=False),
    Column("doc", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True)),
)

runs = Table(
    "runs", metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, index=True, nullable=False),
    Column("kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("doc", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

search_runs = Table(
    "search_runs", metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, index=True),
    Column("project_id", String, index=True),
    Column("question_id", String, index=True),
    Column("doc", JSON, nullable=False),
    Column("started_at", DateTime(timezone=True)),
)

extract_runs = Table(
    "extract_runs", metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, index=True),
    Column("project_id", String, index=True),
    Column("question_id", String, index=True),
    Column("url", String, index=True),
    Column("doc", JSON, nullable=False),
    Column("started_at", DateTime(timezone=True)),
)

task_runs = Table(
    "task_runs", metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, index=True),
    Column("project_id", String, index=True),
    Column("resource_id", String, index=True),
    Column("doc", JSON, nullable=False),
    Column("started_at", DateTime(timezone=True)),
)

findall_runs = Table(
    "findall_runs", metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, index=True),
    Column("project_id", String, index=True),
    Column("resource_id", String, index=True),
    Column("doc", JSON, nullable=False),
    Column("started_at", DateTime(timezone=True)),
)

memory_reads = Table(
    "memory_reads", metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, index=True),
    Column("run_id", String, index=True),
    Column("scope_key", String, index=True),
    Column("doc", JSON, nullable=False),
    Column("started_at", DateTime(timezone=True)),
)

activity = Table(
    "activity", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String, unique=True),
    Column("run_id", String, index=True),
    Column("project_id", String, index=True),
    Column("ts", DateTime(timezone=True)),
    Column("kind", String),
    Column("message", String),
    Column("meta", JSON),
)

changesets = Table(
    "changesets", metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, index=True),
    Column("run_id", String, index=True),
    Column("doc", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True)),
)


def make_engine(url: str | None = None) -> Engine:
    url = url or settings.database_url
    if url.startswith("sqlite"):
        if url.endswith(":memory:"):
            engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
        else:
            engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)
        with engine.begin() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
    else:
        engine = create_engine(url, pool_pre_ping=True, future=True)
    metadata.create_all(engine)
    return engine
