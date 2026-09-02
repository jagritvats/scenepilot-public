"""Turning a Parallel Task dossier into production constraints — the confidence gate.

Two decisions live here, and they are the honest core of the feature.

**1. How much authority a discovered fact may have.** Parallel returns a `basis` per field with a
confidence level and citations. We grade it:

    high + at least one citation  → HARD      may reject a schedule option
    high without a citation       → SOFT      prices the option, never rejects
    medium                        → SOFT
    low / absent                  → ADVISORY  shown for a human to check, never enforced

and then — crucially — **a HARD fact still does nothing until a producer accepts it**
(`LocationFact.binds`). Nothing the web says changes a shoot day on its own; that is the same rule
the ChangeSet already follows, applied to knowledge.

**2. Which facts can bind at all.** Only two shapes are mechanically checkable: a ban on working at
a location during a time window (a noise curfew), and a ban on an activity at a location (drones,
pyrotechnics, generators). Everything else Parallel finds is displayed with its citation but never
auto-enforced. We deliberately do not try to parse arbitrary prose into constraints — pretending to
would be worse than the limit.
"""

from __future__ import annotations

import re

from ..domain.enums import FactBinding, ResourceType
from ..domain.models import ExternalRule, FieldBasis, LocationFact, Project, Resource, TaskRun

# Field key → (human label, the activity it can ban, if any)
FIELD_LABELS: dict[str, tuple[str, str | None]] = {
    "permit_authority": ("Permit authority", None),
    "permit_lead_time_days": ("Permit lead time", None),
    "fee_band_inr": ("Fee band", None),
    "noise_curfew": ("Noise curfew", None),
    "drone_rules": ("Drone rules", "drone"),
    "fireworks_rules": ("Pyrotechnics rules", "fireworks"),
    "restrictions": ("Restriction", None),
    "nearest_hospital": ("Nearest hospital", None),
    "monsoon_flooding_history": ("Monsoon flooding", None),
}

# Live runs show the model answers "No information found on X" rather than returning an empty
# string. Those are non-answers: dropping them matters, because otherwise they are graded SOFT and
# carry the run's general citation, which reads as if a source supported the absence of a rule.
#
# Matched narrowly on purpose. A real restriction very often starts with "No" ("No vehicle access
# after 21:00", "No drones permitted"), so a blanket "starts with no" rule would silently discard
# genuine constraints. Only phrasing about *information being absent* counts.
_NON_ANSWER_EXACT = {"", "-", "none", "n/a", "na", "null", "unknown", "not applicable", "not specified", "not available", "not found"}
_NON_ANSWER = re.compile(
    r"\bno (information|data|details|records?|mention|specifics?|publicly available)\b"
    r"|\bnot (found|available|specified|stated|listed|mentioned|determined|disclosed|documented)\b"
    r"|\b(unable to (determine|find|locate|verify)|could ?not (be )?(found|determined|located)|insufficient information)\b"
    r"|\bno\b[^.]{0,40}\b(was |were |is |are )?(found|available|specified|listed|mentioned|documented)\b",
    re.IGNORECASE,
)

# Words that make a restriction a *ban* rather than a permission. Checked against a lowercased value.
_PROHIBITED = re.compile(r"\b(prohibit\w*|forbidden|banned|not permitted|not allowed|no-fly|no fly|disallowed)\b")
_ALLOWED = re.compile(r"\b(permitted|allowed|unrestricted|no restriction)\b")

# "22:00-06:00", "22.00 to 06.00", "10 pm – 6 am"
_RANGE = re.compile(r"(\d{1,2})[:.](\d{2})\s*(?:-|–|—|to|until|till)\s*(\d{1,2})[:.](\d{2})")
_RANGE_AMPM = re.compile(r"(\d{1,2})\s*(am|pm)\s*(?:-|–|—|to|until|till)\s*(\d{1,2})\s*(am|pm)", re.IGNORECASE)

# Which resources count as the banned activity
ACTIVITY_KEYWORDS = {
    "drone": ("drone", "aerial", "uav"),
    "fireworks": ("firework", "pyro", "explosive"),
    "generator": ("generator", "genset", "dg set"),
}


def parse_time_range(value: str) -> tuple[str, str] | None:
    """Pull an unambiguous HH:MM–HH:MM out of a value. Returns None when it is not clear-cut."""
    m = _RANGE.search(value)
    if m:
        h1, m1, h2, m2 = (int(g) for g in m.groups())
        if h1 < 24 and h2 < 24 and m1 < 60 and m2 < 60:
            return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"
    m = _RANGE_AMPM.search(value)
    if m:
        h1, p1, h2, p2 = m.group(1), m.group(2).lower(), m.group(3), m.group(4).lower()
        to24 = lambda h, p: (int(h) % 12) + (12 if p == "pm" else 0)  # noqa: E731
        return f"{to24(h1, p1):02d}:00", f"{to24(h2, p2):02d}:00"
    return None


def is_non_answer(value: str) -> bool:
    """True when the value says nothing was found, rather than stating a fact."""
    v = value.strip()
    return v.lower().rstrip(".") in _NON_ANSWER_EXACT or bool(_NON_ANSWER.search(v))


def prohibits(value: str) -> bool:
    """True only when the text plainly forbids something — an unclear value never binds."""
    low = value.lower()
    if _PROHIBITED.search(low):
        # "prohibited without permission" is a permission regime, not a ban
        return not re.search(r"\b(without|unless|except)\b", low)
    return False


