"use client";

import { useEffect, useRef, useState } from "react";
import { fmtTime, type ActivityEvent } from "@/lib/api";

/* Must stay in step with `ACTIVITY_KINDS` in `api/app.py`, which the production log reads from the
 * API. `decision`, `dispatch` and `dispatch_reping` were missing here and fell through to `info`, so a producer
 * accepting a cited statute as a hard constraint — the most consequential line this feed carries —
 * rendered in the same grey as a database migration note. */
const TAG: Record<string, { label: string; cls: string }> = {
  parallel: { label: "PARALLEL", cls: "text-parallel" },
  gemini: { label: "GEMINI", cls: "text-gemini" },
  deterministic: { label: "ENGINE", cls: "text-accent" },
  decision: { label: "PRODUCER", cls: "text-ok" },
  approval: { label: "APPROVED", cls: "text-ok" },
  action: { label: "ACTION", cls: "text-ok" },
  dispatch: { label: "DISPATCH", cls: "text-accent" },
  dispatch_reping: { label: "DISPATCH", cls: "text-accent" },
  warning: { label: "WARN", cls: "text-warn" },
  error: { label: "ERROR", cls: "text-bad" },
  info: { label: "ORCH", cls: "text-muted" },
};

const PACE_MS = 650;

export function ActivityFeed({ events, live, onOpenSearch }: { events: ActivityEvent[]; live?: boolean; onOpenSearch?: (id: string) => void }) {
  const log = useRef<HTMLDivElement>(null);
  // Demo pacing: reveal new events one at a time at reading speed (for recordings); off = show everything at once.
  const [paced, setPaced] = useState(false);
  const [revealed, setRevealed] = useState(0);
  useEffect(() => {
    if (!paced || revealed >= events.length) return;
    const t = setTimeout(() => setRevealed((v) => Math.min(v + 1, events.length)), PACE_MS);
    return () => clearTimeout(t);
  }, [paced, revealed, events.length]);
  const shown = paced ? events.slice(0, Math.min(revealed, events.length)) : events;
  // Pin the log to its newest line by moving the log's *own* scrollbar.
  //
  // This used to call `scrollIntoView` on a marker at the end of the list, and `scrollIntoView`
  // scrolls every scrollable ancestor — the window included. The feed sits low in the right rail, so
  // the moment a day's events arrived the browser scrolled the whole page down to it, which is why
  // deep links into the sections above never appeared to land: they landed, and then this dragged
  // the page off them a moment later.
  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown.length]);
  const togglePace = () => {
    const next = !paced;
    setRevealed(events.length); // already-visible events stay; only new ones are paced
    setPaced(next);
  };
  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-2.5 border-b border-line flex items-center justify-between">
        <div className="kicker">Agent activity</div>
        <div className="flex items-center gap-3 text-[11px] text-dim">
          <span className="text-parallel">● parallel</span>
          <span className="text-gemini">● gemini</span>
          <span className="text-accent">● engine</span>
          {live && <span className="chip chip-info pulse">running</span>}
          <button onClick={togglePace} className={`chip ${paced ? "chip-accent" : "chip-dim"}`} title="Reveal events at reading speed (for demo recordings)">
            {paced ? "paced" : "pace"}
          </button>
        </div>
      </div>
      <div ref={log} className="mono text-[12px] leading-5 max-h-[420px] overflow-auto scroll-thin p-3 space-y-0.5">
        {events.length === 0 && <div className="text-dim">No activity yet.</div>}
        {shown.map((e) => {
          const t = TAG[e.kind] || TAG.info;
          const sid = typeof e.meta?.search_run_id === "string" ? (e.meta.search_run_id as string) : typeof e.meta?.extract_run_id === "string" ? (e.meta.extract_run_id as string) : null;
          return (
            <div key={e.id} className="rise flex gap-3 items-start">
              <span className="text-dim shrink-0">{fmtTime(e.ts)}</span>
              <span className={`shrink-0 w-16 font-medium ${t.cls}`}>{t.label}</span>
              <span className="text-fg/90 break-words">
                {e.message}
                {sid && onOpenSearch && (
                  <button onClick={() => onOpenSearch(sid)} className="ml-2 text-parallel underline decoration-dotted underline-offset-2 hover:text-fg">
                    view
                  </button>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
