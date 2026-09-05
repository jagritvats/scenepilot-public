# ScenePilot — Production Intelligence & Resilient Field Coordination 🎬

> Built with **Google ADK + Gemini 3.5** and the **Parallel Runtime API Suite (Search, Extract, Task, FindAll, Memory, Monitor)**.  
> **614 Automated Tests Passing** · **Next.js 16 (Turbopack)** · **Deterministic Constraint Engine**

ScenePilot is an intelligent production control room for film and television crews. It answers two connected operational questions:

1. **Planning & Creative Intelligence** — *Can we actually shoot this scene, where, and what must be resolved first?*  
   Fountain / FDX screenplay ingestion → **eighths-of-a-page estimator** (a line/word-count heuristic, not physical page measurement) → Gemini 3.5 extracts **32 standard breakdown element categories** with physical safety stop-conditions → Day-Out-Of-Days (DOOD) cast matrix → **real Parallel searches** → graded evidence → **autonomous follow-up research** → grounded production plan separating **FACT / INFERENCE / RECOMMENDATION / UNKNOWN**.
2. **Shoot Rescue & Field Coordination** (Hero Workflow) — *Something changed in the real world. What is affected, and what is the safest feasible recovery?*  
   Disruption → **Parallel verification of external situation** → NOAA astronomical ephemeris (golden hours) → pluggable **union labor rule packs** (FWICE/CINTAA for this Mumbai production — flat meal penalties, 10 h turnaround; DGA/SAG-AFTRA available with compounding penalties and 12 h) → **deterministic hard constraint rejection** (police permits, wet-stunt hazards) → **multi-day cascading ripple solver** (downstream absorption or dedicated pickup unit) → Gemini AI 1st AD rationale → **producer approval** → applied `ChangeSet` with audit trail → **DGA Call Sheet 2.0 & a multi-channel WhatsApp/SMS/Email dispatch log** (messages are composed from the sheet and queued — nothing is transmitted; read and confirmed states are set by hand to demonstrate the tracking view).

The differentiator is *external evidence + structured production state + constraint reasoning + autonomous recovery planning + human-approved coordinated action* — not a chatbot, not a movie generator.

---

## Start here

**Live demo:** https://scenepilot-web-483371182667.asia-south1.run.app — open straight on the hero day: https://scenepilot-web-483371182667.asia-south1.run.app/projects/proj_nightfall/days/day_4

**The 60-second path.** Open the hosted URL → **Shoot Day 4** → click **Rain expected 13:00–17:00** →
watch the activity feed verify it through Parallel Search → read the two options the engine
**rejects** and why → **Approve recovery A** → the stripboard re-lays itself and the call sheet
reissues **white → blue**. Then open the **Production log** for the audit trail of everything you
just did.

