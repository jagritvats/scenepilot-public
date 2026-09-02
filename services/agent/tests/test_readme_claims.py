"""The README's checkable claims, checked.

Stage One of this hackathon is pass/fail on one thing being findable: that the Parallel Search API is
visibly called at runtime. The README answers that with a table of line-anchored permalinks, which
is the fastest possible answer — and also the most fragile, because a line number is correct until
somebody inserts an import. A permalink that lands two lines off is worse than no permalink: it
reads as a claim the reader then cannot verify.

So the table is treated as an assertion about the source and checked like one. The same goes for the
test count, which has gone stale in the docs more times than any other number in this project.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"

# `[`path:line`](path#Lline)` — the shape the runtime-call table uses.
PERMALINK = re.compile(r"\[`(?P<label>[^`]+?):(?P<label_line>\d+)`\]\((?P<path>[^)#]+)#L(?P<line>\d+)\)")


def _readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.mark.skipif(not README.exists(), reason="README.md is not in this checkout")
def test_every_permalink_in_the_readme_points_at_a_line_that_exists():
    links = list(PERMALINK.finditer(_readme()))
    assert links, "the README's runtime-call table has no line-anchored permalinks left in it"
    for link in links:
        target = REPO_ROOT / link.group("path")
        assert target.exists(), f"README links to {link.group('path')}, which does not exist"
        lines = target.read_text(encoding="utf-8").splitlines()
        number = int(link.group("line"))
        assert 1 <= number <= len(lines), (
            f"README links to {link.group('path')}#L{number}, but that file has {len(lines)} lines"
        )
        # The label and the anchor have to agree; they are written by hand and drift independently.
        assert link.group("label_line") == link.group("line"), (
            f"README permalink label says line {link.group('label_line')} but the anchor says {link.group('line')}"
        )


@pytest.mark.skipif(not README.exists(), reason="README.md is not in this checkout")
def test_each_parallel_api_permalink_lands_on_the_call_it_claims():
    """The Stage-One claim, verified: each row points at the line that actually makes that call.

    Matched on the distinctive part of the invocation rather than the whole line, so reformatting the
    arguments does not fail this — only the line moving does, which is exactly what should fail.
    """
    expected = {
        "parallel_search.py": ".search(",
        "parallel_task.py": ".task_run.create(",
        "parallel_extract.py": ".extract(",
        "parallel_findall.py": ".findall.entity_search(",
        "parallel_memory.py": ".memory.retrieve(",
        "parallel_monitor.py": ".monitor.create(",
    }
    seen: set[str] = set()
    for link in PERMALINK.finditer(_readme()):
        path = REPO_ROOT / link.group("path")
        name = path.name
        if name not in expected:
            continue
        seen.add(name)
        line = path.read_text(encoding="utf-8").splitlines()[int(link.group("line")) - 1]
        assert expected[name] in line, (
            f"README says {name}:{link.group('line')} calls {expected[name]!r}, but that line is:\n  {line.strip()}"
        )
    missing = sorted(set(expected) - seen)
    assert not missing, f"the README's runtime-call table no longer links these Parallel APIs: {missing}"


@pytest.mark.skipif(not README.exists(), reason="README.md is not in this checkout")
def test_the_test_count_the_readme_advertises_is_the_one_the_suite_reports():
    """The number that has gone stale more often than any other in this project.

    Counted rather than run: collecting the suite from inside the suite is a recursion, and the point
    is only to catch a docs number that stopped tracking reality.
    """
    claimed = {int(n) for n in re.findall(r"\*\*(\d{3,4}) Automated Tests Passing\*\*", _readme())}
    if not claimed:
        pytest.skip("the README no longer advertises a test count")
    here = Path(__file__).resolve().parent
    actual = sum(
        len(re.findall(r"^def test_", f.read_text(encoding="utf-8"), re.M))
        for f in here.rglob("test_*.py")
    )
    # Parametrised cases make the executed count higher than the count of test functions, so this is
    # a floor with a tolerance rather than an equality — enough to catch a number that has drifted by
    # a whole day's work, which is how it has always gone wrong.
    assert claimed, "no claim to check"
    for number in claimed:
        assert abs(number - actual) <= 40, (
            f"README advertises {number} tests; this suite defines {actual} test functions. "
            "Re-verify from `uv run pytest -q` and sweep every claim site."
        )
