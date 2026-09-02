/* API client + domain types mirrored from services/agent/scenepilot/domain/models.py */

export type ClaimKind = "FACT" | "INFERENCE" | "RECOMMENDATION" | "UNKNOWN";
export type EvidenceStatus = "SUPPORTED" | "WEAK" | "CONFLICTING" | "MISSING";

export interface Requirement {
  id: string;
  scene_id: string;
  category: string;
  description: string;
  importance: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  source_ref: string | null;
  depends_on: string[];
  resource_ids: string[];
  weather_sensitive: boolean;
}

export interface Scene {
  id: string;
  number: string;
  heading: string;
  int_ext: "INT" | "EXT";
  time_of_day: string;
  synopsis: string;
  script_text: string;
  location_id: string | null;
  cast_ids: string[];
  equipment_ids: string[];
  estimated_minutes: number;
  estimated_minutes_breakdown: number | null;
  continuity_group: string | null;
  rain_tolerant: boolean;
  is_cover: boolean;
  eighths?: number | null;
  breakdown_elements?: BreakdownElement[];
  requirements: Requirement[];
}

export interface BreakdownElement {
  id: string;
  category: string;
  name: string;
  description: string;
  count: number;
  implied: boolean;
  safety_notes: string | null;
}

export interface ParsedDialogue {
  character: string;
  parenthetical: string | null;
  text: string;
}

export interface ParsedSceneData {
  scene_number: string;
  heading: string;
  int_ext: "INT" | "EXT";
  time_of_day: string;
  setting: string;
  page_start: number;
  page_end: number;
  eighths: number;
  action_text: string;
  dialogue: ParsedDialogue[];
  elements: BreakdownElement[];
  stop_conditions?: string[];
  continuity_notes?: string[];
  raw_text: string;
}

export interface CastDOODEntry {
  cast_id: string;
  /** The production's cast number. Null on a performer who has none — those sort after the numbered. */
  cast_number: number | null;
  name: string;
  day_status: Record<string, string>;
  total_work_days: number;
  total_hold_days: number;
  /** First call to last, inclusive — what the production is engaged for. Work + hold. */
  total_engaged_days: number;
  /** The performer's contracted day rate, or null where the production has stated none. */
  day_rate_inr: number | null;
  hold_day_cost_warning: boolean;
  /** Null — not 0 — when there is no rate to price the holds with. Zero is a cost; this is its absence. */
  estimated_hold_cost_inr: number | null;
  warning_message: string | null;
  /** Whether this performer's hold runs could be released under the agreement in force. Advisory. */
  drop_pickup: {
    /** null = no hold days to decide about; false = the pack forbids it or the runs are too short. */
    available: boolean | null;
    minimum_days: number | null;
    longest_hold_run: number;
    releasable_days: number;
    saving_inr: number | null;
    note: string;
  };
}

/** The bottom line a UPM reads first: how many days is this cast costing, and how many shoot. */
export interface DoodTotals {
  performers: number;
  performers_engaged: number;
  work_days: number;
  hold_days: number;
  engaged_days: number;
  /** Summed only across performers who carry a rate — a floor, not the whole cost. */
  hold_cost_inr: number | null;
  unpriced_performers: string[];
  labor_pack: string;
  /** Null when the agreement in force models no drop-and-pickup provision at all. */
  drop_pickup_minimum_days: number | null;
  releasable_days: number;
}

/** What an approved recovery did to the cast schedule, performer by performer. */
export interface DoodDelta {
  shoot_day_id: string;
  changes: {
    cast_id: string;
    cast_number: number | null;
    name: string;
    cells: { shoot_day_id: string; before: string; after: string }[];
    hold_days_before: number;
    hold_days_after: number;
    hold_days_gained: number;
    work_days_before: number;
    work_days_after: number;
    day_rate_inr: number | null;
    added_hold_cost_inr: number | null;
    unpriced_reason: string | null;
  }[];
  /** The one line worth putting on screen: who this cost the most, and what it cost. */
  headline: string | null;
  total_added_hold_cost_inr: number | null;
  unpriced_performers: string[];
}

/* The one-liner — services/agent/scenepilot/services/oneliner.py. The whole shoot, one line a
   scene, which is the document a producer circulates when a schedule moves because the entire
   before and after fits on one page. */

export interface OneLinerRow {
  item_id: string;
  scene_id: string;
  scene: string;
  heading: string;
  int_ext: "INT" | "EXT";
  time_of_day: string;
  synopsis: string;
  start: string;
  end: string;
  minutes: number;
  eighths: number | null;
  pages: string | null;
  cast: { cast_number: number | null; name: string }[];
  location: string | null;
  status: string;
  cover: boolean;
  /** MAIN | SECOND | STUNT | SPLINTER — chipped only when it is not the main unit. */
  unit: string;
}

export interface OneLinerDay {
  shoot_day_id: string;
  day_number: number;
  date: string;
  unit_call: string;
  status: string;
  scenes: OneLinerRow[];
  scene_count: number;
  /** Null where a scene on the day carries no page count — withheld, never part-summed. */
  total_eighths: number | null;
  total_label: string | null;
  sets: string[];
  company_moves: number;
  /** Present on the current sheet only — the baseline is history and carries no figure. */
  cost?: { total_inr: number; basis: "projected" | "record" };
  /** Today's pages against the production's own average. Null where the day has no page total. */
  velocity: {
    day_eighths: number;
    day_label: string;
    /** Null when any scheduled day carries an unpaginated scene — then there is no average to quote. */
    average_eighths: number | null;
    average_label: string | null;
    delta_label: string | null;
    days_counted: number;
    /** How many of the averaged days are a record rather than a plan. */
    days_wrapped: number;
    withheld_reason: string | null;
  } | null;
  /** Rest since the previous day wrapped. Null on the first day and either side of an empty one. */
  rest_before: {
    minutes: number;
    required_minutes: number;
    hours_label: string;
    required_label: string;
    breach: boolean;
    deficit_minutes: number;
    from_wrap: string;
    to_call: string;
    pack: string;
  } | null;
}

export interface OneLiner {
  production: string;
  days: OneLinerDay[];
  scene_count: number;
  total_eighths: number | null;
  total_label: string | null;
  unpriced_reason: string | null;
}

export interface OneLinerView {
  current: OneLiner;
  /** The same document built against the rescue's own pre-recovery schedule, or null if none applied. */
  baseline: OneLiner | null;
  changed_day_id: string | null;
  moves: { scene: string; from_day: number | null; from_slot: string | null; to_day: number | null; to_slot: string | null; carried_out: boolean }[];
}

/** The production log — every recorded act on this show, project- and run-scoped alike.
 *  `kinds` is the vocabulary, sent by the API so the log and the run feed cannot disagree about
 *  what an event *is*; colour for each category is presentation and lives in the page. */
export interface ProductionLog {
  events: ActivityEvent[];
  kinds: Record<string, { label: string; category: string; description: string }>;
  categories: string[];
  counts_by_category: Record<string, number>;
  runs: { id: string; kind: string; status: string; scene_id: string | null; shoot_day_id: string | null; created_at: string }[];
  total: number;
  /** True when older activity exists beyond the returned window. */
  truncated: boolean;
}

export interface DoodView {
  project_id: string;
  entries: CastDOODEntry[];
  shoot_days: { id: string; day_number: number; date: string }[];
  /** The codes this engine actually emits, sent rather than hardcoded so the legend cannot overclaim. */
  codes: Record<string, string>;
  /** Codes a real DOOD carries that this production has no state behind. */
  unmodelled_codes: Record<string, string>;
  totals: DoodTotals;
  /** Characters the breakdown found that nobody is cast for — a casting gap, never a work day. */
  unlinked_characters: { character: string; scenes: string[]; scheduled: boolean }[];
  delta: DoodDelta | null;
}

export interface DownstreamDayPlacement {
  shoot_day_id: string;
  day_number: number;
  date: string;
  scene_id: string;
  scene_number: string;
  scheduled_start: string;
  scheduled_end: string;
  added_overtime_minutes: number;
  added_cost_inr: number;
  feasible: boolean;
  notes: string[];
}

/** What placing the deferred scenes downstream would cost in cast the production has to keep.
 *  A projection, and labelled as one wherever it renders: nothing here is approved, so these hold
 *  days do not exist yet — they are what the DOOD would say if the placements were committed. */
