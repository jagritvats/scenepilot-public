"use client";

import { useEffect, useState } from "react";
import { api, type DossierView, type FeatureState, type LocationFact } from "@/lib/api";
import { ChangeCard } from "./FactChangeCard";
import { Citations } from "./Citations";
import { ExcerptWithClause } from "./ProvenanceChain";
import { Kicker, Spinner } from "./ui";

/**
 * Location dossiers — Parallel Task research whose cited facts become production constraints.
 *
 * The gate is the point: a fact is graded HARD only when Parallel returned high confidence *with*
 * a citation *and* the fact is mechanically checkable; even then it constrains nothing until a
 * producer accepts it.
 *
 * The layout is that same idea, applied to attention. A dossier comes back with a dozen or more
 * facts, and treating them as equals is how the one that can cancel a scene gets lost among the
 * ones that merely inform. So the panel is ordered by consequence:
 *
 *   1. what *changed* since the production last looked (a schedule may be running on a stale rule);
 *   2. what can actually *reject a schedule option*;
 *   3. everything else, folded away behind a count until asked for.
 *
 * Each tier states its own caveat once, rather than repeating it on every card.
 */

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

const BINDING_CHIP: Record<string, string> = { HARD: "chip-bad", SOFT: "chip-warn", ADVISORY: "chip-dim" };
const BINDING_NOTE: Record<string, string> = {
  HARD: "can reject a schedule option once you accept it",
  SOFT: "prices an option; never rejects one",
  ADVISORY: "for a human to verify; never enforced",
};

/** A fact that can actually stop a scene: high confidence, cited, and mechanically checkable. */
const constrains = (f: LocationFact) => f.binding === "HARD" && f.rule !== null;
const enforced = (f: LocationFact) => constrains(f) && f.accepted && !f.rejected;

function RuleChip({ fact }: { fact: LocationFact }) {
  if (!fact.rule) return null;
  return (
    <span className="chip chip-parallel">
      {fact.rule.kind === "TIME_WINDOW_BAN" ? `no work ${fact.rule.window_start}–${fact.rule.window_end}` : `no ${fact.rule.activity}`}
    </span>
  );
}

