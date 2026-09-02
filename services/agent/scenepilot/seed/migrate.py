"""Bring an already-persisted seed project forward to what the seed now describes.

`_ensure_seed` only builds the project when it is not there. With a persistent `DATABASE_URL` — a
local `scenepilot.db`, or Cloud SQL behind the hosted demo — it is always there, written by whatever
version of the seed happened to be running the day that database was first touched. So every field
added to a seeded entity after that day is null on the only deployment anybody will ever look at,
while every test passes, because a test builds the project fresh. That is how four real Mumbai
coordinates reached the code and never reached the map.

The seam is deliberately narrow, because a producer's decisions live in the same document:

* it **backfills, never rebuilds** — accepted facts, runs, changesets, monitors and evidence are
  untouched, and so is anything a producer has already changed;
* a field is adopted from the seed only where the stored value is still the model's **class
  default**, which is the signature of a field nobody has ever set, on an entity the seed owns; and
* the derived windows (`day_window`, `golden_hour_*`) are recomputed rather than copied, because
  they are a function of the day's date and the production's city, not something the seed states.

Idempotent and cheap: after the first pass it finds nothing and returns an empty list, which is what
lets the API layer call it on every read rather than only at startup — a Cloud Run process with
`--min-instances 1` can outlive several deployments of this file.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from pydantic_core import PydanticUndefined

from ..domain.enums import ResourceType
from ..domain.models import Project
from ..services.ephemeris import apply_solar_windows
from .nightfall import LOCATION_COORDINATES, build_project

log = logging.getLogger(__name__)

# Facts the seed owns for the entities it created. Everything else is left alone.
SEEDED_LOCATION_FIELDS = ("latitude", "longitude", "locality")
SEEDED_DAY_FIELDS = ("unit_call", "standard_hours", "hard_wrap", "crew_size", "overtime_rate_per_hour", "notes")


class SeedMigrationError(RuntimeError):
    """A field was listed for migration that this seam cannot decide about. Raised at import/test time."""

# A note may not restate a window the engine derives — that is how "golden hour ≈17:45–19:15" outlived
# the hardcoded default it was copied from and went on contradicting the board.
_DERIVED_WINDOW_CLAIM = re.compile(r"\s*Golden hour\s*[≈~]?\s*\d{1,2}:\d{2}\s*[–—-]\s*\d{1,2}:\d{2}\s*\.?")


@lru_cache(maxsize=1)
def _seed_reference() -> Project:
    """The seed as this code now defines it. Read for field values only — never mutated, never saved."""
    return build_project()


def _adopt(stored, seeded, fields: tuple[str, ...]) -> list[str]:
    """Copy `fields` from the seeded entity onto the stored one where nobody ever set them.

    "Nobody ever set it" is read as "the stored value is still the model's class default". It is the
    one signal available in a document that keeps no per-field history, and it is the right one here:
    the value being replaced is the default a panel would otherwise print as an operating fact.

    That test needs a class default to compare against, and a field declared with `default_factory`
    — `Resource.attributes`, `Resource.availability`, `ShootDay.items` — does not have one: pydantic
    reports `PydanticUndefined`, which equals nothing, so the field was skipped, silently, forever.
    `geo.py` already reads `attributes["kind"]`, so the day this list grows `attributes` is the day a
    map goes quiet with no error anywhere. It now refuses instead: the caller must either give the
    field a real class default or write a migration that knows what "nobody set this" means for a
    mutable container.
    """
    changed: list[str] = []
    model_fields = type(stored).model_fields
    for name in fields:
        field = model_fields.get(name)
        if field is None:
            raise SeedMigrationError(f"{type(stored).__name__} has no field {name!r} to migrate")
        default = field.default
        if default is PydanticUndefined:
            raise SeedMigrationError(
                f"{type(stored).__name__}.{name} is declared with default_factory, so it has no class "
                "default to recognise an unset value by; `_adopt` cannot decide whether the stored "
                "value was ever set. Give it a class default or migrate it explicitly."
            )
        fresh = getattr(seeded, name)
        if fresh == default or getattr(stored, name) != default:
            continue
        setattr(stored, name, fresh)
        changed.append(name)
    return changed


def _location_coordinates(project: Project) -> list[str]:
    seed = _seed_reference()
    filled: list[str] = []
    for res in project.resources:
        if res.type != ResourceType.LOCATION or res.id not in LOCATION_COORDINATES:
            continue
        seeded = next((r for r in seed.resources if r.id == res.id), None)
        if seeded is not None and _adopt(res, seeded, SEEDED_LOCATION_FIELDS):
            filled.append(res.name)
    if not filled:
        return []
    return [f"Backfilled real coordinates for {len(filled)} seeded location(s) — {', '.join(filled)} — so company moves can be drawn instead of reported as unknown"]


def _cast_terms(project: Project) -> list[str]:
    """Give a stored performer the cast number and day rate the seed now states for them.

    A cast number is the join key the board, the DOOD, the call sheet and the dispatch share, so a
    deployment whose database predates the field has four documents that agree on nothing but a
    name. A day rate is what the DOOD prices a hold day with, and a database that predates *it*
    reads 0, which the matrix reports as "no rate on file" — correct, and still not what the
    production states. Only CAST, and only where nobody has set either — `_adopt`'s rule, unchanged.
    """
    seed = _seed_reference()
    numbered: list[str] = []
    priced: list[str] = []
    for res in project.resources:
        if res.type != ResourceType.CAST:
            continue
        seeded = next((r for r in seed.resources if r.id == res.id), None)
        if seeded is None:
            continue
        adopted = _adopt(res, seeded, ("cast_number", "day_rate_inr"))
        short = res.name.split(" (")[0]
        if "cast_number" in adopted:
            numbered.append(f"#{res.cast_number} {short}")
        if "day_rate_inr" in adopted:
            priced.append(f"{short} ₹{res.day_rate_inr:,}/day")
    notes: list[str] = []
    if numbered:
        notes.append(
            f"Assigned the seed's cast numbers to {len(numbered)} performer(s) — {', '.join(sorted(numbered))} — "
            "so the board, the DOOD, the call sheet and the dispatch join on one key"
        )
    if priced:
        notes.append(
            f"Adopted the production's contracted day rate for {len(priced)} performer(s) — {', '.join(sorted(priced))} — "
            "so the DOOD prices a hold day at what that performer actually costs"
        )
    return notes


def _walkie_channels(project: Project) -> list[str]:
    """Give a stored department head the radio channel the seed now states for them.

    Same shape as `_cast_terms`, and the same reason it is a typed field rather than an entry in
    `attributes`: a channel plan a database predates comes back `None`, and a call sheet that prints
    no channel column is a call sheet the unit cannot use to raise anybody. Only CREW, and only
    where nobody has set one.
    """
    seed = _seed_reference()
    tuned: list[str] = []
    for res in project.resources:
        if res.type != ResourceType.CREW:
            continue
        seeded = next((r for r in seed.resources if r.id == res.id), None)
        if seeded is not None and _adopt(res, seeded, ("walkie_channel",)):
            tuned.append(f"ch {res.walkie_channel} {res.attributes.get('department', res.name)}")
    if not tuned:
        return []
    return [f"Adopted the seed's radio plan for {len(tuned)} department(s) — {', '.join(sorted(tuned))} — so the call sheet carries the channel each is raised on"]


def _page_counts(project: Project) -> list[str]:
    """Give a stored scene the page count the seed states, distinguishing three histories.

    `eighths` is the one seeded field with two ways of being wrong, so it needs both halves of this
    module's vocabulary. Which half applies is decided per scene, by what the stored value *is*:

    * **Nobody ever set it** — the stored value is `None`, `Scene.eighths`'s class default. That is
      `_adopt`'s rule exactly, unchanged, and it is the case a database written before the seed
      stated any counts is in: the board prints "pages not on file" for a scene the seed can name a
      figure for. Filled, never overwritten, and only where the seed itself states a count.
    * **An older warm pass measured it** — the stored value is a non-default integer that is exactly
      what re-parsing the same five-scene Fountain excerpt still produces for that scene number.
      `seed/warm.py` used to adopt the parser's `eighths`, and the draft in `fixtures/` is an
      excerpt — six lines a scene — so every one of those five scenes was stored as 1/8 of a page
      against 150 scheduled minutes, and the hero day's board totalled "4 sc · 4/8 pgs" in a 12.5 h
      day. Overwritten, because that number is a true statement about the Studio's text and a false
      one about the board.
    * **A producer set it** — any other non-default value. Left exactly as it is.

    The first two are disjoint by construction and cannot both fire: `_adopt` acts only on a stored
    class default, the excerpt branch only on a stored non-default. A producer's count stays safe
    because it fails both tests — it is not `None`, so `_adopt` skips it, and it would have to
    coincide with the excerpt's own parse to the eighth to reach the second branch, which for these
    five scenes means a producer paginating a 150-minute scene at exactly 1/8 of a page.

    Splitting them also stops the notes lying. The null case used to reach the excerpt branch by
    coincidence — `None != excerpt.get(...)` is false for a scene the draft does not contain — so
    the four non-drafted scenes were backfilled while the five drafted ones were skipped, and the
    activity feed announced all four as counts "measured off the draft excerpt" that had never been
    measured at all.
    """
    from ..ingestion.parsers import parse_screenplay
    from .warm import hero_screenplay_text

    seed = _seed_reference()
    excerpt = {str(ps.scene_number): ps.eighths for ps in parse_screenplay(hero_screenplay_text(), format_hint="fountain")}
    filled: list[str] = []
    corrected: list[str] = []
    for scene in project.scenes:
        seeded = next((s for s in seed.scenes if s.id == scene.id), None)
        if seeded is None:
            continue
        if _adopt(scene, seeded, ("eighths",)):
            filled.append(f"Sc {scene.number} {scene.eighths}/8")
            continue
        if seeded.eighths is None or scene.eighths == seeded.eighths:
            continue
        if scene.eighths != excerpt.get(str(scene.number)):
            continue
        corrected.append(f"Sc {scene.number} {scene.eighths}→{seeded.eighths}/8")
        scene.eighths = seeded.eighths
    notes: list[str] = []
    if filled:
        notes.append(
            f"Filled in the seed's stated page count on {len(filled)} scene(s) that had none on file — "
            f"{', '.join(filled)} — so the board totals pages instead of reporting them missing"
        )
    if corrected:
        notes.append(
            f"Restored the production's own page count on {len(corrected)} scene(s) — {', '.join(corrected)} — "
            "which had been measured off the five-scene draft excerpt rather than stated by the producer"
        )
    return notes


def _day_operating_facts(project: Project) -> list[str]:
    seed = _seed_reference()
    touched: list[str] = []
    for day in project.shoot_days:
        seeded = next((d for d in seed.shoot_days if d.id == day.id), None)
        if seeded is not None and _adopt(day, seeded, SEEDED_DAY_FIELDS):
            touched.append(f"Day {day.day_number} (call {day.unit_call}, {day.crew_size} crew)")
    if not touched:
        return []
    return [f"Adopted the seed's stated operating facts for {', '.join(touched)}, which had been reading the model's defaults"]


def _solar_windows(project: Project) -> list[str]:
    moved = [d for d in project.shoot_days if apply_solar_windows(d, project.base_city)]
    if not moved:
        return []
    day = moved[0]
    return [f"Recomputed the lighting windows of {len(moved)} shoot day(s) from astronomical ephemeris for {project.base_city} — Day {day.day_number} golden hour (dusk) is {day.golden_hour_dusk[0]}–{day.golden_hour_dusk[1]}"]


def _notes_restating_derived_windows(project: Project) -> list[str]:
    cleaned = 0
    for day in project.shoot_days:
        if not day.notes:
            continue
        stripped = _DERIVED_WINDOW_CLAIM.sub("", day.notes).strip()
        if stripped != day.notes:
            day.notes = stripped or None
            cleaned += 1
    if not cleaned:
        return []
    return [f"Removed a hardcoded golden-hour claim from {cleaned} shoot-day note(s); the window is derived from the day's own date"]


def _travel_times(project: Project) -> list[str]:
    """Add pairs the seed times that the stored project has no entry for. Never re-time an existing pair.

    A `TravelTime` is a whole row rather than a field, so `_adopt`'s "still at the class default"
    test cannot see it: a pair added to the seed after a database was written is simply absent, and
    absence is indistinguishable from a producer having deleted it — except that `travel_times` is a
    seed-owned table nothing in the product deletes from. The asymmetry is deliberate: adding a
    missing pair turns `geo.py`'s honest "no travel time on file for this pair" back into a number,
    while changing one already there would overwrite a measurement somebody may have corrected.
    """
    seed = _seed_reference()
    have = {frozenset((t.from_location_id, t.to_location_id)) for t in project.travel_times}
    known = {r.id for r in project.resources}
    added = []
    for t in seed.travel_times:
        pair = frozenset((t.from_location_id, t.to_location_id))
        if pair in have or not pair <= known:
            continue
        project.travel_times.append(t.model_copy())
        have.add(pair)
        added.append(f"{project.resource(t.from_location_id).name.split(' —')[0]}↔{project.resource(t.to_location_id).name.split(' —')[0]} {t.minutes} min")
    if not added:
        return []
    return [f"Added {len(added)} seeded travel time(s) the stored project had no entry for — {', '.join(added)} — so the scheduler enforces a measured gap instead of its 30-minute fallback"]


def _availability_windows(project: Project) -> list[str]:
    """Add the seed's booking window for a day a stored resource has no window for at all.

    Same shape of problem as `_travel_times`, and the same asymmetry: an `Availability` is a row, so
    `_adopt` cannot see it, and a day added to a resource's bookings after the database was written
    is simply absent. Absence is not neutral here — `availability_windows` reads "no row for this
    day" as *unavailable*, so a resource the seed later booked onto Day 5 makes the deterministic
    validator reject Day 5's own seeded schedule, and the day's constraints panel comes up blank.

    Narrow on purpose: a day the stored resource already has any window for is left exactly as it
    is, because that window may be a producer's correction. Only whole missing days are added.
    """
    seed = _seed_reference()
    known_days = {d.id for d in project.shoot_days}
    added: list[str] = []
    for res in project.resources:
        seeded = next((r for r in seed.resources if r.id == res.id), None)
        if seeded is None or not seeded.availability:
            continue
        have = {a.shoot_day_id for a in res.availability if a.shoot_day_id is not None}
        # A day the producer released is not a day the seed knows better about. Absence alone cannot
        # tell "the seed grew a booking this database predates" from "somebody took this booking
        # away", and putting the second one back is the migration overruling a decision.
        have |= set(res.released_day_ids)
        for a in seeded.availability:
            if a.shoot_day_id is None or a.shoot_day_id in have or a.shoot_day_id not in known_days:
                continue
            res.availability.append(a.model_copy())
            have.add(a.shoot_day_id)
            added.append(f"{res.name.split(' (')[0].split(' —')[0]} on {a.shoot_day_id.replace('_', ' ')}")
    if not added:
        return []
    return [
        f"Added {len(added)} seeded booking window(s) the stored project had no entry for — {', '.join(added)} — "
        "so those days stop reading as 'resource not available on this day' against their own schedule"
    ]


def _report_entities_the_seed_grew(project: Project) -> None:
    """Say — in the log, not the activity feed — what this seam deliberately does not do.

    A whole location or shoot day added to the seed after a deployment's database was written does
    not arrive here, and should not: creating a shoot day changes what the turnaround rule reads for
    its neighbours, and creating a resource puts something on a call sheet nobody scheduled. But
    "does not arrive" must not read as "there is nothing to arrive". This is a log line rather than
    a returned note because a note makes the caller persist the project, and this migration changes
    nothing — it would rewrite the document on every read for as long as the gap existed.
    """
    seed = _seed_reference()
    missing_resources = [r.id for r in seed.resources if r.id not in {x.id for x in project.resources}]
    missing_days = [d.id for d in seed.shoot_days if d.id not in {x.id for x in project.shoot_days}]
    if missing_resources or missing_days:
        log.warning(
            "Seed defines %d resource(s) and %d shoot day(s) this stored project does not have (%s); "
            "entities are not created by migration — reset the project or rebuild the database to adopt them",
            len(missing_resources), len(missing_days), ", ".join(missing_resources + missing_days),
        )


MIGRATIONS = (
    _location_coordinates,
    _cast_terms,
    _walkie_channels,
    _page_counts,
    _day_operating_facts,
    _travel_times,
    _availability_windows,
    _solar_windows,
    _notes_restating_derived_windows,
)


def migrate_seed_state(project: Project) -> list[str]:
    """Run every migration against a stored project. Returns one note per migration that changed it.

    An empty list means the stored project already matches what a fresh build would have produced,
    which is the normal answer and the one that lets the caller skip the write.
    """
    notes: list[str] = []
    for migration in MIGRATIONS:
        notes.extend(migration(project))
    _report_entities_the_seed_grew(project)
    return notes
