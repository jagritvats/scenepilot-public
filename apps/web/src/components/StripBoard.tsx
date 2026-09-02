"use client";

import { toMin, type Disruption, type Scene, type ScheduleItem, type ShootDay } from "@/lib/api";
import { stripToneClass } from "@/lib/stripboard";

/** Only for a caller with no day to derive an axis from — the old main-unit assumption. */
const FALLBACK_START = 6 * 60;
const FALLBACK_END = 22 * 60;

/**
 * Minutes past the shoot day's own midnight, written the way the schedule writes them: a night unit
 * wraps at "28:00" and lib/api.ts's `toMin` parses that straight back. Nothing here clamps to 23:59
 * — the clamp is what printed "WRAP (23:59)" on both night units, and clamping a *written* time
 * would silently move a strip several hours earlier on its way to the server.
 */
export const minToHhmm = (m: number) => {
  const t = Math.max(0, Math.round(m));
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
};

/**
 * The same minute for a human to read, in OperationsStrip's convention, so one wrap minute cannot
 * print as three different times across the page. Display only: `toMin("04:00 +1d")` is NaN, and that
 * NaN would go out in the /simulate-strip-move and apply payloads if this ever reached a ScheduleItem.
 */
export const hhmmDay = (m: number) => (m >= 24 * 60 ? `${minToHhmm(m % (24 * 60))} +1d` : minToHhmm(m));

export interface StripAxis {
  startMin: number;
  endMin: number;
  spanMin: number;
  /** 0–100, clamped — a strip that runs past the chart must not be painted outside it. */
  pct: (m: number) => number;
  /** Two-hourly ticks across the axis, for the header and the grid lines. */
  hours: number[];
}

/**
 * One time axis for every view of a shoot day, derived from the day instead of fixed at 06:00–22:00.
 *
 * The constants were a main-unit assumption. Day 6 calls at 16:00 and hard-wraps at "28:00", so its
 * last strip (21:00–23:30, 150 min) clamped at the right border and drew at *half* the width of the
 * 120-min strip before it, its label still reading 21:00–23:30; day 5's entire shoot sat in the last
 * sixth of a chart whose first three-quarters were empty. The bounds are the day's own operating
 * window — unit call, hard wrap and the standard-wrap marker — widened to whatever the strips
 * actually occupy, then padded half an hour and snapped outward to the hour so that nothing, marker
 * labels included, lands on the border.
 */
export function stripAxis(day?: ShootDay | null, ...itemSets: (ScheduleItem[] | undefined)[]): StripAxis {
  const build = (startMin: number, endMin: number): StripAxis => {
    const spanMin = endMin - startMin;
    const hours: number[] = [];
    for (let h = startMin; h <= endMin; h += 120) hours.push(h);
    return {
      startMin,
      endMin,
      spanMin,
      hours,
      pct: (m: number) => ((Math.max(startMin, Math.min(endMin, m)) - startMin) / spanMin) * 100,
    };
  };

  const marks: number[] = [];
  if (day) {
    const call = toMin(day.unit_call);
    if (Number.isFinite(call)) marks.push(call, call + Math.round(day.standard_hours * 60));
    const hard = toMin(day.hard_wrap);
    if (Number.isFinite(hard)) marks.push(hard);
  }
  // A caller that names no schedule of its own — the scrubber — is asking about the day as filed.
  const sets = itemSets.some((s) => s !== undefined) ? itemSets : [day?.items];
  for (const set of sets) {
    for (const it of set ?? []) {
      const s = toMin(it.start);
      const e = toMin(it.end);
      if (Number.isFinite(s)) marks.push(s);
      if (Number.isFinite(e)) marks.push(e);
    }
  }

  // Nothing to derive from: the old constants unpadded, so a caller with no day in hand draws
  // exactly the chart it drew before rather than a differently wrong one.
  if (!marks.length) return build(FALLBACK_START, FALLBACK_END);

  const startMin = Math.max(0, Math.floor((Math.min(...marks) - 30) / 60) * 60);
  const endMin = Math.max(startMin + 120, Math.ceil((Math.max(...marks) + 30) / 60) * 60);
  return build(startMin, endMin);
}

/** One mapping for every view that paints a strip — see lib/stripboard.ts. */
export function stripClass(s: Scene | undefined) {
  return stripToneClass(s) ?? "bg-line-strong";
}

