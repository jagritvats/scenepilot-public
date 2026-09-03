"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { api, type MovementOrder } from "@/lib/api";
import { Kicker, LoadError } from "@/components/ui";

/**
 * The movement order — the one page a transport captain carries when the day moves.
 *
 * Every leg here is the production's own: real coordinates, the production's own travel minutes, and
 * the vehicle actually booked. What it will not do is imply a road route or a driving time nobody
 * measured, and where a departure falls before the scene it follows, it prints the overlap rather
 * than quietly re-timing it — that conflict is what the document is for.
 */

const MONO = { fontFamily: "var(--font-plex-mono)" } as const;

export default function MovementOrderPage({ params }: { params: Promise<{ id: string; dayId: string }> }) {
  const { id, dayId } = use(params);
  const [order, setOrder] = useState<MovementOrder | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.movementOrder(id, dayId).then((r) => alive && setOrder(r.movement_order)).catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [id, dayId]);

  if (!order) return error ? <LoadError error={error} missing="Shoot day not found" /> : <div className="card p-8 shimmer h-72" />;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap print:hidden">
        <div>
          <Kicker>Movement order</Kicker>
          <h1 className="display text-3xl font-bold">Day {order.day_number} transport</h1>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <Link href={`/projects/${id}/days/${dayId}/call-sheet`} className="btn text-xs">Call sheet</Link>
          <button className="btn text-xs" onClick={() => window.print()}>Print / PDF</button>
          <Link href={`/projects/${id}/days/${dayId}`} className="btn btn-ghost text-xs">Back to the day</Link>
        </div>
      </div>

      <article
        className="print-portrait print-tint bg-white text-[#14171d] rounded-md shadow-xl p-6 print:shadow-none print:p-3 print:rounded-none print:border print:border-[#c9ced8]"
        style={{ fontFamily: "var(--font-plex-sans)" }}
      >
        <header className="flex items-start gap-4 border-b-2 border-[#14171d] pb-3">
          <div className="flex-1">
            <div className="text-[11px] tracking-[.18em] uppercase text-[#5a6272]">
              {order.production}{order.fictional ? " · fictional production" : ""} · movement order
            </div>
            <div className="display text-3xl font-bold leading-none mt-1">
              DAY {order.day_number}
              <span className="text-[#5a6272] font-semibold"> of {order.day_of_total}</span>
              <span className="text-[#5a6272] font-semibold"> · {order.date}</span>
            </div>
          </div>
          <div className="text-right">
            <div className="display text-2xl font-bold">{order.move_count} MOVE{order.move_count === 1 ? "" : "S"}</div>
            <div className="text-[12px]" style={MONO}>
              unit call {order.unit_call}
              {order.total_straight_line_km !== null && ` · ${order.total_straight_line_km} km`}
              {order.total_travel_minutes !== null && ` · ${order.total_travel_minutes} min in transit`}
            </div>
          </div>
        </header>

        {order.note ? (
          <p className="mt-4 text-[12px] text-[#5a6272] italic">{order.note}</p>
        ) : (
          <section className="mt-3">
            <SheetTitle>Legs</SheetTitle>
            <div className="overflow-x-auto scroll-thin print:overflow-visible">
            <table className="w-full min-w-[19rem] text-[12px]">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-[#5a6272] border-b border-[#c9ced8]">
                  <th className="py-1 pr-2 w-6">#</th>
                  <th className="py-1 pr-2">From → to</th>
                  <th className="py-1 pr-2">Vehicle</th>
                  <th className="py-1 pr-2">Depart</th>
                  <th className="py-1 pr-2">Arrive</th>
                  <th className="py-1 pr-2 text-right">Km</th>
                  <th className="py-1 text-right">Transit</th>
                </tr>
              </thead>
              <tbody>
                {order.legs.map((leg) => (
                  <tr key={leg.index} className="border-b border-[#e6e9ef] align-top">
                    <td className="py-1.5 pr-2 display font-bold text-[14px]">{leg.index}</td>
                    <td className="py-1.5 pr-2">
                      {leg.from_name} <span className="text-[#5a6272]">→</span> {leg.to_name}
                      <div className="text-[10px] text-[#5a6272]" style={MONO}>
                        wraps Sc {leg.after_scene} at {leg.wrap_at} · first shot Sc {leg.before_scene} at {leg.next_shot_at}
                        {leg.gap_minutes !== null && ` · ${leg.gap_minutes} min off camera`}
                      </div>
                      {/* A conflict on a real sheet, printed as one. */}
                      {leg.departure_before_wrap_minutes !== null && (
                        <div className="text-[10px] font-semibold text-[#a63a2e]">
                          Departs {leg.departure_before_wrap_minutes} min before Sc {leg.after_scene} wraps — the van cannot
                          leave with the unit still shooting.
                        </div>
                      )}
                      {leg.load_squeezed && (
                        <div className="text-[10px] text-[#8a5a00]">
                          Only {leg.load_margin_minutes} min between wrap and departure — a van takes about 15 to load, so
                          the loading is coming out of the move.
                        </div>
                      )}
                      {leg.slack_minutes !== null && leg.slack_minutes < 0 && (
                        <div className="text-[10px] font-semibold text-[#a63a2e]">
                          {Math.abs(leg.slack_minutes)} min short: the transit is longer than the gap between scenes.
                        </div>
                      )}
                    </td>
                    <td className="py-1.5 pr-2">{leg.vehicle_name || <span className="text-[#5a6272] italic">none booked</span>}</td>
                    <td className="py-1.5 pr-2 whitespace-nowrap" style={MONO}>{leg.departure || "—"}</td>
                    <td className="py-1.5 pr-2 whitespace-nowrap" style={MONO}>
                      {leg.arrival || <span className="text-[#5a6272]">—</span>}
                    </td>
                    <td className="py-1.5 pr-2 text-right" style={MONO}>{leg.straight_line_km ?? "—"}</td>
                    <td className="py-1.5 text-right" style={MONO}>
                      {leg.travel_minutes !== null ? `${leg.travel_minutes} min` : <span className="text-[#5a6272]">untimed</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
            {order.legs.some((l) => l.untimed) && (
              <p className="mt-1 text-[9px] text-[#5a6272]">
                A leg reads <b>untimed</b> where this production holds no travel time between those two locations. No arrival
                is printed for it — the scheduler&apos;s own fallback is an assumption for placing a scene, not a time to give a driver.
              </p>
            )}
          </section>
        )}

        <section className="mt-4 break-inside-avoid">
          <SheetTitle>Locations, in the order the day works them</SheetTitle>
          <ol className="text-[12px] space-y-0.5">
            {order.locations.map((loc, i) => (
              <li key={loc.id}>
                <b>{i + 1}. {loc.name}</b>
                <span className="text-[#5a6272]" style={MONO}>
                  {" "}· {loc.first_start}–{loc.last_end} · Sc {loc.scene_numbers.join(", ")}
                  {loc.latitude !== null && loc.longitude !== null && ` · ${loc.latitude.toFixed(4)}, ${loc.longitude.toFixed(4)}`}
                </span>
              </li>
            ))}
          </ol>
          {order.locations_missing_coordinates.length > 0 && (
            <p className="mt-1 text-[9px] text-[#5a6272]">
              No coordinates on file for {order.locations_missing_coordinates.join(", ")} — distance to and from those sets is not measured.
            </p>
          )}
        </section>

        <section className="mt-4 break-inside-avoid">
          <SheetTitle>For the transport captain to complete</SheetTitle>
          <div className="grid gap-2 md:grid-cols-2 text-[11px]">
            {order.to_be_completed.map((f) => (
              <div key={f.field} className="border border-dashed border-[#c9ced8] rounded px-2.5 py-2">
                <div className="font-semibold">{f.field}</div>
                <div className="text-[10px] text-[#5a6272]">{f.reason}</div>
              </div>
            ))}
          </div>
        </section>

        <footer className="mt-4 pt-2 border-t border-[#c9ced8] text-[9px] text-[#5a6272] break-inside-avoid space-y-0.5">
          <p>{order.basis.distance}</p>
          <p>{order.basis.travel_minutes}</p>
          <p>{order.basis.coordinates}</p>
        </footer>
      </article>
    </div>
  );
}

function SheetTitle({ children }: { children: React.ReactNode }) {
  return <div className="display font-bold uppercase tracking-[.12em] text-[12px] border-b border-[#14171d] mb-1">{children}</div>;
}
