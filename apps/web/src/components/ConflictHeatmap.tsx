"use client";

import { useEffect, useState } from "react";
import { api, type ConflictHeatmap as Heatmap } from "@/lib/api";
import { Kicker } from "./ui";

/**
 * Where this production has no slack left — every constrained resource against every day.
 *
 * The board answers "is this day shootable"; this answers the question behind it, which is why one
 * rainy afternoon cascades and another does not. A row with three tight days has nowhere to move a
 * scene *to*, and that is the thing a producer cannot see by looking at any single day.
 *
 * The three-way colour is load-bearing. A cell with no availability on file is *unconstrained* — a
 * grip who can work any hour — while a cell whose resource has booked days elsewhere but none here
 * is *unavailable*, which is the validator's own reading. Painting those alike would show the crew
 * as maximally constrained and an unbooked lead as free.
 */

const TONE: Record<string, string> = {
  unconstrained: "bg-line",
  not_booked: "bg-bad",
};

function cellTone(cell: Heatmap["rows"][number]["cells"][number]): string {
  if (!cell.booked) return "bg-transparent border border-line/50";
  if (cell.conflicts.length) return "bg-bad";
  if (cell.availability === "not_booked") return TONE.not_booked;
  if (cell.availability === "unconstrained" || cell.pressure === null) return TONE.unconstrained;
  if (cell.pressure >= 0.85) return "bg-warn";
  if (cell.pressure >= 0.6) return "bg-warn/60";
  return "bg-ok/50";
}

export function ConflictHeatmap({ projectId }: { projectId: string }) {
  const [data, setData] = useState<Heatmap | null>(null);

  useEffect(() => {
    let alive = true;
    api.conflictHeatmap(projectId).then((r) => alive && setData(r)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [projectId]);

  if (!data) return <div className="card h-40 shimmer" />;
  if (data.rows.length === 0) {
    return (
      <section className="card p-4">
        <Kicker>Booking pressure</Kicker>
        <p className="mt-1 text-[12px] text-muted">Nothing on this schedule is called yet, so there is no pressure to measure.</p>
      </section>
    );
  }

  return (
    <section className="card p-4 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Kicker>Booking pressure · resource × day</Kicker>
        <span className="ml-auto text-[11px] text-dim">
          {data.rows.filter((r) => r.conflict_days > 0).length} resource(s) in conflict
        </span>
      </div>

      <div className="overflow-x-auto scroll-thin">
        <table className="w-full text-[12px] min-w-[420px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-dim">
              <th className="text-left py-1 pr-2 font-normal">Resource</th>
              {data.days.map((d) => (
                <th key={d.shoot_day_id} className="py-1 px-1 font-normal text-center" title={`${d.date} · ${d.status.toLowerCase()}`}>
                  D{d.day_number}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={row.resource_id} className="border-t border-line/60">
                <td className="py-1 pr-2 truncate max-w-[220px]" title={`${row.name} · ${row.type.toLowerCase()}`}>
                  {row.cast_number !== null && <span className="mono text-dim mr-1">{row.cast_number}</span>}
                  {row.name}
                </td>
                {row.cells.map((cell, i) => (
                  <td key={i} className="py-1 px-1">
                    <div
                      className={`h-5 rounded-sm ${cellTone(cell)} flex items-center justify-center`}
                      title={`${row.name} · Day ${data.days[i].day_number} — ${cell.detail}${
                        cell.conflicts.length ? `\n\nCONFLICT: ${cell.conflicts.join("; ")}` : ""
                      }${cell.margin_minutes !== null ? `\n${cell.margin_minutes} min of slack left in the window` : ""}`}
                    >
                      {cell.conflicts.length > 0 && <span className="text-[9px] font-bold text-bg">!</span>}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-x-3 gap-y-1 flex-wrap text-[10px] text-dim">
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 rounded-sm bg-ok/50" /> room to move</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 rounded-sm bg-warn/60" /> tight</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 rounded-sm bg-warn" /> almost no slack</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 rounded-sm bg-bad" /> conflict or not cleared for the day</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2.5 w-4 rounded-sm bg-line" /> unconstrained</span>
      </div>
      <p className="text-[10px] text-dim">
        {data.legend.unconstrained} {data.legend.not_booked} {data.provenance}
      </p>
    </section>
  );
}
