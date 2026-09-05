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


# Every phrasing in which this repository states its *current* test count, and every file it says it
# in. Six drifts happened under a guard that read one file and matched one phrasing, so the sites
# that actually went stale were never looked at: a chip on the landing page a judge reads first, a
# line in the tour modal, and a second sentence in the README's own test section. This is not a
# README claim. It is a claim the project makes in four files, and the guard has to know all of them.
#
# Historical counts are deliberately left unmatched — "grew from 154 to 586" says something true
# about the past in its first number — so only phrasings that assert what is true *now* appear here.
CLAIM_PATTERNS = (
    re.compile(r"\*\*(\d{3,4}) Automated Tests Passing\*\*"),  # README badge line
    re.compile(r"All \*\*(\d{3,4}) tests\*\* pass"),  # README test section
    re.compile(r"\*\*(\d{3,4}) passed in"),  # SUBMISSION metrics
    re.compile(r"Engine: (\d{3,4}) Tests"),  # SUBMISSION architecture diagram
    re.compile(r"to (\d{3,4}) tests during this build"),  # SUBMISSION "what we learned"
    re.compile(r"(\d{3,4}) Tests Passing"),  # landing page chip
    re.compile(r"(\d{3,4})/(\d{3,4}) Tests Passing"),  # tour modal, both halves
)

# Split by whether the public export ships the file, because this suite runs in both checkouts and
# a judge who clones the public repo runs the command the README documents. `scripts/export-public.ps1`
# strips `SUBMISSION.md` and all of `docs/`, so asserting those exist unconditionally would fail
# `pytest` on the published repository — for a file that is absent exactly as intended.
PUBLIC_CLAIM_SITES = (
    "README.md",
    "apps/web/src/app/page.tsx",
    "apps/web/src/components/HackathonTourModal.tsx",
)
PRIVATE_CLAIM_SITES = (
    "SUBMISSION.md",
    "docs/devpost/project-story.md",
)
CLAIM_SITES = PUBLIC_CLAIM_SITES + PRIVATE_CLAIM_SITES

# Present only in the private repository: the export strips it along with the rest of `scripts/`.
# Used to tell the two checkouts apart, so the private one still gets the strict existence check.
PRIVATE_REPO_SENTINEL = "scripts/export-public.ps1"


def _defined_tests() -> int:
    here = Path(__file__).resolve().parent
    return sum(
        len(re.findall(r"^def test_", f.read_text(encoding="utf-8"), re.M))
        for f in here.rglob("test_*.py")
    )


def test_every_place_this_project_states_its_test_count_states_the_same_one():
    """The number that has gone stale more often than any other here — checked everywhere it appears.

    Counted rather than run: collecting the suite from inside the suite is a recursion, and the point
    is only to catch a number that stopped tracking reality. Parametrised cases make the executed
    count higher than the count of `def test_` functions, so each claim is checked with a tolerance —
    wide enough not to fail on parametrisation, far narrower than a day's work.
    """
    actual = _defined_tests()
    found: list[tuple[str, int]] = []
    for site in CLAIM_SITES:
        path = REPO_ROOT / site
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in CLAIM_PATTERNS:
            for match in pattern.finditer(text):
                found.extend((site, int(g)) for g in match.groups() if g)

    assert found, "no site states a test count any more — if that is deliberate, delete this test"
    stale = [(site, n) for site, n in found if abs(n - actual) > 40]
    assert not stale, (
        f"this suite defines {actual} test functions, but these claim otherwise: "
        + "; ".join(f"{site} says {n}" for site, n in stale)
        + ". Re-verify with `uv run pytest -q` and sweep every site in CLAIM_SITES."
    )


def test_the_claim_sites_all_still_exist():
    """A guard that silently skips a renamed file is how a drift gets through unnoticed.

    Checked in two tiers because this repository is published as a filtered copy of itself. The
    exported sites must exist in *any* checkout; the private-only ones are asserted only when the
    sentinel says we are in the source repository, so a judge running `pytest` on the public clone
    does not get a failure for a file the export deliberately removed.
    """
    missing = [s for s in PUBLIC_CLAIM_SITES if not (REPO_ROOT / s).exists()]
    assert not missing, f"exported claim sites that no longer exist: {missing}"

    if (REPO_ROOT / PRIVATE_REPO_SENTINEL).exists():
        missing = [s for s in PRIVATE_CLAIM_SITES if not (REPO_ROOT / s).exists()]
        assert not missing, f"private claim sites that no longer exist: {missing}"


DEEP_PARALLEL_SUITES = (
    "test_dossier.py", "test_findall.py", "test_memory.py",
    "test_fact_watch.py", "test_monitor.py", "test_parallel_tools.py",
)


def test_the_deep_parallel_sub_count_matches_the_suites_it_names():
    """README quotes a second, narrower count — "Deep Parallel integration (N tests)".

    The whole-suite guard above never saw it, so it drifted on its own: it read 76 against 79 actual.
    A judge on this track is more likely to check *that* number than the headline one, because it is
    the claim about the partner integration they were asked to judge. The README names the six files
    it is counting, so the count can simply be taken from them.
    """
    tests_dir = Path(__file__).resolve().parent
    actual = sum(
        len(re.findall(r"^def test_", (tests_dir / name).read_text(encoding="utf-8"), re.M))
        for name in DEEP_PARALLEL_SUITES
    )
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    claimed = re.search(r"\*\*Deep Parallel integration\*\* \((\d+) tests\)", readme)
    assert claimed, "README no longer states a deep-Parallel test count in the expected form"
    stated = int(claimed.group(1))
    assert abs(stated - actual) <= 3, (
        f"README claims {stated} deep-Parallel tests; {DEEP_PARALLEL_SUITES} hold {actual}. "
        "Update the README bullet."
    )