**If you have another two minutes.** Press **Ctrl+K** anywhere and type a scene number, a performer or
a phrase from the log. Then, on Day 4: accept the cited curfew in the **location dossier** and watch a
recovery option turn red *where it stands*, with the statute's own sentence marked inside the citation
it was parsed from. Open the **sides** for Day 4 (complete) and then for Day 6 (which prints named gaps,
because this production's Studio holds a five-scene excerpt and says so). Ask Day 3 for a **Daily
Production Report** and then ask Day 4 for one — it refuses, and tells you the call sheet is the
document for a day that has not happened yet.

**Where the Parallel APIs are actually called at runtime.** All six are called at runtime, each behind a cost-labelled feature gate, each recorded as an observable run in the database:

| Parallel API | Runtime call site | What it grounds |
|---|---|---|
| **Search** | [`tools/parallel_search.py:192`](services/agent/scenepilot/tools/parallel_search.py#L192) — `self.session.client.search(...)` | Verifying a reported disruption against the outside world before the schedule is touched |
| **Task** | [`tools/parallel_task.py:300`](services/agent/scenepilot/tools/parallel_task.py#L300) — `client.task_run.create(...)` | Location dossiers and hourly weather timelines, with per-field citations, reasoning and calibrated confidence (*Basis*) |
| **Extract** | [`tools/parallel_extract.py:110`](services/agent/scenepilot/tools/parallel_extract.py#L110) — `client.extract(...)` | Reading the page behind a claim rather than trusting a snippet |
| **FindAll** | [`tools/parallel_findall.py:265`](services/agent/scenepilot/tools/parallel_findall.py#L265) — `client.beta.findall.entity_search(...)` | Replacement vendors when a booked resource falls through |
| **Memory** | [`tools/parallel_memory.py:112`](services/agent/scenepilot/tools/parallel_memory.py#L112) — `client.beta.memory.retrieve(...)` | What this production already learned, scoped per project |
| **Monitor** | [`tools/parallel_monitor.py:60`](services/agent/scenepilot/tools/parallel_monitor.py#L60) — `client.monitor.create(...)` | Watching a location's facts for change between planning and shooting |

The wrapper around each is deliberately thin and **observable**: every call writes a `SearchRun` /
`TaskRun` / `FindAllRun` row with the exact request, the queries as sent, the sources as returned and
the usage Parallel reported — which is what the **🌐 Parallel Console** in the top bar reads.

**The one thing worth knowing before you open the demo.** Every value on screen traces to an API
response, the seed, or a deterministic computation over those. Where the production genuinely does
not hold a fact, the UI prints a **named blank with the reason** rather than a plausible number — the
insurance packet's policy fields, a set whose dossier returned no hospital, a day whose page counts
are incomplete.
That rule cost four panels and three hardcoded constants during the build, and it is why a hold day with no contracted rate behind it is counted and left unpriced rather than defaulted.

---

## What ScenePilot Delivers

- 🚀 **Phase 1: Creative & Script Intelligence**: Fountain & Final Draft XML (.fdx) parsers with a 1/8th-page estimator (line and word counts blended against standard screenplay layout — an approximation, not a rendered-page count), `CreativeBreakdownAgent` extracting 32 breakdown element categories with safety stop-conditions, Day-Out-Of-Days (DOOD) cast matrix, and Next.js Screenplay Studio.
- ☀️ **Phase 2: Environmental Grounding & Interactive Scheduling**: NOAA Astronomical Ephemeris solar engine, pluggable DGA & FWICE labor rules with compounding meal penalties and 12-hour turnaround rest, multi-unit concurrency, the municipal-curfew confidence gate that turns a cited Parallel fact into a HARD or SOFT constraint (`services/dossier.py`), and the Interactive Stripboard Studio with live strip nudges.
- 🚨 **Phase 3: Autonomous Multi-Day Rescue & Coordinated Field Dispatch**: Multi-day cascading ripple solver across downstream days (or synthesizing dedicated Pickup Unit days), the AI 1st AD pair `rescue_planner` (proposes orderings) and `rescue_explainer` (writes the rationale), DGA Call Sheet 2.0, and a multi-channel WhatsApp/SMS/Email dispatch log built from the call sheet — queued, never transmitted, with read/confirmed states marked by hand and labelled as simulated.
- 📄 **The paper a production actually runs on**: beside the call sheet, four more printable documents built from the same committed state — a **Daily Production Report** (issued only for a wrapped day; it refuses a day that has not happened, and says why), a **movement order** (legs, departures and arrivals from the production's own travel times, with no invented road route), a **sides packet** (the day's pages in shooting order, where a scene the Studio holds no text for prints as a *named gap* rather than a missing page), and a printable **force-majeure claim packet**.
- 🔁 **Decisions that go both ways**: a producer can now **commit a downstream placement** or **materialise the synthesised pickup day** into the schedule — and **revert an applied recovery**, as its own audit-trailed change set, with the original approval left standing on the record. A committed pickup day is deliberately *uncleared*: it names the cast and locations nobody has booked onto it rather than inventing their availability.
- 🌦️ **Hourly weather, per hour, with its own citations**: a Parallel **Task** run that asks for each hour separately, so an answered hour carries its own sources, reasoning and calibrated confidence, drawn over the disruption scrubber. An hour no source covered is left blank, and a day where *no* hour resolved draws no axis at all — a dry hour and an unresearched hour must never look alike. Mumbai currently answers at day resolution rather than hourly, so the demo shows exactly that: a cited day summary, and the hourly ask still on offer.
- 📊 **Where the production is fragile, and what a day costs**: a **fact-drift inbox** (what moved in the world since this production last looked), a **risk register** ordered by severity × likelihood, a **booking-pressure heatmap** (resource × day), and a **day-cost card** rolling up overtime, meal penalties, carry-overs, re-rentals, company moves and held cast — with anything the production cannot price *named* rather than counted as zero.
- ⚡ **Guided Demo Tour & Parallel Intelligence Console**: 1-click guided demo tour banner on the Home page, a **Ctrl+K command palette** over every scene, day, resource, discovered fact and log line, and a telemetry console inspecting all 6 Parallel APIs — now with a **dollar ledger that distinguishes what was spent from what a recording answered for free**.

## The hero demo — Project Nightfall (fictional)

Shoot Day 4 in Mumbai: alley (EXT), apartment (INT), market street (EXT, police permit 13:00–18:00 only), rooftop motorcycle jump at sunset (EXT, drone + crane + fireworks). A nowcast arrives: **rain 13:00–17:00**.

ScenePilot verifies the weather picture through Parallel, finds **2 scenes affected**, evaluates hundreds of orderings, rejects the "hold the schedule" option (rain exposure) and the "shoot the market in the morning" option (**permit window — a real constraint**), and recommends: pull the interior cover scene into the rain window, carry the market scene over, and push the rooftop jump to the post-rain golden hour at ₹7,500 overtime. The producer approves; the ChangeSet re-times the crane/drone/fireworks calls, re-routes the cast van, adds a crew dinner and drafts the location notices — all derived from the approved change, never hardcoded.

## Architecture

```
apps/web            Next.js 16 · React 19 · Tailwind 4 — the control-room UI (proxies /api/* to the agent service)
services/agent      Python 3.12 · FastAPI · google-adk 2.7 · google-genai · parallel-web 1.3 · SQLAlchemy (SQLite → Postgres)
  scenepilot/domain      Pydantic domain: Project, Scene, Requirement, Resource, Availability, ResearchQuestion, SearchRun,
                         Evidence, Risk, Candidate, ProductionPlan, ShootDay, ScheduleItem, Disruption, ImpactAnalysis,
                         RecoveryOption, ChangeSet, CoordinationAction, ActivityEvent
  scenepilot/services    Deterministic logic: time intervals, availability, hard/soft constraint validation, day packing,
                         impact propagation, candidate enumeration, transparent scoring, ChangeSet diff/apply, coordination,
                         dossier (the confidence gate turning cited web facts into constraints)
  scenepilot/tools       Parallel session (client_model + per-run session_id), Search and Extract tools (observable
                         SearchRun/ExtractRun records, budgeted ADK FunctionTools, query hygiene) + record/replay;
                         Task (location dossiers), FindAll/Entity Search (substitute suppliers), Monitor, Memory
  scenepilot/agents      ADK LlmAgents with typed output schemas + versioned prompts (prompts/v1)
  scenepilot/workflows   Production Orchestrator (planning) & Rescue Orchestrator, both executed as
                         `google.adk.workflow.Workflow` graphs (graph.py) — persisted state, activity events
  scenepilot/api         FastAPI routes + deps.py (feature gating for the paid/slow Parallel APIs)
  tests/                 Scheduling / constraint / recovery-rejection / ChangeSet tests
adk_agents/             `adk web` entry point — an interactive ADK agent with the Parallel tool
```

**Gemini** (via ADK `LlmAgent` + `output_schema`) handles semantic scene understanding, research planning, evidence synthesis/grading, alternative production approaches and explanations.
**Deterministic code** handles interval overlap, availability, durations, hard constraints, resource conflicts, cost arithmetic, ChangeSet generation/validation and coordination actions. Gemini never decides whether an actor is available.

### The orchestrators are ADK graphs, and the UI draws the graph the engine runs

Both pipelines execute as `google.adk.workflow.Workflow` graphs — ADK 2.7's node/edge orchestrator,
which deprecates `SequentialAgent` / `ParallelAgent` / `LoopAgent`. Each node wraps the same step
function with the same prompts, so ADK schedules and the deterministic engine still decides.

```
scenepilot_planning   breakdown → research_plan → research → evidence ⇄ follow_up → plan
                                                              └── routed cycle: loop while a question is
                                                                  still WEAK/CONFLICTING/MISSING *and* the
                                                                  analyst asked for specific follow-up queries
scenepilot_rescue     disruption → verify → impact → candidates → proposals → explain → awaiting_approval ■
                                                                  └── terminal: applying a change is not a
                                                                      step the pipeline may take on its own
```

Two things fall out of this that are hard to fake. The research → evaluate → research-again loop is a
**routed cycle** rather than a `while`, which is what it always was in behaviour. And `GET /api/agent-graph`
serialises the very objects that run, so the pipeline strip on the scene and day pages is a *projection* of
the graph, not a hand-kept list beside it: rename a node in Python and it is renamed on screen. Node names
are the same strings a run reports as its stage, which is what lets the strip highlight the live node.

## How ScenePilot uses Parallel

Parallel is the external-world intelligence layer. Every call goes through the `parallel-web` SDK at runtime, is persisted as a `SearchRun` / `ExtractRun` (request as sent, results, `usage[]`, `warnings[]`, `session_id`, `client_model`), shows up in the **Agent activity** feed, and is inspectable in the **Evidence drawer**. The **Parallel usage strip** on each scene/day page totals calls, modes, SKUs, the session id and an estimated cost.

| Where | API · mode | What is sent | Why |
|---|---|---|---|
| Planning, round 1 (one per research question, concurrent) | Search · `fast` | objective + exactly 3 keyword queries, `client_model`, `session_id` — no advanced settings | cheap fan-out; Parallel sizes excerpts for the consuming model |
| Evidence Analyst follow-ups (ADK tool, ≤2 per question) | Search · `advanced` | model-written objective/queries, cleaned by `clean_queries()` | the agentic research → evaluate → follow-up loop |
| Evidence Analyst deep read (ADK tool, ≤1 per question) | **Extract** | one URL + objective, `full_content` ≤20k chars | policy pages / PDFs whose exact wording matters — instead of re-searching |
| Orchestrator follow-up (any question still WEAK / CONFLICTING / MISSING, when the analyst returned follow-up queries) | Search · `advanced` | analyst's `follow_up_queries` | guarantees a second look |
| Disruption verification (WEATHER, TRANSPORT, REGULATORY) | Search · `fast`, ×2 concurrent | `location`, `max_results=6`, `after_date`, 1 h cache tolerance; the IMD search uses `include_domains` because IMD is the single authoritative publisher | fast-changing real-world data; confidence feeds the recovery score |
| "Open source" on any evidence card | **Extract** | the cited URL + the research question as objective, cached per run | full page as markdown with the cited excerpt highlighted |
| Location dossier (producer clicks *Research this location*) | **Task** · `core-fast`, JSON schema, `field-basis` | location + city + date; per-field output schema | structured facts with a citation *each*, graded into HARD/SOFT/ADVISORY constraints |
| Substitute suppliers (producer clicks *Find replacements*) | **Entity Search** (sync) or **FindAll** (async) | a category objective, or an objective + match conditions | real companies who can replace a lost resource, with sources |
| Watching a shoot day (producer clicks *Start monitors*) | **Monitor** · `event_stream`, `1h` | one query per kind — IMD warnings for the date, road closures near the day's locations | the outside world pushes: a detected change opens a *draft* disruption for the producer to confirm |
| Watching a location's rules (producer clicks *Watch for changes*) | **Monitor** · `snapshot`, `1d` | the dossier's `task_run_id` — Parallel derives schema and baseline from it | a permit rule or curfew that moves after you planned against it; the event carries only the changed fields, freshly cited |
| Production brain (producer clicks *Recall*) | **Memory** (beta) | `memory_scope_key = scenepilot_<project_id>` | what this shoot's Task/Monitor/FindAll runs already learned; the producer can evict what is stale |
| Research plan, with *start from prior research* ticked | **Memory** (beta) | one read, scene heading as the query | the planner spends its questions on what earlier dossiers left open instead of re-asking them |
| Re-researching a location | **Task**, chained | the prior run's `interaction_id` as `previous_interaction_id` | continues that investigation rather than starting cold |

By-the-book details (docs.parallel.ai/search/best-practices): `client_model` on every call; one descriptive `session_id` per run (`scenepilot_planning_<run>`) shared by Search and Extract; `max_chars_total` left to Parallel's dynamic default; advanced settings only where strictly required; tool schemas use Parallel's recommended wording ("exactly 3 keyword queries … NEVER sentences or `site:` operators"), enforced deterministically. Cost transparency uses Parallel's published prices (Search turbo/fast $1 per 1k, basic/advanced $5 per 1k; Extract $1 per 1k URLs; Task `core` $25 per 1k runs; Entity Search $5 per 1k; FindAll `base` $0.25 + $0.03/match).

**Every rejection is traceable.** When an accepted fact rejects a schedule option, the option detail draws the chain that produced it — Task run → the cited page and its excerpt → the accepted constraint → this option. On the hero night unit that reads: *Location dossier → indiacode.nic.in, "Night time shall mean from 10.00 p.m. to 6.00 a.m." → no work 22:00–06:00, accepted by producer → Scene 58 runs 90 min inside the noise curfew.* The evidence waterfall beside the cost waterfall.

**Every accepted rule stays watched.** An `event_stream` monitor answers *what happened today*; a
`snapshot` monitor answers the harder question — *is what I planned against still true?* It re-runs a
location dossier and reports only the fields that moved, so a curfew that shifts from 22:00 to 21:00
arrives as a diff with a fresh citation, not as a fresh dossier to re-read. It lands as a **pending
change**: the value the producer accepted keeps constraining the schedule until they adopt the new
one, and adopting a *binding* change clears the acceptance — acceptance was given to a value, not to
a field, so the new window is signed off separately or not at all.

Honest limits: internal disruptions (cast, equipment) are not externally verified — there is nothing on the web to verify; if Parallel is unavailable, a question is graded MISSING ("search unavailable") and a run with no searches at all fails loudly rather than producing a plan graded on nothing.

## Run it locally

Prerequisites: Node 22+, pnpm 9+, Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env           # add GOOGLE_API_KEY and PARALLEL_API_KEY
# Gemini keys from Google Cloud "express mode" start with AQ. and need GOOGLE_GENAI_USE_VERTEXAI=TRUE;
# AI Studio keys (AIza…) work with GOOGLE_GENAI_USE_VERTEXAI=FALSE.

# agent service (http://localhost:8000)
cd services/agent
uv sync
uv run uvicorn scenepilot.api.app:app --reload --port 8000

# web app (http://localhost:3000)
cd apps/web
pnpm install
pnpm dev
```

Then open http://localhost:3000 → **Project Nightfall** → **Day 4** → pick the **Rain expected 13:00–17:00** fixture and watch the control room work. Open **Scene 42** and press **Break down & research** for the planning workflow.

### What a cold instance already has (`SCENEPILOT_WARM_DEMO=1`, default)

Nothing expensive fires on its own — which used to mean a freshly-deployed instance opened on empty
panels, and the beat that carries the whole argument (*a cited statute rejects a scene*) was invisible
until someone clicked and waited five minutes. On Cloud Run it was worse: without `DATABASE_URL` the
store is SQLite under `/tmp`, so a new revision threw that state away again.

So a new project is seeded from the recordings this repo already ships: the hero screenplay in the
Screenplay Studio, and one location dossier per location. Three rules keep it honest, and they are why
this is a seed rather than a fixture:

| | |
|---|---|
| **Nothing is invented** | every dossier is a recording of a real Parallel Task run, looked up by the exact request the live tool would send. No recording, no dossier. |
| **It is labelled** | the runs are stored `REPLAY`/`replayed`, the same state replay mode produces, so the UI calls them *replayed* and **Re-research** runs them live. |
| **It accepts nothing** | facts arrive graded but unaccepted, so the producer's acceptance click — the moment the web becomes a constraint — still has to happen. |

Set `SCENEPILOT_WARM_DEMO=0` to start blank. `POST /api/projects/proj_nightfall/reset` restores the
same warm state, which is also what makes the reset button safe to press mid-demo.

### Modes

| Env | Meaning |
|---|---|
| `SCENEPILOT_MODE=live` (default) | Real Gemini + Parallel calls. |
| `SCENEPILOT_RECORD=1` | Also save every live response under `services/agent/scenepilot/seed/fixtures/recordings/`. |
| `SCENEPILOT_MODE=replay` | Serve those recordings instead of calling out. Replayed results are labelled in the UI. Only ever produced by real runs — nothing is hand-written. |
| `SCENEPILOT_FALLBACK_TO_RECORDING=1` (default) | Demo hardening: if a **live** Gemini or Parallel call fails and this exact request was recorded earlier, serve the recording — visibly labelled *replayed* in the activity feed and evidence drawer — instead of dead-ending. Requests never recorded still fail honestly. |

### Deep-Parallel features (off by default)

Search and Extract are cheap and sub-second, so the workflows call them freely. The Task, FindAll and
Memory APIs are slower and cost real money per run, so this rule applies to them:

> **No Parallel call that costs more than a Search, or takes longer than a few seconds, may fire
> implicitly.** Each is behind an env flag that is off by default, is reached only from an explicit
> named button that shows its estimated cost and latency *before* the click, and is reported by
> `GET /api/features` so the UI renders it **disabled with the reason** rather than hiding it.

| Env | Feature | Cost when on |
|---|---|---|
| `SCENEPILOT_PARALLEL_MEMORY=1` | **Production brain** — read back what this production's Task/Monitor/FindAll runs learned (`memory_scope_key = scenepilot_<project_id>`), and let the producer *evict* a fact that has gone stale | none |
| `SCENEPILOT_PARALLEL_TASK=1` | **Location dossiers** — structured research per location whose cited facts become constraints | ~$0.025 / location |
| `SCENEPILOT_PARALLEL_FINDALL=1` | **Substitute suppliers** — real vendors who can replace a lost resource | ~$0.005 (Entity Search) – ~$0.49 (FindAll) |

### Irreversible writes (on locally, off on the hosted demo)

Two writes a producer cannot take back from the UI. They are gated for the opposite reason to the
Parallel flags — nothing here costs money or calls anyone — and so they default the opposite way:

> **A local clone owns its own database and is the product; a public URL with no auth is not.** These
> are **on in code** and closed by `deploy/cloudrun.sh`, whereas a paid Parallel call is off in code
> and opened by the deploy. Both are reported by `GET /api/features`, so a closed one renders
> **disabled with the reason** rather than hidden.

| Env | Write | What it costs you if it is open in public |
|---|---|---|
| `SCENEPILOT_ALLOW_WRAP=1` (default) | **Wrap a day** — mark each strip shot or carried and close the day out | A wrapped day is a record: it cannot be rescued, re-timed or re-wrapped, and only a full reset undoes it |
| `SCENEPILOT_ALLOW_COMMIT_BOARD=0` (default) | **Commit a board** — keep the times a producer nudged by hand | Replaces the engine's own schedule with typed times; re-validated under the pack in force, but nothing proposed it |

#### Location dossiers → constraints (Task API)

One `core-fast` run per location returns permit authority, lead time, fee band, noise curfew, drone
and pyrotechnics rules, restrictions and nearest hospital — **every field with its own citation**
(per-element basis, so each item of `restrictions[]` is separately sourced). A confidence gate
decides how much authority a discovered fact may have:

| Parallel's basis | Binding | Effect |
|---|---|---|
| `high` **+ a citation** | **HARD** | may reject a schedule option |
| `high`, no citation, or `medium` | SOFT | prices the option, never rejects it |
| `low` / absent | ADVISORY | shown for a human to check, never enforced |

and **a HARD fact still constrains nothing until a producer accepts it**. Only two shapes are
mechanically checkable — a time-window ban (noise curfew) and an activity ban (drones, pyro) — so
everything else is displayed with its source but never auto-enforced; ScenePilot does not pretend to
parse arbitrary prose into constraints. When a rule does bind, the resulting violation carries the
fact id and the URL, so a rejected option traces back to the page it came from.

#### Substitute suppliers (FindAll / Entity Search)

When a resource falls through, reshuffling the day is not a recovery. `Entity Search` (synchronous,
seconds) or `FindAll` (async, deeper, citation-rich) returns real companies who could replace it; a
producer selects one, and the approved ChangeSet puts it on the regenerated call sheet with its
phone number and its source. Nothing reaches production state on the strength of a search result.

Memory and Monitors are deliberately **not** record/replayed — they are server-side state, so in
replay mode a memory read reports `UNAVAILABLE` rather than inventing entries. Task dossiers *are*
recorded, like Search and Extract.

```bash
# the deep features, live, with their costs (needs the flags above)
cd services/agent && uv run python scripts/live_validate.py deep
```

Without keys the rescue workflow still runs end-to-end on deterministic logic: the verification searches fail visibly (logged as warnings, shown as errored `SearchRun`s), the report is treated as unverified, and Gemini explanations fall back to deterministic text. The planning workflow needs Gemini and Parallel.

### Automated Tests (614/614 Passing)

```bash
cd services/agent && uv run pytest -q
```

All **614 tests** pass in under a minute:
- **Orchestration**: both pipelines are ADK `Workflow` graphs — the follow-up loop is a routed cycle, the
  rescue graph is terminal at producer approval, node names match the stages a run reports, and a node that
  raises stops the graph while keeping its original exception (`test_graph.py`).
- **Demo seed**: the pre-loaded state is a replay of real recordings, labelled as one, accepting nothing and
  rewriting no committed production state (`test_warm_seed.py`).
- **Phase 1**: Screenplay parsers (Fountain & FDX), eighths math, Day-Out-Of-Days (DOOD) generator, and Gemini breakdown extraction (`test_parsers.py`, `test_dood.py`, `test_breakdown_agent.py`, `test_screenplay_api.py`).
- **Phase 2**: NOAA astronomical ephemeris equations, DGA compounding meal penalties & turnaround rest, and multi-unit concurrency (`test_ephemeris.py`, `test_labor_rules.py`, `test_scheduling_api.py`).
- **Phase 3**: Multi-day cascading ripple solver across downstream days, pickup day synthesis, and multi-channel WhatsApp/SMS call sheet field dispatch (`test_multiday_solver.py`, `test_delivery.py`, `test_phase3_api.py`).
- **Deep Parallel integration** (79 tests): the confidence gate turning cited facts into HARD/SOFT/ADVISORY constraints (`test_dossier.py`), substitute-supplier discovery across Entity Search and FindAll (`test_findall.py`), the production brain (`test_memory.py`), snapshot monitors and pending fact changes — a binding change keeps constraining the schedule until the producer adopts it, and adopting clears the acceptance (`test_fact_watch.py`, `test_monitor.py`), and the Parallel tool layer with its fake SDK client double, query hygiene and record/replay keying (`test_parallel_tools.py`).
- **Deployment safety**: the paid-call budget and per-endpoint cooldown that bound spend on a public hosted demo (`test_budget.py`), and the shoot-day re-anchor that keeps the hero day dated today on a long-lived instance (`test_seed_anchor.py`).
- **Core domain**: interval logic, permit windows, travel, disruption exposure, candidate ranking, **infeasible-option rejection** against real constraints, ChangeSet build/apply idempotency, derived coordination actions, and both orchestrators end-to-end.

### Live validation + recording

```bash
cd services/agent && uv run python scripts/live_validate.py   # runs Sc 42 planning + Day 4 rescue live, records responses
```

### Browser walk-through (Playwright)

```bash
cd services/agent && uv run playwright install chromium
uv run python tests/e2e/walk_demo.py data/screens   # drives the hero demo headless and saves screenshots
```

### Call sheet

`GET /api/projects/{id}/shoot-days/{day}/call-sheet` regenerates a real call sheet from production state — schedule, cast calls/wraps, equipment calls, transport, meals, locations, advisories (including the Parallel-verified disruption window) — and, after an approved recovery, the pre-recovery sheet alongside it. Open **Call sheet** on the day page for the printable before/after view.

### Rules the engine enforces

12-hour standard shift with overtime beyond it (FWICE / CINTAA norm; ₹/h configurable per day), lunch due ~6 h after call (meal-penalty exposure), 10 h turnaround before the next day's call, usable-daylight and golden-hour windows, cast/location/equipment availability and permit windows, travel time between locations, disruption exposure incl. dry-out. Hard rules reject an option; soft rules price it (the option's **cost waterfall**).

### ADK eval

```bash
cd services/agent && uv run python scripts/adk_eval.py
```

Runs `adk eval` on the `scenepilot_research` agent with `adk_agents/evals/research.evalset.json`: a deterministic custom metric `parallel_tool_use` (Parallel Search called before answering, exactly 3 keyword queries per call following Parallel's rules, answer cites a returned URL) plus an ADK rubric-based judge for grounding, FACT/INFERENCE/RECOMMENDATION/UNKNOWN separation and production usefulness. Results print to the console (`--print_detailed_results`).

### ADK dev UI

```bash
cd services/agent && uv run adk web ../../adk_agents
```

Loads the `scenepilot_research` agent — a Gemini agent with the same `parallel_search` and `parallel_extract` tools (fresh Parallel session and budgets per invocation; every call persisted under run `adk-web`) — so you can watch tool calls in ADK's own UI.

## Deploy (Cloud Run)

See `deploy/cloudrun.sh`: builds both containers with Cloud Build, deploys the agent service (secrets from Secret Manager, optional Cloud SQL via `DATABASE_URL`) and the web app with `AGENT_URL` pointing at it.

## Honesty notes

- Project Nightfall, its cast, locations, permits and prices are synthetic and labelled as such in the UI.
- Web evidence is real and live; the verifier grades how far it supports a fictional day's report and says so.
- `readinessScore` and recovery scores are transparent product heuristics with visible components and weights, not scientific truth.
- No consequential production change is applied without explicit approval; every applied change keeps before/after values and a reason.

## License

MIT — see `LICENSE`.
