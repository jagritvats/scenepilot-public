"""Every day-scoped GET answers 404 for a day that does not exist — none of them 500s.

Two routes did not, and they disagreed in opposite directions: `/ephemeris` called
`Project.shoot_day` unguarded, so a `KeyError` escaped as a 500 where its thirty-odd siblings return
404; and `/multiday-plan` answered **200** for an id the production has never heard of, because the
solver tolerates a missing source day internally. That tolerance is deliberate — a scene can be
deferred from a day that was later removed — but it belongs in the solver, not in the contract the
API presents.

Written as a sweep rather than two cases because the failure mode is *inconsistency*: any new
day-scoped route that forgets the guard should fail here rather than be found by someone poking the
deployed API with a hand-edited URL.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scenepilot.api.app import _ensure_seed, app
from scenepilot.seed.nightfall import PROJECT_ID

MISSING = "day_does_not_exist"

DAY_ROUTES = [
    "", "/call-sheet", "/dispatch", "/dpr", "/ephemeris", "/insurance-dossier", "/monitors",
    "/movement-order", "/multiday-plan", "/sides", "/weather-timeline", "/export/mmsx",
]


@pytest.fixture(autouse=True)
def _seeded_database():
    _ensure_seed()


@pytest.mark.parametrize("suffix", DAY_ROUTES)
def test_an_unknown_day_id_is_a_404_everywhere(suffix):
    with TestClient(app) as client:
        r = client.get(f"/api/projects/{PROJECT_ID}/shoot-days/{MISSING}{suffix}")
    assert r.status_code == 404, (
        f"/shoot-days/{{day}}{suffix} answered {r.status_code} for a nonexistent day; "
        "every day-scoped route must 404 (a 500 leaks an unguarded KeyError, a 200 invents a day)"
    )


def test_a_real_day_still_works():
    """The guard must reject only what is genuinely absent."""
    with TestClient(app) as client:
        assert client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_4/ephemeris").status_code == 200
        assert client.get(f"/api/projects/{PROJECT_ID}/shoot-days/day_4/multiday-plan").status_code == 200
