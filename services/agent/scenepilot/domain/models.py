"""ScenePilot domain models.

These are *production* concepts, deliberately generic (not "location scouting").
Times inside a shoot day are "HH:MM" strings (may exceed 24:00 for night shoots);
`services.timeutil` converts them to minutes for arithmetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from ..services.timeutil import to_minutes
from .enums import (
    Authority,
    ClaimKind,
    ConstraintKind,
    CoordinationKind,
    DisruptionType,
    EvidenceStatus,
    FactBinding,
    Freshness,
    Importance,
    IntExt,
    RequirementCategory,
    ResourceType,
    RunKind,
    RunStatus,
    ScheduleItemStatus,
    RiskStatus,
    Severity,
    ShootDayStatus,
    TimeOfDay,
    VerificationStatus,
)
from .breakdown_models import BreakdownElement, ParsedSceneData


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Creative intent → production requirements
# --------------------------------------------------------------------------- #


class ProductionBrief(BaseModel):
    """Normalised creative input. `source_kind` keeps ingestion extensible (PDF later)."""

    id: str = Field(default_factory=lambda: new_id("brief"))
    project_id: str
    source_kind: str = "pasted_text"  # pasted_text | manual_brief | pdf (future)
    raw_text: str
    notes: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Requirement(BaseModel):
    id: str
    scene_id: str
    category: RequirementCategory
    description: str
    importance: Importance = Importance.MEDIUM
    source_ref: str | None = Field(
        default=None, description="Quote or pointer into the input that motivated this requirement"
    )
    depends_on: list[str] = Field(default_factory=list, description="Other requirement ids")
    resource_ids: list[str] = Field(default_factory=list)
    weather_sensitive: bool = False


class Availability(BaseModel):
    shoot_day_id: str | None = None  # None = every day
    date: str | None = None  # ISO date, alternative to shoot_day_id
    start: str = "00:00"
    end: str = "23:59"
    note: str | None = None


class Resource(BaseModel):
    id: str
    type: ResourceType
    name: str
    # Days a producer has deliberately taken this resource off, as opposed to days nobody has booked
    # them onto yet. `availability` cannot carry that difference — both read as an absent row — and
    # `seed/migrate.py` fills absent seeded days back in, so without this a release was undone by the
    # very next read of the project.
    released_day_ids: list[str] = Field(default_factory=list)
    # The performer's cast number: 1 is the lead, and the number is assigned once, in billing order,
    # and never reused or renumbered. It is the join key every production document shares — the
    # stripboard's cast column, the DOOD rows, the "Cast #" column a call sheet leads with, the
    # dispatch — which is what makes those four read as one system rather than three spreadsheets
    # and a board. Production state, not a display index: a number computed in a frontend is a
    # production identifier that a real backend field will later silently contradict.
    #
    # `None` on everything that is not CAST, and deliberately so — crew, locations, equipment and
    # vehicles do not carry one, because a call sheet does not number them.
    cast_number: int | None = Field(default=None, ge=1)
    # The radio channel this department is reached on. A production decision, not a derivable one:
    # two units on the same show number their channels differently, and departments share a channel
    # wherever they work together. Stated by the production, like `cast_number`, for the same reason
    # — a channel computed in a call-sheet renderer is an operating instruction invented at print
    # time, and the crew would be told to hail a department on a channel nobody agreed.
    #
    # `None` on everything that is not CREW: a location does not carry a radio.
    walkie_channel: int | None = Field(default=None, ge=1, le=8)
    availability: list[Availability] = Field(default_factory=list)
    weather_sensitive: bool = False
    prep_minutes: int = 0  # call-time lead before first use
    hourly_cost: int = 0  # INR, approximate
    # What a performer is engaged for, per day. Cast are contracted by the day, not the hour, which
    # is why this is its own field and not `hourly_cost` multiplied by an assumed shift: the DOOD
    # prices a *hold* day — a day the production pays for and does not shoot — and multiplying an
    # hourly rate by a working day nobody worked is how a matrix ends up quoting a retention cost
    # that no contract contains. `0` means the production has not stated one, and every figure
    # derived from it is withheld rather than defaulted.
    day_rate_inr: int = 0
    rerental_cost: int = 0  # INR, if it must be booked again for a carry-over
    contact: str | None = None
    # Where a LOCATION actually is, WGS84, plus the real place those coordinates name. Optional
    # everywhere and absent on everything that is not a place: a resource without coordinates is
    # simply not drawn, never plotted at a guessed point. Deliberately not in `attributes`, which is
    # hashed into the Parallel Task dossier prompt — geography must not re-key a recording.
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    locality: str | None = None  # what the coordinates are the centre of, e.g. "Kala Ghoda, Fort, Mumbai"
    attributes: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class Scene(BaseModel):
    id: str
    number: str
    heading: str
    int_ext: IntExt
    time_of_day: TimeOfDay
    synopsis: str = ""
    script_text: str = ""
    location_id: str | None = None
    cast_ids: list[str] = Field(default_factory=list)
    equipment_ids: list[str] = Field(default_factory=list)
    estimated_minutes: int = 120
    estimated_minutes_breakdown: int | None = None  # Gemini's estimate from the breakdown; never overrides a scheduled scene
    continuity_group: str | None = None
    rain_tolerant: bool = False
    is_cover: bool = False  # cover set: can be pulled forward when exteriors fail
    eighths: int | None = None  # standard screenplay pagination in eighths of a page
    breakdown_elements: list[BreakdownElement] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    continuity_notes: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    brief_id: str | None = None


# --------------------------------------------------------------------------- #
# Research & evidence
# --------------------------------------------------------------------------- #


class ResearchQuestion(BaseModel):
    id: str
    scene_id: str
    question: str
    rationale: str = ""
    priority: Importance = Importance.MEDIUM
    requirement_ids: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    round: int = 1
    parent_question_id: str | None = None
    status: EvidenceStatus | None = None
    assessment: str | None = None
    search_run_ids: list[str] = Field(default_factory=list)
    extract_run_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class SearchResultItem(BaseModel):
    url: str
    title: str | None = None
    publish_date: str | None = None
    excerpts: list[str] = Field(default_factory=list)


class ParallelUsageItem(BaseModel):
    """Usage SKU reported by Parallel for one call (name + count)."""

    name: str
    count: int


class ParallelWarning(BaseModel):
    type: str
    message: str
    detail: dict[str, Any] | None = None


class SearchRun(BaseModel):
    """One observable Parallel Search API call."""

    id: str = Field(default_factory=lambda: new_id("search"))
    run_id: str | None = None
    project_id: str | None = None
    question_id: str | None = None
    purpose: str = "research"  # research | follow_up | agent_follow_up | disruption_verification
    round: int = 1
    provider: str = "parallel"
    objective: str
    queries: list[str]
    mode: str = "advanced"
    session_id: str | None = None  # Parallel task session shared with Extract calls of the same run
    client_model: str | None = None  # the Gemini model consuming the results (sent to Parallel)
    advanced_settings: dict[str, Any] | None = None  # exactly what was sent; None = Parallel defaults
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "PENDING"  # PENDING | OK | ERROR | REPLAY
    provider_search_id: str | None = None
    results: list[SearchResultItem] = Field(default_factory=list)
    usage: list[ParallelUsageItem] = Field(default_factory=list)
    warnings: list[ParallelWarning] = Field(default_factory=list)
    error: str | None = None
    replayed: bool = False


class ExtractResultItem(BaseModel):
    url: str
    title: str | None = None
    publish_date: str | None = None
    excerpts: list[str] = Field(default_factory=list)
    full_content: str | None = None


class ExtractRun(BaseModel):
    """One observable Parallel Extract API call (full page content for specific URLs)."""

    id: str = Field(default_factory=lambda: new_id("extract"))
    run_id: str | None = None
    project_id: str | None = None
    question_id: str | None = None
    search_run_id: str | None = None  # the search that surfaced the URL, if any
    purpose: str = "evidence_open_source"  # evidence_open_source | agent_extract
    provider: str = "parallel"
    objective: str
    urls: list[str]
    session_id: str | None = None
    client_model: str | None = None
    advanced_settings: dict[str, Any] | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "PENDING"  # PENDING | OK | ERROR | REPLAY
    provider_extract_id: str | None = None
    results: list[ExtractResultItem] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    usage: list[ParallelUsageItem] = Field(default_factory=list)
    warnings: list[ParallelWarning] = Field(default_factory=list)
    error: str | None = None
    replayed: bool = False


class BasisCitation(BaseModel):
    """One source backing a single field of a Parallel Task output."""

    url: str
    title: str | None = None
    excerpts: list[str] = Field(default_factory=list)


class FieldBasis(BaseModel):
    """Parallel's justification for one output field: reasoning, confidence and citations.

    `field` may be dot-indexed for per-list-element basis (e.g. `restrictions.0`), GA since
    2026-08-24; the UI splits on the dot, nothing else depends on the shape.
    """

    field: str
    reasoning: str = ""
    confidence: str | None = None  # high | medium | low — only some processors report it
    citations: list[BasisCitation] = Field(default_factory=list)


class TaskRun(BaseModel):
    """One observable Parallel Task API run (structured research with per-field citations).

    Unlike Search and Extract, the Task API takes no `session_id` and no `client_model`; runs are
    linked by `metadata` and by the project's `memory_scope_key`.
    """

    id: str = Field(default_factory=lambda: new_id("task"))
    run_id: str | None = None  # owning workflow run, when there is one
    project_id: str | None = None
    resource_id: str | None = None  # the location this dossier is about
    shoot_day_id: str | None = None  # the day a weather timeline is about; dossiers stay location-scoped
    purpose: str = "location_dossier"  # location_dossier | weather_timeline
    provider: str = "parallel"
    processor: str = "core-fast"
    input: str = ""
    output_schema: dict[str, Any] | None = None
    memory_scope_key: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "PENDING"  # PENDING | OK | ERROR | REPLAY
    provider_run_id: str | None = None
    interaction_id: str | None = None  # pass as previous_interaction_id to continue this research
    output: dict[str, Any] = Field(default_factory=dict)
    basis: list[FieldBasis] = Field(default_factory=list)
    warnings: list[ParallelWarning] = Field(default_factory=list)
    error: str | None = None
    replayed: bool = False


class VendorCandidate(BaseModel):
    """A real supplier Parallel found who could replace a resource the production has lost."""

    id: str = Field(default_factory=lambda: new_id("vendor"))
    findall_run_id: str
    name: str
    url: str
    description: str = ""
    match_status: str = "matched"  # matched | generated | unmatched | discarded
    match_reasons: list[str] = Field(default_factory=list)
    citations: list[BasisCitation] = Field(default_factory=list)
    phone: str | None = None
    address: str | None = None
    distance_km: float | None = None
    day_rate_band: str | None = None
    selected: bool = False  # a producer picked this one for the recovery


class FindAllRun(BaseModel):
    """One observable Parallel FindAll / Entity Search run — finding substitutes for a lost resource."""

    id: str = Field(default_factory=lambda: new_id("findall"))
    run_id: str | None = None
    project_id: str | None = None
    resource_id: str | None = None  # the resource being replaced
    shoot_day_id: str | None = None
    purpose: str = "substitute_vendors"
    provider: str = "parallel"
    mode: str = "entity_search"  # entity_search (sync) | findall (async, deeper)
    generator: str | None = None  # FindAll only
    objective: str = ""
    entity_type: str = "companies"
    match_conditions: list[dict[str, str]] = Field(default_factory=list)
    match_limit: int = 8
    memory_scope_key: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "PENDING"  # PENDING | RUNNING | OK | ERROR | REPLAY
    provider_findall_id: str | None = None
    termination_reason: str | None = None
    enriched: bool = False  # FindAll only: contact details were fetched for the matched candidates
    candidates: list[VendorCandidate] = Field(default_factory=list)
    warnings: list[ParallelWarning] = Field(default_factory=list)
    error: str | None = None
    replayed: bool = False


class ExternalRule(BaseModel):
    """The machine-checkable core of a discovered fact.

    Only two shapes can bind the scheduler, deliberately: a ban on working at a location during a
    time window, and a ban on an activity at a location. Everything else Parallel finds is shown to
    the producer but never auto-enforced — see `services/dossier.py`.
    """

    kind: str  # TIME_WINDOW_BAN | ACTIVITY_BAN
    window_start: str | None = None  # HH:MM, may wrap past midnight
    window_end: str | None = None
    activity: str | None = None  # drone | fireworks | generator


class LocationFact(BaseModel):
    """A fact Parallel discovered about a location, graded into how much authority it may have.

    `binding` is *proposed* by the confidence gate; a HARD fact only actually constrains the
    schedule once a producer has `accepted` it. Nothing the web says changes a shoot day on its own.
    """

    id: str = Field(default_factory=lambda: new_id("fact"))
    project_id: str
    resource_id: str
    task_run_id: str
    key: str  # noise_curfew | permit_authority | drone_rules | restriction …
    label: str
    value: str
    binding: FactBinding = FactBinding.ADVISORY
    confidence: str | None = None
    reasoning: str = ""
    citations: list[BasisCitation] = Field(default_factory=list)
    rule: ExternalRule | None = None  # present only when the fact is mechanically checkable
    accepted: bool = False
    accepted_at: datetime | None = None
    accepted_by: str | None = None
    rejected: bool = False
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def binds(self) -> bool:
        """A fact constrains the schedule only when it is HARD, machine-checkable and accepted."""
        return self.binding == FactBinding.HARD and self.accepted and not self.rejected and self.rule is not None


class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ev"))
    question_id: str | None = None
    search_run_id: str | None = None
    extract_run_id: str | None = None  # set when the claim came from a Parallel Extract of the page
    claim: str
    source_url: str
    source_title: str | None = None
    excerpt: str
    publish_date: str | None = None
    freshness: Freshness = Freshness.UNKNOWN
    relevance: float = 0.5
    authority: Authority = Authority.UNKNOWN
    confidence: float = 0.5
    kind: ClaimKind = ClaimKind.FACT
    production_implication: str | None = None


class Risk(BaseModel):
    id: str = Field(default_factory=lambda: new_id("risk"))
    scene_id: str | None = None
    title: str
    description: str
    severity: Severity = Severity.MEDIUM
    likelihood: float = 0.5
    confidence: float = 0.5
    kind: ClaimKind = ClaimKind.INFERENCE
    mitigations: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    # The producer's half. Without it the register is a printout: it names what could go wrong and
    # has nowhere to record who owns it or what was decided, which in a real office is the whole
    # purpose of a register. Carried across a re-plan by title — see `workflows/planning.py`.
    status: RiskStatus = RiskStatus.OPEN
    owner: str | None = None
    decision_note: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None


class Candidate(BaseModel):
    id: str = Field(default_factory=lambda: new_id("cand"))
    scene_id: str | None = None
    title: str
    description: str
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    kind: ClaimKind = ClaimKind.RECOMMENDATION


class UnresolvedQuestion(BaseModel):
    question: str
    why_it_matters: str = ""
    kind: ClaimKind = ClaimKind.UNKNOWN
    question_id: str | None = None


class ReadinessBreakdown(BaseModel):
    requirement_coverage: float
    evidence_strength: float
    risk_exposure: float
    unresolved_penalty: float
    explanation: list[str] = Field(default_factory=list)


class ProductionPlan(BaseModel):
    """Grounded plan for a scene = the ProductionDecision record."""

    id: str = Field(default_factory=lambda: new_id("plan"))
    scene_id: str
    run_id: str | None = None
    version: int = 1
    readiness_score: int = 0
    readiness: ReadinessBreakdown | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    recommended_candidate_id: str | None = None
    recommendation: str = ""
    recommendation_kind: ClaimKind = ClaimKind.RECOMMENDATION
    risks: list[Risk] = Field(default_factory=list)
    unresolved: list[UnresolvedQuestion] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utcnow)
    prompt_version: str = "v1"


# --------------------------------------------------------------------------- #
# Production state: shoot days
# --------------------------------------------------------------------------- #


class ScheduleItem(BaseModel):
    id: str
    scene_id: str
    start: str  # HH:MM
    end: str  # HH:MM
    location_id: str | None = None
    status: ScheduleItemStatus = ScheduleItemStatus.SCHEDULED
    note: str | None = None
    unit: str = "MAIN"  # MAIN | SECOND | STUNT | SPLINTER


class EquipmentCall(BaseModel):
    resource_id: str
    call_time: str


class TransportLeg(BaseModel):
    id: str
    vehicle_id: str
    from_location_id: str | None
    to_location_id: str | None
    departure: str
    purpose: str = "company move"


class ShootDay(BaseModel):
    id: str
    project_id: str
    day_number: int
    date: str  # ISO date
    unit_call: str = "06:30"
    standard_hours: float = 12.0
    hard_wrap: str = "22:00"
    # When the camera actually stopped, recorded at wrap. `None` on every day still ahead, and on
    # every day wrapped before this field existed. Without it the record derives its own wrap from
    # `max(end)` over all items — which a *carried* strip would win, dating the wrap from a scene
    # that was never shot and inflating the overtime read off it.
    camera_wrap: str | None = None
    crew_size: int = 45
    overtime_rate_per_hour: int = 5000  # INR for whole crew
    company_move_cost: int = 12000  # INR per additional move
    carry_over_cost: int = 60000  # INR approximate cost of pushing a scene to another day
    pickup_day_cost: int = 350000  # INR to mount a dedicated pickup unit day, when no downstream day can absorb
    golden_hour_dawn: tuple[str, str] = ("06:00", "07:30")
    golden_hour_dusk: tuple[str, str] = ("17:45", "19:15")
    day_window: tuple[str, str] = ("06:30", "18:45")
    night_window: tuple[str, str] = ("19:15", "29:30")
    items: list[ScheduleItem] = Field(default_factory=list)
    equipment_calls: list[EquipmentCall] = Field(default_factory=list)
    transport: list[TransportLeg] = Field(default_factory=list)
    status: ShootDayStatus = ShootDayStatus.READY
    active_disruption_id: str | None = None
    notes: str | None = None


class TravelTime(BaseModel):
    from_location_id: str
    to_location_id: str
    minutes: int


# --------------------------------------------------------------------------- #
# Disruption → impact → recovery
# --------------------------------------------------------------------------- #


class MonitorRecord(BaseModel):
    """A Parallel Monitor watching the outside world for a shoot day."""

    id: str  # Parallel monitor_id
    project_id: str
    shoot_day_id: str | None = None  # event_stream monitors watch one day; snapshot monitors do not
    kind: str = "WEATHER"  # WEATHER | TRANSPORT | REGULATORY | DOSSIER
    query: str = ""
    monitor_type: str = "event_stream"  # event_stream (new information) | snapshot (a diff of known facts)
    task_run_id: str | None = None  # snapshot: the dossier whose output is re-run and diffed
    resource_id: str | None = None  # snapshot: the location that dossier is about
    frequency: str = "1h"
    processor: str = "lite"
    status: str = "active"  # active | cancelled | simulated
    webhook_url: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    last_event_at: datetime | None = None
    event_count: int = 0


class FactChange(BaseModel):
    """A fact this production relies on is no longer what it was.

    Two ways to find out, and they suit different moments. A **snapshot monitor** re-runs a dossier
    on Parallel's schedule and pushes what moved — good for the weeks between planning and shooting.
    A **pre-flight re-check** is the producer asking, deliberately, the night before a day locks —
    which is when anyone actually looks. Both land here, and both are *pending* until a producer
    adopts or dismisses: the accepted value keeps constraining the schedule in the meantime. Same
    rule as everywhere else — the web informs, a human decides.
    """

    id: str = Field(default_factory=lambda: new_id("factchg"))
    project_id: str
    resource_id: str
    detected_by: str = "monitor"  # monitor (a snapshot fired) | preflight (a producer re-checked)
    monitor_id: str | None = None
    task_run_id: str | None = None  # the run that produced the new value, when there was one
    event_id: str
    event_date: str | None = None
    key: str
    label: str
    fact_id: str | None = None  # the fact this supersedes, when one matched
    old_value: str = ""
    new_value: str
    binding: FactBinding = FactBinding.ADVISORY  # how the *new* value grades
    confidence: str | None = None
    reasoning: str = ""
    citations: list[BasisCitation] = Field(default_factory=list)
    rule: ExternalRule | None = None
    old_accepted: bool = False  # the production had signed off the value this replaces
    old_binds: bool = False  # ...and the schedule is being enforced against it right now
    detected_at: datetime = Field(default_factory=utcnow)
    status: str = "PENDING"  # PENDING | ADOPTED | DISMISSED
    decided_at: datetime | None = None
    decided_by: str | None = None
    simulated: bool = False

    @property
    def pending(self) -> bool:
        return self.status == "PENDING"

    @property
    def affects_schedule(self) -> bool:
        """True when adopting this would change what the scheduler enforces."""
        return self.binding == FactBinding.HARD and self.rule is not None


class MemoryEntry(BaseModel):
    """One thing Parallel remembers for this production, from a Task, Monitor or FindAll run.

    Shapes mirror `parallel.types.beta.{Task,Monitor,FindAll}MemoryResult`; the union is flattened
    to one row type so the UI renders a single list.
    """

    kind: str  # task | monitor | findall
    ref_id: str  # the task run / monitor / findall run id it came from
    input_excerpt: str = ""  # the run's input, monitor query, or FindAll objective
    output_excerpt: str = ""  # task output preview; monitor matched-event excerpts; "" for findall
    updated_at: datetime | None = None
    status: str | None = None  # monitors only: active | cancelled
    matched_count: int | None = None  # findall only
    event_ids: list[str] = Field(default_factory=list)  # monitors only


class MemoryRead(BaseModel):
    """One observable read of Parallel's memory for a project — never fired implicitly.

    Memory is server-side state (like Monitors), so it is deliberately *not* record/replayed: in
    replay mode a read returns UNAVAILABLE rather than inventing entries.
    """

    id: str = Field(default_factory=lambda: new_id("memread"))
    project_id: str
    run_id: str | None = None
    scope_key: str
    query: str = ""
    kind: str | None = None
    limit: int = 10
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "PENDING"  # PENDING | OK | ERROR | UNAVAILABLE
    entries: list[MemoryEntry] = Field(default_factory=list)
    error: str | None = None


class Disruption(BaseModel):
    id: str = Field(default_factory=lambda: new_id("dis"))
    project_id: str
    shoot_day_id: str
    type: DisruptionType
    title: str
    description: str
    window_start: str | None = None  # HH:MM within the shoot day
    window_end: str | None = None
    affects_exteriors: bool = True
    affects_location_ids: list[str] = Field(default_factory=list)
    affects_resource_ids: list[str] = Field(default_factory=list)
    dry_out_minutes: int = 0  # safety buffer after the window (wet surfaces)
    source: str = "manual"  # manual | fixture | parallel_monitor
    fixture_id: str | None = None
    draft: bool = False  # detected by a monitor; waits for producer confirmation before any rescue runs
    monitor_id: str | None = None
    monitor_event: dict[str, Any] | None = None
    received_at: datetime = Field(default_factory=utcnow)
    verification_status: VerificationStatus | None = None
    verification_summary: str | None = None
    verification_confidence: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    search_run_ids: list[str] = Field(default_factory=list)
    synthetic: bool = True


    @model_validator(mode="after")
    def _window_is_a_window(self) -> "Disruption":
        """A window that cannot be read, or that ends before it starts, is not a finding — it is input.

        `DisruptionInput(window_start='banana')` used to validate cleanly, and the first parse then
        happened inside the spawned task: `to_minutes` raised, `run_rescue`'s catch-all marked the run
        FAILED, and the day was left carrying a disruption with no options and no way back. Reversed
        windows were worse than a crash — `overlaps` clamps at zero, so 17:00–13:00 matched nothing and
        `analyze_impact` printed it back as "0 scheduled scene(s) directly affected during
        17:00–13:00", which reads as a confident finding rather than a rejected input.

        The API rejects both at the edge with a 400 that names the offending value; this is the
        backstop for every other construction path. `to_minutes` is the parser rather than a second
        regex, so HH>23 keeps working — the night units encode past-midnight as "28:00".
        """
        for label, value in (("window_start", self.window_start), ("window_end", self.window_end)):
            if value is not None:
                try:
                    to_minutes(value)
                except ValueError as exc:
                    raise ValueError(f"{label}: {exc}") from exc
        if self.window_start and self.window_end and to_minutes(self.window_end) <= to_minutes(self.window_start):
            raise ValueError(f"window_end {self.window_end} is not after window_start {self.window_start}")
        if self.dry_out_minutes < 0:
            raise ValueError(f"dry_out_minutes {self.dry_out_minutes} is negative; it would drive the window end backwards")
        return self


class ViolatedRequirement(BaseModel):
    item_id: str
    scene_id: str
    requirement_id: str | None
    reason: str


class ItemMobility(BaseModel):
    item_id: str
    scene_id: str
    reason: str


class ImpactAnalysis(BaseModel):
    disruption_id: str
    directly_affected_item_ids: list[str] = Field(default_factory=list)
    violated_requirements: list[ViolatedRequirement] = Field(default_factory=list)
    implicated_resource_ids: list[str] = Field(default_factory=list)
    immovable: list[ItemMobility] = Field(default_factory=list)
    movable: list[ItemMobility] = Field(default_factory=list)
    cover_scene_ids: list[str] = Field(default_factory=list)
    summary: str = ""


class ConstraintViolation(BaseModel):
    kind: ConstraintKind
    hard: bool
    message: str
    item_id: str | None = None
    scene_id: str | None = None
    resource_id: str | None = None
    cost_inr: int = 0
    minutes: int = 0
    fact_id: str | None = None  # the LocationFact this rule came from (EXTERNAL_RULE only)
    evidence_url: str | None = None  # the page Parallel cited, so a rejection is traceable


class ScoreComponents(BaseModel):
    feasible: bool
    schedule_preservation: int  # 0-100
    cost_impact: int  # 0-100 (100 = no extra cost)
    overtime_risk: int  # 0-100 (100 = none)
    company_moves: int  # 0-100 (100 = no extra moves)
    resource_conflicts: int  # 0-100
    creative_compromise: int  # 0-100
    confidence: int  # 0-100 (disruption verification confidence)
    total: int  # weighted
    estimated_extra_cost_inr: int = 0
    overtime_minutes: int = 0
    extra_company_moves: int = 0
    deferred_scene_ids: list[str] = Field(default_factory=list)


class RecoveryOption(BaseModel):
    id: str
    label: str  # A, B, C...
    title: str
    strategy: str
    origin: str = "deterministic"  # deterministic | gemini
    schedule: list[ScheduleItem]
    deferred_scene_ids: list[str] = Field(default_factory=list)
    violations: list[ConstraintViolation] = Field(default_factory=list)
    feasible: bool = True
    score: ScoreComponents | None = None
    rank: int | None = None
    rejected_reason: str | None = None
    explanation: str = ""
    trade_offs: list[str] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)  # [{label, ok, detail}]


class Change(BaseModel):
    entity_type: str  # schedule_item | equipment_call | transport | scene | shoot_day
    entity_id: str
    label: str
    field: str
    before: Any
    after: Any
    reason: str


class ChangeSet(BaseModel):
    id: str = Field(default_factory=lambda: new_id("cs"))
    project_id: str
    shoot_day_id: str
    run_id: str | None = None
    disruption_id: str | None = None
    recovery_option_id: str | None = None
    changes: list[Change] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    approved_by: str | None = None
    applied_at: datetime | None = None
    summary: str = ""

    @property
    def is_revert_record(self) -> bool:
        """Is this the inverted change set a *revert* wrote, rather than a recovery a producer approved?

        `services/revert.py` records a revert as its own change set and never appends it to
        `project.changeset_ids`, so "absent from changeset_ids" alone cannot tell a rescinded
        recovery from the record that rescinded it — the day payload would flag the revert itself as
        rescinded. Every change a revert writes carries the same stamped reason; that is the signature.
        """
        return bool(self.changes) and all(c.reason.startswith("Reverted by ") for c in self.changes)


class CoordinationAction(BaseModel):
    id: str = Field(default_factory=lambda: new_id("act"))
    changeset_id: str
    kind: CoordinationKind
    title: str
    details: list[str] = Field(default_factory=list)
    target: str | None = None  # crew group / vendor / location contact
    payload: dict[str, Any] = Field(default_factory=dict)
    channel: str = "simulated"  # simulated | email | calendar | sms (future adapters)
    derived_from_change_ids: list[int] = Field(default_factory=list)


class ActivityEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    run_id: str | None = None
    project_id: str | None = None
    ts: datetime = Field(default_factory=utcnow)
    kind: str = "info"  # info | parallel | gemini | deterministic | approval | warning | error
    message: str
    meta: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #


class Project(BaseModel):
    id: str
    title: str
    synthetic: bool = True
    logline: str = ""
    currency: str = "INR"
    base_city: str = "Mumbai"
    country_code: str = "IN"
    briefs: list[ProductionBrief] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    travel_times: list[TravelTime] = Field(default_factory=list)
    shoot_days: list[ShootDay] = Field(default_factory=list)
    plans: dict[str, ProductionPlan] = Field(default_factory=dict)  # scene_id → latest plan
    disruptions: list[Disruption] = Field(default_factory=list)
    monitors: list[MonitorRecord] = Field(default_factory=list)
    memory_scope_key: str | None = None  # Parallel memory scope shared by this project's Task/Monitor/FindAll runs
    location_facts: list[LocationFact] = Field(default_factory=list)  # what Parallel Task runs discovered about locations
    fact_changes: list[FactChange] = Field(default_factory=list)  # changes to those facts that snapshot monitors detected
    parsed_screenplay_scenes: list[ParsedSceneData] = Field(default_factory=list)  # parsed from screenplay ingestion
    changeset_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    # ----- lookups -----
    def scene(self, scene_id: str) -> Scene:
        for s in self.scenes:
            if s.id == scene_id:
                return s
        raise KeyError(scene_id)

    def scene_by_number(self, number: str) -> Scene:
        for s in self.scenes:
            if s.number == number:
                return s
        raise KeyError(number)

    def resource(self, resource_id: str) -> Resource:
        for r in self.resources:
            if r.id == resource_id:
                return r
        raise KeyError(resource_id)

    def shoot_day(self, shoot_day_id: str) -> ShootDay:
        for d in self.shoot_days:
            if d.id == shoot_day_id:
                return d
        raise KeyError(shoot_day_id)

    def disruption(self, disruption_id: str) -> Disruption:
        for d in self.disruptions:
            if d.id == disruption_id:
                return d
        raise KeyError(disruption_id)

    def travel_minutes(self, a: str | None, b: str | None) -> int:
        if a is None or b is None or a == b:
            return 0
        for t in self.travel_times:
            if {t.from_location_id, t.to_location_id} == {a, b}:
                return t.minutes
        return 30  # conservative default for unknown pairs


class PlanningState(BaseModel):
    """Persisted workflow state for a planning run (Production Orchestrator)."""

    scene_id: str
    stage: str = "pending"
    requirements: list[Requirement] = Field(default_factory=list)
    questions: list[ResearchQuestion] = Field(default_factory=list)
    search_run_ids: list[str] = Field(default_factory=list)
    extract_run_ids: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    follow_up_rounds: int = 0
    used_memory: bool = False  # the producer asked the planner to start from prior research
    memory_entries_used: int = 0
    plan: ProductionPlan | None = None


class RescueState(BaseModel):
    """Persisted workflow state for a rescue run (Rescue Orchestrator)."""

    shoot_day_id: str
    disruption_id: str
    stage: str = "pending"
    baseline: list[ScheduleItem] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    search_run_ids: list[str] = Field(default_factory=list)
    extract_run_ids: list[str] = Field(default_factory=list)
    impact: ImpactAnalysis | None = None
    options: list[RecoveryOption] = Field(default_factory=list)
    recommended_option_id: str | None = None
    recommendation_rationale: str = ""
    changeset: ChangeSet | None = None
    actions: list[CoordinationAction] = Field(default_factory=list)
    # The status the day carried before this run touched it, so a run that ends without a recovery —
    # nothing to recover, or a failure — can hand the day back instead of stranding it AT_RISK with a
    # disruption nobody can act on. `baseline` is the same idea for the schedule.
    prior_day_status: ShootDayStatus | None = None
    # Set only when the run stopped because the disruption touches nothing on this day. Non-null is
    # the signal a producer is owed an answer rather than an empty option list; the string is the
    # sentence to print.
    no_impact_reason: str | None = None
    # Set only when a producer ended the run without taking any of it. A sibling to the field above
    # rather than a reuse of it: "the engine found nothing to do" and "somebody decided not to do it"
    # are both reasons a run ends at COMPLETED with no change set, and they are not the same sentence.
    stood_down_reason: str | None = None
    stood_down_by: str | None = None
    stood_down_at: datetime | None = None


class WorkflowRun(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    project_id: str
    kind: RunKind
    status: RunStatus = RunStatus.PENDING
    stage: str = "pending"
    mode: str = "live"
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    planning: PlanningState | None = None
    rescue: RescueState | None = None
