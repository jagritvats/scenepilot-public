"""Point the suite at a throwaway database before anything reads the configuration.

`api/app.py` builds its `repo` at import, and `_ensure_seed` now re-anchors and migrates whatever it
finds there. Fifteen test modules import that module-level `app`/`repo`, so with `DATABASE_URL`
unset a plain `pytest` run opened `services/agent/data/scenepilot.db` — the developer's own demo
state, the one with accepted facts and applied changesets in it — re-dated its shoot days, ran the
seed migrations over it and left it written. An auditor watched its mtime move during a suite run.

Setting `DATABASE_URL` here, at conftest import, is what fixes it: pytest imports this file before it
imports a single test module, `scenepilot.config` reads the variable once at *its* import, and
`load_dotenv` does not override a variable that is already set. Every `Repo()` and `make_engine()`
that does not name its own URL therefore lands in a temporary directory that is deleted when the
session ends. Tests that already build their own `sqlite:///:memory:` engine are unaffected.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="scenepilot-tests-"))

# Both, deliberately: DATABASE_URL is the one that carries the writes, and SCENEPILOT_DATA_DIR keeps
# anything that later decides to write beside the database out of the working tree too. Recordings
# are read from their own directory and are not touched by either.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(_TMP / 'scenepilot.db').as_posix()}")
os.environ.setdefault("SCENEPILOT_DATA_DIR", str(_TMP))

# The repo-root .env sets SCENEPILOT_MODE=live and SCENEPILOT_RECORD=1, so a plain `pytest` — the
# command the README documents — made real paid Gemini calls and wrote fresh files into
# seed/fixtures/recordings/, the committed fixtures the demo and the trailer both replay from.
# Forcing the safe pair here means the documented command is the safe one; a test that wants live or
# recording behaviour sets it explicitly, and an operator can still override from the environment.
os.environ.setdefault("SCENEPILOT_MODE", "replay")
os.environ.setdefault("SCENEPILOT_RECORD", "0")


@pytest.fixture(scope="session", autouse=True)
def _database_is_disposable():
    """Refuse to run against real state, so this cannot quietly regress into overwriting it again."""
    from scenepilot.config import settings

    live = (Path(__file__).resolve().parent.parent / "data" / "scenepilot.db").as_posix()
    assert live not in settings.database_url, (
        f"the suite is pointed at the working database ({settings.database_url}); "
        "tests must never write real production state"
    )
    assert _TMP.as_posix() in settings.database_url or ":memory:" in settings.database_url
    assert not settings.record, "the suite must never record: it would overwrite committed fixtures"
    yield
    shutil.rmtree(_TMP, ignore_errors=True)