export interface CastRetentionProjection {
  cast_id: string;
  cast_number: number | null;
  name: string;
  hold_days_added: number;
  day_rate_inr: number | null;
  /** Null where the production states no day rate — the hold is counted, never priced at a default. */
  added_cost_inr: number | null;
  reason: string;
}

export interface MultiDayRipplePlan {
  recovery_option_id: string;
  deferred_scene_ids: string[];
  placements: DownstreamDayPlacement[];
  synthesized_pickup_day: ShootDay | null;
  total_ripple_cost_inr: number;
  cast_retention: CastRetentionProjection[];
  cast_retention_cost_inr: number | null;
  summary: string;
}

export interface Availability {
  shoot_day_id: string | null;
  date: string | null;
  start: string;
  end: string;
  note: string | null;
}

export interface Resource {
  id: string;
  type: "CAST" | "LOCATION" | "EQUIPMENT" | "VEHICLE" | "CREW";
  name: string;
  /** Billing order, and the key the board, the DOOD, the call sheet and the dispatch join on. Null on
   *  everything that is not CAST — a call sheet does not number a location, a truck or a grip. */
  cast_number: number | null;
  availability: Availability[];
  weather_sensitive: boolean;
  prep_minutes: number;
  contact: string | null;
  /** Neighbourhood, where the production states one. Null on anything with no address. */
  locality?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  attributes: Record<string, unknown>;
}

export interface ResearchQuestion {
  id: string;
  question: string;
  rationale: string;
  priority: string;
  requirement_ids: string[];
  search_queries: string[];
  status: EvidenceStatus | null;
  assessment: string | null;
  search_run_ids: string[];
  evidence_ids: string[];
}

export interface SearchResultItem {
  url: string;
  title: string | null;
  publish_date: string | null;
  excerpts: string[];
}

export interface UsageItem {
  name: string;
  count: number;
}

export interface ApiWarning {
  type: string;
  message: string;
  detail: Record<string, unknown> | null;
}

export interface AdvancedSearchSettings {
  location?: string;
  max_results?: number;
  excerpt_settings?: { max_chars_per_result?: number };
  source_policy?: { include_domains?: string[]; exclude_domains?: string[]; after_date?: string };
  fetch_policy?: { max_age_seconds?: number };
}

export interface SearchRun {
  id: string;
  question_id: string | null;
  purpose: string;
  round: number;
  provider: string;
  objective: string;
  queries: string[];
  mode: string;
  session_id: string | null;
  client_model: string | null;
  advanced_settings: AdvancedSearchSettings | null;
  usage: UsageItem[];
  warnings: ApiWarning[];
  started_at: string;
  finished_at: string | null;
  status: string;
  provider_search_id: string | null;
  results: SearchResultItem[];
  error: string | null;
  replayed: boolean;
}

export interface ExtractResultItem {
  url: string;
  title: string | null;
  publish_date: string | null;
  excerpts: string[];
  full_content: string | null;
}

export interface ExtractRun {
  id: string;
  question_id: string | null;
  search_run_id: string | null;
  purpose: string;
  objective: string;
  urls: string[];
  session_id: string | null;
  client_model: string | null;
  started_at: string;
  finished_at: string | null;
  status: string;
  provider_extract_id: string | null;
  results: ExtractResultItem[];
  errors: { url: string; error_type: string; http_status_code: number | null }[];
  usage: UsageItem[];
  warnings: ApiWarning[];
  error: string | null;
  replayed: boolean;
}

export interface ParallelUsage {
  searches: number;
  by_mode: Record<string, number>;
  extracts: number;
  urls: number;
  tasks: number;
  task_processors: Record<string, number>;
  findalls: number;
  vendors: number;
  usage: UsageItem[];
  warnings: number;
  replayed: number;
  errors: number;
  session_ids: string[];
  client_model: string | null;
  /** What was actually spent — live calls only. A replayed demo is $0 here, because it spent $0. */
  est_cost_usd: number;
  /** What the replayed calls would have cost live. Never spent; quotable as "a run like this". */
  replayed_cost_usd: number;
  cost_by_api: Record<"search" | "extract" | "task" | "findall", { spent_usd: number; replayed_usd: number }>;
}

export interface Evidence {
  id: string;
  question_id: string | null;
  search_run_id: string | null;
  extract_run_id: string | null;
  claim: string;
  source_url: string;
  source_title: string | null;
  excerpt: string;
  publish_date: string | null;
  freshness: string;
  relevance: number;
  authority: string;
  confidence: number;
  kind: ClaimKind;
  production_implication: string | null;
}

export interface Risk {
  id: string;
  title: string;
  description: string;
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  likelihood: number;
  confidence: number;
  kind: ClaimKind;
  mitigations: string[];
  evidence_ids: string[];
  requirement_ids: string[];
}

export interface Candidate {
  id: string;
  title: string;
  description: string;
  pros: string[];
  cons: string[];
  evidence_ids: string[];
  kind: ClaimKind;
}

export interface ProductionPlan {
  id: string;
  scene_id: string;
  run_id: string | null;
  readiness_score: number;
  readiness: {
    requirement_coverage: number;
    evidence_strength: number;
    risk_exposure: number;
    unresolved_penalty: number;
    explanation: string[];
  } | null;
  candidates: Candidate[];
  recommended_candidate_id: string | null;
  recommendation: string;
  risks: Risk[];
  unresolved: { question: string; why_it_matters: string; kind: ClaimKind; question_id: string | null }[];
  key_facts: string[];
  inferences: string[];
  evidence_ids: string[];
  generated_at: string;
  prompt_version: string;
}

export interface ScheduleItem {
  id: string;
  scene_id: string;
  start: string;
  end: string;
  location_id: string | null;
  status: "SCHEDULED" | "AT_RISK" | "MOVED" | "DEFERRED" | "COMPLETED";
  note: string | null;
  unit?: string;
}

export interface ShootDay {
  id: string;
  project_id: string;
  day_number: number;
  date: string;
  unit_call: string;
  standard_hours: number;
  hard_wrap: string;
  /** When the camera actually stopped, recorded at wrap. `null` on every day still ahead — and the
   *  reason a wrapped day's record is not re-derived from `max(end)`, which a carried strip wins. */
  camera_wrap: string | null;
  crew_size: number;
  overtime_rate_per_hour: number;
  golden_hour_dusk: [string, string];
  items: ScheduleItem[];
  equipment_calls: { resource_id: string; call_time: string }[];
  transport: { id: string; vehicle_id: string; from_location_id: string | null; to_location_id: string | null; departure: string }[];
  status: "READY" | "AT_RISK" | "RECOVERY_PROPOSED" | "RECOVERED" | "WRAPPED";
  active_disruption_id: string | null;
  notes: string | null;
}

export interface MonitorRecord {
  id: string;
  shoot_day_id: string | null;
  monitor_type: "event_stream" | "snapshot";
  task_run_id: string | null;
  resource_id: string | null;
  kind: string;
  query: string;
  frequency: string;
  processor: string;
  status: string;
  webhook_url: string | null;
  created_at: string;
  last_event_at: string | null;
  event_count: number;
}

export interface MonitorsView {
  monitors: MonitorRecord[];
  proposed: { kind: string; query: string }[];
  live_possible: boolean;
  webhook_url: string | null;
  drafts: Disruption[];
}

export interface Disruption {
  id: string;
  type: string;
  title: string;
  description: string;
  window_start: string | null;
  window_end: string | null;
  dry_out_minutes: number;
  source: string;
  fixture_id: string | null;
  draft: boolean;
  monitor_id: string | null;
  monitor_event: { event_id: string | null; event_group_id: string | null; event_date: string | null; simulated: boolean } | null;
  received_at: string;
  verification_status: string | null;
  verification_summary: string | null;
  verification_confidence: number | null;
  evidence_ids: string[];
  search_run_ids: string[];
  synthetic: boolean;
}

export interface SolarLightingProfile {
  date: string;
  latitude: number;
  longitude: number;
  timezone_offset: number;
  sunrise: string;
  sunset: string;
  solar_noon: string;
  civil_twilight_dawn: string;
  civil_twilight_dusk: string;
  nautical_twilight_dawn: string;
  nautical_twilight_dusk: string;
  golden_hour_dawn: [string, string];
  golden_hour_dusk: [string, string];
  day_window: [string, string];
  night_window: [string, string];
  golden_hour_dusk_minutes: number;
  sun_azimuth_at_sunset: number;
}

