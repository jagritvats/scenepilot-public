import type { ChangeSet, CoordinationAction } from "@/lib/api";

const GROUP: Record<string, string> = { schedule_item: "Schedule", equipment_call: "Equipment", transport: "Transport", shoot_day: "Day", scene: "Scenes" };

export function ChangeSetView({ cs }: { cs: ChangeSet }) {
  const groups = new Map<string, ChangeSet["changes"]>();
  for (const c of cs.changes) if (c.entity_type !== "shoot_day") groups.set(c.entity_type, [...(groups.get(c.entity_type) || []), c]);
  const dayChange = cs.changes.find((c) => c.entity_type === "shoot_day");
  return (
    <div className="card p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="kicker">ChangeSet</div>
        <span className="mono text-[11px] text-dim">{cs.id}</span>
        {/* A reverted recovery keeps its row exactly as it was approved — the audit trail has to read
            *approved, then reverted*, not "nothing ever happened" — so the chip is the only thing
            that can say the production no longer stands behind it. Without this, a rolled-back
            recovery sat under the live option list still claiming "applied · producer", beside the
            inverted change set that undid it, which carries the same stamps. */}
        {cs.applied_at && (
          cs.rescinded
            ? <span className="chip chip-warn" title={`Approved by ${cs.approved_by}, then rolled back. Both are on the record.`}>reverted · was applied by {cs.approved_by}</span>
            : <span className="chip chip-ok">applied · {cs.approved_by}</span>
        )}
        {dayChange && <span className="chip chip-dim">{dayChange.label}: {dayChange.before} → {dayChange.after}</span>}
        <span className="ml-auto text-[12px] text-muted">{cs.summary}</span>
      </div>
      {/* Wide, nowrap and the screen the demo ends on — it scrolls rather than being clipped away
          by the body's `overflow-x: hidden`, and only splits where two columns actually fit. */}
    <div className="mt-3 grid gap-4 xl:grid-cols-2">
        {[...groups.entries()].map(([type, changes]) => (
          <div key={type}>
            <div className="text-[11px] text-dim uppercase tracking-wider mb-1">{GROUP[type] || type}</div>
            <table className="w-full text-[12px]">
              <tbody>
                {changes.map((c, i) => (
                  <tr key={i} className="border-t border-line align-top">
                    <td className="py-1.5 pr-2 font-medium whitespace-nowrap">{c.label}</td>
                    <td className="py-1.5 pr-2 text-dim whitespace-nowrap">{c.field}</td>
                    <td className="py-1.5 mono whitespace-nowrap">
                      <span className="text-muted">{c.before ?? "—"}</span>
                      <span className="text-dim mx-1">→</span>
                      <span className={c.after === null ? "text-warn" : "text-ok"}>{c.after ?? "unscheduled"}</span>
                    </td>
                    <td className="py-1.5 pl-2 text-muted">{c.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}

const ICON: Record<string, string> = {
  SCHEDULE_REGENERATED: "▤", CALL_SHEET_REGENERATED: "▥", CREW_NOTIFICATION: "✉", CAST_NOTIFICATION: "★", EQUIPMENT_CALL_UPDATED: "⚙", TRANSPORT_UPDATED: "⛟", MEAL_COUNT_UPDATED: "◔", LOCATION_CONTACT_UPDATE: "⌖", SCENE_CARRY_OVER: "↷",
};

export function ActionsList({ actions }: { actions: CoordinationAction[] }) {
  return (
    <div className="card p-4">
      <div className="flex items-center gap-3">
        <div className="kicker">Coordinated actions</div>
        <span className="text-[12px] text-muted">{actions.length} derived from the approved ChangeSet</span>
        <span className="ml-auto chip chip-dim">simulated delivery</span>
      </div>
      <ul className="mt-3 grid gap-2 md:grid-cols-2">
        {actions.map((a, i) => (
          <li key={a.id} className="rise rounded border border-line bg-elev p-3" style={{ animationDelay: `${i * 60}ms` }}>
            <div className="flex items-center gap-2">
              <span className="text-accent w-4 text-center">{ICON[a.kind] || "•"}</span>
              <span className="text-sm font-medium">{a.title}</span>
              {a.target && <span className="ml-auto text-[11px] text-dim truncate max-w-[45%]">→ {a.target}</span>}
            </div>
            <ul className="mt-1.5 pl-6 text-[12px] text-muted space-y-0.5 list-disc">
              {a.details.slice(0, 4).map((d, j) => (
                <li key={j}>{d}</li>
              ))}
              {a.details.length > 4 && <li className="list-none text-dim">+{a.details.length - 4} more</li>}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  );
}