export function StripBoard({
  day,
  items,
  scenes,
  disruption,
  affectedItemIds = [],
  deferredSceneIds = [],
  title,
  compact = false,
  ghost,
  scrubMin,
}: {
  day: ShootDay;
  items: ScheduleItem[];
  scenes: Record<string, Scene>;
  disruption?: Disruption | null;
  affectedItemIds?: string[];
  deferredSceneIds?: string[];
  title?: string;
  compact?: boolean;
  ghost?: ScheduleItem[];
  scrubMin?: number;
}) {
  // The ghost is on the axis too: CompareOptions draws two options against one baseline side by side,
  // and two charts that agree about the baseline but not about their own bounds compare nothing.
  const axis = stripAxis(day, items, ghost);
  const left = (m: number) => `${axis.pct(m)}%`;
  const width = (a: number, b: number) => `${axis.pct(b) - axis.pct(a)}%`;
  const wrap = toMin(day.unit_call) + Math.round(day.standard_hours * 60);
  const win = disruption?.window_start && disruption.window_end ? [toMin(disruption.window_start), toMin(disruption.window_end) + (disruption.dry_out_minutes || 0)] : null;
  const rowH = compact ? 34 : 46;
  const sorted = [...items].sort((a, b) => toMin(a.start) - toMin(b.start));
  return (
    <div className="card p-4">
      {title && <div className="kicker mb-3">{title}</div>}
      <div className="relative" style={{ height: rowH * Math.max(1, sorted.length + (deferredSceneIds.length ? 1 : 0)) + 28 }}>
        {/* hour grid */}
        <div className="absolute inset-x-0 top-0 h-5 text-[10px] mono text-dim">
          {axis.hours.map((h) => (
            <span key={h} className="absolute -translate-x-1/2" style={{ left: left(h) }}>
              {hhmmDay(h)}
            </span>
          ))}
        </div>
        <div className="absolute inset-x-0 top-6 bottom-0">
          {axis.hours.map((h) => (
            <div key={h} className="absolute top-0 bottom-0 border-l border-line" style={{ left: left(h) }} />
          ))}
          {/* golden hour */}
          <div className="absolute top-0 bottom-0 golden" style={{ left: left(toMin(day.golden_hour_dusk[0])), width: width(toMin(day.golden_hour_dusk[0]), toMin(day.golden_hour_dusk[1])) }} title={`Golden hour ${day.golden_hour_dusk[0]}–${day.golden_hour_dusk[1]}`} />
          {/* overtime line */}
          <div className="absolute top-0 bottom-0 border-l border-dashed border-warn/70" style={{ left: left(wrap) }} title={`Overtime after ${hhmmDay(wrap)}`} />
          {/* disruption window */}
          {win && (
            <div className="absolute top-0 bottom-0 hatch" style={{ left: left(win[0]), width: width(win[0], win[1]) }} title={`${disruption?.title} (+${disruption?.dry_out_minutes} min dry-out)`}>
              <span className="absolute bottom-0.5 left-1.5 text-[10px] mono text-bad whitespace-nowrap">{disruption?.window_start}–{disruption?.window_end}</span>
            </div>
          )}
          {/* timeline scrub needle */}
          {scrubMin !== undefined && (
            <div
              className="absolute top-0 bottom-0 border-l-2 border-accent z-20 pointer-events-none transition-all shadow-[0_0_8px_rgba(56,189,248,0.8)]"
              style={{ left: left(scrubMin) }}
            >
              <span className="absolute -top-5 -translate-x-1/2 bg-accent text-black mono text-[9px] font-bold px-1 rounded shadow">
                {hhmmDay(scrubMin)}
              </span>
            </div>
          )}
          {/* ghost strips (before) */}
          {ghost?.map((g, i) => (
            <div key={"g" + g.id} className="absolute rounded-[3px] border border-dashed border-line-strong" style={{ left: left(toMin(g.start)), width: width(toMin(g.start), toMin(g.end)), top: i * rowH + 4, height: rowH - 8 }} />
          ))}
          {/* strips */}
          {sorted.map((it, i) => {
            const s = scenes[it.scene_id];
            if (!s) return null;
            const risk = affectedItemIds.includes(it.id) || it.status === "AT_RISK";
            return (
              <div key={it.id} className={`strip ${stripClass(s)} ${risk ? "strip-risk" : ""}`} style={{ left: left(toMin(it.start)), width: width(toMin(it.start), toMin(it.end)), top: i * rowH + 4, height: rowH - 8 }} title={`${it.start}–${it.end} Sc ${s.number} ${s.heading}`}>
                <div className="px-2 h-full flex items-center gap-2 overflow-hidden">
                  <span className="display font-bold text-[15px] leading-none">{s.number}</span>
                  {!compact && <span className="text-[11px] font-medium truncate">{s.heading}</span>}
                  <span className="mono text-[10px] ml-auto shrink-0 opacity-80">{it.start}–{it.end}</span>
                  {it.status === "MOVED" && <span className="text-[10px] font-semibold shrink-0">MOVED</span>}
                  {risk && <span className="text-[10px] font-bold text-bad shrink-0">AT RISK</span>}
                </div>
              </div>
            );
          })}
          {deferredSceneIds.length > 0 && (
            <div className="absolute left-0 right-0 flex items-center gap-2 px-2 text-[11px] text-muted" style={{ top: sorted.length * rowH + 4, height: rowH - 8 }}>
              <span className="chip chip-warn">carried over</span>
              {deferredSceneIds.map((sid) => (
                <span key={sid} className="mono">Sc {scenes[sid]?.number}</span>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-dim">
        <span><i className="inline-block w-3 h-2 rounded-sm align-middle mr-1" style={{ background: "var(--strip-ext-day)" }} />EXT day</span>
        <span><i className="inline-block w-3 h-2 rounded-sm align-middle mr-1" style={{ background: "var(--strip-int-day)" }} />INT day</span>
        <span><i className="inline-block w-3 h-2 rounded-sm align-middle mr-1" style={{ background: "var(--strip-dusk)" }} />golden hour</span>
        <span><i className="inline-block w-3 h-2 rounded-sm align-middle mr-1" style={{ background: "var(--strip-ext-night)" }} />EXT night</span>
        <span><i className="inline-block w-3 h-2 rounded-sm align-middle mr-1" style={{ background: "var(--strip-int-night)" }} />INT night</span>
        <span className="ml-auto">dashed line = overtime threshold · hatched = disruption window incl. dry-out</span>
      </div>
    </div>
  );
}