export interface LaborRulePack {
  name: string;
  preset: "DGA_SAG" | "FWICE_CINTAA" | "CUSTOM";
  standard_shift_hours: number;
  lunch_due_hours: number;
  lunch_window_slack_minutes: number;
  minimum_lunch_minutes: number;
  compounding_meal_penalties: boolean;
  meal_penalty_tiers_inr: number[];
  minimum_turnaround_hours: number;
  forced_call_penalty_enabled: boolean;
  forced_call_flat_penalty_inr: number;
  golden_time_threshold_hours: number;
  golden_time_multiplier: number;
}

export interface StripSimulationResult {
  valid: boolean;
  hard_violations: ConstraintViolation[];
  soft_violations: ConstraintViolation[];
  total_penalty_cost_inr: number;
  labor_pack_used: string;
}

/** Somebody a broadcast would address, resolved from the production's own cast and CREW resources. */
export interface DispatchRecipient {
  resource_id: string;
  /** Cast only — a department head is not numbered. */
  cast_number: number | null;
  name: string;
  role: string;
  department: string;
  contact: string | null;
  call_time: string;
  scenes: string[];
  payload_preview: string;
}

/** A row in the simulated delivery log. Nothing is transmitted — see `DispatchLog.note`. */
export interface CrewDispatchRecord {
  id: string;
  recipient_id: string;
  recipient_name: string;
  recipient_role: string;
  department: string;
  channel: "WHATSAPP" | "SMS" | "EMAIL";
  contact: string | null;
  call_time: string;
  // No SENT and no DELIVERED: neither ever happened. READ and ACKNOWLEDGED are only ever set by a
  // person clicking in this view, never stamped when the log is generated.
  status: "QUEUED" | "READ" | "ACKNOWLEDGED";
  simulated: boolean;
  queued_at: string;
  read_at: string | null;
  acknowledged_at: string | null;
  payload_preview: string;
}

export interface DispatchLog {
  day_id: string;
  dispatches: CrewDispatchRecord[];
  count: number;
  simulated: boolean;
  note: string;
  /** Present on the GET: who a broadcast would reach, derived without writing anything down. */
  roster?: DispatchRecipient[];
}

export interface ConstraintViolation {
  kind: string;
  hard: boolean;
  message: string;
  item_id: string | null;
  scene_id: string | null;
  cost_inr: number;
  minutes: number;
  fact_id: string | null;
  evidence_url: string | null;
}

export interface ScoreComponents {
  feasible: boolean;
  schedule_preservation: number;
  cost_impact: number;
  overtime_risk: number;
  company_moves: number;
  resource_conflicts: number;
  creative_compromise: number;
  confidence: number;
  total: number;
  estimated_extra_cost_inr: number;
  overtime_minutes: number;
  extra_company_moves: number;
  deferred_scene_ids: string[];
}

export interface RecoveryOption {
  id: string;
  label: string;
  title: string;
  strategy: string;
  origin: string;
  schedule: ScheduleItem[];
  deferred_scene_ids: string[];
  violations: ConstraintViolation[];
  feasible: boolean;
  score: ScoreComponents | null;
  rank: number | null;
  rejected_reason: string | null;
  explanation: string;
  trade_offs: string[];
  checks: { label: string; ok: boolean; hard: boolean; detail: string | null }[];
}

export interface Change {
  entity_type: string;
  entity_id: string;
  label: string;
  field: string;
  before: string | null;
  after: string | null;
  reason: string;
}

export interface ChangeSet {
  id: string;
  changes: Change[];
  created_at: string;
  approved_by: string | null;
  applied_at: string | null;
  summary: string;
  recovery_option_id: string | null;
  /** Approved, then rolled back. Both change sets stay on the record — the approval and the revert
   *  that undid it — so this is what separates "what we did" from "what still stands". */
  rescinded?: boolean;
}

export interface CoordinationAction {
  id: string;
  kind: string;
  title: string;
  details: string[];
  target: string | null;
  channel: string;
}

export interface ImpactAnalysis {
  disruption_id: string;
  directly_affected_item_ids: string[];
  violated_requirements: { item_id: string; scene_id: string; requirement_id: string | null; reason: string }[];
  implicated_resource_ids: string[];
  immovable: { item_id: string; scene_id: string; reason: string }[];
  movable: { item_id: string; scene_id: string; reason: string }[];
  cover_scene_ids: string[];
  summary: string;
}

export interface ActivityEvent {
  id: string;
  run_id: string | null;
  ts: string;
  kind: string;
  message: string;
  meta: Record<string, unknown>;
}

export interface WorkflowRun {
  id: string;
  project_id: string;
  kind: "PLANNING" | "RESCUE";
  status: "PENDING" | "RUNNING" | "AWAITING_APPROVAL" | "APPLIED" | "COMPLETED" | "FAILED";
  stage: string;
  mode: string;
  error: string | null;
  created_at: string;
  updated_at: string;
  planning: {
    scene_id: string;
    stage: string;
    requirements: Requirement[];
    questions: ResearchQuestion[];
    search_run_ids: string[];
    evidence: Evidence[];
    follow_up_rounds: number;
    used_memory: boolean;
    memory_entries_used: number;
    plan: ProductionPlan | null;
  } | null;
  rescue: {
    shoot_day_id: string;
    disruption_id: string;
    stage: string;
    baseline: ScheduleItem[];
    evidence: Evidence[];
    search_run_ids: string[];
    impact: ImpactAnalysis | null;
    options: RecoveryOption[];
    recommended_option_id: string | null;
    recommendation_rationale: string;
    changeset: ChangeSet | null;
    actions: CoordinationAction[];
    /** Set only when the run stopped because the disruption touches nothing on this day. Non-null is
     *  what a producer is owed instead of an empty option list, and the string is the sentence. */
    no_impact_reason?: string | null;
  } | null;
}

export interface ProjectSummary {
  id: string;
  title: string;
  synthetic: boolean;
  logline: string;
  base_city: string;
  scene_count: number;
  shoot_day_count: number;
  readiness: Record<string, number>;
  avg_readiness: number | null;
  shoot_days: { id: string; day_number: number; date: string; status: ShootDay["status"]; scene_count: number }[];
  updated_at: string;
}

export interface Project {
  id: string;
  title: string;
  synthetic: boolean;
  logline: string;
  base_city: string;
  country_code: string;
  scenes: Scene[];
  resources: Resource[];
  shoot_days: ShootDay[];
  plans: Record<string, ProductionPlan>;
  disruptions: Disruption[];
  changeset_ids: string[];
  /** Facts Parallel discovered about this production's locations. Sent on the project document. */
  location_facts: LocationFact[];
}

/** Which colour paper this issue of the sheet goes out on — the WGA revision ladder. */
export interface CallSheetRevision {
  index: number;
  name: string;
  hex: string;
  label: string;
  is_original: boolean;
}

export interface CallSheetWeather {
  reported: boolean;
  headline: string | null;
  /** Why there is no weather block, when there is none. Null whenever `reported`. */
  reason: string | null;
  description?: string;
  window: { start: string; end: string; dry_out_minutes: number; clear_at: string } | null;
  verification: { status: string; confidence_pct: number | null; summary: string | null } | null;
  sources: { claim: string; url: string; title: string | null; publish_date: string | null; authority: string }[];
}

export interface CallSheetHospitals {
  /** One per set the day works that has a dossier answer, in shooting order. */
  entries: { location: string; value: string; confidence: string | null; binding: string; source_url: string | null; source_title: string | null }[];
  /** Sets on this day whose dossier has not returned one — a gap a 1st AD needs to see. */
  sets_without_one: string[];
  reason: string | null;
}

