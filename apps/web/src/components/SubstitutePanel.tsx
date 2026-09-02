"use client";

import { useEffect, useState } from "react";
import { api, type FeatureState, type FindAllRun, type Resource, type VendorCandidate } from "@/lib/api";
import { Kicker, Spinner } from "./ui";

/**
 * Substitute suppliers — Parallel FindAll turns "the crane vendor cancelled" into real companies
 * that could replace it, each with a source. Selecting one records the producer's proposal and
 * nothing more: it only reaches the call sheet when the recovery ChangeSet is approved, which is
 * why it can be withdrawn right up until then.
 */

const hostname = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

function VendorCard({ vendor, busy, onSelect, onUnselect }: { vendor: VendorCandidate; busy: boolean; onSelect: () => void; onUnselect: () => void }) {
  return (
    <li className={`rounded border p-2.5 ${vendor.selected ? "border-accent/60 bg-accent/5" : "border-line bg-elev"}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-medium">{vendor.name}</span>
        {/* "chosen" read like a booking. Nothing is booked until the recovery ChangeSet is approved,
            and a chip is the one place a producer scanning six cards actually reads. */}
        {vendor.selected && <span className="chip chip-accent">proposed</span>}
        {vendor.day_rate_band && <span className="chip chip-dim">{vendor.day_rate_band}</span>}
        {vendor.selected ? (
          <button className="btn btn-ghost ml-auto text-[11px]" disabled={busy} onClick={onUnselect} title="Withdraw this proposal. Nothing downstream has to be undone — the choice never left this panel.">
            Take it back
          </button>
        ) : (
          <button className="btn btn-primary ml-auto text-[11px]" disabled={busy} onClick={onSelect} title="Propose this as the replacement; it lands on the call sheet when the recovery is approved">
            Use this vendor
          </button>
        )}
      </div>
      {vendor.description && <p className="mt-1 text-[12px] text-muted">{vendor.description}</p>}
      <div className="mt-1 flex gap-3 flex-wrap text-[11px] text-dim">
        {vendor.phone && <span className="mono">{vendor.phone}</span>}
        {vendor.address && <span>{vendor.address}</span>}
      </div>
      {vendor.match_reasons.length > 0 && (
        <ul className="mt-1 text-[11px] text-muted list-disc list-inside">
          {vendor.match_reasons.slice(0, 2).map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <div className="mt-1.5 flex gap-1.5 flex-wrap">
        {(vendor.citations.length ? vendor.citations.map((c) => c.url) : [vendor.url]).filter(Boolean).map((url) => (
          <a key={url} href={url} target="_blank" rel="noopener noreferrer" className="chip chip-parallel hover:underline">
            {hostname(url)}
          </a>
        ))}
      </div>
    </li>
  );
}

export function SubstitutePanel({ projectId, dayId, resources, disabled, onChanged }: { projectId: string; dayId: string; resources: Resource[]; disabled?: boolean; onChanged?: () => void }) {
  const [feature, setFeature] = useState<FeatureState | null>(null);
  const [runs, setRuns] = useState<FindAllRun[]>([]);
  const [mode, setMode] = useState<string>("entity_search");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.features().then((f) => setFeature(f.features.findall)).catch(() => setFeature(null));
    api.substitutes(projectId).then((s) => { setRuns(s.findall_runs); setMode(s.mode); }).catch(() => setRuns([]));
  }, [projectId]);

  const find = async (resourceId: string) => {
    setBusy(resourceId);
    setError(null);
    try {
      const s = await api.findSubstitutes(projectId, resourceId, dayId);
      setRuns(s.findall_runs);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const select = async (run: FindAllRun, vendor: VendorCandidate) => {
    setBusy(vendor.id);
    setError(null);
    try {
      const { findall_run } = await api.selectVendor(run.id, vendor.id);
      setRuns((prev) => prev.map((r) => (r.id === findall_run.id ? findall_run : r)));
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  // The button used to disable itself once a vendor was picked, so a shortlist a producer was still
  // thinking about hardened into a decision they could not reverse. The server refuses an unselect
  // when nothing is selected (409, "nothing to take back") — that sentence is worth surfacing, so it
  // goes through the same error line as everything else here.
  const unselect = async (run: FindAllRun, vendor: VendorCandidate) => {
    if (!confirm(`Withdraw ${vendor.name} as the proposed replacement?\n\nNothing downstream changes: a vendor only reaches the call sheet through an approved recovery, and this one has not been approved. The search and its candidates stay; you can propose another.`)) return;
    setBusy(vendor.id);
    setError(null);
    try {
      const { findall_run } = await api.unselectVendor(run.id);
      setRuns((prev) => prev.map((r) => (r.id === findall_run.id ? findall_run : r)));
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const off = !feature?.enabled;
  // Observed live 2026-08-29: entity_search returns in 1-5 s; a base FindAll run was still
  // going at 7 min, so it is labelled honestly rather than optimistically.
  const cost = mode === "entity_search" ? "~$0.005 · seconds" : "~$0.49 · 5-10 min";
  const latest = (resourceId: string) => [...runs].reverse().find((r) => r.resource_id === resourceId);

  return (
    <section className="card p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Kicker>Who can actually fix it · Parallel FindAll</Kicker>
        <span className="text-[12px] text-muted">
          When a resource falls through, reshuffling the day is not a recovery. Parallel finds real suppliers who could replace it — a proposed one reaches the regenerated call sheet when the recovery is approved, and not before.
        </span>
      </div>

      {off && (
        <p className="mt-3 text-[12px] text-muted">
          Substitute discovery is off in this deployment. Enable with <span className="mono text-dim">{feature?.env || "SCENEPILOT_PARALLEL_FINDALL=1"}</span>. {feature?.cost}
        </p>
      )}
      {error && <p className="mt-3 text-[12px] text-bad">{error}</p>}

      <div className="mt-3 grid gap-3">
        {resources.map((r) => {
          const run = latest(r.id);
          const chosen = run?.candidates.find((v) => v.selected) || null;
          return (
            <div key={r.id}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium">{r.name}</span>
                <span className="chip chip-dim">{r.type.toLowerCase()}</span>
                {chosen && <span className="chip chip-accent">replacement proposed</span>}
                {run?.enriched && <span className="chip chip-dim" title="Parallel fetched contact details for the matched vendors">contacts enriched</span>}
                <button
                  className="btn ml-auto text-[11px]"
                  disabled={off || disabled || busy !== null}
                  title={off ? `Disabled. Set ${feature?.env || "SCENEPILOT_PARALLEL_FINDALL=1"}.` : `Parallel ${mode === "entity_search" ? "Entity Search" : "FindAll"} — ${cost}`}
                  onClick={() => find(r.id)}
                >
                  {run ? "Search again" : "Find replacements"} · {cost}
                </button>
              </div>
              {/* The honesty claim this product makes is that only an approved ChangeSet puts a
                  vendor on a call sheet. Until this line existed the chip implied it had landed. */}
              {chosen && run && (
                <p className="mt-1 text-[12px] text-muted">
                  Proposed: <span className="text-fg">{chosen.name}</span> — a proposal, not a booking. It reaches the call sheet only when the recovery ChangeSet is approved, and can be taken back until then.
                </p>
              )}
              {busy === r.id && <div className="mt-2"><Spinner label={`Parallel is looking for a replacement for ${r.name}`} /></div>}
              {run?.status === "ERROR" && <p className="mt-1 text-[12px] text-bad">{run.error}</p>}
              {run && run.candidates.length === 0 && run.status === "OK" && <p className="mt-1 text-[12px] text-dim">Parallel found no supplier matching every condition.</p>}
              {run && run.candidates.length > 0 && (
                <ul className="mt-2 grid gap-2 md:grid-cols-2 text-[13px]">
                  {run.candidates.map((v) => (
                    <VendorCard key={v.id} vendor={v} busy={busy !== null} onSelect={() => select(run, v)} onUnselect={() => unselect(run, v)} />
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      {resources.length === 0 && <p className="mt-3 text-[12px] text-dim">No equipment or locations on this day to replace.</p>}
    </section>
  );
}
