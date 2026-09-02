"""Typed output schemas for every Gemini step. All model output is validated against these."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..domain.breakdown_models import BREAKDOWN_CATEGORIES

Category = Literal["CREATIVE", "LOCATION", "CAST", "LOGISTICS", "WEATHER", "REGULATORY", "SAFETY", "TECHNICAL", "EQUIPMENT", "SCHEDULE", "BUDGET", "CONTINUITY"]
ImportanceL = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
SeverityL = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
StatusL = Literal["SUPPORTED", "WEAK", "CONFLICTING", "MISSING"]


# ---------------- Scene breakdown ----------------


class RequirementOut(BaseModel):
    ref: str = Field(description="Short local id like R1, R2 …")
    category: Category
    description: str
    importance: ImportanceL
    source_ref: str = Field(default="", description="Exact phrase from the input that motivates this requirement, or empty")
    depends_on: list[str] = Field(default_factory=list, description="refs of requirements this depends on")
    weather_sensitive: bool = Field(default=False, description="True if rain/wind/heat would violate it")


class SceneFacts(BaseModel):
    heading: str
    int_ext: Literal["INT", "EXT"]
    time_of_day: Literal["DAY", "NIGHT", "SUNSET", "DAWN", "ANY"]
    synopsis: str
    estimated_minutes: int = Field(description="Realistic shooting time for this scene in minutes")
    cast_roles: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    rain_tolerant: bool = False


class SceneBreakdownOutput(BaseModel):
    scene: SceneFacts
    requirements: list[RequirementOut]


# ---------------- Research planning ----------------


class ResearchQuestionOut(BaseModel):
    ref: str = Field(description="Q1, Q2 …")
    question: str
    rationale: str
    priority: ImportanceL
    requirement_refs: list[str] = Field(default_factory=list, description="Requirement ids (req_…) this question serves")
    objective: str = Field(description="Concise, self-contained search objective naming the key entity, city and the source preference in words (sent to the Parallel Search API)")
    search_queries: list[str] = Field(description="Exactly 3 diverse keyword queries, 3-6 words each; never sentences, quotes, OR, or site: operators")


class ResearchPlanOutput(BaseModel):
    questions: list[ResearchQuestionOut]


# ---------------- Evidence analysis ----------------


class EvidenceOut(BaseModel):
    claim: str = Field(description="One specific claim stated by the source")
    source_ref: str = Field(description="Exact result reference in the form <search_run_id>#<n>, copied from the results block")
    relevance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1, description="How much this source supports the claim")
    production_implication: str = Field(default="", description="What this means for the production")


class EvidenceAssessmentOutput(BaseModel):
    status: StatusL
    assessment: str = Field(description="2–4 sentences: what the evidence establishes and what is still unknown")
    evidence: list[EvidenceOut]
    follow_up_objective: str = Field(default="", description="If status is not SUPPORTED: what a follow-up search should find")
    follow_up_queries: list[str] = Field(default_factory=list, description="If status is not SUPPORTED: exactly 3 refined, diverse keyword queries of 3-6 words (no sentences, quotes, OR, or site:)")


# ---------------- Production plan ----------------


class FactOut(BaseModel):
    statement: str
    evidence_ids: list[str] = Field(default_factory=list, description="ev_… ids from the evidence block")


class CandidateOut(BaseModel):
    title: str
    description: str
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class RiskOut(BaseModel):
    title: str
    description: str
    severity: SeverityL
    likelihood: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    kind: Literal["FACT", "INFERENCE"] = "INFERENCE"
    mitigations: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)


class UnresolvedOut(BaseModel):
    question: str
    why_it_matters: str = ""


class ProductionPlanOutput(BaseModel):
    key_facts: list[FactOut] = Field(description="Only statements directly grounded in evidence")
    inferences: list[str] = Field(description="Conclusions derived from facts — label clearly as inference")
    candidates: list[CandidateOut]
    recommended_candidate_index: int = Field(description="0-based index into candidates")
    recommendation: str = Field(description="Recommended production approach, 2–4 sentences")
    risks: list[RiskOut]
    unresolved: list[UnresolvedOut]


# ---------------- Disruption verification ----------------


class DisruptionVerificationOutput(BaseModel):
    status: Literal["CORROBORATED", "PARTIALLY_CORROBORATED", "UNCORROBORATED", "CONTRADICTED"]
    summary: str = Field(description="2–3 sentences on what current external sources say about the disruption")
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceOut]
    notes_for_planning: list[str] = Field(default_factory=list, description="Concrete implications, e.g. expected rain timing, wind")


# ---------------- Rescue planning ----------------


class RescueProposal(BaseModel):
    title: str
    strategy: str = Field(description="One sentence on why this ordering could work")
    order_scene_numbers: list[str] = Field(description="Scene numbers in shooting order for the day")
    deferred_scene_numbers: list[str] = Field(default_factory=list, description="Scene numbers to carry over to another day")


class RescueProposalOutput(BaseModel):
    proposals: list[RescueProposal] = Field(description="Up to 2 alternative orderings not already in the evaluated list")
    reasoning: str = Field(description="Short note on the production logic behind the proposals")


class OptionExplanation(BaseModel):
    label: str
    explanation: str = Field(description="2–3 sentences a producer would understand")
    trade_offs: list[str] = Field(default_factory=list)


class RescueExplanationOutput(BaseModel):
    headline: str = Field(description="At most 10 words: what the recommended recovery does (e.g. 'Cover set in the rain, rooftop after it clears')")
    options: list[OptionExplanation]
    recommendation_rationale: str = Field(description="Why the recommended option wins over the others, referencing the score components and constraints")


# ---------------- Comprehensive Element Breakdown ----------------


# Built from the domain's canonical tuple rather than restated here — see `domain/breakdown_models`.
BreakdownCategoryL = Literal[BREAKDOWN_CATEGORIES]  # type: ignore[valid-type]


class ElementBreakdownItem(BaseModel):
    category: BreakdownCategoryL
    name: str
    description: str = ""
    count: int = 1
    implied: bool = False
    safety_notes: str | None = None


class ComprehensiveBreakdownOutput(BaseModel):
    scene_summary: str
    elements: list[ElementBreakdownItem]
    stop_conditions: list[str] = Field(default_factory=list, description="Immediate safety stop conditions (e.g. wet surface for bike stunts, wind > 25 km/h for drones)")
    continuity_notes: list[str] = Field(default_factory=list)