export interface CallSheet {
  label: string;
  production: string;
  synthetic: boolean;
  day_number: number;
  /** The production's own day numbering, not how many days are on file — see `days_held`. */
  day_of_total: number;
  days_held: number[];
  revision: CallSheetRevision;
  date: string;
  unit_call: string;
  first_shot: string | null;
  estimated_wrap: string;
  standard_wrap: string;
  crew_size: number;
  status: string;
  sun: string;
  solar: {
    sunrise: string; sunset: string; solar_noon: string;
    civil_twilight_dawn: string; civil_twilight_dusk: string;
    golden_hour_dawn: [string, string]; golden_hour_dusk: [string, string];
    source: string;
  };
  weather: CallSheetWeather;
  /** Totalled only where every scene carries a count; `total_eighths` is null otherwise. */
  pages: { total_eighths: number | null; total_label: string | null; scene_count: number; unpriced_scenes: string[]; reason: string | null };
  schedule: { start: string; end: string; scene: string; heading: string; int_ext: string; time_of_day: string; minutes: number; cast: string[]; location: string; status: string; note: string | null; cover: boolean; unit: string; eighths: number | null; pages: string | null }[];
  /** Rows are in first-shot order, not cast-number order — `cast_number` is what ties each to the board. */
  cast: { cast_number: number | null; name: string; call: string; pickup: string; hmu: string; wardrobe: string; ready: string; on_set: string; wrap: string; scenes: string[]; note: string | null }[];
  /** Department heads this day's work implicates, with the radio channel each is raised on. */
  departments: { department: string; name: string; role: string | null; contact: string | null; channel: number | null; safety_critical: boolean }[];
  safety: {
    meeting: string;
    meeting_note: string;
    hazards: { item: string; why: string; owner: string | null }[];
    hospitals: CallSheetHospitals;
    standing_notes: string[];
  };
  /** Tomorrow, or null when this is the last day the production holds. */
  advance: { day_number: number; date: string; unit_call: string; status: string; scenes: { scene: string; heading: string; start: string }[]; sets: string[]; note: string | null } | null;
  signatures: {
    prepared_by: { name: string; role: string | null; contact: string | null } | null;
    prepared_by_reason: string | null;
    approved_by: string | null;
    approved_at_utc: string | null;
    approved_reason: string | null;
    generated_by: string;
  };
  equipment: { name: string; call: string; contact: string | null }[];
  transport: { vehicle: string; from: string; to: string; departure: string }[];
  meals: { lunch: { time: string; count: number; scheduled_gap: boolean }; dinner: { time: string | null; count: number } };
  locations: { name: string; contact: string | null; window: string; note: string | null }[];
  advisories: string[];
  notes: string | null;
}

/* Force Majeure claim packet — services/agent/scenepilot/services/insurance_dossier.py.
   Every field below is read back off persisted state; a section the production has no record for
   arrives as null rather than as a plausible number, which is why so much of this is nullable. */

export interface DossierViolation {
  kind: string;
  hard: boolean;
  description: string;
  scene_number: string | null;
  resource: string | null;
  minutes: number | null;
  amount_inr: number | null;
  fact_id: string | null;
  evidence_url: string | null;
}

export interface DossierCertifiedSource {
  search_run_id: string;
  provider: string;
  mode: string;
  status: string;
  replayed: boolean;
  ran_at_utc: string | null;
  objective: string;
  queries: string[];
  settings_sent: AdvancedSearchSettings | null;
  results: { url: string; title: string | null; publish_date: string | null; excerpt: string | null }[];
}

export interface DossierFinding {
  claim: string;
  source_url: string;
  source_title: string | null;
  publish_date: string | null;
  excerpt: string;
  authority: string;
  freshness: string;
  confidence: number;
  production_implication: string | null;
  search_run_id: string | null;
}

export interface DossierPerilEvidence {
  peril: {
    type: string;
    title: string;
    description: string;
    window_start: string | null;
    window_end: string | null;
    window_minutes: number | null;
    dry_out_minutes: number;
    affects_exteriors: boolean;
    affected_locations: string[];
    affected_resources: string[];
    reported_at_utc: string | null;
    reported_via: string;
    fixture_id: string | null;
    monitor_id: string | null;
    synthetic: boolean;
  };
  verification: {
    verified_by: string;
    status: string | null;
    summary: string | null;
    confidence: number | null;
    confidence_pct: number | null;
    searches_run: number;
    sources_returned: number;
    findings_retained: number;
  };
  certified_sources: DossierCertifiedSource[];
  analyst_findings: DossierFinding[];
}

export interface DossierMitigation {
  alternatives_evaluated: number;
  rejected_by_hard_constraint: number;
  rejected_alternatives: { label: string; title: string; strategy: string | null; origin: string; rejected_reason: string | null; violations: DossierViolation[] }[];
  alternatives_not_selected: { label: string; title: string; strategy: string | null; score_total: number | null; extra_cost_inr: number | null; carried_over: string[] }[];
  selected_option: {
    label: string;
    title: string;
    strategy: string | null;
    origin: string;
    rank: number | null;
    feasible: boolean;
    explanation: string | null;
    trade_offs: string[];
    checks: { label: string; ok: boolean; hard: boolean; detail: string | null }[];
    score: ScoreComponents | null;
    schedule: { scene_number: string; heading: string | null; start: string; end: string; minutes: number; location: string | null }[];
    carried_over: string[];
  } | null;
  rationale: string | null;
  decision: { approved_by: string | null; approved_at_utc: string; changeset_id: string; summary: string; changes: Change[] } | null;
}

export interface DossierCostDelta {
  currency: string;
  /* Whether the figure belongs to a schedule a producer signed, or one only recommended so far. */
  basis: "approved" | "recommended" | null;
  rates: { overtime_per_hour_inr: number; carry_over_per_scene_inr: number; company_move_inr: number };
  mitigation_cost_inr: number | null;
  overtime_minutes: number | null;
  extra_company_moves: number | null;
  line_items: DossierViolation[];
  unpriced_constraints: DossierViolation[];
  alternatives_priced: { label: string; feasible: boolean; extra_cost_inr: number | null; selected: boolean }[];
  /* Rows a claim needs that ScenePilot does not hold: named, explained and left blank on purpose. */
  not_in_production_state: { field: string; label: string; value: null; why: string }[];
}

export interface DossierConstraint {
  fact_id: string;
  label: string;
  value: string;
  location: string | null;
  binding: "HARD" | "SOFT" | "ADVISORY";
  confidence: string | null;
  reasoning: string;
  rule: ExternalRule | null;
  citations: { url: string; title: string | null; excerpt: string | null }[];
  accepted_by: string | null;
  accepted_at_utc: string | null;
  discovered_by: string;
  task_run_id: string;
  current_schedule_violations: DossierViolation[];
  rejected_schedules: { option_label: string; message: string }[];
}

export interface InsuranceDossier {
  dossier_id: string;
  claim_type: string;
  claim_status: "NO_PERIL_ON_RECORD" | "PERIL_REPORTED" | "AWAITING_PRODUCER_DECISION" | "MITIGATION_APPLIED";
  generated_at_utc: string;
  production: { id: string; title: string; base_city: string; country_code: string; currency: string; fictional: boolean };
  notice: string;
  shoot_day: { id: string; day_number: number; date: string; unit_call: string; standard_hours: number; hard_wrap: string; crew_size: number; status: string };
  peril_evidence: DossierPerilEvidence | null;
  proof_of_mitigation: DossierMitigation | null;
  cost_delta: DossierCostDelta;
  constraints_on_record: DossierConstraint[];
  summary: string;
  provenance: Record<string, string>;
}

export interface Health {
  ok: boolean;
  mode: "live" | "replay";
  record: boolean;
  gemini_model: string;
  gemini_configured: boolean;
  parallel_configured: boolean;
  parallel_search_mode: string;
  parallel_client_model: string;
  parallel_apis: string[];
  database: string;
  recordings: { gemini: number; parallel_search: number; parallel_extract: number };
  adk: string;
}

export interface ShootDayView {
  day: ShootDay;
  scenes: Record<string, Scene>;
  resources: Record<string, Resource>;
  disruption: Disruption | null;
  run: WorkflowRun | null;
  activity: ActivityEvent[];
  search_runs: SearchRun[];
  extract_runs: ExtractRun[];
  parallel_usage: ParallelUsage | null;
  /** `applicable` is false when the fixture provably cannot touch this day — a crane fault on a unit
   *  that calls no crane, an afternoon window on a night unit. The card is still sent so it can be
   *  shown disabled with its reason, rather than silently disappearing from a list of three. */
  fixtures: { id: string; type: string; title: string; description: string; applicable: boolean; not_applicable_reason: string | null }[];
  changesets: ChangeSet[];
  location_facts: LocationFact[];
  day_cost: DayCostCard;
  /** What this day delivered, once it has been wrapped. `null` while it is still ahead. */
  completion: DayCompletion | null;
  /** Cast, locations and equipment this day calls that declare no window naming it. */
  pending_clearance: PendingClearance[];
  /** scene_id → the day number it shoots on, across the whole production. */
  scene_days: Record<string, number>;
}

