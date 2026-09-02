"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { api, type RegisterRisk, type RiskRegister, type RiskStatus } from "@/lib/api";
import { ConflictHeatmap } from "@/components/ConflictHeatmap";
import { Kicker, LoadError, Spinner } from "@/components/ui";

/**
 * The risk register — every planned scene's risks in one place, ordered by exposure.
 *
 * The engine has always written these and weighted them into the readiness score; they were only
 * ever visible one scene at a time, which is the single view in which a register is useless.
 *
 * The denominator is the honest part and is stated on the page: a scene nobody has planned has no
 * register, not an empty one. "0 risks" for an unresearched scene would read as "safe" and mean
 * "nobody looked", so the page names those scenes instead of counting them as clear.
 *
 * The second half is the producer's. A register that can only be read is a printout; in a real
 * production office the register *is* the decision log, and the row that has been settled — who owns
 * it, what they decided, when — is the part worth reading back weeks later. So a decided risk keeps
 * its row rather than disappearing behind a filter, and the filter defaults to showing everything.
 */

const SEVERITY_TONE: Record<string, string> = {
  CRITICAL: "chip-bad",
  HIGH: "chip-warn",
  MEDIUM: "chip-dim",
  LOW: "chip-dim",
};

const STATUS_TONE: Record<RiskStatus, string> = {
  OPEN: "chip-warn",
  ACCEPTED: "chip-dim",
  MITIGATING: "chip-info",
  CLOSED: "chip-ok",
};

/** What each verdict commits the production to, said out loud — the word alone is ambiguous, and
 *  "accepted" in particular reads to half a crew as "handled" and to the other half as "ignored". */
const STATUS_HINT: Record<RiskStatus, string> = {
  OPEN: "Nobody has decided anything yet. This is the engine's own state, not a producer's.",
  ACCEPTED: "Carried knowingly. No work is planned against it and the day is expected to absorb it.",
  MITIGATING: "Somebody is working it. The mitigations below are the plan, and the owner holds it.",
  CLOSED: "It cannot reach this production any more, or it already happened and is behind us.",
};

const STATUSES: RiskStatus[] = ["OPEN", "MITIGATING", "ACCEPTED", "CLOSED"];

