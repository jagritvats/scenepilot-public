"use client";

import Link from "next/link";
import { useRef } from "react";
import { useDismissOnEscape, useFocusTrap, useMounted } from "@/lib/useDismiss";
import { createPortal } from "react-dom";

interface TourStep {
  title: string;
  badge: string;
  tagline: string;
  description: string;
  href: string;
  highlights: string[];
}

const TOUR_STEPS: TourStep[] = [
  {
    title: "1. Screenplay Studio & AI Breakdown",
    badge: "Phase 1 · Ingestion",
    tagline: "From raw Fountain / FDX to 32 standard breakdown element categories",
    description:
      "Parse industry screenplays with an automatic eighths-of-a-page estimate. Google ADK Gemini extracts 32 element categories, detects implied subtext, and flags physical safety stop-conditions, generating a Day-Out-Of-Days (DOOD) cast matrix with retention cost alerts.",
    href: "/projects/proj_nightfall/screenplay",
    highlights: [
      "Fountain & Final Draft XML (.fdx) lexer",
      "8ths-of-a-page estimator (line & word heuristic)",
      "Gemini CreativeBreakdownAgent extracting 32 categories",
      "Safety Stop-Conditions (wet motorcycle slip hazard)",
      "Interactive Day-Out-Of-Days (DOOD) Cast Retention Matrix",
    ],
  },
  {
    title: "2. Scene Planning & Parallel Grounding",
    badge: "Planning Loop",
    tagline: "Autonomous web research turning live facts into production constraints",
    description:
      "Gemini formulates explicit Research Questions, queries the live web via the Parallel Search API, and evaluates evidence sufficiency. If evidence is weak or conflicting, an autonomous follow-up loop executes. Discovered municipal rules are confidence-gated into hard constraints.",
    href: "/projects/proj_nightfall/scenes/sc_42",
    highlights: [
      "Gemini ResearchPlanner & EvidenceAnalyst",
      "Runtime Parallel Search (Fast & Advanced modes)",
      "Autonomous follow-up search loops",
      "Fact / Inference / Recommendation / Unknown separation",
      "Direct citation URL links & evidence drawer",
    ],
  },
  {
    title: "3. Astronomical Ephemeris & Live Stripboard",
    badge: "Phase 2 · Scheduling",
    tagline: "Deterministic solar physics and pluggable union labor compliance",
    description:
      "Pure Python NOAA solar equations compute sunrise, sunset, twilight, and golden hours for global film hubs. Test real-time strip adjustments with pluggable DGA/SAG compounding meal penalties and 12-hour rest turnaround vs. FWICE standards.",
    href: "/projects/proj_nightfall/days/day_4#stripboard",
    highlights: [
      "NOAA Astronomical Ephemeris solar curves & golden hours",
      "Pluggable DGA (compounding penalties) & FWICE rule packs",
      "Multi-Unit concurrency & resource contention checks",
      "Interactive Gantt stripboard with live timing nudges",
      "1-Click 'Snap to Golden Hour' & meal break insertion",
    ],
  },
  {
    title: "4. Autonomous Shoot Rescue & Multi-Day Ripple",
    badge: "Phase 3 · Hero Story",
    tagline: "When real-world weather hits, rescue the shoot day with deterministic rigor",
    description:
      "Day 4 faces a severe rain disruption. Parallel verifies the external weather in real-time. Deterministic code tests hundreds of orderings, rejecting infeasible options (police permits, wet hazards). The Multi-Day Solver absorbs deferred scenes into downstream days or synthesizes a dedicated Pickup Unit.",
    href: "/projects/proj_nightfall/days/day_4#recovery",
    highlights: [
      "Parallel Search weather verification & IMD corroborate",
      "Deterministic hard constraint rejections (police permit, wet rooftop)",
      "Multi-day ripple solver, held to the same validator as the board",
      "No downstream day can legally take the deferred scene, so a Day 7 Pickup Unit is synthesized",
      "AI 1st AD RescueStrategist rationale & producer ChangeSet",
    ],
  },
  {
    title: "5. DGA Call Sheet & Field Dispatch",
    badge: "Phase 3 · Dispatch",
    tagline: "Multi-channel field dispatch composed from the call sheet, delivery simulated",
    description:
      "Derives a regenerated DGA-compliant Call Sheet from production state. Composes one message per cast member and department head — their name, their call time, the set — across WhatsApp, SMS and Email, and opens a delivery log against each. Nothing is transmitted: rows are queued, and read/confirmed states are set by hand so the tracking view can be shown.",
    href: "/projects/proj_nightfall/days/day_4/call-sheet",
    highlights: [
      "Official DGA Call Sheet 2.0 format",
      "Multi-channel broadcast (WhatsApp, SMS, Email)",
      "Delivery log per recipient — queued only, never transmitted",
      "Mark read / Mark confirmed, both labelled simulated",
      "Audit trail linking schedule changes to call sheet revisions",
    ],
  },
  {
    title: "6. The Paper a Unit Actually Carries",
    badge: "Phase 4 · Documents",
    tagline: "Four printable documents, and the one that refuses to be issued",
    description:
      "Beside the call sheet: a movement order, a sides packet, and a Daily Production Report. Each is built from the same committed state the board is, and each says plainly what it cannot say — a scene the Studio holds no pages for prints as a named gap, and a DPR for a day that has not wrapped is refused outright, because a report of a day that has not happened is a forecast wearing a report's clothes.",
    href: "/projects/proj_nightfall/days/day_4/sides",
    highlights: [
      "Sides in shooting order — Day 4 complete, Day 6 all named gaps",
      "Movement order: departures floored at the wrap, arrivals from the production's own travel times",
      "Daily Production Report — wrapped days only, and it names the right document instead",
      "Printable force-majeure claim packet for the underwriter",
    ],
  },
  {
    title: "7. Fragility, Priced and Located",
    badge: "Phase 4 · Foresight",
    tagline: "What moved, what could go wrong, where there is no slack, and what a day costs",
    description:
      "The fact-drift inbox reports what changed in the world since this production last looked. The risk register orders risks by the same severity × likelihood product the readiness score sums — and states its own denominator, because an unplanned scene has no register rather than an empty one. Booking pressure shows which resources have nowhere left to move. The day-cost card names what it cannot price instead of counting it as zero.",
    href: "/projects/proj_nightfall/risks",
    highlights: [
      "Fact-drift inbox — accept a change and the schedule re-answers",
      "Risk register: 'nobody has looked' is never reported as 'nothing to find'",
      "Booking pressure: unconstrained and not-cleared-for-this-day are different colours",
      "Day cost: overtime, meals, carry-overs, re-rentals, moves and held cast in one figure",
    ],
  },
  {
    title: "8. The Production Log",
    badge: "Provenance · Audit trail",
    tagline: "Who decided what, on what evidence, and when",
    description:
      "Every act on this production, in the order it happened, written at the moment it happened by the code that performed it — a Parallel call, a constraint the engine refused, a fact a producer accepted, a change set applied. Nothing on this page is reconstructed for display, which is what separates an audit trail from a summary.",
    href: "/projects/proj_nightfall/log",
    highlights: [
      "Producer decisions as their own category — the accountable acts",
      "Project-level and workflow-level events as one chronology",
      "Filter by evidence, reasoning, engine verdict or decision",
      "Each entry links back to the day or scene it changed",
      "The vocabulary comes from the API, so the log and the run feed cannot disagree",
    ],
  },
];