export interface SceneView {
  scene: Scene;
  plan: ProductionPlan | null;
  run: WorkflowRun | null;
  search_runs: SearchRun[];
  extract_runs: ExtractRun[];
  parallel_usage: ParallelUsage;
  activity: ActivityEvent[];
  brief: { id: string; source_kind: string; raw_text: string } | null;
}

/** A deep-Parallel integration (Task / FindAll / Memory) and, when it is off, how to turn it on. */
export interface FeatureState {
  enabled: boolean;
  env: string;
  cost: string;
  requires_key: boolean;
}

export interface MemoryEntry {
  kind: string; // task | monitor | findall
  ref_id: string;
  input_excerpt: string;
  output_excerpt: string;
  updated_at: string | null;
  status: string | null;
  matched_count: number | null;
  event_ids: string[];
}

export interface MemoryRead {
  id: string;
  project_id: string;
  scope_key: string;
  query: string;
  kind: string | null;
  limit: number;
  started_at: string;
  finished_at: string | null;
  status: "PENDING" | "OK" | "ERROR" | "UNAVAILABLE";
  entries: MemoryEntry[];
  error: string | null;
}

export interface MemoryView {
  read: MemoryRead;
  scope_key: string;
  writes_memory: { monitors: boolean; task: boolean; findall: boolean };
  recent: MemoryRead[];
}

export interface BasisCitation {
  url: string;
  title: string | null;
  excerpts: string[];
}

export interface FieldBasis {
  field: string;
  reasoning: string;
  confidence: string | null;
  citations: BasisCitation[];
}

export interface ExternalRule {
  kind: "TIME_WINDOW_BAN" | "ACTIVITY_BAN";
  window_start: string | null;
  window_end: string | null;
  activity: string | null;
}

export interface LocationFact {
  id: string;
  resource_id: string;
  task_run_id: string;
  key: string;
  label: string;
  value: string;
  binding: "HARD" | "SOFT" | "ADVISORY";
  confidence: string | null;
  reasoning: string;
  citations: BasisCitation[];
  rule: ExternalRule | null;
  accepted: boolean;
  accepted_at: string | null;
  accepted_by: string | null;
  rejected: boolean;
}

/** One thing a run reused from Parallel Memory, and the local run that first learned it. */
export interface RecalledEntry {
  memory_read_id: string;
  run_id: string | null;
  scope_key: string;
  query: string;
  kind: string;
  kind_label: string;
  ref_id: string;
  excerpt: string;
  input_excerpt: string;
  updated_at: string | null;
  origin: { kind: string; id: string; label: string; resource_id: string | null } | null;
  /** Set when the run that produced this is no longer in local state — said rather than hidden. */
  origin_note: string | null;
}

/** One resource-day cell of the conflict heatmap. `availability` is three-valued, not two. */
export interface HeatmapCell {
  booked: boolean;
  availability: "unconstrained" | "windowed" | "not_booked";
  booked_minutes: number;
  span_minutes: number;
  available_minutes: number;
  margin_minutes: number | null;
  /** Span held ÷ window cleared. Null where the resource is unconstrained — not zero. */
  pressure: number | null;
  conflicts: string[];
  held_from?: string;
  held_to?: string;
  detail: string;
}

export interface ConflictHeatmap {
  days: { shoot_day_id: string; day_number: number; date: string; status: string }[];
  rows: {
    resource_id: string;
    name: string;
    type: string;
    cast_number: number | null;
    cells: HeatmapCell[];
    days_booked: number;
    conflict_days: number;
    peak_pressure: number | null;
  }[];
  legend: Record<string, string>;
  provenance: string;
}

/** One risk on the production-wide register, ordered by severity x likelihood. */
export interface RegisterRisk {
  id: string;
  scene_id: string;
  scene_number: string;
  scene_heading: string;
  scheduled_on: { shoot_day_id: string; day_number: number; date: string }[];
  title: string;
  description: string;
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  likelihood: number;
  confidence: number;
  kind: string;
  mitigations: string[];
  evidence_ids: string[];
  exposure: number;
  /** The producer's half of the register. `OPEN` until somebody decides otherwise; a register nobody
   *  can work is a report, and in a real office the register *is* the decision log. */
  status: RiskStatus;
  owner: string | null;
  decision_note: string | null;
  decided_by: string | null;
  decided_at: string | null;
}

export interface RiskRegister {
  production: string;
  risks: RegisterRisk[];
  by_severity: Record<"CRITICAL" | "HIGH" | "MEDIUM" | "LOW", RegisterRisk[]>;
  counts: Record<"CRITICAL" | "HIGH" | "MEDIUM" | "LOW", number>;
  total: number;
  scenes_planned: number;
  scenes_total: number;
  /** Scenes nobody has planned. They have no register — not an empty one. */
  unplanned_scenes: { scene_id: string; scene_number: string; heading: string }[];
  coverage_note: string;
  empty_note: string | null;
  provenance: string;
}

/** One scene in a day's sides packet. `has_text` false means a named gap, never an empty page. */
export interface SidesScene {
  scene_id: string;
  scene_number: string;
  heading: string;
  int_ext: string;
  time_of_day: string;
  start: string;
  end: string;
  unit: string;
  location: string | null;
  cast: { cast_number: number | null; name: string }[];
  eighths: number | null;
  has_text: boolean;
  draft_heading?: string;
  action_text: string;
  dialogue: { character: string; parenthetical: string | null; text: string }[];
  page_start?: number;
  page_end?: number;
  gap_reason: string | null;
}

export interface SidesPacket {
  production: string;
  fictional: boolean;
  day_number: number;
  day_of_total: number;
  date: string;
  unit_call: string;
  status: string;
  scenes: SidesScene[];
  scene_count: number;
  scenes_with_text: number;
  complete: boolean;
  coverage_note: string | null;
  provenance: string;
}

/** One transport leg on a printed movement order. */
export interface MovementLeg {
  index: number;
  from_name: string;
  to_name: string;
  from_latitude: number | null;
  from_longitude: number | null;
  to_latitude: number | null;
  to_longitude: number | null;
  straight_line_km: number | null;
  /** Null where this production holds no travel time for the pair — no arrival is printed then. */
  travel_minutes: number | null;
  departure: string | null;
  arrival: string | null;
  wrap_at: string | null;
  next_shot_at: string | null;
  after_scene: string | null;
  before_scene: string | null;
  gap_minutes: number | null;
  slack_minutes: number | null;
  /** Set when the van is booked to leave before the scene it follows wraps — a real conflict. */
  departure_before_wrap_minutes: number | null;
  /** Minutes between wrap and departure. Under 15 means the loading time is being squeezed. */
  load_margin_minutes: number | null;
  load_squeezed: boolean;
  vehicle_name: string | null;
  transport_leg_id: string | null;
  untimed: boolean;
}

export interface MovementOrder {
  production: string;
  fictional: boolean;
  day_number: number;
  day_of_total: number;
  date: string;
  status: string;
  unit_call: string;
  legs: MovementLeg[];
  locations: { id: string; name: string; latitude: number | null; longitude: number | null; scene_numbers: string[]; first_start: string; last_end: string }[];
  move_count: number;
  total_straight_line_km: number | null;
  total_travel_minutes: number | null;
  locations_missing_coordinates: string[];
  basis: { distance: string; travel_minutes: string; coordinates: string };
  to_be_completed: { field: string; reason: string }[];
  note: string | null;
}

/** One row of a day's completion record — a scene as it was actually shot or carried. */
export interface CompletionRow {
  item_id: string;
  scene_id: string;
  scene_number: string;
  heading: string;
  start: string;
  end: string;
  minutes: number;
  eighths: number | null;
  unit: string;
  location: string | null;
  cast: string[];
  status: string;
  note: string | null;
}

/**
 * What a wrapped day delivered. `null` on every day still ahead — a day that has not been shot has
 * no delivery to report, and a panel printing one would be reporting an estimate as a fact.
 *
 * The engine has computed this on every day payload since `completion.py` shipped, and until the
 * wrap flow existed nothing could ever set the `WRAPPED` status it needs, so nothing ever read it.
 */
