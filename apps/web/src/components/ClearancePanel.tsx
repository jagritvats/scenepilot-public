"use client";

import { useState } from "react";
import { api, toMin, type PendingClearance, type ShootDay } from "@/lib/api";
import { Kicker } from "./ui";

/**
 * Clearing a resource onto a day — the write behind every "not cleared" blank.
 *
 * `Resource.availability` is read by the validator, the conflict heatmap, the ripple panel and the
 * call sheet, and until now it was written by the seed and by nothing else. So a committed pickup day
 * named the three people nobody had booked onto it and lost the list on the next navigation, and the
 * day page's constraints panel called that "a gap in the production data" with no remedy anywhere on
 * the screen. This is the remedy.
 *
 * The window stays visible and editable after the write rather than collapsing into a tick, because
 * the one thing the server does not report back is the one thing a producer needs to hear: a window
 * that does not cover the strips the resource is actually called for is a booking that leaves the day
 * exactly as invalid as it was, and looks like a fix. It logs that; the response says nothing of it.
 */

interface Booking {
  start: string;
  end: string;
  note: string;
}

/** HH may pass 23 — the night units on this production write past-midnight as "28:00", which is how
 *  Day 5 and Day 6 hard-wrap, so a pattern tightened to 00–23 would reject their own hard wrap. */