export function HackathonTourModal({
  isOpen,
  onClose,
  onOpenConsole,
}: {
  isOpen: boolean;
  onClose: () => void;
  onOpenConsole: () => void;
}) {
  useDismissOnEscape(isOpen, onClose);
  const panel = useRef<HTMLDivElement>(null);
  useFocusTrap(isOpen, panel);
  const mounted = useMounted();

  if (!isOpen || !mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] overflow-y-auto bg-black/80 backdrop-blur-md p-4 sm:p-6 md:p-8 flex justify-center items-center animate-in fade-in duration-200"
      onClick={onClose}
    >
      <div ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Hackathon guided tour"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="bg-zinc-950 border border-line rounded-xl max-w-4xl w-full max-h-[85vh] my-auto flex flex-col shadow-2xl overflow-hidden relative">
        {/* Modal Header */}
        <div className="p-6 border-b border-line flex items-start justify-between bg-zinc-900/50 shrink-0">
          <div>
            <div className="flex items-center gap-2">
              <span className="mono text-[10px] px-2 py-0.5 rounded bg-accent/20 text-accent font-semibold">
                Google Cloud Hackathon
              </span>
              <span className="mono text-[10px] px-2 py-0.5 rounded bg-parallel/20 text-parallel font-semibold">
                Parallel Partner Track
              </span>
            </div>
            <h2 className="display text-2xl font-bold mt-1.5 flex items-center gap-2">
              <span>ScenePilot Guided Showcase</span>
            </h2>
            <p className="text-xs text-muted mt-1 max-w-2xl">
              An intelligent film production control room that plans scenes against live web evidence
              and rescues shoot days when reality moves. Explore the eight core vertical slices below.
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close the tour"
            className="p-1.5 text-muted hover:text-foreground rounded border border-line/60 hover:border-line text-sm"
          >
            ✕
          </button>
        </div>

        {/* Modal Body: Steps List */}
        <div className="p-6 overflow-y-auto space-y-4 flex-1">
          <div className="grid gap-4 md:grid-cols-2">
            {TOUR_STEPS.map((step, idx) => (
              <div
                key={idx}
                className="card p-4 flex flex-col justify-between hover:border-accent/60 transition group bg-zinc-900/20"
              >
                <div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-accent">
                      {step.badge}
                    </span>
                    <span className="mono text-[10px] text-dim">Slice #{idx + 1}</span>
                  </div>
                  <h3 className="display text-base font-bold text-foreground mt-1 group-hover:text-accent transition">
                    {step.title}
                  </h3>
                  <div className="text-[11px] font-semibold text-dim italic mt-0.5">
                    {step.tagline}
                  </div>
                  <p className="text-xs text-muted mt-2 leading-relaxed">
                    {step.description}
                  </p>

                  <ul className="mt-3 space-y-1 text-[11px] text-zinc-400">
                    {step.highlights.map((h, i) => (
                      <li key={i} className="flex items-start gap-1.5">
                        <span className="text-accent shrink-0">▸</span>
                        <span>{h}</span>
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="mt-4 pt-3 border-t border-line/50 flex items-center justify-between">
                  <Link
                    href={step.href}
                    onClick={onClose}
                    className="btn btn-primary text-xs w-full text-center"
                  >
                    Launch Slice Demo →
                  </Link>
                </div>
              </div>
            ))}
          </div>

          {/* Under the hood summary */}
          <div className="card p-4 bg-zinc-900/50 border border-parallel/30 flex items-center justify-between flex-wrap gap-3">
            <div>
              <div className="text-xs font-semibold text-parallel flex items-center gap-1.5">
                <span>🌐 Deep Parallel Partner Track Telemetry</span>
              </div>
              <p className="text-[11px] text-muted mt-0.5">
                Inspect runtime telemetry across all 6 Parallel APIs (Search, Extract, Task, FindAll, Memory, Monitor).
              </p>
            </div>
            <button
              onClick={() => {
                onClose();
                onOpenConsole();
              }}
              className="btn text-xs border border-parallel/50 text-parallel hover:bg-parallel/10"
            >
              Open Parallel Console ⚡
            </button>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="p-4 sm:p-5 border-t border-line bg-zinc-900/60 flex items-center justify-between text-xs text-muted shrink-0">
          <div className="flex items-center gap-2">
            <span className="mono text-ok font-semibold">611/611 Tests Passing</span>
            <span>·</span>
            <span>Google ADK Gemini 3.5 Flash</span>
            <span>·</span>
            <span>Parallel Web SDK 1.3</span>
          </div>
          <button onClick={onClose} className="btn text-xs">
            Close Tour
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