export interface DayCompletion {
  wrapped: true;
  unit_call: string;
  first_shot: string;
  wrap: string;
  elapsed_minutes: number;
  standard_minutes: number;
  overtime_minutes: number;
  overtime_cost_inr: number;
  carry_over_cost_inr: number;
  cost_inr: number;
  scenes_completed: CompletionRow[];
  scenes_carried: CompletionRow[];
  minutes_shot: number;
  eighths_shot: number | null;
  locations: string[];
  units: string[];
  summary: string;
}

/** Where a risk stands. `OPEN` is the engine's own state — everything else is a producer's. */
export type RiskStatus = "OPEN" | "ACCEPTED" | "MITIGATING" | "CLOSED";

/** A monitor-detected disruption nobody has confirmed yet, listed production-wide. */
export interface DraftDisruption {
  disruption: Disruption;
  shoot_day_id: string;
  /** Null where the draft names a shoot day the production no longer has — unreachable on seeded
   *  data, and the server reports it rather than dropping the draft, so the type says so. */
  day_number: number | null;
  date: string | null;
  monitor_id: string | null;
  detected_at: string | null;
}

/** Someone a day needs who has no availability window naming it — the day cannot be validated
 *  against them, so the validator reads them as unavailable rather than as a quiet yes. */
export interface PendingClearance {
  resource_id: string;
  name: string;
  type: string;
  reason: string;
}

/** The Daily Production Report. Only ever issued for a wrapped day — see services/dpr.py. */
export interface DailyProductionReport {
  production: string;
  fictional: boolean;
  day_number: number;
  day_of_total: number;
  date: string;
  status: string;
  unit_call: string;
  first_shot: string;
  wrap: string;
  elapsed_minutes: number;
  standard_minutes: number;
  hard_wrap: string;
  crew_size: number;
  units: string[];
  locations: string[];
  scenes_completed: CompletionRow[];
  scenes_carried: CompletionRow[];
  minutes_shot: number;
  pages: { shot_eighths: number | null; shot_label: string | null; scheduled_eighths: number | null; scheduled_label: string | null; reason: string | null };
  cast_worked: { cast_id: string; cast_number: number | null; name: string; scenes: string[] }[];
  /** The same cost object the day page prints for this day, on its record branch. */
  cost: DayCostCard;
  advance: { day_number: number; date: string; unit_call: string; status: string; scenes: { scene: string; heading: string; start: string }[]; sets: string[]; note: string | null } | null;
  to_be_completed: { field: string; reason: string }[];
  summary: string;
  provenance: string;
}

export interface DayCostLine {
  key: string;
  label: string;
  cost_inr: number;
  minutes: number;
  detail: string;
}

/** One day's consequence cost. `not_priced` names what was deliberately left out, and why. */
export interface DayCostCard {
  basis: "projected" | "record";
  labor_pack: string | null;
  lines: DayCostLine[];
  total_inr: number;
  not_priced: { key: string; reason: string }[];
  currency: string;
}

export interface CostStripView {
  days: (DayCostCard & { shoot_day_id: string; day_number: number; date: string; status: string })[];
  total_inr: number;
  currency: string;
  unpriced_notes: string[];
}

/** One hour of a researched precipitation timeline, with the Basis that hour was answered under. */
export interface WeatherHour {
  field: string;
  hour: number;
  start_min: number;
  label: string;
  value: string;
  /** null when the source stated a condition but no figure — the strip draws a marker, not a height. */
  precip_pct: number | null;
  confidence: string | null;
  reasoning: string;
  citations: BasisCitation[];
}

export interface WeatherTimeline {
  task_run_id: string;
  status: "OK" | "REPLAY";
  replayed: boolean;
  processor: string;
  researched_at: string | null;
  shoot_day_id: string | null;
  window: { start_min: number; end_min: number };
  day_summary: { value: string; confidence: string | null; reasoning: string; citations: BasisCitation[] } | null;
  /** Only the hours a source actually answered. Gaps are gaps — never zero-filled. */
  hours: WeatherHour[];
  cited_hours: number;
}

export interface TaskRun {
  id: string;
  project_id: string | null;
  resource_id: string | null;
  shoot_day_id: string | null;
  purpose: string;
  processor: string;
  input: string;
  started_at: string;
  finished_at: string | null;
  status: "PENDING" | "OK" | "ERROR" | "REPLAY";
  provider_run_id: string | null;
  interaction_id: string | null;
  output: Record<string, unknown>;
  basis: FieldBasis[];
  error: string | null;
  replayed: boolean;
}

/** A snapshot monitor noticed that a fact this production planned against has moved. */
export interface FactChange {
  id: string;
  resource_id: string;
  detected_by: "monitor" | "preflight";
  monitor_id: string | null;
  task_run_id: string | null;
  event_id: string;
  event_date: string | null;
  key: string;
  label: string;
  fact_id: string | null;
  old_value: string;
  new_value: string;
  binding: "HARD" | "SOFT" | "ADVISORY";
  confidence: string | null;
  reasoning: string;
  citations: BasisCitation[];
  rule: ExternalRule | null;
  old_accepted: boolean;
  old_binds: boolean;
  detected_at: string;
  status: "PENDING" | "ADOPTED" | "DISMISSED";
  decided_at: string | null;
  decided_by: string | null;
  simulated: boolean;
}

/** Re-verifying a day's locations against Parallel before the day locks. */
export interface PreflightResult extends DossierView {
  checked: { resource_id: string; name: string; status: string; changes: number; task_run_id?: string; detail?: string }[];
  unresearched: { id: string; name: string }[];
  changes: FactChange[];
  urgent: number;
}

/** The orchestrators as ADK `Workflow` graphs — served by the engine, not drawn by hand. */
export interface AgentGraph {
  name: string;
  description: string;
  start: string[];
  terminal: string[];
  nodes: { name: string; description: string }[];
  edges: { from: string; to: string; route: string | null }[];
}

export interface AgentGraphCatalog {
  runtime: string;
  graphs: AgentGraph[];
}

export interface DossierView {
  facts: LocationFact[];
  task_runs: TaskRun[];
  watches: MonitorRecord[];
  fact_changes: FactChange[];
  locations: { id: string; name: string; fact_count: number; binding_count: number; watched: boolean; pending_changes: number; replayed: boolean }[];
  processor: string;
  live_watch_possible: boolean;
  task_run?: TaskRun;
}

export interface VendorCandidate {
  id: string;
  findall_run_id: string;
  name: string;
  url: string;
  description: string;
  match_status: string;
  match_reasons: string[];
  citations: BasisCitation[];
  phone: string | null;
  address: string | null;
  distance_km: number | null;
  day_rate_band: string | null;
  selected: boolean;
}

export interface FindAllRun {
  id: string;
  project_id: string | null;
  resource_id: string | null;
  shoot_day_id: string | null;
  mode: "entity_search" | "findall";
  generator: string | null;
  objective: string;
  match_limit: number;
  started_at: string;
  finished_at: string | null;
  status: "PENDING" | "RUNNING" | "OK" | "ERROR" | "REPLAY";
  provider_findall_id: string | null;
  termination_reason: string | null;
  enriched: boolean;
  candidates: VendorCandidate[];
  warnings: ApiWarning[];
  error: string | null;
}

export interface SubstitutesView {
  findall_runs: FindAllRun[];
  mode: string;
  match_limit: number;
  findall_run?: FindAllRun;
}

const BASE = typeof window === "undefined" ? process.env.AGENT_URL || "http://localhost:8000" : "";

