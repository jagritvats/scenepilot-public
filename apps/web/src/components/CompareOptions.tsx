"use client";

import { inr, type LocationFact, type RecoveryOption, type Scene, type ScheduleItem, type ShootDay } from "@/lib/api";
import { CostWaterfall, OptionDetail } from "./OptionCard";
import { MultiDayPanel } from "./MultiDayPanel";
import { StripBoard } from "./StripBoard";
import { Kicker } from "./ui";

/**
 * Two recovery options, side by side.
 *
 * Producers choose between two things, not among five, and the page could only ever show one option
 * at a time — so comparing meant clicking back and forth and holding the difference in your head.
 *
 * The summary is differences-only, in the spirit of the labor-pack table beside it: a row where both
 * options agree is not a comparison, it is furniture. Every figure is read off `ScoreComponents` or
 * computed against the baseline schedule; nothing here is priced or re-derived.
 */

type Row = { label: string; a: string; b: string; delta: string | null };

const movedScenes = (option: RecoveryOption, baseline: ScheduleItem[]) =>
  option.schedule.filter((item) => {
    const before = baseline.find((x) => x.scene_id === item.scene_id);
    return before && before.start !== item.start;
  }).length;

function summaryRows(a: RecoveryOption, b: RecoveryOption, baseline: ScheduleItem[]): Row[] {
  const money = (n: number) => inr(n);
  const plain = (n: number) => String(n);
  const rows: [string, number, number, (n: number) => string][] = [
    ["Extra cost", a.score?.estimated_extra_cost_inr ?? 0, b.score?.estimated_extra_cost_inr ?? 0, money],
    ["Overtime", a.score?.overtime_minutes ?? 0, b.score?.overtime_minutes ?? 0, (n) => `${n} min`],
    ["Extra company moves", a.score?.extra_company_moves ?? 0, b.score?.extra_company_moves ?? 0, plain],
    ["Scenes carried over", a.deferred_scene_ids.length, b.deferred_scene_ids.length, plain],
    ["Scenes moved", movedScenes(a, baseline), movedScenes(b, baseline), plain],
    ["Score", Math.round(a.score?.total ?? 0), Math.round(b.score?.total ?? 0), plain],
  ];

  const out: Row[] = [
    {
      label: "Verdict",
      a: a.feasible ? "feasible" : "rejected",
      b: b.feasible ? "feasible" : "rejected",
      delta: a.feasible === b.feasible ? null : "differ",
    },
  ];
  for (const [label, av, bv, fmt] of rows) {
    if (av === bv) continue; // a row both options agree on is furniture, not a comparison
    const diff = bv - av;
    out.push({ label, a: fmt(av), b: fmt(bv), delta: `${diff > 0 ? "+" : "−"}${fmt(Math.abs(diff))}` });
  }
  return out;
}

function Column({
  option,
  baseline,
  day,
  scenes,
  facts,
  projectId,
  dayId,
}: {
  option: RecoveryOption;
  baseline: ScheduleItem[];
  day: ShootDay;
  scenes: Record<string, Scene>;
  facts: LocationFact[];
  projectId: string;
  dayId: string;
}) {
  return (
    <div className="space-y-3 min-w-0">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`chip ${option.feasible ? "chip-ok" : "chip-bad"}`}>Option {option.label}</span>
        <span className="text-[12px] text-muted truncate">{option.title}</span>
      </div>
      <StripBoard
        day={day}
        items={option.schedule}
        scenes={scenes}
        deferredSceneIds={option.deferred_scene_ids}
        ghost={baseline}
        compact
        title={`Option ${option.label} against the baseline`}
      />
      <CostWaterfall violations={option.violations} />
      <OptionDetail o={option} facts={facts} />
      {option.deferred_scene_ids.length > 0 && (
        <MultiDayPanel projectId={projectId} dayId={dayId} deferredSceneIds={option.deferred_scene_ids} scenes={scenes} />
      )}
    </div>
  );
}

export function CompareOptions({
  a,
  b,
  baseline,
  day,
  scenes,
  facts,
  projectId,
  dayId,
  onClose,
}: {
  a: RecoveryOption;
  b: RecoveryOption;
  baseline: ScheduleItem[];
  day: ShootDay;
  scenes: Record<string, Scene>;
  facts: LocationFact[];
  projectId: string;
  dayId: string;
  onClose: () => void;
}) {
  const rows = summaryRows(a, b, baseline);

  return (
    <section id="compare" className="card p-4 space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <Kicker>Compare · option {a.label} against option {b.label}</Kicker>
        <button className="btn btn-ghost text-xs ml-auto" onClick={onClose}>
          Close comparison
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[12px] min-w-[420px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-dim">
              <th className="text-left py-1 pr-2 font-normal">Only what differs</th>
              <th className="text-right py-1 pr-2 font-normal">Option {a.label}</th>
              <th className="text-right py-1 pr-2 font-normal">Option {b.label}</th>
              <th className="text-right py-1 font-normal">Δ</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 1 && rows[0].delta === null ? (
              <tr>
                <td colSpan={4} className="py-2 text-muted">
                  These two options cost the same, move the same scenes and carry the same nothing — they differ only in
                  their ordering.
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.label} className="border-t border-line/60">
                  <td className="py-1 pr-2 text-muted">{r.label}</td>
                  <td className="py-1 pr-2 text-right mono">{r.a}</td>
                  <td className="py-1 pr-2 text-right mono">{r.b}</td>
                  <td className="py-1 text-right mono text-dim">{r.delta ?? "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Column option={a} baseline={baseline} day={day} scenes={scenes} facts={facts} projectId={projectId} dayId={dayId} />
        <Column option={b} baseline={baseline} day={day} scenes={scenes} facts={facts} projectId={projectId} dayId={dayId} />
      </div>
    </section>
  );
}
