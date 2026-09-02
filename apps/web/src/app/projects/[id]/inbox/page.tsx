"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import { api, type DossierView, type DraftDisruption, type FactChange, type Project } from "@/lib/api";
import { ChangeCard } from "@/components/FactChangeCard";
import { Kicker, LoadError, Spinner } from "@/components/ui";

/**
 * The inbox — everything that says "the world moved", in one place, waiting on a producer.
 *
 * Two kinds of movement reach this production and they used to arrive on different screens. Fact
 * drift: snapshot monitors re-run a location's dossier and report only the fields that changed, each
 * landing as a pending `FactChange`, and the only way to see one was to open the day page for a day
 * that happened to book that location and scroll its dossier panel. Monitor drafts: an event-stream
 * monitor fires and a draft disruption is written, and those were reachable only through the day
 * page's monitor panel, which renders one day at a time — so a monitor firing on Day 6 while the
 * producer read Day 4 announced itself to nobody at all.
 *
 * Both are the same act: the outside world changed, and somebody has to decide what the schedule
 * does about it. They keep separate sections because the decisions are not interchangeable —
 * adopting a fact re-verdicts a schedule that already exists, confirming a draft starts a rescue
 * run — but they are counted together, and the TopBar badge counts them together too.
 *
 * This screen deliberately holds decisions only: the paid research and watch buttons stay on the day
 * page beside the location they spend money on.
 */

/**
 * A monitor-detected draft, one decision with two homes — this card and the day page's monitor
 * panel — so it reads the same in both: chips, what was seen, then the two ways out.
 */
function DraftCard({
  projectId,
  draft,
  busy,
  window: w,
  onWindow,
  onConfirm,
  onDismiss,
}: {
  projectId: string;
  draft: DraftDisruption;
  busy: boolean;
  window: { start: string; end: string };
  onWindow: (w: { start: string; end: string }) => void;
  onConfirm: () => void;
  onDismiss: () => void;
}) {
  const d = draft.disruption;
  return (
    <li className="rounded border border-warn/70 bg-warn/5 p-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="chip chip-parallel">Parallel Monitor · event</span>
        <span className="chip chip-bad">{d.type.replace(/_/g, " ").toLowerCase()}</span>
        {d.monitor_event?.simulated && <span className="chip chip-warn">simulated event</span>}
        <Link href={`/projects/${projectId}/days/${draft.shoot_day_id}`} className="chip chip-dim hover:text-fg">
          Day {draft.day_number} · {draft.date}
        </Link>
        <span className="ml-auto text-[11px] text-dim">
          {draft.detected_at ? `detected ${new Date(draft.detected_at).toLocaleString()}` : "detection time not recorded"}
        </span>
      </div>

      <div className="mt-1.5 text-[13px] font-medium">{d.title}</div>
      <p className="mt-0.5 text-[12px] text-muted">{d.description}</p>
      {d.verification_summary && (
        <p className="mt-0.5 text-[11px] text-dim">
          verification: {d.verification_status?.toLowerCase() ?? "unchecked"} — {d.verification_summary}
        </p>
      )}

      <div className="mt-2.5 flex items-center gap-2 flex-wrap text-[12px]">
        <span className="text-dim">window</span>
        <input
          value={w.start}
          onChange={(e) => onWindow({ ...w, start: e.target.value })}
          className="bg-elev border border-line rounded px-2 py-1 mono w-20"
          disabled={busy}
        />
        <span className="text-dim">–</span>
        <input
          value={w.end}
          onChange={(e) => onWindow({ ...w, end: e.target.value })}
          className="bg-elev border border-line rounded px-2 py-1 mono w-20"
          disabled={busy}
        />
        <button className="btn btn-primary text-[11px]" disabled={busy} onClick={onConfirm}>
          Confirm &amp; plan recovery
        </button>
        <button
          className="btn btn-ghost text-[11px]"
          disabled={busy}
          onClick={onDismiss}
          title="Drop the draft. The monitor stays live and will raise the next one it sees."
        >
          Dismiss
        </button>
        {busy && <Spinner />}
      </div>
      <p className="mt-1.5 text-[11px] text-dim">
        The monitor pre-filled this window; edit it to what you are actually protecting against. Confirming is the human
        gate — it starts the rescue workflow on Day {draft.day_number} and nothing runs before it.
      </p>
    </li>
  );
}

export default function InboxPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<DossierView | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [drafts, setDrafts] = useState<DraftDisruption[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState<{ runId: string; dayId: string; dayNumber: number | null } | null>(null);
  const [windows, setWindows] = useState<Record<string, { start: string; end: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([api.dossiers(id), api.project(id), api.draftDisruptions(id)])
      .then(([d, p, dd]) => {
        if (!live) return;
        setData(d);
        setProject(p.project);
        setDrafts(dd.drafts);
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [id]);

  /** Which shoot days each location actually works — the reason a drift matters to a schedule. */
  const daysByLocation = useMemo(() => {
    const map = new Map<string, { id: string; day_number: number }[]>();
    for (const day of project?.shoot_days ?? []) {
      for (const item of day.items) {
        const loc = item.location_id;
        if (!loc) continue;
        const list = map.get(loc) ?? [];
        if (!list.some((d) => d.id === day.id)) list.push({ id: day.id, day_number: day.day_number });
        map.set(loc, list);
      }
    }
    return map;
  }, [project]);

  if (!data || !project) {
    return error ? <LoadError error={error} missing="Project not found" /> : <div className="card p-8 shimmer h-72" />;
  }

  const decide = async (change: FactChange, decision: "adopt" | "dismiss") => {
    setBusy(change.id);
    try {
      setData(await api.decideFactChange(id, change.id, decision));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const windowFor = (d: DraftDisruption) =>
    windows[d.disruption.id] || { start: d.disruption.window_start || "13:00", end: d.disruption.window_end || "17:00" };

  const confirmDraft = async (d: DraftDisruption) => {
    const w = windowFor(d);
    setBusy(d.disruption.id);
    setDraftError(null);
    try {
      const r = await api.confirmDisruption(id, d.disruption.id, { window_start: w.start, window_end: w.end });
      setConfirmed({ runId: r.run_id, dayId: d.shoot_day_id, dayNumber: d.day_number });
      setDrafts((await api.draftDisruptions(id)).drafts);
    } catch (e) {
      // Three refusals arrive here and every one of them is a written sentence: a rescue already
      // awaiting approval on that day (named by run id), a day that has already wrapped, and a
      // disruption that names no exterior, resource or location it could touch.
      setDraftError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const dismissDraft = async (d: DraftDisruption) => {
    setBusy(`${d.disruption.id}:dismiss`);
    setDraftError(null);
    try {
      await api.dismissDisruption(id, d.disruption.id);
      setDrafts((await api.draftDisruptions(id)).drafts);
    } catch (e) {
      setDraftError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const pending = data.fact_changes.filter((c) => c.status === "PENDING");
  const settled = data.fact_changes.filter((c) => c.status !== "PENDING");
  const locationName = (rid: string) => data.locations.find((l) => l.id === rid)?.name ?? rid;
  const watched = data.watches.length;
  const researched = data.locations.filter((l) => l.fact_count > 0).length;
  const waiting = pending.length + drafts.length;

  const groups = [...new Map(pending.map((c) => [c.resource_id, pending.filter((x) => x.resource_id === c.resource_id)])).entries()];

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 flex-wrap">
        <div>
          <Kicker>Inbox · what moved since this production last looked</Kicker>
          <h1 className="display text-2xl font-bold mt-1">
            {waiting === 0 ? "Nothing is waiting on you" : `${waiting} decision${waiting === 1 ? "" : "s"} waiting`}
          </h1>
          <p className="text-muted text-sm mt-1 max-w-2xl">
            Two kinds of movement land here. A monitor draft says something is about to happen to a shoot day and asks
            whether to plan a rescue. Fact drift says a value the schedule is validated against has changed underneath it.
            Neither runs on its own — both wait for a producer.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <span className={`chip ${drafts.length ? "chip-warn" : "chip-dim"}`}>{drafts.length} monitor draft{drafts.length === 1 ? "" : "s"}</span>
          <span className={`chip ${pending.length ? "chip-warn" : "chip-dim"}`}>{pending.length} drift pending</span>
          <span className="chip chip-dim">{settled.length} settled</span>
          <Link href={`/projects/${id}`} className="btn btn-ghost text-xs">Back to the production</Link>
        </div>
      </div>

      {error && <p className="text-[12px] text-bad">{error}</p>}

      <section className="card p-4">
        <div className="flex items-center gap-2 flex-wrap">
          <Kicker>Monitor drafts · a day the world is about to reach</Kicker>
          <span className="text-[12px] text-muted">
            production-wide, so a monitor firing on a day you are not reading still reaches you
          </span>
          <span className="ml-auto text-[11px] text-dim">Confirming starts the rescue workflow; dismissing drops the draft.</span>
        </div>

        {drafts.length === 0 ? (
          <p className="mt-3 text-[12px] text-muted">
            No monitor has raised anything a producer has not answered. Event-stream monitors are created per shoot day
            from the day page — a day nobody has put a monitor on cannot raise a draft, which is not the same as a quiet
            day.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {drafts.map((d) => (
              <DraftCard
                key={d.disruption.id}
                projectId={id}
                draft={d}
                busy={busy === d.disruption.id || busy === `${d.disruption.id}:dismiss`}
                window={windowFor(d)}
                onWindow={(w) => setWindows((s) => ({ ...s, [d.disruption.id]: w }))}
                onConfirm={() => confirmDraft(d)}
                onDismiss={() => dismissDraft(d)}
              />
            ))}
          </ul>
        )}

        {confirmed && (
          <p className="mt-3 text-[12px]">
            <span className="chip chip-ok mr-2">rescue running</span>
            The recovery workflow started on {confirmed.dayNumber ? `Day ${confirmed.dayNumber}` : "that day"}.{" "}
            <Link href={`/projects/${id}/days/${confirmed.dayId}`} className="text-accent hover:underline">
              Open {confirmed.dayNumber ? `Day ${confirmed.dayNumber}` : "the day"} to weigh the options →
            </Link>
          </p>
        )}
        {draftError && <p className="mt-3 text-[12px] text-bad">{draftError}</p>}
      </section>

      <div className="flex items-baseline gap-2 pt-1">
        <h2 className="display text-lg font-semibold">Fact drift</h2>
        <span className="text-[12px] text-muted">
          Parallel re-runs each watched location&apos;s dossier and reports only the fields that changed. Adopting a change
          replaces the value the schedule is validated against; keeping yours leaves it exactly as signed off.
        </span>
      </div>

      {pending.length === 0 ? (
        <section className="card p-6 text-center space-y-1">
          <p className="text-sm">No drift is waiting on a decision.</p>
          <p className="text-[12px] text-muted">
            {watched} location dossier{watched === 1 ? "" : "s"} watched · {researched} researched. A watched dossier is
            re-run by Parallel on its own schedule; anything that moves arrives here.
          </p>
          <p className="text-[11px] text-dim">
            Research and watch a location from its shoot day — those calls cost money, so they stay where the day that
            needs them is.
          </p>
        </section>
      ) : (
        groups.map(([resourceId, changes]) => {
          const days = daysByLocation.get(resourceId) ?? [];
          return (
            <section key={resourceId} className="space-y-2">
              <div className="flex items-baseline gap-2 flex-wrap">
                <h3 className="display text-sm font-bold">{locationName(resourceId)}</h3>
                <span className="text-[11px] text-dim">
                  {days.length === 0
                    ? "not booked on any scheduled day"
                    : `works ${days.map((d) => `Day ${d.day_number}`).join(", ")}`}
                </span>
              </div>
              <ul className="space-y-2">
                {changes.map((change) => (
                  <li key={change.id} className="space-y-1">
                    <ul>
                      <ChangeCard change={change} busy={busy === change.id} onDecide={(d) => decide(change, d)} />
                    </ul>
                    {days.length > 0 && (
                      <p className="text-[11px] text-dim pl-1">
                        Adopting re-verdicts any recovery still awaiting approval on{" "}
                        {days.map((d, i) => (
                          <span key={d.id}>
                            {i > 0 && ", "}
                            <Link href={`/projects/${id}/days/${d.id}`} className="text-info hover:underline">
                              Day {d.day_number}
                            </Link>
                          </span>
                        ))}
                        .
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          );
        })
      )}

      {settled.length > 0 && (
        <details className="card p-3">
          <summary className="cursor-pointer text-[12px] text-muted">{settled.length} already decided</summary>
          <ul className="mt-2 space-y-2">
            {settled.map((change) => (
              <ChangeCard key={change.id} change={change} busy={false} onDecide={() => {}} />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