/** FastAPI `detail` is a plain string for most errors, a dict for a disabled paid feature (`api/deps.py`), and a list for 422s. */
function detailText(detail: unknown): string | null {
  if (typeof detail === "string") return detail.trim() || null;
  if (Array.isArray(detail)) {
    const parts = detail.map(detailText).filter(Boolean);
    return parts.length ? parts.join("; ") : null;
  }
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    for (const key of ["message", "msg", "error"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return null;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { ...init, headers: { "content-type": "application/json", ...(init?.headers || {}) }, cache: "no-store" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = detailText(body?.detail) ?? detailText(body) ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => call<Health>("/api/health"),
  projects: () => call<ProjectSummary[]>("/api/projects"),
  project: (id: string) => call<{ project: Project; runs: { id: string; kind: string; status: string; stage: string; created_at: string; scene_id: string | null; shoot_day_id: string | null }[] }>(`/api/projects/${id}`),
  scene: (projectId: string, sceneId: string) => call<SceneView>(`/api/projects/${projectId}/scenes/${sceneId}`),
  // `?? null`, not `|| null`: an empty string is the producer having emptied the box, which is not
  // the same request as leaving it untouched, and `||` collapsed the two. The server tells them
  // apart — see `PlanRequest.text` — so a cleared box no longer plans on the text just deleted.
  planScene: (projectId: string, sceneId: string, text?: string, useMemory = false) => call<{ run_id: string; already_running?: boolean }>(`/api/projects/${projectId}/scenes/${sceneId}/plan`, { method: "POST", body: JSON.stringify({ text: text ?? null, use_memory: useMemory }) }),
  createScene: (projectId: string, body: { number: string; text: string; heading?: string; int_ext?: string; time_of_day?: string; estimated_minutes?: number }) => call<{ scene: Scene }>(`/api/projects/${projectId}/scenes`, { method: "POST", body: JSON.stringify(body) }),
  run: (runId: string) => call<{ run: WorkflowRun; activity: ActivityEvent[]; search_runs: SearchRun[]; extract_runs: ExtractRun[]; parallel_usage: ParallelUsage }>(`/api/runs/${runId}`),
  extractSource: (runId: string, body: { url: string; search_run_id?: string | null; evidence_id?: string | null }) => call<{ extract_run: ExtractRun; cached: boolean }>(`/api/runs/${runId}/extract`, { method: "POST", body: JSON.stringify(body) }),
  extractRun: (id: string) => call<ExtractRun>(`/api/extract-runs/${id}`),
  shootDay: (projectId: string, dayId: string) => call<ShootDayView>(`/api/projects/${projectId}/shoot-days/${dayId}`),
  reportDisruption: (projectId: string, dayId: string, body: Record<string, unknown>) => call<{ run_id: string; disruption_id: string }>(`/api/projects/${projectId}/shoot-days/${dayId}/disruptions`, { method: "POST", body: JSON.stringify(body) }),
  approve: (runId: string, optionId: string) => call<{ run: WorkflowRun; changeset: ChangeSet; actions: CoordinationAction[]; day: ShootDay }>(`/api/runs/${runId}/approve`, { method: "POST", body: JSON.stringify({ option_id: optionId, approved_by: "producer" }) }),
  monitors: (projectId: string, dayId: string) => call<MonitorsView>(`/api/projects/${projectId}/shoot-days/${dayId}/monitors`),
  createMonitors: (projectId: string, dayId: string) => call<{ monitors: MonitorRecord[] }>(`/api/projects/${projectId}/shoot-days/${dayId}/monitors`, { method: "POST" }),
  simulateMonitorEvent: (projectId: string, dayId: string, kind: string) => call<{ disruption: Disruption | null }>(`/api/projects/${projectId}/shoot-days/${dayId}/monitors/simulate?kind=${kind}`, { method: "POST" }),
  confirmDisruption: (projectId: string, disruptionId: string, body: { window_start?: string; window_end?: string; dry_out_minutes?: number }) => call<{ run_id: string }>(`/api/projects/${projectId}/disruptions/${disruptionId}/confirm`, { method: "POST", body: JSON.stringify(body) }),
  dismissDisruption: (projectId: string, disruptionId: string) => call<{ ok: boolean }>(`/api/projects/${projectId}/disruptions/${disruptionId}/dismiss`, { method: "POST" }),
  callSheet: (projectId: string, dayId: string) => call<{ current: CallSheet; baseline: CallSheet | null; changeset: ChangeSet | null; run_id: string | null }>(`/api/projects/${projectId}/shoot-days/${dayId}/call-sheet`),
  reset: (projectId: string) => call<{ ok: boolean }>(`/api/projects/${projectId}/reset`, { method: "POST" }),
  features: () => call<{ features: Record<string, FeatureState> }>("/api/features"),
  agentGraph: () => call<AgentGraphCatalog>("/api/agent-graph"),
  dossiers: (projectId: string, resourceId?: string) => call<DossierView>(`/api/projects/${projectId}/dossiers${resourceId ? `?resource_id=${resourceId}` : ""}`),
  researchLocation: (projectId: string, resourceId: string, date?: string) => call<DossierView>(`/api/projects/${projectId}/resources/${resourceId}/dossier${date ? `?date=${date}` : ""}`, { method: "POST" }),
  substitutes: (projectId: string, resourceId?: string) => call<SubstitutesView>(`/api/projects/${projectId}/substitutes${resourceId ? `?resource_id=${resourceId}` : ""}`),
  findSubstitutes: (projectId: string, resourceId: string, dayId?: string) => call<SubstitutesView>(`/api/projects/${projectId}/resources/${resourceId}/substitutes${dayId ? `?shoot_day_id=${dayId}` : ""}`, { method: "POST" }),
  selectVendor: (findallRunId: string, vendorId: string) => call<{ findall_run: FindAllRun }>(`/api/findall-runs/${findallRunId}/select/${vendorId}`, { method: "POST" }),
  decideFact: (projectId: string, factId: string, decision: "accept" | "reject") => call<DossierView & { fact: LocationFact }>(`/api/projects/${projectId}/facts/${factId}/${decision}`, { method: "POST", body: JSON.stringify({ accepted_by: "producer" }) }),
  watchLocation: (projectId: string, resourceId: string) => call<DossierView & { monitor: MonitorRecord }>(`/api/projects/${projectId}/resources/${resourceId}/watch`, { method: "POST" }),
  simulateSnapshot: (projectId: string, resourceId: string) => call<DossierView & { changes: FactChange[] }>(`/api/projects/${projectId}/resources/${resourceId}/watch/simulate`, { method: "POST" }),
  preflightDay: (projectId: string, dayId: string) => call<PreflightResult>(`/api/projects/${projectId}/shoot-days/${dayId}/preflight`, { method: "POST" }),
  revertRecovery: (runId: string, reason: string) =>
    call<{ changeset: ChangeSet; reverted_changeset_id: string; day: ShootDay; note: string }>(`/api/runs/${runId}/revert`, {
      method: "POST",
      body: JSON.stringify({ reason, reverted_by: "producer" }),
    }),
  commitPlacement: (projectId: string, dayId: string, sceneId: string) =>
    call<{ changeset: ChangeSet; day: ShootDay; added_overtime_cost_inr: number; notes: string[] }>(
      `/api/projects/${projectId}/shoot-days/${dayId}/commit-placement`,
      { method: "POST", body: JSON.stringify({ scene_id: sceneId, committed_by: "producer" }) },
    ),
  commitPickupDay: (projectId: string, dayId: string, deferredSceneIds: string[]) =>
    call<{ changeset: ChangeSet; day: ShootDay; pending_clearance: { resource_id: string; name: string; type: string; reason: string }[]; clearance_note: string | null }>(
      `/api/projects/${projectId}/shoot-days/${dayId}/commit-pickup-day`,
      { method: "POST", body: JSON.stringify({ deferred_scene_ids: deferredSceneIds, committed_by: "producer" }) },
    ),
  // --- Closing the states a producer could enter and not leave ------------------------------- //
  /** End a rescue without approving anything. The disruption stays on the record; the day comes back. */
  standDown: (runId: string, reason: string) =>
    call<{ run: WorkflowRun; day: ShootDay }>(`/api/runs/${runId}/stand-down`, {
      method: "POST",
      body: JSON.stringify({ reason, stood_down_by: "producer" }),
    }),
  /** Close a day out. `SHOT` puts a strip in the can, `CARRIED` hands it to another day. */
  wrapDay: (projectId: string, dayId: string, body: { items: { item_id: string; outcome: "SHOT" | "CARRIED"; actual_end?: string | null; note?: string | null }[]; camera_wrap?: string | null }) =>
    call<{ day: ShootDay; completion: DayCompletion | null; changeset: ChangeSet | null }>(`/api/projects/${projectId}/shoot-days/${dayId}/wrap`, {
      method: "POST",
      body: JSON.stringify({ ...body, wrapped_by: "producer" }),
    }),
  /** Keep a board the producer nudged by hand. Re-validated under the enforced pack, never the what-if. */
  commitSchedule: (projectId: string, dayId: string, items: { item_id: string; start: string; end: string }[], reason?: string) =>
    call<{ changeset: ChangeSet; day: ShootDay; notes: string[] }>(`/api/projects/${projectId}/shoot-days/${dayId}/commit-schedule`, {
      method: "POST",
      body: JSON.stringify({ items, reason: reason ?? null, committed_by: "producer" }),
    }),
  /** Book a resource onto a day. The one write that was missing behind every "not cleared" blank. */
  clearResource: (projectId: string, resourceId: string, body: { shoot_day_id: string; start: string; end: string; note?: string | null }) =>
    call<{ resource: Resource; day: ShootDay }>(`/api/projects/${projectId}/resources/${resourceId}/availability`, {
      method: "POST",
      body: JSON.stringify({ ...body, cleared_by: "producer" }),
    }),
  releaseResource: (projectId: string, resourceId: string, shootDayId: string) =>
    call<{ resource: Resource; day: ShootDay }>(`/api/projects/${projectId}/resources/${resourceId}/availability?shoot_day_id=${encodeURIComponent(shootDayId)}`, { method: "DELETE" }),
  /** Work the register: a risk somebody owns and has decided about is a decision log, not a printout. */
  decideRisk: (projectId: string, riskId: string, body: { status: RiskStatus; owner?: string | null; note?: string | null }) =>
    call<RiskRegister>(`/api/projects/${projectId}/risks/${riskId}/decide`, {
      method: "POST",
      body: JSON.stringify({ ...body, decided_by: "producer" }),
    }),
  cancelMonitor: (projectId: string, monitorId: string) =>
    call<{ monitor: MonitorRecord }>(`/api/projects/${projectId}/monitors/${monitorId}/cancel`, { method: "POST" }),
  forgetMemory: (projectId: string) => call<{ ok: boolean }>(`/api/projects/${projectId}/memory?confirm=true`, { method: "DELETE" }),
  /** Undo a vendor choice. `select` sets one and clears the rest; this clears them all. */
  unselectVendor: (findallRunId: string) => call<{ findall_run: FindAllRun }>(`/api/findall-runs/${findallRunId}/select`, { method: "DELETE" }),
  /** Every draft a monitor raised across the production, so one firing on Day 6 is visible from Day 4. */
  draftDisruptions: (projectId: string) => call<{ drafts: DraftDisruption[] }>(`/api/projects/${projectId}/draft-disruptions`),
  conflictHeatmap: (projectId: string) => call<ConflictHeatmap>(`/api/projects/${projectId}/conflict-heatmap`),
  riskRegister: (projectId: string) => call<RiskRegister>(`/api/projects/${projectId}/risk-register`),
  sides: (projectId: string, dayId: string) => call<{ sides: SidesPacket }>(`/api/projects/${projectId}/shoot-days/${dayId}/sides`),
  movementOrder: (projectId: string, dayId: string) => call<{ movement_order: MovementOrder }>(`/api/projects/${projectId}/shoot-days/${dayId}/movement-order`),
  dpr: (projectId: string, dayId: string) => call<{ dpr: DailyProductionReport }>(`/api/projects/${projectId}/shoot-days/${dayId}/dpr`),
  parallelSpend: (projectId: string) => call<{ usage: ParallelUsage; mode: string; budget: Record<string, unknown> }>(`/api/projects/${projectId}/parallel-spend`),
  costStrip: (projectId: string) => call<CostStripView>(`/api/projects/${projectId}/cost-strip`),
  weatherTimeline: (projectId: string, dayId: string) => call<{ timeline: WeatherTimeline | null }>(`/api/projects/${projectId}/shoot-days/${dayId}/weather-timeline`),
  researchWeather: (projectId: string, dayId: string) => call<{ task_run: TaskRun; timeline: WeatherTimeline | null }>(`/api/projects/${projectId}/shoot-days/${dayId}/weather-timeline`, { method: "POST" }),
  decideFactChange: (projectId: string, changeId: string, decision: "adopt" | "dismiss") => call<DossierView & { change: FactChange }>(`/api/projects/${projectId}/fact-changes/${changeId}/${decision}`, { method: "POST", body: JSON.stringify({ decided_by: "producer" }) }),
  memory: (projectId: string, query: string, limit = 10) => call<MemoryView>(`/api/projects/${projectId}/memory?query=${encodeURIComponent(query)}&limit=${limit}`),
  evictMemory: (projectId: string, kind: string, refId: string) => call<{ ok: boolean }>(`/api/projects/${projectId}/memory/evict`, { method: "POST", body: JSON.stringify({ kind, ref_id: refId }) }),
  uploadScreenplay: (projectId: string, text: string, formatHint: string = "auto", syncScenes: boolean = true) =>
    call<{ scenes: ParsedSceneData[]; scene_count: number; synced_count: number }>(`/api/projects/${projectId}/screenplay/upload`, {
      method: "POST",
      body: JSON.stringify({ text, format_hint: formatHint, sync_scenes: syncScenes }),
    }),
  getScreenplayScenes: (projectId: string) =>
    call<{ scenes: ParsedSceneData[]; count: number }>(`/api/projects/${projectId}/screenplay/scenes`),
  breakdownSceneElements: (projectId: string, sceneId: string) =>
    call<{ scene_id: string; elements: BreakdownElement[]; stop_conditions: string[]; continuity_notes: string[] }>(`/api/projects/${projectId}/scenes/${sceneId}/breakdown-elements`, {
      method: "POST",
    }),
  getDOOD: (projectId: string) =>
    call<DoodView>(`/api/projects/${projectId}/dood`),
  oneLiner: (projectId: string) => call<OneLinerView>(`/api/projects/${projectId}/one-liner`),
  productionLog: (projectId: string) => call<ProductionLog>(`/api/projects/${projectId}/activity`),
  getEphemeris: (projectId: string, dayId: string) =>
    call<{ day_id: string; date: string; profile: SolarLightingProfile }>(`/api/projects/${projectId}/shoot-days/${dayId}/ephemeris`),
  getLaborRules: (projectId: string) =>
    call<{ active_preset: string; presets: Record<string, LaborRulePack> }>(`/api/projects/${projectId}/labor-rules`),
  simulateStripMove: (projectId: string, dayId: string, items: ScheduleItem[], laborPreset: string = "DGA_SAG") =>
    call<StripSimulationResult>(`/api/projects/${projectId}/shoot-days/${dayId}/simulate-strip-move`, {
      method: "POST",
      body: JSON.stringify({ items, labor_preset: laborPreset }),
    }),
  getMultiDayPlan: (projectId: string, dayId: string, deferredSceneIds: string) =>
    call<MultiDayRipplePlan>(`/api/projects/${projectId}/shoot-days/${dayId}/multiday-plan?deferred_scene_ids=${encodeURIComponent(deferredSceneIds)}`),
  dispatchCallSheet: (projectId: string, dayId: string, channels: string[] = ["WHATSAPP", "SMS", "EMAIL"]) =>
    call<DispatchLog>(`/api/projects/${projectId}/shoot-days/${dayId}/dispatch`, {
      method: "POST",
      body: JSON.stringify({ channels }),
    }),
  getDispatches: (projectId: string, dayId: string) =>
    call<DispatchLog>(`/api/projects/${projectId}/shoot-days/${dayId}/dispatch`),
  readDispatch: (projectId: string, dayId: string, dispatchId: string) =>
    call<CrewDispatchRecord>(`/api/projects/${projectId}/shoot-days/${dayId}/dispatch/${dispatchId}/read`, {
      method: "POST",
    }),
  ackDispatch: (projectId: string, dayId: string, dispatchId: string) =>
    call<CrewDispatchRecord>(`/api/projects/${projectId}/shoot-days/${dayId}/dispatch/${dispatchId}/ack`, {
      method: "POST",
    }),
  repingDispatch: (projectId: string, dayId: string) =>
    call<{ status: string; repinged_count: number; dispatches: CrewDispatchRecord[] }>(
      `/api/projects/${projectId}/shoot-days/${dayId}/dispatch/re-ping`,
      { method: "POST" }
    ),
  getInsuranceDossier: (projectId: string, dayId: string) =>
    call<InsuranceDossier>(`/api/projects/${projectId}/shoot-days/${dayId}/insurance-dossier`),
  exportMmsxUrl: (projectId: string, dayId: string) =>
    `/api/projects/${projectId}/shoot-days/${dayId}/export/mmsx`,
};

export const fmtTime = (iso: string) => {
  try {
    return new Date(iso).toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
};

export const toMin = (hhmm: string) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

export const inr = (n: number) => `₹${n.toLocaleString("en-IN")}`;

/** Parallel excerpts are markdown, but some pages leak HTML tags/entities; clean for display only. */
export const cleanExcerpt = (s: string) =>
  s
    .replace(/<[^>]+>/g, "")
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