const when = (iso: string | null) => {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

function RiskRow({
  projectId,
  risk,
  busy,
  editing,
  onEdit,
  onDecide,
}: {
  projectId: string;
  risk: RegisterRisk;
  busy: boolean;
  editing: boolean;
  onEdit: (open: boolean) => void;
  onDecide: (body: { status: RiskStatus; owner: string | null; note: string | null }) => void;
}) {
  // Seeded from the row so amending a decision starts from the decision, not from a blank form —
  // re-typing an owner to change a note is how a register loses its owners.
  const [status, setStatus] = useState<RiskStatus>(risk.status);
  const [owner, setOwner] = useState(risk.owner ?? "");
  const [note, setNote] = useState(risk.decision_note ?? "");
  const decided = risk.status !== "OPEN";

  return (
    <li className={`card p-3 space-y-1.5 ${decided ? "border-l-2 border-l-line-strong" : ""}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`chip ${SEVERITY_TONE[risk.severity]}`}>{risk.severity.toLowerCase()}</span>
        <span className="chip chip-dim" title="A risk is only a FACT where a citation survived validation.">
          {risk.kind.toLowerCase()}
        </span>
        <span className="text-[12px] font-medium">{risk.title}</span>
        <span className="ml-auto mono text-[11px] text-dim">
          likelihood {Math.round(risk.likelihood * 100)}% · confidence {Math.round(risk.confidence * 100)}%
        </span>
      </div>

      <p className="text-[12px] text-muted">{risk.description}</p>

      <div className="flex items-center gap-2 flex-wrap text-[11px] text-dim">
        <Link href={`/projects/${projectId}/scenes/${risk.scene_id}`} className="text-info hover:underline">
          Sc {risk.scene_number} — {risk.scene_heading}
        </Link>
        {risk.scheduled_on.map((d) => (
          <Link key={d.shoot_day_id} href={`/projects/${projectId}/days/${d.shoot_day_id}`} className="chip chip-dim hover:text-fg">
            Day {d.day_number}
          </Link>
        ))}
        {risk.scheduled_on.length === 0 && <span>not scheduled on any day</span>}
      </div>

      {risk.mitigations.length > 0 && (
        <ul className="list-disc pl-5 text-[11px] text-muted">
          {risk.mitigations.map((m) => <li key={m}>{m}</li>)}
        </ul>
      )}

      {/* The decision, shown where it was made. A verdict kept on a separate screen from the risk it
          settles is a verdict nobody re-reads. */}
      <div className="pt-1.5 border-t border-line flex items-start gap-2 flex-wrap text-[11px]">
        <span className={`chip ${STATUS_TONE[risk.status]}`} title={STATUS_HINT[risk.status]}>
          {risk.status.toLowerCase()}
        </span>
        {risk.owner && <span className="text-muted">owner <span className="text-fg">{risk.owner}</span></span>}
        {decided && risk.decided_by && (
          <span className="text-dim">
            decided by {risk.decided_by}
            {risk.decided_at ? ` · ${when(risk.decided_at)}` : ""}
          </span>
        )}
        {!decided && !risk.owner && <span className="text-dim">nobody owns this yet</span>}
        <button className="btn btn-ghost ml-auto text-[11px]" disabled={busy} onClick={() => onEdit(!editing)}>
          {editing ? "Cancel" : decided ? "Amend the decision" : "Decide"}
        </button>
        {busy && <Spinner />}
      </div>

      {risk.decision_note && !editing && (
        <p className="text-[11px] text-muted border-l-2 border-line pl-2.5">{risk.decision_note}</p>
      )}

      {editing && (
        <div className="rounded border border-line bg-elev p-2.5 space-y-2">
          <div className="flex items-center gap-2 flex-wrap text-[11px]">
            {STATUSES.map((s) => (
              <button
                key={s}
                className={`chip ${s === status ? STATUS_TONE[s] : "chip-dim"} cursor-pointer`}
                title={STATUS_HINT[s]}
                onClick={() => setStatus(s)}
              >
                {s.toLowerCase()}
              </button>
            ))}
            <span className="text-dim">{STATUS_HINT[status]}</span>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <input
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
              placeholder="who holds this — 1st AD, line producer, locations"
              className="bg-bg border border-line rounded px-2 py-1 text-[12px] flex-1 min-w-[220px]"
            />
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="what was decided, and on what basis"
              className="bg-bg border border-line rounded px-2 py-1 text-[12px] flex-1 min-w-[260px]"
            />
            <button
              className="btn btn-primary text-[11px]"
              disabled={busy}
              onClick={() => onDecide({ status, owner: owner.trim() || null, note: note.trim() || null })}
            >
              Record the decision
            </button>
          </div>
          <p className="text-[10px] text-dim">
            Decisions are carried across a re-plan by title, so re-researching this scene will not quietly reopen what
            you settled here.
          </p>
        </div>
      )}
    </li>
  );
}

export default function RiskRegisterPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [reg, setReg] = useState<RiskRegister | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A string key, not a boolean: one row records its decision while every other row stays live.
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "open" | "decided">("all");

  useEffect(() => {
    let alive = true;
    api.riskRegister(id).then((r) => alive && setReg(r)).catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [id]);

  if (!reg) return error ? <LoadError error={error} missing="Project not found" /> : <div className="card p-8 shimmer h-72" />;

  const decide = async (risk: RegisterRisk, body: { status: RiskStatus; owner: string | null; note: string | null }) => {
    setBusy(risk.id);
    setError(null);
    try {
      setReg(await api.decideRisk(id, risk.id, body));
      setEditing(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const decidedCount = reg.risks.filter((r) => r.status !== "OPEN").length;
  const openCount = reg.total - decidedCount;
  // Open first inside each severity bucket, so the highest-exposure thing nobody has answered is
  // always the first row on the page; the settled rows keep their place below rather than vanishing.
  const rows = (severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW") =>
    reg.by_severity[severity]
      .filter((r) => (filter === "open" ? r.status === "OPEN" : filter === "decided" ? r.status !== "OPEN" : true))
      .slice()
      .sort((a, b) => Number(a.status !== "OPEN") - Number(b.status !== "OPEN") || b.exposure - a.exposure);

  const visible = (["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).filter((s) => rows(s).length > 0);

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 flex-wrap">
        <div>
          <Kicker>Risk register · severity × likelihood</Kicker>
          <h1 className="display text-2xl font-bold mt-1">
            {reg.total} risk{reg.total === 1 ? "" : "s"} on record
          </h1>
          <p className="text-muted text-sm mt-1 max-w-2xl">{reg.coverage_note}</p>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map((s) => (
            <span key={s} className={`chip ${reg.counts[s] ? SEVERITY_TONE[s] : "chip-dim"}`}>
              {reg.counts[s]} {s.toLowerCase()}
            </span>
          ))}
          <Link href={`/projects/${id}`} className="btn btn-ghost text-xs">Back to the production</Link>
        </div>
      </div>

      {error && <p className="mt-3 text-[12px] text-bad">{error}</p>}

      {!reg.empty_note && (
        <div className="flex items-center gap-2 flex-wrap text-[11px]">
          <span className="text-dim">show</span>
          {([
            ["all", `all ${reg.total}`],
            ["open", `${openCount} undecided`],
            ["decided", `${decidedCount} decided`],
          ] as const).map(([key, label]) => (
            <button
              key={key}
              className={`chip ${filter === key ? "chip-accent" : "chip-dim"} cursor-pointer`}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
          <span className="text-dim">
            Whatever is showing, the undecided rows sort above the settled ones inside each severity band.
          </span>
        </div>
      )}

      {reg.empty_note ? (
        <section className="card p-6 space-y-2">
          <p className="text-sm">{reg.empty_note}</p>
          <p className="text-[12px] text-dim">{reg.provenance}</p>
        </section>
      ) : visible.length === 0 ? (
        <section className="card p-6 space-y-1">
          <p className="text-sm">
            {filter === "open" ? "Every risk on this register has been decided." : "Nothing on this register has been decided yet."}
          </p>
          <p className="text-[12px] text-muted">
            {filter === "open"
              ? "That is a state worth re-reading rather than trusting — show all to see what each verdict actually was."
              : "A risk stays OPEN until somebody owns it and says what the production is doing about it."}
          </p>
        </section>
      ) : (
        <div className="space-y-4">
          {visible.map((severity) => (
            <section key={severity} className="space-y-2">
              <div className="flex items-baseline gap-2">
                <h2 className="display text-sm font-bold uppercase tracking-wider">{severity}</h2>
                <span className="text-[11px] text-dim">{rows(severity).length}</span>
              </div>
              <ul className="space-y-2">
                {rows(severity).map((risk) => (
                  <RiskRow
                    // Keyed on the decision as well as the id: the editor seeds its fields from the
                    // row, and a stale editor would print the old owner back over a fresh decision.
                    key={`${risk.id}:${risk.status}:${risk.decided_at ?? ""}`}
                    projectId={id}
                    risk={risk}
                    busy={busy === risk.id}
                    editing={editing === risk.id}
                    onEdit={(open) => setEditing(open ? risk.id : null)}
                    onDecide={(body) => decide(risk, body)}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      {/* The other half of "how fragile is this production": risks are what could go wrong, pressure
          is whether there is anywhere to move when it does. */}
      <ConflictHeatmap projectId={id} />

      {reg.unplanned_scenes.length > 0 && (
        <section className="card p-4 space-y-1.5">
          <Kicker>Scenes this register cannot speak for</Kicker>
          <p className="text-[12px] text-muted">
            Nothing has been researched for these, so they carry no risks — which is not the same as carrying none.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {reg.unplanned_scenes.map((s) => (
              <Link key={s.scene_id} href={`/projects/${id}/scenes/${s.scene_id}`} className="chip chip-dim hover:text-fg" title={s.heading}>
                Sc {s.scene_number}
              </Link>
            ))}
          </div>
        </section>
      )}

      <p className="text-[11px] text-dim">{reg.provenance}</p>
    </div>
  );
}
