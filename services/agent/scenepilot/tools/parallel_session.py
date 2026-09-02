"""One Parallel client + one session per task.

Parallel's guidance: "Use the same session_id across search and extract calls that are
part of the same task, and a new unique id for each new task" and pass `client_model`
("the model consuming the results; enables optimizations and tailors defaults").
Both are attached here once and reused by the Search and Extract tools.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..config import Settings, settings as default_settings
from .recorder import Recorder


# Defaults for the shared client. A caller that a human is waiting behind — the reset button's
# monitor cancels — passes something shorter and stops retrying.
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_RETRIES = 2


def new_session_id(kind: str, run_id: str | None = None) -> str:
    """Descriptive prefix + unique suffix, as Parallel recommends (e.g. scenepilot_rescue_run_ab12cd34ef)."""
    suffix = run_id or uuid.uuid4().hex[:12]
    return f"scenepilot_{kind}_{suffix}"


class ParallelSession:
    def __init__(self, settings: Settings | None = None, recorder: Recorder | None = None, session_id: str | None = None, client_model: str | None = None, memory_scope_key: str | None = None, timeout: float = DEFAULT_TIMEOUT_S, max_retries: int = DEFAULT_MAX_RETRIES):
        self.settings = settings or default_settings
        self.timeout = timeout
        self.max_retries = max_retries
        self.recorder = recorder or Recorder(self.settings.recordings_dir, self.settings.active_mode, self.settings.record)
        self.session_id = session_id or new_session_id("adhoc")
        self.client_model = client_model or self.settings.gemini_model
        # Task / Monitor / FindAll runs share a per-project memory scope (Search and Extract do not
        # take one — they use `session_id`). None when the Memory feature is disabled.
        self.memory_scope_key = memory_scope_key
        self.calls: list[Any] = []  # SearchRun | ExtractRun, in call order
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from parallel import Parallel  # lazy: tests and keyless runs never import the SDK

            self._client = Parallel(api_key=self.settings.parallel_api_key, max_retries=self.max_retries, timeout=self.timeout)
        return self._client

    def meta(self) -> dict[str, str]:
        """Per-task metadata sent with every call but excluded from record/replay keys."""
        return {"session_id": self.session_id, "client_model": self.client_model}

    @property
    def replay(self) -> bool:
        return self.recorder.replay
