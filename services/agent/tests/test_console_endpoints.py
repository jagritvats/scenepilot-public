"""The Parallel Console's endpoint labels must exist in the installed Parallel SDK.

That console is the one screen in the product aimed squarely at this track's judges, who work at
Parallel — a wrong path there is read by exactly the audience that knows it is wrong. Three of the
six were: `POST /v1/tasks` (really `/v1/tasks/runs`), `POST /v1/findall` (really
`/v1beta/findall/entity-search`, and `/v1beta/findall/runs` for the batch mode) and
`GET/POST /v1/memory` (really `POST /v1beta/memory/retrieve`, and beta, not v1).

Checked against `parallel-web` itself rather than a hand-written list, so the labels track the SDK
the service actually calls: if a version bump moves a route, this fails instead of the console
quietly describing an API that no longer exists.
"""

from __future__ import annotations

import pathlib
import re

import pytest

CONSOLE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "apps" / "web" / "src" / "components" / "ParallelConsoleModal.tsx"
)
SDK = pathlib.Path("parallel").resolve()


def _sdk_paths() -> set[str]:
    import parallel

    root = pathlib.Path(parallel.__file__).parent
    found: set[str] = set()
    for f in root.rglob("*.py"):
        found |= set(re.findall(r'"(/v1(?:beta)?/[^"{]*)"', f.read_text(encoding="utf-8")))
    return {p.rstrip("/") for p in found}


def _console_paths() -> list[str]:
    text = CONSOLE.read_text(encoding="utf-8")
    labels = re.findall(r'endpoint:\s*"([^"]+)"', text)
    assert labels, "no endpoint labels found — did the console change shape?"
    out: list[str] = []
    for label in labels:
        # "POST /v1beta/findall/entity-search · /runs" — a panel may cover more than one route.
        verbs, _, rest = label.partition(" ")
        base = ""
        for part in [p.strip() for p in rest.split("·")]:
            if part.startswith("/v1"):
                base = "/".join(part.split("/")[:3])
                out.append(part)
            elif part.startswith("/"):
                out.append(base + part)
    return out


@pytest.mark.skipif(not CONSOLE.exists(), reason="web app not present in this checkout")
def test_every_console_endpoint_exists_in_the_parallel_sdk():
    sdk = _sdk_paths()
    assert sdk, "could not read any routes out of the parallel SDK"
    unknown = [p for p in _console_paths() if p not in sdk]
    assert not unknown, f"console names routes the SDK does not have: {unknown}"


@pytest.mark.skipif(not CONSOLE.exists(), reason="web app not present in this checkout")
def test_the_console_covers_the_six_apis_the_service_uses():
    labels = _console_paths()
    for fragment in ("/v1/search", "/v1/extract", "/v1/tasks/runs", "/v1beta/findall", "/v1beta/memory", "/v1/monitors"):
        assert any(p.startswith(fragment) for p in labels), f"console no longer names {fragment}"
