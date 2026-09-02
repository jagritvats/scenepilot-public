"""Record/replay of external calls (Parallel, Gemini).

Recordings are produced ONLY by real live runs (SCENEPILOT_RECORD=1) and replayed in
SCENEPILOT_MODE=replay so a demo cannot be taken down by an outage. Replayed results
are flagged so the UI can label them honestly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ReplayMiss(RuntimeError):
    pass


class Recorder:
    def __init__(self, directory: Path, mode: str = "live", record: bool = False):
        self.directory = Path(directory)
        self.mode = mode
        self.record = record

    @property
    def replay(self) -> bool:
        return self.mode == "replay"

    @staticmethod
    def key(namespace: str, payload: Any) -> str:
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(f"{namespace}::{canon}".encode("utf-8")).hexdigest()[:24]

    def _path(self, namespace: str, key: str) -> Path:
        return self.directory / namespace / f"{key}.json"

    def load(self, namespace: str, key: str) -> Any | None:
        p = self._path(namespace, key)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, namespace: str, key: str, value: Any, request: Any | None = None) -> None:
        if not self.record:
            return
        p = self._path(namespace, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"request": request, "response": value}, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def lookup(self, namespace: str, key: str) -> Any | None:
        rec = self.load(namespace, key)
        if rec is None:
            return None
        return rec.get("response") if isinstance(rec, dict) and "response" in rec else rec

    def has_any(self, namespace: str) -> bool:
        d = self.directory / namespace
        return d.exists() and any(d.glob("*.json"))

    def list_keys(self, namespace: str) -> list[str]:
        d = self.directory / namespace
        return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []
