"use client";

import { useEffect, useState } from "react";
import { api, inr, type MultiDayRipplePlan, type Scene } from "@/lib/api";
import { Kicker, Spinner } from "./ui";

interface MultiDayPanelProps {
  projectId: string;
  dayId: string;
  deferredSceneIds: string[];
  scenes: Record<string, Scene>;
}

export function MultiDayPanel({
  projectId,
  dayId,
  deferredSceneIds,
  scenes,
  onCommitted,
}: MultiDayPanelProps & { onCommitted?: () => void }) {
  // Committing is a producer decision, so it is a named button with its own result — never a side
  // effect of opening the panel.
  const [committing, setCommitting] = useState<string | null>(null);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [committed, setCommitted] = useState<string | null>(null);
  const [clearance, setClearance] = useState<{ name: string; type: string; reason: string }[]>([]);

  const commitPlacement = async (targetDayId: string, sceneId: string) => {
    setCommitting(sceneId);
    setCommitError(null);
    try {
      const r = await api.commitPlacement(projectId, targetDayId, sceneId);
      setCommitted(`Placed on Day ${r.day.day_number} at ${r.day.items.find((i) => i.scene_id === sceneId)?.start ?? ""}`);
      onCommitted?.();
    } catch (e) {
      setCommitError(e instanceof Error ? e.message : String(e));
    } finally {
      setCommitting(null);
    }
  };

  const commitPickup = async () => {
    setCommitting("pickup");
    setCommitError(null);
    try {
      const r = await api.commitPickupDay(projectId, dayId, deferredSceneIds);
      setCommitted(`Day ${r.day.day_number} is on the schedule for ${r.day.date}`);
      setClearance(r.pending_clearance);
      onCommitted?.();
    } catch (e) {
      setCommitError(e instanceof Error ? e.message : String(e));
    } finally {
      setCommitting(null);
    }
  };

  /* The plan is stored *with the scene set it was fetched for*, and read back only when the two
   * still match. Two things fall out of that. The effect no longer clears state synchronously on
   * every render with nothing deferred — which is a cascading render React 19 flags — and a plan
   * fetched for one set of deferred scenes can no longer be rendered against a different set while
   * the next request is still in flight, which is a ripple cost attributed to the wrong recovery. */
  const [fetched, setFetched] = useState<{ key: string; plan: MultiDayRipplePlan } | null>(null);
  const [failed, setFailed] = useState<{ key: string; message: string } | null>(null);
  const [isOpen, setIsOpen] = useState<boolean>(true);

  const deferredKey = deferredSceneIds.join(",");

  useEffect(() => {
    if (!deferredKey) return;

    let active = true;
    api
      .getMultiDayPlan(projectId, dayId, deferredKey)
      .then((res) => {
        if (active) setFetched({ key: deferredKey, plan: res });
      })
      .catch((err: unknown) => {
        if (active) setFailed({ key: deferredKey, message: err instanceof Error ? err.message : String(err) });
      });

    return () => {
      active = false;
    };
  }, [projectId, dayId, deferredKey]);

  /* Both derived, not stored. `loading` is simply "there are deferred scenes and we have neither an
   * answer nor an error for *this* set yet" — storing it meant setting state synchronously in the
   * effect, and it could also disagree with what was on screen. */
  const plan = fetched?.key === deferredKey ? fetched.plan : null;
  const error = failed?.key === deferredKey ? failed.message : null;
  const loading = Boolean(deferredKey) && !plan && !error;

  if (deferredSceneIds.length === 0) return null;

  return (
    <section className="card p-4 border-l-4 border-l-amber-500 bg-zinc-950/80 space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2 border-b border-zinc-800 pb-2">
        <div className="flex items-center gap-2">
          <span className="mono text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-400 font-semibold">
            Downstream Ripple
          </span>
          <Kicker>Multi-Day Cascading Horizon & Pickup Day Synthesis</Kicker>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsOpen(!isOpen)}
            className="text-xs text-muted hover:text-foreground"
          >
            {isOpen ? "Hide Horizon" : "Show Horizon"}
          </button>
        </div>
      </div>

      {/* A failed solve used to render nothing at all — the panel simply stopped, with the reason
          only in the browser console. A producer looking at a day with deferred scenes and no
          downstream answer needs to know it is a failure and not a "no ripple". */}
      {error ? (
        <div className="text-xs text-bad py-2">
          Could not solve the downstream placement for {deferredSceneIds.length} postponed scene(s):{" "}
          <span className="text-muted">{error}</span>
        </div>
      ) : loading ? (
        <div className="py-4 flex justify-center">
          <Spinner label="Solving downstream placement & pickup feasibility..." />
        </div>
      ) : plan ? (
        isOpen && (
          <div className="space-y-3 pt-1">
            <div className="text-xs text-zinc-300">
              {plan.summary || `Evaluated downstream placement for ${deferredSceneIds.length} postponed scene(s).`}
            </div>

            {/* Placements on future days */}
            {plan.placements.length > 0 && (
              <div className="space-y-1.5">
                <div className="text-[11px] uppercase tracking-wider text-dim font-semibold">
                  Downstream Day Placements
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {plan.placements.map((p, idx) => {
                    const sc = scenes[p.scene_id];
                    return (
                      <div
                        key={idx}
                        className="p-2.5 rounded bg-zinc-900/60 border border-zinc-800 space-y-1 text-xs"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-bold text-foreground">
                            Shoot Day {p.day_number} · {p.date}
                          </span>
                          <span
                            className={`mono text-[10px] px-1.5 py-0.5 rounded ${
                              p.feasible
                                ? "bg-emerald-500/20 text-emerald-400"
                                : "bg-red-500/20 text-red-400"
                            }`}
                          >
                            {p.feasible ? "FEASIBLE" : "INFEASIBLE"}
                          </span>
                        </div>
                        <div className="text-zinc-200">
                          Sc {sc?.number || p.scene_number} {sc?.heading || ""}
                        </div>
                        <div className="flex justify-between text-dim text-[11px] pt-1 border-t border-zinc-800/60">
                          <span>Slot: {p.scheduled_start}–{p.scheduled_end}</span>
                          <span>Overtime: +{p.added_overtime_minutes}m</span>
                          <span>Delta: {inr(p.added_cost_inr)}</span>
                        </div>
                        {p.feasible && (
                          <button
                            className="btn btn-ghost text-[11px] w-full"
                            disabled={committing !== null}
                            onClick={() => commitPlacement(`day_${p.day_number}`, p.scene_id)}
                            title="Writes this placement into the schedule with an audit trail, after re-validating the whole day."
                          >
                            {committing === p.scene_id ? <Spinner /> : null} Place Sc {sc?.number || p.scene_number} on Day {p.day_number}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* The cast half of the ripple. A schedule shows where a scene lands; it does not show
                that landing it there keeps a performer on the books for the days in between, and
                that is money nobody has budgeted. Projected, not committed — nothing here has been
                approved, which is exactly why it is worth seeing before approving it. */}
            {plan.cast_retention.length > 0 && (
              <div className="p-3 rounded-lg border border-amber-500/30 bg-zinc-900/50 space-y-2">
                <div className="flex items-baseline justify-between gap-2 flex-wrap">
                  <span className="text-[11px] uppercase tracking-wider text-dim font-semibold">
                    Cast retention this placement would add
                  </span>
                  {plan.cast_retention_cost_inr !== null && (
                    <span className="mono text-xs font-bold text-amber-400">{inr(plan.cast_retention_cost_inr)}</span>
                  )}
                </div>
                <ul className="space-y-1.5 text-[11px]">
                  {plan.cast_retention.map((r) => (
                    <li key={r.cast_id} className="flex items-baseline gap-2">
                      <span className="mono font-bold text-foreground w-4 shrink-0">{r.cast_number ?? "—"}</span>
                      <span className="flex-1">
                        <b className="text-zinc-200">{r.name}</b> · +{r.hold_days_added} paid hold day
                        {r.hold_days_added === 1 ? "" : "s"}
                        {r.added_cost_inr !== null && (
                          <span className="text-amber-400 font-semibold"> · {inr(r.added_cost_inr)}</span>
                        )}
                        <span className="block text-dim">{r.reason}</span>
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="text-[10px] text-dim">
                  A projection against the DOOD, not a committed cost: these hold days exist only if the placements
                  above are approved.
                </p>
              </div>
            )}

            {/* Synthesized Pickup Day Card */}
            {plan.synthesized_pickup_day && (
              <div className="p-3 rounded-lg border border-amber-500/40 bg-amber-950/20 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping" />
                    <span className="text-xs font-bold text-amber-300 uppercase tracking-wide">
                      Dedicated Pickup Day Synthesized
                    </span>
                  </div>
                  <span className="mono text-xs font-bold text-amber-400">
                    Est. Budget: {inr(plan.total_ripple_cost_inr)}
                  </span>
                </div>
                {/* Says what the solver decided, not what a pickup day sounds like it should be for.
                    This read "saturated within standard union hours ... without triggering rolling
                    6th-day meal and overtime penalties" — three claims the engine never makes. There
                    is no consecutive-day rule anywhere in `labor_rules.py`; `_can_accommodate` places
                    a scene unless it raises a HARD violation, which is a different question from
                    capacity; and the scene being carried here is a market-street chase, not a stunt.
                    An invented union rule beside the largest number on the page is the last place
                    this product can afford one. */}
                <div className="text-xs text-zinc-300">
                  No downstream day can take this scene without breaking a constraint the validator treats as hard —
                  the day/night window, a permit window, cast availability or turnaround. A dedicated pickup unit is
                  the only placement left, priced at this production&apos;s pickup-day rate.
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1 text-[11px]">
                  <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800">
                    <div className="text-dim">Day Index</div>
                    <div className="font-bold text-foreground">Day {plan.synthesized_pickup_day.day_number}</div>
                  </div>
                  <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800">
                    <div className="text-dim">Target Date</div>
                    <div className="font-bold text-foreground">{plan.synthesized_pickup_day.date}</div>
                  </div>
                  <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800">
                    <div className="text-dim">Unit Call</div>
                    <div className="font-bold text-foreground">{plan.synthesized_pickup_day.unit_call} IST</div>
                  </div>
                  <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800">
                    <div className="text-dim">Crew Size</div>
                    <div className="font-bold text-foreground">{plan.synthesized_pickup_day.crew_size} Crew</div>
                  </div>
                </div>
                <button
                  className="btn btn-primary text-[11px]"
                  disabled={committing !== null}
                  onClick={commitPickup}
                  title="Puts this day on the schedule. Nobody is booked onto it yet — the day will name who still has to be cleared."
                >
                  {committing === "pickup" ? <Spinner /> : null} Commit Day {plan.synthesized_pickup_day.day_number} to the schedule
                </button>
              </div>
            )}

            {committed && (
              <div className="p-2.5 rounded border border-ok/40 bg-ok/5 text-xs space-y-1">
                <div className="font-semibold text-ok">{committed}</div>
                {clearance.length > 0 && (
                  <div className="text-dim">
                    Nobody is booked onto it yet. Still to clear:{" "}
                    {clearance.map((c) => c.name).join(", ")}. Until they are, the day reports them as unavailable —
                    which is what they are.
                  </div>
                )}
              </div>
            )}
            {commitError && <p className="text-xs text-bad">{commitError}</p>}
          </div>
        )
      ) : null}
    </section>
  );
}
