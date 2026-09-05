"use client";

import { useRef, useState } from "react";
import { useDismissOnEscape, useFocusTrap } from "@/lib/useDismiss";
import { createPortal } from "react-dom";
import type { FeatureState, Health, ParallelUsage } from "@/lib/api";
import { Kicker } from "./ui";

/**
 * What ScenePilot uses each Parallel API for — and, for the three that cost real money, whether
 * this deployment actually has it switched on. The gate state is the same one `GET /api/features`
 * serves and the Dossier and Substitute panels obey; `/api/health` carries it, so this console
 * reads TopBar's poll rather than opening a second one.
 */

/** `/api/health` reports the feature gate defined in `api/deps.py` alongside the rest of its state. */
export type HealthWithFeatures = Health & { parallel_features?: Record<string, FeatureState> };

interface ParallelApi {
  name: string;
  badge: string;
  endpoint: string;
  /** Key in the feature gate; `null` for the two APIs that are cheap enough to run unflagged. */
  feature: string | null;
  role: string;
  /** Used only when the gate payload carries no cost of its own — the same rates the usage strip prices with. */
  cost: string;
  details: string[];
}

/** Which metered bucket each catalogue entry is priced in. Memory and Monitor are not metered by
 *  `summarize()` — Memory has no per-call price, and a Monitor bills daily on Parallel's side, not
 *  per call here — so they carry no key rather than a misleading zero. */
const COST_KEY: Record<string, "search" | "extract" | "task" | "findall" | undefined> = {
  "Parallel Search": "search",
  "Parallel Extract": "extract",
  "Parallel Task": "task",
  "Parallel FindAll & Entity Search": "findall",
};

const PARALLEL_APIS: ParallelApi[] = [
  {
    name: "Parallel Search",
    badge: "Fast & Advanced",
    endpoint: "POST /v1/search",
    feature: null,
    role: "Production planning fan-out & real-time weather disruption verification",
    cost: "$1 / 1k (fast) · $5 / 1k (advanced)",
    details: [
      "Exact 3-keyword query hygiene enforced deterministically (no conversational fluff)",
      "Domain inclusion targeting authoritative publishers (e.g. mausam.imd.gov.in)",
      "Excerpt sizing left to Parallel's dynamic default unless the deployment overrides it",
      "Corroboration scoring turns raw results into a verdict — corroborated, contradicted or uncorroborated — with the confidence it was reached at",
    ],
  },
  {
    name: "Parallel Extract",
    badge: "Full Content",
    endpoint: "POST /v1/extract",
    feature: null,
    role: "Deep reading of municipal curfews, drone regulations, and police permits",
    cost: "$1 / 1k URLs",
    details: [
      "Extracts full HTML/PDF policies without re-searching, sharing the session with Search",
      "Precise source provenance chain: URL → exact excerpt → rule",
      "Highlights evidence quotes directly in the interactive Evidence Drawer",
    ],
  },
  {
    name: "Parallel Task",
    badge: "Structured Dossier",
    endpoint: "POST /v1/tasks/runs",
    feature: "task",
    role: "Structured location intelligence & confidence-graded constraints",
    cost: "$25 / 1k runs on core",
    details: [
      "Evaluates per-field citations for permit authority, curfew, and drone rules",
      "Confidence gating: HIGH + citation binds as a HARD constraint; MEDIUM as a SOFT penalty",
      "Chained task execution with previous_interaction_id for investigative depth",
    ],
  },
  {
    name: "Parallel FindAll & Entity Search",
    badge: "Supplier Discovery",
    endpoint: "POST /v1beta/findall/entity-search · /runs",
    feature: "findall",
    role: "Autonomous substitute vendor and replacement equipment sourcing",
    cost: "$5 / 1k (Entity Search) · $0.25 + $0.03/match (FindAll base)",
    details: [
      "Discovers real camera rental houses, waterproof gear, and blowers near the unit",
      "Contact extraction for instant call sheet integration",
      "Deduplication across category search candidates",
    ],
  },
  {
    name: "Parallel Memory",
    badge: "Production Brain",
    endpoint: "POST /v1beta/memory/retrieve",
    feature: "memory",
    role: "Cross-shoot-day institutional memory and verified fact retention",
    cost: "Beta — no per-call cost",
    details: [
      "Scoped to memory_scope_key = scenepilot_<project_id>",
      "Prevents redundant research by recalling earlier location dossiers and permit findings",
      "Producer eviction controls to purge stale or invalidated operational facts",
    ],
  },
  {
    name: "Parallel Monitor",
    badge: "Event Stream & Snapshot",
    endpoint: "POST /v1/monitors",
    feature: "monitors",
    role: "Autonomous background surveillance of weather alerts and curfew shifts",
    cost: "Needs a reachable webhook URL",
    details: [
      "Event-stream: watches warning feeds and opens draft disruptions automatically",
      "Snapshot: detects regulatory shifts (e.g. a noise curfew moving from 22:00 to 21:00)",
      "Diff-only notifications: alerts the producer only when a binding rule moves",
    ],
  },
];

