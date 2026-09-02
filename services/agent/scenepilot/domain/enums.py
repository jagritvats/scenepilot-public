"""Enumerations shared across the ScenePilot domain."""

from __future__ import annotations

from enum import StrEnum


class RequirementCategory(StrEnum):
    CREATIVE = "CREATIVE"
    LOCATION = "LOCATION"
    CAST = "CAST"
    LOGISTICS = "LOGISTICS"
    WEATHER = "WEATHER"
    REGULATORY = "REGULATORY"
    SAFETY = "SAFETY"
    TECHNICAL = "TECHNICAL"
    EQUIPMENT = "EQUIPMENT"
    SCHEDULE = "SCHEDULE"
    BUDGET = "BUDGET"
    CONTINUITY = "CONTINUITY"


class Importance(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ClaimKind(StrEnum):
    """Epistemic status of any statement the product shows."""

    FACT = "FACT"  # directly grounded in evidence
    INFERENCE = "INFERENCE"  # derived from facts
    RECOMMENDATION = "RECOMMENDATION"  # proposed production choice
    UNKNOWN = "UNKNOWN"  # insufficient information


class EvidenceStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    WEAK = "WEAK"
    CONFLICTING = "CONFLICTING"
    MISSING = "MISSING"


class Freshness(StrEnum):
    CURRENT = "CURRENT"  # < 90 days or clearly current
    RECENT = "RECENT"  # < 1 year
    DATED = "DATED"  # older
    UNKNOWN = "UNKNOWN"


class Authority(StrEnum):
    OFFICIAL = "OFFICIAL"  # government / regulator / operator
    NEWS = "NEWS"  # established news organisation
    INDUSTRY = "INDUSTRY"  # trade press, professional bodies, vendors
    COMMUNITY = "COMMUNITY"  # forums, blogs, social
    UNKNOWN = "UNKNOWN"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ResourceType(StrEnum):
    CAST = "CAST"
    LOCATION = "LOCATION"
    EQUIPMENT = "EQUIPMENT"
    VEHICLE = "VEHICLE"
    CREW = "CREW"


class IntExt(StrEnum):
    INT = "INT"
    EXT = "EXT"


class TimeOfDay(StrEnum):
    DAY = "DAY"
    NIGHT = "NIGHT"
    SUNSET = "SUNSET"  # golden hour (dusk)
    DAWN = "DAWN"  # golden hour (morning)
    ANY = "ANY"


class DisruptionType(StrEnum):
    WEATHER = "WEATHER"
    CAST_UNAVAILABLE = "CAST_UNAVAILABLE"
    LOCATION_UNAVAILABLE = "LOCATION_UNAVAILABLE"
    EQUIPMENT_FAILURE = "EQUIPMENT_FAILURE"
    TRANSPORT = "TRANSPORT"
    REGULATORY = "REGULATORY"
    OTHER = "OTHER"


class VerificationStatus(StrEnum):
    CORROBORATED = "CORROBORATED"
    PARTIALLY_CORROBORATED = "PARTIALLY_CORROBORATED"
    UNCORROBORATED = "UNCORROBORATED"
    CONTRADICTED = "CONTRADICTED"


class ScheduleItemStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    AT_RISK = "AT_RISK"
    MOVED = "MOVED"
    DEFERRED = "DEFERRED"  # carried over to another day
    COMPLETED = "COMPLETED"


class ShootDayStatus(StrEnum):
    READY = "READY"
    AT_RISK = "AT_RISK"
    RECOVERY_PROPOSED = "RECOVERY_PROPOSED"
    RECOVERED = "RECOVERED"
    WRAPPED = "WRAPPED"


class RunKind(StrEnum):
    PLANNING = "PLANNING"
    RESCUE = "RESCUE"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPLIED = "APPLIED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RiskStatus(StrEnum):
    """Where a risk stands. `OPEN` is the engine's own state; the rest are a producer's."""

    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"      # we are living with it
    MITIGATING = "MITIGATING"  # somebody is doing something about it
    CLOSED = "CLOSED"          # it cannot happen any more


class ConstraintKind(StrEnum):
    # hard
    CAST_UNAVAILABLE = "CAST_UNAVAILABLE"
    LOCATION_UNAVAILABLE = "LOCATION_UNAVAILABLE"
    EQUIPMENT_UNAVAILABLE = "EQUIPMENT_UNAVAILABLE"
    TIME_OF_DAY_INCOMPATIBLE = "TIME_OF_DAY_INCOMPATIBLE"
    ITEM_OVERLAP = "ITEM_OVERLAP"
    TRAVEL_OVERLAP = "TRAVEL_OVERLAP"
    DISRUPTION_EXPOSURE = "DISRUPTION_EXPOSURE"
    WEATHER_SENSITIVE_EQUIPMENT = "WEATHER_SENSITIVE_EQUIPMENT"
    DAY_BOUNDS = "DAY_BOUNDS"
    EXTERNAL_RULE = "EXTERNAL_RULE"  # a rule Parallel discovered and the producer accepted (see LocationFact)
    # soft
    OVERTIME = "OVERTIME"
    EXTRA_COMPANY_MOVE = "EXTRA_COMPANY_MOVE"
    LIGHTING_COMPROMISE = "LIGHTING_COMPROMISE"
    CONTINUITY_SPLIT = "CONTINUITY_SPLIT"
    SCENE_DEFERRED = "SCENE_DEFERRED"
    EQUIPMENT_RERENTAL = "EQUIPMENT_RERENTAL"
    MEAL_BREAK = "MEAL_BREAK"
    TURNAROUND = "TURNAROUND"  # rest before the next day's unit call


class FactBinding(StrEnum):
    """How much authority a Parallel-discovered fact is allowed to have over the schedule."""

    HARD = "HARD"  # high confidence + a citation: rejects options once a producer accepts it
    SOFT = "SOFT"  # medium confidence, or high without a citation: prices the option, never rejects
    ADVISORY = "ADVISORY"  # low/absent confidence: shown for a human to verify, never enforced


class CoordinationKind(StrEnum):
    SCHEDULE_REGENERATED = "SCHEDULE_REGENERATED"
    CALL_SHEET_REGENERATED = "CALL_SHEET_REGENERATED"
    CREW_NOTIFICATION = "CREW_NOTIFICATION"
    CAST_NOTIFICATION = "CAST_NOTIFICATION"
    EQUIPMENT_CALL_UPDATED = "EQUIPMENT_CALL_UPDATED"
    TRANSPORT_UPDATED = "TRANSPORT_UPDATED"
    MEAL_COUNT_UPDATED = "MEAL_COUNT_UPDATED"
    LOCATION_CONTACT_UPDATE = "LOCATION_CONTACT_UPDATE"
    SCENE_CARRY_OVER = "SCENE_CARRY_OVER"
    EQUIPMENT_SUBSTITUTE = "EQUIPMENT_SUBSTITUTE"  # a replacement supplier Parallel found and the producer chose
