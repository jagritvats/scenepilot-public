import type { ParallelUsage } from "@/lib/api";

/** One-line account of every Parallel call in this run: modes, SKUs, session, estimated cost. */
export function ParallelUsageStrip({ usage, onOpen }: { usage: ParallelUsage | null | undefined; onOpen?: () => void }) {
  if (!usage || (usage.searches === 0 && usage.extracts === 0 && !usage.tasks && !usage.findalls)) return null;
  const modes = Object.entries(usage.by_mode).map(([m, n]) => `${m} ×${n}`).join(" · ");
  const processors = Object.entries(usage.task_processors || {}).map(([p, n]) => `${p} ×${n}`).join(" · ");
  return (
    <div className="card px-4 py-2 flex items-center gap-x-4 gap-y-1 flex-wrap text-[12px]">
      <span className="chip chip-parallel">Parallel</span>
      <span>
        <b className="display text-[15px]">{usage.searches}</b> search{usage.searches === 1 ? "" : "es"}
        {modes && <span className="text-dim"> ({modes})</span>}
      </span>
      <span>
        <b className="display text-[15px]">{usage.extracts}</b> extract{usage.extracts === 1 ? "" : "s"}
        {usage.urls > 0 && <span className="text-dim"> ({usage.urls} URL{usage.urls === 1 ? "" : "s"})</span>}
      </span>
      {usage.tasks > 0 && (
        <span>
          <b className="display text-[15px]">{usage.tasks}</b> dossier{usage.tasks === 1 ? "" : "s"}
          {processors && <span className="text-dim"> ({processors})</span>}
        </span>
      )}
      {usage.findalls > 0 && (
        <span>
          <b className="display text-[15px]">{usage.findalls}</b> entity search{usage.findalls === 1 ? "" : "es"}
          {usage.vendors > 0 && <span className="text-dim"> ({usage.vendors} vendor{usage.vendors === 1 ? "" : "s"})</span>}
        </span>
      )}
      {usage.usage.length > 0 && (
        <span className="flex gap-1 flex-wrap">
          {usage.usage.map((u) => (
            <span key={u.name} className="mono text-[11px] px-1.5 rounded bg-elev border border-line text-muted">{u.name} ×{u.count}</span>
          ))}
        </span>
      )}
      {/* Spend and its counterfactual are different sentences and are printed as two. A replayed
          call cost nothing, so quoting it as spend would price money nobody paid. */}
      <span className="mono text-dim" title="What this deployment actually spent: live calls only.">
        spent ${usage.est_cost_usd.toFixed(3)}
      </span>
      {usage.replayed_cost_usd > 0 && (
        <span className="mono text-dim" title="What the replayed calls would have cost had they run live. Not spent.">
          replayed ${usage.replayed_cost_usd.toFixed(3)} unspent
        </span>
      )}
      {usage.client_model && <span className="mono text-dim">client_model {usage.client_model}</span>}
      {usage.session_ids[0] && <span className="mono text-dim truncate max-w-[260px]" title={usage.session_ids.join(", ")}>session {usage.session_ids[0]}</span>}
      {usage.warnings > 0 && <span className="chip chip-warn">{usage.warnings} warning{usage.warnings === 1 ? "" : "s"}</span>}
      {usage.errors > 0 && <span className="chip chip-bad">{usage.errors} failed</span>}
      {usage.replayed > 0 && <span className="chip chip-warn">{usage.replayed} replayed</span>}
      {onOpen && (
        <button onClick={onOpen} className="ml-auto text-parallel underline decoration-dotted underline-offset-2 hover:text-fg">
          inspect every call
        </button>
      )}
    </div>
  );
}
