"use client";

import Link from "next/link";
import { use, useMemo, useState } from "react";
import { api, fmtTime, type ActivityEvent, type ProductionLog } from "@/lib/api";
import { boardDate } from "@/lib/stripboard";
import { Kicker, LoadError } from "@/components/ui";
import { usePoll } from "@/lib/usePoll";

/**
 * The production log — every recorded act on this show, in the order it happened.
 *
 * The audit trail was always being written: twenty-eight sites across the API and the workflows log
 * a line at the moment they do something, and `GET /api/projects/{id}/activity` has always returned
 * all of it. Nothing read it. So the one thing a producer most needs from a system that changes
 * their schedule — *who decided what, on what evidence, and when* — existed complete in the database
 * and appeared on no screen.
 *
 * Two rules shape this page. Nothing here is reconstructed for display: each line was written by the
 * code that performed the act, not assembled afterwards from state. And the vocabulary comes from
 * the API rather than from a table in this file, because the previous copy of that table was missing
 * `decision` — so a producer accepting a cited statute as a hard constraint rendered in the same grey
 * as a database migration note.
 */

/** Colour is presentation and lives here; what a kind *means* comes from the API. */
const CATEGORY_TONE: Record<string, { chip: string; text: string; label: string }> = {
  decision: { chip: "chip-ok", text: "text-ok", label: "Producer decisions" },
  evidence: { chip: "chip-parallel", text: "text-parallel", label: "Evidence — Parallel" },
  reasoning: { chip: "chip-gemini", text: "text-gemini", label: "Reasoning — Gemini" },
  engine: { chip: "chip-accent", text: "text-accent", label: "Constraint engine" },
  attention: { chip: "chip-warn", text: "text-warn", label: "Needs attention" },
  orchestration: { chip: "chip-dim", text: "text-muted", label: "Orchestration" },
};

