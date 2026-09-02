"use client";

import type { FactChange } from "@/lib/api";
import { Citations } from "./Citations";

/**
 * A rule moved, and the schedule may be running on the old one right now.
 *
 * Lifted out of DossierPanel unchanged so the project-level drift inbox renders drift exactly as the
 * location panel does — one card, one vocabulary, whichever screen a producer meets it on.
 */
export function ChangeCard({ change, busy, onDecide }: { change: FactChange; busy: boolean; onDecide: (d: "adopt" | "dismiss") => void }) {
  const live = change.old_accepted && change.old_binds;
  const pending = change.status === "PENDING";
  const source = change.detected_by === "preflight" ? "Parallel Task · pre-flight re-check" : "Parallel Monitor · snapshot";
  return (
    <li className={`rounded border p-3 ${live && pending ? "border-warn/70 bg-warn/5" : "border-line bg-elev"}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="chip chip-parallel">{source}</span>
        <span className={`chip ${change.binding === "HARD" ? "chip-bad" : "chip-dim"}`}>{change.binding.toLowerCase()}</span>
        {change.simulated && <span className="chip chip-warn">simulated event</span>}
        {!pending && <span className="chip chip-dim">{change.status.toLowerCase()}</span>}
        <span className="ml-auto text-[11px] text-dim">{change.label}</span>
      </div>

      {live && pending && <p className="mt-1.5 text-[12px] text-warn">Your schedule is being enforced against the old value right now.</p>}

      <div className="mt-1.5 text-[13px]">
        <div className="text-dim line-through">{change.old_value || "(nothing recorded)"}</div>
        <div className="font-medium">{change.new_value}</div>
      </div>
      {change.reasoning && <p className="mt-0.5 text-[12px] text-muted">{change.reasoning}</p>}

      <div className="mt-2 flex items-center gap-2 flex-wrap">
        <Citations citations={change.citations} />
      </div>

      {pending ? (
        <div className="mt-2.5 flex items-center gap-2 flex-wrap">
          <button className="btn btn-primary text-[11px]" disabled={busy} onClick={() => onDecide("adopt")}>
            Adopt the new value
          </button>
          <button className="btn btn-ghost text-[11px]" disabled={busy} onClick={() => onDecide("dismiss")} title="Keep the value you already signed off">
            Keep mine
          </button>
          {change.binding === "HARD" && <span className="text-[11px] text-dim">adopting clears your acceptance — you sign off the new window separately</span>}
        </div>
      ) : (
        <div className="mt-2 text-[11px] text-dim">
          {change.status === "ADOPTED" ? "adopted" : "dismissed"}
          {change.decided_by ? ` by ${change.decided_by}` : ""}
        </div>
      )}
    </li>
  );
}