const TIME = /^\d{1,2}:[0-5]\d$/;
const asMinutes = (value: string) => (TIME.test(value.trim()) ? toMin(value.trim()) : null);
const hhmm = (minutes: number) => `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;

interface ClearanceRow {
  resourceId: string;
  name: string;
  type: string;
  reason: string | null;
}

export interface ClearancePanelProps {
  projectId: string;
  dayId: string;
  day: ShootDay;
  pendingClearance: PendingClearance[];
  disabled?: boolean;
  onChanged?: () => void;
}

export function ClearancePanel({ projectId, dayId, day, pendingClearance, disabled, onChanged }: ClearancePanelProps) {
  const [windows, setWindows] = useState<Record<string, Booking>>({});
  // What this panel has cleared in this session. A cleared resource drops straight out of
  // `pending_clearance` on the next payload, so without this the row — and the only way back out of
  // the booking — would vanish the instant it succeeded, with no confirmation of what was recorded.
  const [cleared, setCleared] = useState<Record<string, { name: string; type: string; start: string; end: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rows: ClearanceRow[] = [
    ...pendingClearance.map((p) => ({ resourceId: p.resource_id, name: p.name, type: p.type, reason: p.reason })),
    ...Object.entries(cleared)
      .filter(([id]) => !pendingClearance.some((p) => p.resource_id === id))
      .map(([id, c]) => ({ resourceId: id, name: c.name, type: c.type, reason: null })),
  ];

  // "Cleared for this day" means the day's own working span, which is what the window defaults to.
  const windowOf = (resourceId: string): Booking => windows[resourceId] ?? { start: day.unit_call, end: day.hard_wrap, note: "" };
  const edit = (resourceId: string, patch: Partial<Booking>) =>
    setWindows((prev) => ({ ...prev, [resourceId]: { ...(prev[resourceId] ?? { start: day.unit_call, end: day.hard_wrap, note: "" }), ...patch } }));

  // The span the day actually shoots, which is what a window has to cover to be worth anything. Held
  // in minutes rather than compared as strings: a night unit writes 01:00 the next morning as 25:00,
  // and "25:00" sorts before "07:00" the moment you compare the text.
  const strips = day.items.length
    ? { start: Math.min(...day.items.map((i) => toMin(i.start))), end: Math.max(...day.items.map((i) => toMin(i.end))) }
    : null;

  const problem = (w: Booking): string | null => {
    const start = asMinutes(w.start);
    const end = asMinutes(w.end);
    if (start === null || end === null) {
      return "That is not a time. Use HH:MM on the day's own clock — hours past midnight count on, so 01:00 the next morning is 25:00.";
    }
    if (end <= start) return `The window ${w.start}–${w.end} ends before it starts.`;
    return null;
  };

  /** Not a refusal. The server accepts a window this short and mentions the shortfall only in the
   *  activity log, so a producer who narrows one would otherwise read the 200 as a fix. */
  const shortfall = (w: Booking): string | null => {
    const start = asMinutes(w.start);
    const end = asMinutes(w.end);
    if (!strips || start === null || end === null) return null;
    if (start <= strips.start && end >= strips.end) return null;
    return `Day ${day.day_number} shoots ${hhmm(strips.start)}–${hhmm(strips.end)}. A window missing a strip this resource is called for is recorded all the same, and the day still validates as unavailable for it.`;
  };

  const clear = async (row: ClearanceRow) => {
    const w = windowOf(row.resourceId);
    setBusy(row.resourceId);
    setError(null);
    try {
      await api.clearResource(projectId, row.resourceId, { shoot_day_id: dayId, start: w.start.trim(), end: w.end.trim(), note: w.note.trim() || null });
      setCleared((prev) => ({ ...prev, [row.resourceId]: { name: row.name, type: row.type, start: w.start.trim(), end: w.end.trim() } }));
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const release = async (resourceId: string, name: string) => {
    if (
      !confirm(
        `Release ${name} from Day ${day.day_number}? The window naming this day is removed, the validator reads them as unavailable here again, and every scene ` +
          "on the day that calls them goes back to being unschedulable against them.",
      )
    )
      return;
    setBusy(resourceId);
    setError(null);
    try {
      await api.releaseResource(projectId, resourceId, dayId);
      setCleared((prev) => {
        const next = { ...prev };
        delete next[resourceId];
        return next;
      });
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  // A day with nothing to clear should not carry an empty card. One this panel has just cleared keeps
  // its rows, because the receipt and the way back out of the booking are the same row.
  if (rows.length === 0) return null;

  return (
    <section className="card p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Kicker>Not cleared for this day</Kicker>
        <span className="text-[12px] text-muted">
          Day {day.day_number}&rsquo;s scenes call these, and each of them has booked days on this production — but none naming this one. That reads to the
          validator as unavailable rather than as a quiet yes, so clearing one is what makes the day schedulable against it.
        </span>
      </div>

      <ul className="mt-3 grid gap-2 text-[13px]">
        {rows.map((row) => {
          const w = windowOf(row.resourceId);
          const done = cleared[row.resourceId];
          const bad = problem(w);
          const short = shortfall(w);
          return (
            <li key={row.resourceId} className={`rounded border p-2.5 ${done ? "border-ok/50 bg-ok/5" : "border-line bg-elev"}`}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="chip chip-dim">{row.type.toLowerCase()}</span>
                <span className="font-medium">{row.name}</span>
                {done && (
                  <span className="chip chip-ok">
                    cleared {done.start}–{done.end}
                  </span>
                )}
              </div>

              {row.reason && <p className="mt-1 text-[12px] text-muted">{row.reason}</p>}

              <div className="mt-1.5 flex items-center gap-2 flex-wrap text-[12px]">
                <span className="text-dim shrink-0">available</span>
                <input
                  value={w.start}
                  onChange={(e) => edit(row.resourceId, { start: e.target.value })}
                  disabled={!!disabled || busy !== null}
                  className="bg-elev border border-line rounded px-2 py-1 mono w-20 disabled:opacity-50"
                />
                <span className="text-dim">–</span>
                <input
                  value={w.end}
                  onChange={(e) => edit(row.resourceId, { end: e.target.value })}
                  disabled={!!disabled || busy !== null}
                  className="bg-elev border border-line rounded px-2 py-1 mono w-20 disabled:opacity-50"
                />
                <input
                  value={w.note}
                  onChange={(e) => edit(row.resourceId, { note: e.target.value })}
                  placeholder="note on the booking (optional)"
                  disabled={!!disabled || busy !== null}
                  className="bg-elev border border-line rounded px-2 py-1 flex-1 min-w-[10rem] disabled:opacity-50"
                />
                <button
                  className="btn btn-primary text-[11px]"
                  disabled={!!disabled || busy !== null || bad !== null}
                  title={
                    done
                      ? "Replaces the window naming this day — a booking is corrected, never widened by a second row."
                      : `Books ${row.name} for this window on Day ${day.day_number}.`
                  }
                  onClick={() => clear(row)}
                >
                  {done ? "Update the window" : `Clear for Day ${day.day_number}`}
                </button>
                {done && (
                  <button className="btn btn-ghost text-[11px]" disabled={!!disabled || busy !== null} onClick={() => release(row.resourceId, row.name)}>
                    Release
                  </button>
                )}
              </div>

              {bad && <p className="mt-1 text-[12px] text-warn">{bad}</p>}
              {!bad && short && <p className="mt-1 text-[12px] text-warn">{short}</p>}
            </li>
          );
        })}
      </ul>

      {error && <p className="mt-3 text-[12px] text-bad">{error}</p>}
    </section>
  );
}