/** Tier 2 — a rule that can reject an option. Shown in full, always. */
function ConstraintCard({ fact, busy, onDecide }: { fact: LocationFact; busy: boolean; onDecide: (d: "accept" | "reject") => void }) {
  const live = enforced(fact);
  return (
    <li className={`rounded border p-3 ${live ? "border-bad/60 bg-bad/5" : "border-line bg-elev"}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <RuleChip fact={fact} />
        {live ? <span className="chip chip-bad">enforced</span> : <span className="chip chip-dim">not yet accepted</span>}
        {fact.confidence && <span className="chip chip-dim">Parallel confidence: {fact.confidence}</span>}
        {fact.rejected && <span className="chip chip-dim">rejected</span>}
        <span className="ml-auto text-[11px] text-dim">{fact.label}</span>
      </div>

      <div className="mt-1.5 font-medium">{fact.value}</div>
      {fact.reasoning && <p className="mt-0.5 text-[12px] text-muted">{fact.reasoning}</p>}

      <div className="mt-2 flex items-center gap-2 flex-wrap">
        <Citations citations={fact.citations} />
      </div>

      {/* The sentence the rule was parsed from, marked — so what is about to be accepted as binding
          is legible as the source's own words before the click, not only after a rejection. */}
      {fact.rule && fact.citations[0]?.excerpts?.[0] && <ExcerptWithClause excerpt={fact.citations[0].excerpts[0]} rule={fact.rule} />}

      <div className="mt-2.5 flex items-center gap-2 flex-wrap">
        {!fact.accepted && (
          <button className="btn btn-primary text-[11px]" disabled={busy} onClick={() => onDecide("accept")} title="Accept as a real constraint — options that break it will be rejected">
            Accept as a hard constraint
          </button>
        )}
        {!fact.rejected && (
          <button className="btn btn-ghost text-[11px]" disabled={busy} onClick={() => onDecide("reject")}>
            {fact.accepted ? "Withdraw" : "Reject"}
          </button>
        )}
        {fact.accepted && fact.accepted_by && <span className="text-[11px] text-dim">accepted by {fact.accepted_by}</span>}
      </div>
    </li>
  );
}

/** Tier 3 — one line. The caveat that used to repeat on every card now lives on the section. */
function AdvisoryRow({ fact, busy, onDecide }: { fact: LocationFact; busy: boolean; onDecide: (d: "accept" | "reject") => void }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-b border-line/60 last:border-0">
      <div className="flex items-baseline gap-2 py-1.5 text-[12px]">
        <span className={`chip ${BINDING_CHIP[fact.binding]} shrink-0`} title={BINDING_NOTE[fact.binding]}>
          {fact.binding.toLowerCase()}
        </span>
        <span className="text-dim shrink-0 w-[104px] truncate" title={fact.label}>{fact.label}</span>
        <button className="text-left flex-1 min-w-0 hover:text-fg" onClick={() => setOpen(!open)} title={fact.reasoning || "Show detail"}>
          <span className={open ? "" : "line-clamp-1"}>{fact.value}</span>
        </button>
        {fact.accepted && <span className="chip chip-dim shrink-0">acknowledged</span>}
        {fact.rejected && <span className="chip chip-dim shrink-0">rejected</span>}
      </div>
      {open && (
        <div className="pb-2 pl-[6.5rem] text-[12px]">
          {fact.reasoning && <p className="text-muted">{fact.reasoning}</p>}
          <div className="mt-1.5 flex items-center gap-2 flex-wrap">
            <Citations citations={fact.citations} />
            {fact.citations.length === 0 && <span className="text-[11px] text-dim">no citation</span>}
            {!fact.accepted && (
              <button className="btn btn-ghost text-[11px]" disabled={busy} onClick={() => onDecide("accept")}>
                Acknowledge
              </button>
            )}
            {!fact.rejected && (
              <button className="btn btn-ghost text-[11px]" disabled={busy} onClick={() => onDecide("reject")}>
                {fact.accepted ? "Withdraw" : "Reject"}
              </button>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

function SectionLabel({ children, note }: { children: React.ReactNode; note?: string }) {
  return (
    <div className="mt-3 mb-1.5 flex items-baseline gap-2 flex-wrap">
      <span className="text-[10px] uppercase tracking-[0.14em] text-dim">{children}</span>
      {note && <span className="text-[11px] text-dim">{note}</span>}
    </div>
  );
}

export function DossierPanel({
  projectId,
  locationIds,
  dayId,
  dayNumber,
  disabled,
  onChanged,
}: {
  projectId: string;
  locationIds: string[];
  dayId?: string;
  dayNumber?: number;
  disabled?: boolean;
  onChanged?: () => void;
}) {
  const [feature, setFeature] = useState<FeatureState | null>(null);
  const [data, setData] = useState<DossierView | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    api.features().then((f) => setFeature(f.features.task)).catch(() => setFeature(null));
    api.dossiers(projectId).then(setData).catch(() => setData(null));
  }, [projectId]);

  const act = async (key: string, run: () => Promise<DossierView>) => {
    setBusy(key);
    setError(null);
    setNote(null);
    try {
      setData(await run());
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const preflight = async () => {
    if (!dayId) return;
    setBusy("preflight");
    setError(null);
    setNote(null);
    try {
      const r = await api.preflightDay(projectId, dayId);
      setData(r);
      const missing = r.unresearched.length ? ` ${plural(r.unresearched.length, "location has", "locations have")} never been researched.` : "";
      setNote(
        r.changes.length
          ? `${plural(r.changes.length, "rule has", "rules have")} changed since this production last looked${r.urgent ? `, ${r.urgent} of them currently enforced` : ""}.${missing}`
          : `Re-verified ${plural(r.checked.length, "location", "locations")} against Parallel — nothing has changed.${missing}`,
      );
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const off = !feature?.enabled;
  const locations = (data?.locations || []).filter((l) => locationIds.length === 0 || locationIds.includes(l.id));
  // Nothing researched anywhere yet — a fresh instance, not a panel with a problem.
  const cold = locations.every((l) => l.fact_count === 0);
  const researched = locations.filter((l) => l.fact_count > 0).length;
  const busyAll = busy !== null;

  return (
    <section className="card p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Kicker>What the real world forbids · Parallel Task</Kicker>
        <span className="text-[12px] text-muted">
          Structured research per location, with a citation behind every field. A fact only constrains the day once you accept it.
        </span>
        {dayId && researched > 0 && (
          <button
            className="btn ml-auto text-[11px]"
            disabled={off || disabled || busyAll}
            title={`Re-run each researched location's dossier and report only what moved since you accepted it — one Parallel Task run per location`}
            onClick={preflight}
          >
            Re-verify {dayNumber ? `Day ${dayNumber}` : "this day"} · ~${(researched * 0.03).toFixed(2)} · 1–5 min
          </button>
        )}
      </div>

      {off && (
        <p className="mt-3 text-[12px] text-muted">
          Location dossiers are off in this deployment. Enable with <span className="mono text-dim">{feature?.env || "SCENEPILOT_PARALLEL_TASK=1"}</span>. {feature?.cost}
          {locations.length > 0 && cold && (
            <>
              {" "}
              {locations.length} location{locations.length === 1 ? "" : "s"} on this day would be researched:{" "}
              <span className="text-dim">{locations.map((l) => l.name).join(", ")}</span>.
            </>
          )}
        </p>
      )}
      {busy === "preflight" && <div className="mt-3"><Spinner label="Parallel is re-verifying this day's locations" /></div>}
      {note && <p className="mt-3 text-[12px] text-muted">{note}</p>}
      {error && <p className="mt-3 text-[12px] text-bad">{error}</p>}

      <div className="mt-1 grid gap-4">
        {locations.map((loc) => {
          const facts = (data?.facts || []).filter((f) => f.resource_id === loc.id);
          const rules = facts.filter(constrains);
          const advisory = facts.filter((f) => !constrains(f));
          const forLoc = (data?.fact_changes || []).filter((c) => c.resource_id === loc.id);
          const changes = [...forLoc.filter((c) => c.status === "PENDING"), ...forLoc.filter((c) => c.status !== "PENDING").slice(-2)];
          const open = expanded[loc.id] ?? false;

          return (
            <div key={loc.id}>
              <div className="flex items-center gap-2 flex-wrap border-b border-line pb-1.5">
                <span className="font-medium">{loc.name}</span>
                {loc.binding_count > 0 && <span className="chip chip-bad">{loc.binding_count} enforced</span>}
                {loc.pending_changes > 0 && <span className="chip chip-warn">{loc.pending_changes} to review</span>}
                {loc.watched && <span className="chip chip-parallel" title="A Parallel snapshot monitor re-runs this dossier and reports only what changed">watched</span>}
                {loc.replayed && (
                  <span className="chip chip-dim" title="Restored from a recording of a real Parallel Task run so this page is not empty on a cold instance. Re-research runs it live.">
                    replayed
                  </span>
                )}
                {loc.fact_count > 0 && <span className="text-[11px] text-dim">{loc.fact_count} facts</span>}

                <span className="ml-auto flex items-center gap-2">
                  <button
                    className="btn text-[11px]"
                    disabled={off || disabled || busyAll}
                    title={off ? `Disabled. Set ${feature?.env || "SCENEPILOT_PARALLEL_TASK=1"}.` : `One Parallel Task run on ${data?.processor || "core-fast"} — roughly $0.03 and 1–5 minutes`}
                    onClick={() => act(loc.id, () => api.researchLocation(projectId, loc.id))}
                  >
                    {facts.length ? "Re-research" : "Research this location"} · ~$0.03
                  </button>
                  {facts.length > 0 && !loc.watched && (
                    <button
                      className="btn btn-ghost text-[11px]"
                      disabled={off || disabled || busyAll || !data?.live_watch_possible}
                      title={data?.live_watch_possible ? "Parallel re-runs this dossier daily and calls our webhook with only the fields that changed" : "Needs PUBLIC_BASE_URL (hosted URL) so Parallel can reach the webhook — use Simulate locally"}
                      onClick={() => act(loc.id, () => api.watchLocation(projectId, loc.id))}
                    >
                      Watch
                    </button>
                  )}
                  {facts.length > 0 && (
                    <button
                      className="btn btn-ghost text-[11px]"
                      disabled={off || disabled || busyAll}
                      title="Push a fabricated snapshot diff through the real ingestion path (clearly labelled)"
                      onClick={() => act(loc.id, () => api.simulateSnapshot(projectId, loc.id))}
                    >
                      Simulate a change
                    </button>
                  )}
                </span>
              </div>

              {busy === loc.id && <div className="mt-2"><Spinner label={`Parallel is researching ${loc.name}`} /></div>}

              {changes.length > 0 && (
                <>
                  <SectionLabel note="a rule moved — your schedule may still be running on the old one">Changed since you looked</SectionLabel>
                  <ul className="grid gap-2">
                    {changes.map((c) => (
                      <ChangeCard key={c.id} change={c} busy={busyAll} onDecide={(d) => act(c.id, () => api.decideFactChange(projectId, c.id, d))} />
                    ))}
                  </ul>
                </>
              )}

              {rules.length > 0 && (
                <>
                  <SectionLabel note="the only facts that can reject a schedule option">Constrains the schedule</SectionLabel>
                  <ul className="grid gap-2">
                    {rules.map((f) => (
                      <ConstraintCard key={f.id} fact={f} busy={busyAll} onDecide={(d) => act(f.id, () => api.decideFact(projectId, f.id, d))} />
                    ))}
                  </ul>
                </>
              )}

              {advisory.length > 0 && (
                <>
                  <SectionLabel>
                    <button className="uppercase tracking-[0.14em] hover:text-muted" onClick={() => setExpanded({ ...expanded, [loc.id]: !open })}>
                      {open ? "▾" : "▸"} Advisory · {advisory.length}
                    </button>
                  </SectionLabel>
                  <p className="-mt-1 mb-1 text-[11px] text-dim">
                    Not mechanically checkable, so these inform the plan rather than constraining it.
                    {!open && " " + advisory.slice(0, 4).map((f) => f.label).join(" · ") + (advisory.length > 4 ? " …" : "")}
                  </p>
                  {open && (
                    <ul className="rounded border border-line bg-elev px-2.5">
                      {advisory.map((f) => (
                        <AdvisoryRow key={f.id} fact={f} busy={busyAll} onDecide={(d) => act(f.id, () => api.decideFact(projectId, f.id, d))} />
                      ))}
                    </ul>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>

      {!off && locations.every((l) => l.fact_count === 0) && (
        <p className="mt-3 text-[12px] text-dim">
          No dossiers yet. Each run costs about $0.03 and takes a few minutes — which is why it never starts on its own.
        </p>
      )}
    </section>
  );
}
