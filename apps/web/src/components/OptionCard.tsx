"use client";

import { inr, type ConstraintViolation, type LocationFact, type RecoveryOption, type Scene } from "@/lib/api";
import { ProvenanceChain } from "./ProvenanceChain";
import { Bar } from "./ui";

export function OptionRow({ o, selected, recommended, onSelect, scenes }: { o: RecoveryOption; selected: boolean; recommended: boolean; onSelect: () => void; scenes: Record<string, Scene> }) {
  return (
    <button onClick={onSelect} className={`w-full min-w-0 max-w-full text-left rounded-lg border px-3 py-2.5 transition ${selected ? "border-accent bg-accent/5" : "border-line hover:border-line-strong"} ${!o.feasible ? "opacity-80" : ""}`} aria-pressed={selected}>
      <div className="flex items-center gap-3">
        <span className={`display font-bold text-2xl w-7 ${o.feasible ? "text-fg" : "text-dim line-through"}`}>{o.label}</span>
        <span className={`display font-bold text-2xl w-12 ${o.feasible ? "text-accent" : "text-dim"}`}>{o.feasible ? o.score?.total : "—"}</span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium truncate">{o.title}</div>
          {(!o.feasible || o.title !== o.strategy) && (
            <div className="text-[11px] text-muted truncate">{o.feasible ? o.strategy : `rejected — ${o.rejected_reason}`}</div>
          )}
        </div>
        <div className="shrink-0 flex items-center gap-1.5">
          {recommended && <span className="chip chip-accent">recommended</span>}
          {!o.feasible && <span className="chip chip-bad">infeasible</span>}
          {o.origin.includes("gemini") && <span className="chip chip-gemini">gemini</span>}
        </div>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1 text-[10px] mono text-dim">
        {o.schedule.map((it) => (
          <span key={it.id} className="px-1.5 py-0.5 rounded bg-elev border border-line">Sc {scenes[it.scene_id]?.number} {it.start}</span>
        ))}
        {o.deferred_scene_ids.map((s) => (
          <span key={s} className="px-1.5 py-0.5 rounded border border-warn/40 text-warn">Sc {scenes[s]?.number} → carry over</span>
        ))}
      </div>
    </button>
  );
}

/**
 * Where an option's money goes, by kind.
 *
 * Extracted from OptionDetail so two options can be priced side by side in the compare view without
 * the breakdown being rebuilt — and diverging — in a second place.
 */
export function CostWaterfall({ violations }: { violations: ConstraintViolation[] }) {
  const costs = violations.filter((v) => v.cost_inr > 0);
  if (costs.length === 0) return null;
  const byKind = new Map<string, number>();
  for (const v of costs) byKind.set(v.kind, (byKind.get(v.kind) || 0) + v.cost_inr);
  const total = [...byKind.values()].reduce((a, b) => a + b, 0);
  const label: Record<string, string> = { OVERTIME: "overtime", SCENE_DEFERRED: "carry-over", EQUIPMENT_RERENTAL: "equipment re-rental", EXTRA_COMPANY_MOVE: "extra company moves", MEAL_BREAK: "meal penalty" };
  return (
    <div>
      <div className="kicker mb-1">Cost waterfall</div>
      <div className="space-y-1">
        {[...byKind.entries()].sort((a, b) => b[1] - a[1]).map(([k, v]) => (
          <div key={k} className="flex items-center gap-2 text-[12px]">
            <span className="w-24 lg:w-36 shrink-0 text-muted truncate">{label[k] || k.toLowerCase()}</span>
            <div className="flex-1 h-2 rounded bg-line overflow-hidden"><div className="h-full bg-warn" style={{ width: `${(v / total) * 100}%` }} /></div>
            <span className="mono w-16 lg:w-20 text-right shrink-0">{inr(v)}</span>
          </div>
        ))}
        <div className="flex items-center gap-2 text-[12px] font-semibold pt-1 border-t border-line">
          <span className="w-24 lg:w-36 shrink-0">total extra cost</span>
          <span className="flex-1" />
          <span className="mono w-16 lg:w-20 text-right shrink-0">{inr(total)}</span>
        </div>
      </div>
    </div>
  );
}

