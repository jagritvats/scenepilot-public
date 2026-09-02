"""Versioned prompt templates. `load("scene_breakdown")` → prompts/v1/scene_breakdown.md."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent
DEFAULT_VERSION = "v1"


@lru_cache(maxsize=64)
def load(name: str, version: str = DEFAULT_VERSION) -> str:
    path = PROMPTS_DIR / version / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt {name}@{version} not found at {path}")
    return path.read_text(encoding="utf-8").strip()