export default function ProductionLogPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, loading } = usePoll(() => api.productionLog(id), () => false, 10000);
  const [active, setActive] = useState<Set<string>>(new Set());
  const [newestFirst, setNewestFirst] = useState<boolean>(false);

  const runIndex = useMemo(
    () => new Map((data?.runs || []).map((r) => [r.id, r] as const)),
    [data?.runs]
  );

  if (!data) {
    return loading || !error ? (
      <div className="card p-8 shimmer h-72" />
    ) : (
      <LoadError error={error} missing="Production log unavailable" hint="This project has no recorded activity to show." />
    );
  }

  const categoryOf = (kind: string) => data.kinds[kind]?.category ?? "orchestration";
  const shown = data.events.filter((e) => active.size === 0 || active.has(categoryOf(e.kind)));
  const ordered = newestFirst ? [...shown].reverse() : shown;

  // Grouped by the calendar day the act happened on, which is how a log is read back.
  const byDay: { date: string; events: ActivityEvent[] }[] = [];
  for (const event of ordered) {
    const date = event.ts.slice(0, 10);
    const last = byDay[byDay.length - 1];
    if (last && last.date === date) last.events.push(event);
    else byDay.push({ date, events: [event] });
  }

  const toggle = (category: string) => {
    const next = new Set(active);
    if (next.has(category)) next.delete(category);
    else next.add(category);
    setActive(next);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 flex-wrap print:hidden">
        <div>
          <Kicker>Production log · the audit trail</Kicker>
          <h1 className="display text-3xl font-bold">Every decision, and the evidence behind it</h1>
          <p className="text-muted text-sm mt-1 max-w-3xl">
            Each line below was written at the moment the thing happened, by the code that did it — a Parallel call, a
            constraint the engine refused, a fact a producer accepted, a change set applied. Nothing here is
            reconstructed for display.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button className={`chip ${newestFirst ? "chip-accent" : "chip-dim"}`} onClick={() => setNewestFirst(!newestFirst)}>
            {newestFirst ? "newest first" : "oldest first"}
          </button>
          <button className="btn text-xs" onClick={() => window.print()}>Print / PDF</button>
          <Link href={`/projects/${id}`} className="btn btn-ghost text-xs">Back to the production</Link>
        </div>
      </div>

      {/* Counts by category, doubling as the filter. A category with nothing in it is still shown,
          at zero, because "no producer decision has been recorded yet" is information. */}
      <div className="flex items-center gap-2 flex-wrap print:hidden">
        {data.categories.map((category) => {
          const tone = CATEGORY_TONE[category] ?? CATEGORY_TONE.orchestration;
          const count = data.counts_by_category[category] ?? 0;
          const on = active.has(category);
          return (
            <button
              key={category}
              onClick={() => toggle(category)}
              disabled={count === 0}
              className={`chip ${on ? tone.chip : "chip-dim"} ${count === 0 ? "opacity-40 cursor-not-allowed" : ""}`}
              title={
                Object.entries(data.kinds)
                  .filter(([, v]) => v.category === category)
                  .map(([, v]) => v.description)
                  .join(" ") || undefined
              }
            >
              {tone.label} · {count}
            </button>
          );
        })}
        {active.size > 0 && (
          <button className="text-[11px] text-muted hover:text-fg underline" onClick={() => setActive(new Set())}>
            clear filter
          </button>
        )}
      </div>

      {data.truncated && (
        <p className="text-[11px] text-warn print:hidden">
          Showing the most recent {data.total} entries. Older activity is still in the database and is not displayed here.
        </p>
      )}

      {ordered.length === 0 ? (
        <div className="card p-6 text-sm text-muted">
          {data.events.length === 0
            ? "Nothing has happened on this production yet. Report a disruption or research a location and every step of it is recorded here."
            : "No entry matches the selected categories."}
        </div>
      ) : (
        <div className="space-y-4">
          {byDay.map((group) => (
            <section key={group.date} className="card overflow-hidden break-inside-avoid">
              <div className="px-4 py-2 border-b border-line flex items-baseline gap-3">
                <span className="display font-bold text-sm">{boardDate(group.date)}</span>
                <span className="text-[11px] text-dim">{group.events.length} entr{group.events.length === 1 ? "y" : "ies"}</span>
              </div>
              <ul className="divide-y divide-line/60">
                {group.events.map((event) => (
                  <LogRow key={event.id} event={event} projectId={id} kinds={data.kinds} runIndex={runIndex} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function LogRow({
  event,
  projectId,
  kinds,
  runIndex,
}: {
  event: ActivityEvent;
  projectId: string;
  kinds: ProductionLog["kinds"];
  runIndex: Map<string, ProductionLog["runs"][number]>;
}) {
  const kind = kinds[event.kind] ?? { label: event.kind.toUpperCase(), category: "orchestration", description: "" };
  const tone = CATEGORY_TONE[kind.category] ?? CATEGORY_TONE.orchestration;
  const run = event.run_id ? runIndex.get(event.run_id) : undefined;
  const links = deepLinks(event, projectId, run);

  return (
    <li className="px-4 py-2 flex gap-3 items-start text-[12px] hover:bg-elev/40">
      <span className="mono text-dim shrink-0 pt-0.5">{fmtTime(event.ts)}</span>
      <span className={`mono shrink-0 w-20 font-semibold pt-0.5 ${tone.text}`} title={kind.description}>
        {kind.label}
      </span>
      <div className="flex-1 min-w-0">
        <span className="text-fg/90 break-words">{event.message}</span>
        {(links.length > 0 || run) && (
          <div className="flex items-center gap-2 flex-wrap mt-0.5">
            {run && (
              <span className="mono text-[10px] text-dim" title={`Written inside a ${run.kind.toLowerCase()} run (${run.status.toLowerCase()}).`}>
                {run.kind.toLowerCase()} run
              </span>
            )}
            {links.map((l) => (
              <Link key={l.href} href={l.href} className="text-[11px] text-accent hover:underline print:hidden">
                {l.label} →
              </Link>
            ))}
          </div>
        )}
      </div>
    </li>
  );
}

/**
 * Where an entry lets you go next, derived from the `meta` the writer recorded.
 *
 * Only links to routes that exist. A log whose every line offers a link, half of them dead, is worse
 * than one that links where it can — so a `meta` key with nowhere to point simply produces no link.
 */
function deepLinks(
  event: ActivityEvent,
  projectId: string,
  run?: ProductionLog["runs"][number]
): { href: string; label: string }[] {
  const meta = (event.meta || {}) as Record<string, unknown>;
  const out: { href: string; label: string }[] = [];
  const str = (k: string) => (typeof meta[k] === "string" ? (meta[k] as string) : null);

  const dayId = str("shoot_day_id") || run?.shoot_day_id || null;
  if (dayId) out.push({ href: `/projects/${projectId}/days/${dayId}`, label: "the day" });

  const sceneId = str("scene_id") || run?.scene_id || null;
  if (sceneId) out.push({ href: `/projects/${projectId}/scenes/${sceneId}`, label: "the scene" });

  // A fact belongs to a location, and a location's dossier is read on the day that shoots it — so a
  // fact entry links to its day when the writer recorded one, and otherwise to nothing rather than
  // to a resource route this app does not have.
  if (str("fact_id") && !dayId && !sceneId) out.push({ href: `/projects/${projectId}`, label: "the production" });

  return out;
}