export function OptionDetail({ o, facts = [] }: { o: RecoveryOption; facts?: LocationFact[] }) {
  const s = o.score;
  // A rejection that came from web research shows its chain back to the page that said so.
  const externals = o.violations.filter((v) => v.fact_id || v.evidence_url);
  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-3">
          <span className="display font-bold text-3xl">{o.label}</span>
          <div>
            <div className="font-medium">{o.title}</div>
            <div className="text-[12px] text-muted">{o.strategy}</div>
          </div>
        </div>
        {o.explanation && <p className="mt-3 text-sm text-fg/90 leading-relaxed">{o.explanation}</p>}
      </div>
      <ul className="space-y-1.5">
        {o.checks.map((c) => (
          <li key={c.label} className="flex items-start gap-2 text-sm">
            <span className={`mono shrink-0 w-4 ${c.ok ? "text-ok" : c.hard ? "text-bad" : "text-warn"}`}>{c.ok ? "✓" : c.hard ? "✗" : "⚠"}</span>
            <span className={c.ok ? "text-fg" : c.hard ? "text-bad" : "text-fg"}>
              {c.label}
              {c.detail && <span className="text-muted"> — {c.detail}</span>}
            </span>
          </li>
        ))}
      </ul>
      {externals.map((v, i) => (
        <ProvenanceChain key={v.fact_id || i} violation={v} fact={facts.find((f) => f.id === v.fact_id)} />
      ))}
      {s && (
        <div className="space-y-1.5">
          <div className="kicker">Score components</div>
          <Bar label="schedule preservation" value={s.schedule_preservation} tone="ok" />
          <Bar label="cost impact" value={s.cost_impact} tone="warn" />
          <Bar label="overtime risk" value={s.overtime_risk} tone="warn" />
          <Bar label="company moves" value={s.company_moves} tone="info" />
          <Bar label="resource conflicts" value={s.resource_conflicts} tone="info" />
          <Bar label="creative compromise" value={s.creative_compromise} tone="accent" />
          <Bar label="evidence confidence" value={s.confidence} tone="info" />
          <div className="text-[11px] text-dim mono pt-1">
            extra cost ≈ {inr(s.estimated_extra_cost_inr)} · overtime {s.overtime_minutes} min · +{s.extra_company_moves} moves · total = weighted sum (0.30/0.15/0.15/0.10/0.10/0.15/0.05){!s.feasible && " · forced to 0: hard constraint"}
          </div>
        </div>
      )}
      {o.deferred_scene_ids.length > 0 && (
        <div className="card p-3 bg-amber-500/10 border-amber-500/30 text-xs space-y-1">
          <div className="text-[10px] uppercase font-bold text-amber-400 tracking-wider flex items-center gap-1.5">
            <span>⚡ Multi-Day Downstream Ripple</span>
          </div>
          <p className="text-amber-200">
            {o.deferred_scene_ids.map((id) => id.replace("sc_", "Sc ")).join(", ")} deferred to preserve crew turnaround and avoid wet-hazard stunts.
          </p>
          <div className="text-[11px] text-muted pt-1 border-t border-amber-500/20 flex items-center justify-between">
            <span>Target placement: <strong className="text-foreground">Day 5 / Day 6</strong></span>
            <span className="mono text-amber-300 font-semibold">Absorbed downstream</span>
          </div>
        </div>
      )}
      <CostWaterfall violations={o.violations} />
      {o.trade_offs.length > 0 && (
        <div>
          <div className="kicker mb-1">Trade-offs</div>
          <ul className="list-disc pl-5 text-sm text-muted space-y-0.5">
            {o.trade_offs.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
      )}
      {o.violations.length > 0 && (
        <details className="text-[12px]">
          <summary className="cursor-pointer text-muted">Constraint checks ({o.violations.length})</summary>
          <ul className="mt-1 space-y-0.5 mono">
            {o.violations.map((v, i) => (
              <li key={i} className={v.hard ? "text-bad" : "text-warn"}>
                {v.hard ? "HARD" : "soft"} {v.kind} — {v.message}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