def resource_matches_activity(resource: Resource, activity: str) -> bool:
    words = ACTIVITY_KEYWORDS.get(activity, ())
    haystack = f"{resource.name} {' '.join(str(v) for v in resource.attributes.values())}".lower()
    return any(w in haystack for w in words)


def _binding(basis: FieldBasis | None) -> tuple[FactBinding, str | None]:
    """The confidence gate. No basis at all is treated as unverified, never as fact."""
    if basis is None:
        return FactBinding.ADVISORY, None
    confidence = (basis.confidence or "").lower()
    if confidence == "high":
        return (FactBinding.HARD if basis.citations else FactBinding.SOFT), confidence
    if confidence == "medium":
        return FactBinding.SOFT, confidence
    return FactBinding.ADVISORY, confidence or None


def _rule(key: str, value: str, activity: str | None) -> ExternalRule | None:
    """The machine-checkable core of a fact, or None when it is prose we will not pretend to parse."""
    if key == "noise_curfew":
        window = parse_time_range(value)
        if window:
            return ExternalRule(kind="TIME_WINDOW_BAN", window_start=window[0], window_end=window[1])
        return None
    if activity and prohibits(value):
        return ExternalRule(kind="ACTIVITY_BAN", activity=activity)
    return None


def find_basis(basis: list[FieldBasis], field: str) -> FieldBasis | None:
    """Exact match first, then the per-element form (`restrictions.0`) that GA'd on 2026-08-24."""
    for b in basis:
        if b.field == field:
            return b
    for b in basis:
        if b.field.split(".")[0] == field:
            return b
    return None


def map_facts(task_run: TaskRun, project: Project) -> list[LocationFact]:
    """Grade a completed dossier into LocationFacts. Empty values are dropped, never invented."""
    if task_run.status not in {"OK", "REPLAY"} or not task_run.resource_id:
        return []
    facts: list[LocationFact] = []
    for key, raw in (task_run.output or {}).items():
        label, activity = FIELD_LABELS.get(key, (key.replace("_", " ").capitalize(), None))
        values = raw if isinstance(raw, list) else [raw]
        for index, item in enumerate(values):
            value = str(item or "").strip()
            if not value or is_non_answer(value):
                continue
            field = f"{key}.{index}" if isinstance(raw, list) else key
            basis = find_basis(task_run.basis, field) or find_basis(task_run.basis, key)
            binding, confidence = _binding(basis)
            rule = _rule(key, value, activity)
            if rule is None and binding == FactBinding.HARD:
                # High-confidence prose we cannot check mechanically still must not bind.
                binding = FactBinding.SOFT
            facts.append(
                LocationFact(
                    project_id=project.id,
                    resource_id=task_run.resource_id,
                    task_run_id=task_run.id,
                    key=key,
                    label=label if not isinstance(raw, list) else f"{label} {index + 1}",
                    value=value,
                    binding=binding,
                    confidence=confidence,
                    reasoning=basis.reasoning if basis else "",
                    citations=list(basis.citations) if basis else [],
                    rule=rule,
                )
            )
    return facts


def merge_facts(project: Project, resource_id: str, facts: list[LocationFact]) -> list[LocationFact]:
    """Replace this location's facts with the newest dossier's, keeping producer acceptances.

    A re-run should not silently un-accept what a producer already signed off, but it also must not
    carry an acceptance onto a *different* value — so acceptance survives only an identical fact.

    Which leaves the case that used to fall between the two, and it is only dangerous for a fact
    that *binds*. When a re-run returned a different value for a key the producer had accepted, the
    old fact matched nothing, so it was neither carried nor kept: the last line dropped it. For a
    binding rule — a HARD, machine-checkable, accepted one, the only kind the scheduler ever sees —
    that meant a curfew the producer had signed off silently stopped constraining anything, and every
    recovery option the validator had rejected for breaching it became feasible again with nothing
    re-verdicting them.

    So a *binding* fact is held in force and the newcomer is handed back to the caller, which turns
    it into a `FactChange` for the producer to decide — the same route a monitor's finding takes.
    Every other fact still merges outright: a re-research is the producer asking for the newest
    answer, and only a rule that is actually holding a schedule up is worth refusing to overwrite.

    Returns the facts that were withheld; an empty list means the merge was total.
    """
    previous = {(f.key, f.value): f for f in project.location_facts if f.resource_id == resource_id}
    binding_now = {f.key: f for f in project.location_facts if f.resource_id == resource_id and f.binds}
    kept: list[LocationFact] = []
    withheld: list[LocationFact] = []
    for f in facts:
        old = previous.get((f.key, f.value))
        if old is not None:
            f.accepted, f.accepted_at, f.accepted_by, f.rejected = old.accepted, old.accepted_at, old.accepted_by, old.rejected
            kept.append(f)
            continue
        standing = binding_now.get(f.key)
        if standing is not None and standing.value.strip() != f.value.strip():
            withheld.append(f)
            continue
        kept.append(f)
    survivors = [f for f in project.location_facts if f.resource_id == resource_id and f.binds and f.key in {w.key for w in withheld}]
    project.location_facts = [f for f in project.location_facts if f.resource_id != resource_id] + kept + survivors
    return withheld


def binding_facts(project: Project, resource_id: str | None) -> list[LocationFact]:
    """Accepted, machine-checkable facts for one location — the only ones the scheduler sees."""
    if not resource_id:
        return []
    return [f for f in project.location_facts if f.resource_id == resource_id and f.binds]


def location_resources(project: Project) -> list[Resource]:
    return [r for r in project.resources if r.type == ResourceType.LOCATION]
