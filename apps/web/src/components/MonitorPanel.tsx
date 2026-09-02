"use client";

import { useEffect, useState } from "react";
import { api, fmtTime, type FeatureState, type MonitorsView } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { Kicker } from "./ui";

/** Parallel Monitor: the outside world pushes changes; a producer confirms before anything runs. */
export function MonitorPanel({ projectId, dayId, disabled, onChanged }: { projectId: string; dayId: string; disabled: boolean; onChanged: () => void }) {
  const { data, reload } = usePoll<MonitorsView>(() => api.monitors(projectId, dayId), (d) => !!d && d.drafts.length > 0, 4000);
  const [feature, setFeature] = useState<FeatureState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [windows, setWindows] = useState<Record<string, { start: string; end: string }>>({});

  // The recurring charge is the whole reason Cancel had to exist, so the panel has to be able to
  // name it — same `GET /api/features` entry every other gated capability prints.
  useEffect(() => {
    api.features().then((f) => setFeature(f.features.monitors)).catch(() => setFeature(null));
  }, []);

  const dailyCost = feature?.cost || "Parallel Monitor — ~$0.07/day per hourly lite monitor";

  // Was alert(). A cancel can fail at Parallel with a 502 naming the monitor that is still billing,
  // and that sentence is worth reading twice — a modal the producer swats away is the wrong home
  // for it, and the house idiom is a line under the header that stays put.
  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      await reload();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const cancel = (id: string, kind: string) => {
    const warning = [
      `Cancel the ${kind.toLowerCase()} monitor?`,
      `It stops executing at Parallel and stops charging: ${dailyCost}, billed every day until cancelled.`,
      "It cannot be restarted. Watching this again means creating a new monitor; what this one already caught stays on the day.",
    ].join("\n\n");
    if (!confirm(warning)) return;
    act(`cancel:${id}`, () => api.cancelMonitor(projectId, id));
  };

  if (!data) return null;
  const anyActive = data.monitors.some((m) => m.status === "active");

  return (
    <section className="card p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Kicker>Watching the outside world · Parallel Monitor</Kicker>
        <span className="text-[12px] text-muted">Monitors run on a schedule and push material changes to ScenePilot; nothing runs until a producer confirms.</span>
        <div className="ml-auto flex gap-2">
          <button className="btn" disabled={disabled || busy !== null || !data.live_possible} title={data.live_possible ? `${dailyCost}, billed until cancelled · Webhook: ${data.webhook_url}` : "Needs PUBLIC_BASE_URL (hosted URL) so Parallel can reach the webhook"} onClick={() => act("create", () => api.createMonitors(projectId, dayId))}>
            {anyActive ? "Monitors active" : "Create live monitors"}
          </button>
          <button className="btn" disabled={disabled || busy !== null} onClick={() => act("sim", () => api.simulateMonitorEvent(projectId, dayId, "WEATHER"))} title="Push a fabricated monitor event through the real ingestion path (clearly labelled)">
            Simulate a monitor event
          </button>
        </div>
      </div>
      {anyActive && <p className="mt-2 text-[12px] text-muted">{dailyCost} — a live monitor keeps billing every day until it is cancelled, and cancelling it here is the only thing that ends the charge.</p>}
      {error && <p className="mt-3 text-[12px] text-bad">{error}</p>}
      <ul className="mt-3 grid gap-2 md:grid-cols-2 text-[12px]">
        {(data.monitors.length ? data.monitors : data.proposed.map((q) => ({ id: q.kind, kind: q.kind, query: q.query, status: "proposed", frequency: "1h", processor: "lite", last_event_at: null, event_count: 0 }))).map((m) => (
          <li key={m.id} className={`rounded border border-line bg-elev p-2.5 ${m.status === "cancelled" ? "opacity-70" : ""}`}>
            <div className="flex items-center gap-2">
              <span className={`chip ${m.status === "active" ? "chip-parallel" : m.status === "simulated" ? "chip-warn" : "chip-dim"}`}>{m.kind.toLowerCase()} · {m.status}</span>
              <span className="mono text-dim">every {m.frequency} · {m.processor}</span>
              {m.event_count > 0 && <span className="ml-auto mono text-dim">{m.event_count} event{m.event_count === 1 ? "" : "s"}{m.last_event_at ? ` · last ${fmtTime(m.last_event_at)}` : ""}</span>}
            </div>
            <div className="mt-1 text-muted">{m.query}</div>
            {/* Deliberately not gated on the `disabled` prop: that one stops actions which would
                start a second run while a rescue is being decided, and ending a recurring charge is
                neither a run nor something worth making a producer approve a recovery to reach. */}
            {m.status === "active" && (
              <div className="mt-2 flex items-center gap-2">
                <button className="btn btn-ghost text-[11px]" disabled={busy !== null} onClick={() => cancel(m.id, m.kind)} title={`Stop this monitor at Parallel. ${dailyCost} — this is where the charge ends.`}>
                  {busy === `cancel:${m.id}` ? "Cancelling…" : "Cancel monitor"}
                </button>
                <span className="text-dim">billing daily until cancelled</span>
              </div>
            )}
            {/* A cancelled monitor stays on the list rather than disappearing: that it ran, and what
                it caught, is part of the record for this day long after it stops costing anything. */}
            {m.status === "cancelled" && <div className="mt-2 text-dim">Cancelled — no longer executing, no longer billing. Watching this again means creating a new monitor.</div>}
          </li>
        ))}
      </ul>
      {data.drafts.map((d) => {
        const w = windows[d.id] || { start: d.window_start || "13:00", end: d.window_end || "17:00" };
        return (
          <div key={d.id} className="mt-3 rounded-lg border border-warn/60 bg-warn/5 p-3">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="chip chip-parallel">detected by Parallel Monitor</span>
              {d.monitor_event?.simulated && <span className="chip chip-warn">simulated event</span>}
              <span className="chip chip-bad">{d.type.replace(/_/g, " ")}</span>
              <span className="display text-lg font-bold">{d.title}</span>
            </div>
            <p className="text-[12px] text-muted mt-1">{d.description}</p>
            <div className="mt-2 flex items-center gap-2 flex-wrap text-[12px]">
              <span className="text-dim">window</span>
              <input value={w.start} onChange={(e) => setWindows((s) => ({ ...s, [d.id]: { ...w, start: e.target.value } }))} className="bg-elev border border-line rounded px-2 py-1 mono w-20" />
              <span className="text-dim">–</span>
              <input value={w.end} onChange={(e) => setWindows((s) => ({ ...s, [d.id]: { ...w, end: e.target.value } }))} className="bg-elev border border-line rounded px-2 py-1 mono w-20" />
              <button className="btn btn-primary" disabled={disabled || busy !== null} onClick={() => act(d.id, () => api.confirmDisruption(projectId, d.id, { window_start: w.start, window_end: w.end }))}>
                Confirm &amp; plan recovery
              </button>
              <button className="btn btn-ghost" disabled={busy !== null} onClick={() => act(d.id + "x", () => api.dismissDisruption(projectId, d.id))}>Dismiss</button>
              <span className="text-dim">Confirmation is the human gate — the rescue workflow only starts here.</span>
            </div>
          </div>
        );
      })}
    </section>
  );
}