interface Gate {
  label: string;
  chip: string;
  reason: string | null;
  cost: string;
}

function gateOf(a: ParallelApi, health: HealthWithFeatures | null): Gate {
  if (!health) return { label: "checking…", chip: "chip-dim", reason: null, cost: a.cost };
  if (a.feature === null) {
    if (health.mode === "replay") return { label: "replay", chip: "chip-warn", reason: "This deployment replays recorded responses from earlier live runs instead of calling out.", cost: a.cost };
    if (health.parallel_configured) return { label: "live", chip: "chip-ok", reason: null, cost: a.cost };
    return { label: "no API key", chip: "chip-dim", reason: "Set PARALLEL_API_KEY=… to call this live.", cost: a.cost };
  }
  const f = health.parallel_features?.[a.feature];
  if (!f) return { label: "unreported", chip: "chip-dim", reason: "This deployment did not report a state for this integration.", cost: a.cost };
  const cost = f.cost || a.cost;
  if (f.enabled) return { label: "enabled", chip: "chip-ok", reason: null, cost };
  return { label: "disabled", chip: "chip-dim", reason: `Off in this deployment. Enable with ${f.env}.${f.requires_key ? " It also needs PARALLEL_API_KEY=…." : ""}`, cost };
}

export function ParallelConsoleModal({
  isOpen,
  onClose,
  health,
  usage,
}: {
  isOpen: boolean;
  onClose: () => void;
  health: HealthWithFeatures | null;
  /** What this view's calls actually cost, split live vs replayed. Absent where nothing has run. */
  usage?: ParallelUsage | null;
}) {
  useDismissOnEscape(isOpen, onClose);
  const panel = useRef<HTMLDivElement>(null);
  useFocusTrap(isOpen, panel);
  const [selectedApi, setSelectedApi] = useState<string>(PARALLEL_APIS[0].name);

  if (!isOpen || typeof document === "undefined") return null;

  const current = PARALLEL_APIS.find((a) => a.name === selectedApi) || PARALLEL_APIS[0];
  const gates = new Map(PARALLEL_APIS.map((a) => [a.name, gateOf(a, health)]));
  const currentGate = gates.get(current.name)!;
  // The measured spend for the API being inspected, where this view has metered any. The list price
  // above it stays: one is what Parallel charges, the other is what this demo actually spent.
  const meteredKey = COST_KEY[current.name];
  const metered = usage && meteredKey ? usage.cost_by_api?.[meteredKey] : undefined;
  const on = PARALLEL_APIS.filter((a) => ["live", "enabled", "replay"].includes(gates.get(a.name)!.label)).length;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] overflow-y-auto bg-black/80 backdrop-blur-md p-4 sm:p-6 md:p-8 flex justify-center items-center animate-in fade-in duration-200"
      onClick={onClose}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Parallel Intelligence Console"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="bg-zinc-950 border border-line rounded-xl max-w-4xl w-full max-h-[85vh] my-auto flex flex-col shadow-2xl overflow-hidden relative"
      >
        {/* Header */}
        <div className="p-6 border-b border-line flex items-start justify-between bg-zinc-900/50 shrink-0">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="mono text-[10px] px-2 py-0.5 rounded bg-parallel/20 text-parallel font-semibold">
                Parallel Partner Track
              </span>
              <span className="chip chip-dim">
                {health ? `${on} of ${PARALLEL_APIS.length} available in this deployment` : "reading /api/health…"}
              </span>
              {health && <span className={`chip ${health.mode === "live" ? "chip-ok" : "chip-warn"}`}>{health.mode}</span>}
            </div>
            <h2 className="display text-2xl font-bold mt-1.5">Parallel Intelligence Console</h2>
            <p className="text-xs text-muted mt-1 max-w-2xl">
              ScenePilot uses Parallel as its external-world intelligence engine. The integrations that cost real
              money per run — or need a reachable webhook — sit behind environment flags that are off by default,
              so this console reports what this deployment actually has on rather than what a demo would prefer.
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close the Parallel console"
            className="p-1.5 text-muted hover:text-foreground rounded border border-line/60 hover:border-line text-sm"
          >
            ✕
          </button>
        </div>

        {/* Console Body */}
        <div className="p-6 overflow-y-auto flex-1 space-y-5">
          {/* API Selector Tabs */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {PARALLEL_APIS.map((a) => {
              const g = gates.get(a.name)!;
              return (
                <button
                  key={a.name}
                  onClick={() => setSelectedApi(a.name)}
                  title={g.reason || undefined}
                  className={`p-3 rounded text-left transition border ${
                    selectedApi === a.name
                      ? "bg-parallel/15 border-parallel text-foreground font-semibold shadow"
                      : "bg-zinc-900/30 border-line/60 text-muted hover:text-foreground"
                  }`}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="text-xs font-bold truncate">{a.name}</span>
                    <span className={`chip ${g.chip} text-[9px]`}>{g.label}</span>
                  </div>
                  <div className="text-[10px] text-dim mt-0.5">{a.badge}</div>
                </button>
              );
            })}
          </div>

          {/* Selected API Deep Dive Card */}
          <div className="card p-5 bg-zinc-900/30 border border-line space-y-4">
            <div className="flex items-start justify-between flex-wrap gap-2 border-b border-line pb-3">
              <div>
                <div className="text-[11px] uppercase tracking-wider text-parallel font-bold">
                  {current.endpoint}
                </div>
                <h3 className="display text-xl font-bold text-foreground mt-0.5">
                  {current.name} · {current.badge}
                </h3>
                <p className="text-xs text-muted mt-1">{current.role}</p>
              </div>

              <div className="text-right">
                <span className={`chip ${currentGate.chip}`}>{currentGate.label}</span>
                <div className="mono text-xs font-semibold text-accent mt-1">{currentGate.cost}</div>
                {metered && (metered.spent_usd > 0 || metered.replayed_usd > 0) && (
                  <div className="mono text-[11px] text-dim mt-0.5" title="Measured on this page's runs, not a list price.">
                    this page: spent ${metered.spent_usd.toFixed(3)}
                    {metered.replayed_usd > 0 && ` · ${metered.replayed_usd.toFixed(3)} replayed, unspent`}
                  </div>
                )}
              </div>
            </div>

            {currentGate.reason && (
              <p className="text-xs text-muted">
                {currentGate.reason}
              </p>
            )}

            {/* Architectural Highlights */}
            <div>
              <div className="text-[11px] uppercase font-semibold text-dim mb-2">
                Structural Integration Highlights
              </div>
              <ul className="space-y-1.5 text-xs text-zinc-300">
                {current.details.map((d, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="text-parallel font-bold">✓</span>
                    <span>{d}</span>
                  </li>
                ))}
              </ul>
            </div>

            {/* Engineering Hygiene Box — every value below comes from /api/health */}
            <div className="p-3 bg-zinc-950 rounded border border-line/80 font-mono text-[11px] text-zinc-400 space-y-1">
              <div className="text-parallel font-semibold">{"// Session hygiene, as this deployment reports it:"}</div>
              <div>
                client_model: &quot;{health?.parallel_client_model || "…"}&quot; — the consuming-model tag Parallel&rsquo;s best
                practices ask callers to send; it names the model ScenePilot feeds these results to, not a model Parallel runs
              </div>
              <div>search_mode: &quot;{health?.parallel_search_mode || "…"}&quot;</div>
              <div>session_id: &quot;scenepilot_&lt;workflow&gt;_&lt;run id&gt;&quot; — one session per run, shared by search and extract</div>
              <div>apis_in_use: {(health?.parallel_apis || []).join(", ") || "…"}</div>
              <div>citations_persisted: true (inspectable in the Evidence Drawer)</div>
            </div>
          </div>

          {/* Verification Banner */}
          <div className="card p-4 bg-zinc-900/50 border border-line flex items-center justify-between flex-wrap gap-3">
            <div>
              <Kicker>Autonomous verifiability</Kicker>
              <div className="text-xs font-semibold mt-0.5">
                Every external claim is anchored to a real URL citation.
              </div>
              <p className="text-[11px] text-muted">
                Open the Evidence Drawer on any run to read the search, the excerpt and the source behind each fact.
              </p>
            </div>
            {health && (
              <div className="flex items-center gap-2 flex-wrap">
                <span className="chip chip-parallel text-xs font-semibold">{health.parallel_configured ? "PARALLEL_API_KEY set" : "no PARALLEL_API_KEY"}</span>
                <span className="chip chip-dim text-xs font-semibold mono">{health.recordings.parallel_search + health.recordings.parallel_extract} recorded Parallel calls</span>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="p-4 sm:p-5 border-t border-line bg-zinc-900/60 flex items-center justify-between text-xs text-muted shrink-0">
          <span className="flex items-center gap-2 flex-wrap">
            <span>Targeting Parallel Partner Track — Agentic Cinema Hackathon</span>
            {usage && (
              <span className="mono text-dim" title="Every Parallel call behind this page, priced. Replayed calls are listed separately because they cost nothing.">
                · this page cost <b className="text-accent">${usage.est_cost_usd.toFixed(2)}</b>
                {usage.replayed_cost_usd > 0 && ` (+ $${usage.replayed_cost_usd.toFixed(2)} answered from recordings, unspent)`}
              </span>
            )}
          </span>
          <button onClick={onClose} className="btn text-xs">
            Close Console
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
