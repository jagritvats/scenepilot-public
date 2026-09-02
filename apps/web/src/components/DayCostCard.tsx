"use client";

import { inr, type DayCostCard as DayCostCardData } from "@/lib/api";
import { Kicker } from "./ui";

/**
 * What one day costs in consequences — overtime, meal penalties, carry-overs, re-rentals, company
 * moves and held cast, added up once.
 *
 * The footer is not a disclaimer, it is part of the number: a total that quietly dropped a
 * performer whose day rate nobody has stated would read as smaller than the day is, so anything
 * withheld is named here beside the figure it is missing from.
 */
export function DayCostCard({ card }: { card: DayCostCardData }) {
  const record = card.basis === "record";
  const max = Math.max(1, ...card.lines.map((l) => l.cost_inr));

  return (
    <section className="card p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Kicker>What this day costs in consequences</Kicker>
        <span className={`chip ${record ? "chip-dim" : "chip-warn"} ml-auto`} title={record ? "Read off the day's own completion record." : `Projected from the current schedule under ${card.labor_pack}.`}>
          {record ? "as shot" : "projected"}
        </span>
      </div>

      {card.lines.length === 0 ? (
        <p className="text-[12px] text-muted">
          Nothing on this day carries a penalty, a carry-over, a move or a hold. That is a cost of zero, not an
          absence of information.
        </p>
      ) : (
        <ul className="space-y-1">
          {card.lines.map((line) => (
            <li key={line.key} className="space-y-0.5" title={line.detail}>
              <div className="flex items-baseline gap-2 text-[12px]">
                <span className="truncate">{line.label}</span>
                {line.minutes > 0 && <span className="mono text-[10px] text-dim">{line.minutes} min</span>}
                <span className="mono ml-auto shrink-0">{inr(line.cost_inr)}</span>
              </div>
              <div className="h-[3px] rounded bg-line">
                <div className="h-full rounded bg-warn/70" style={{ width: `${(line.cost_inr / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-baseline gap-2 border-t border-line pt-1.5">
        <span className="text-[12px] font-medium">Total</span>
        <span className="mono ml-auto text-[15px] font-bold">{inr(card.total_inr)}</span>
      </div>

      {card.not_priced.length > 0 && (
        <p className="text-[10px] text-dim">
          <span className="uppercase tracking-wider">Not priced — </span>
          {card.not_priced.map((n) => n.reason).join(" ")}
        </p>
      )}
    </section>
  );
}
