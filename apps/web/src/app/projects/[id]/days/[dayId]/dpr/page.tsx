"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { api, inr, type DailyProductionReport } from "@/lib/api";
import { Kicker } from "@/components/ui";

/**
 * The Daily Production Report — the receipt for a day the unit has finished.
 *
 * The call sheet next door is an instruction for a day ahead; this is the record of one behind, and
 * it is the document a completion bond and an insurer read before any other. Its whole body already
 * existed in state and appeared on no screen.
 *
 * It refuses to render for a day that has not wrapped, and the refusal is the feature: an
 * authoritative-looking report of a day that has not happened is the most convincing lie this
 * product could tell. The API says so with a 409 and a reason; this page prints that reason and
 * points at the document that *is* right for a day still ahead.
 */

const HHMM = { fontFamily: "var(--font-plex-mono)" } as const;

function mins(total: number) {
  const h = Math.floor(total / 60);
  const m = total % 60;
  return m ? `${h} h ${String(m).padStart(2, "0")} min` : `${h} h`;
}

export default function DprPage({ params }: { params: Promise<{ id: string; dayId: string }> }) {
  const { id, dayId } = use(params);
  const [dpr, setDpr] = useState<DailyProductionReport | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .dpr(id, dayId)
      .then((r) => alive && setDpr(r.dpr))
      .catch((e) => alive && setRefusal(e instanceof Error ? e.message : String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [id, dayId]);

  if (loading) return <div className="card p-8 shimmer h-72" />;

  if (!dpr) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          <div>
            <Kicker>Daily production report</Kicker>
            <h1 className="display text-3xl font-bold">Not issued</h1>
          </div>
          <Link href={`/projects/${id}/days/${dayId}`} className="btn btn-ghost text-xs ml-auto">Back to the day</Link>
        </div>
        <section className="card p-6 space-y-3">
          <p className="text-sm">{refusal || "This day has delivered nothing to report."}</p>
          <Link href={`/projects/${id}/days/${dayId}/call-sheet`} className="btn text-xs">
            Open the call sheet instead
          </Link>
        </section>
      </div>
    );
  }

  const shot = dpr.scenes_completed;
  const carried = dpr.scenes_carried;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap print:hidden">
        <div>
          <Kicker>Daily production report</Kicker>
          <h1 className="display text-3xl font-bold">Day {dpr.day_number} — what it delivered</h1>
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
              {dpr.production}{dpr.fictional ? " · fictional production" : ""} · daily production report
            </div>
            <div className="display text-3xl font-bold leading-none mt-1">
              DAY {dpr.day_number}
              <span className="text-[#5a6272] font-semibold"> of {dpr.day_of_total}</span>
              <span className="text-[#5a6272] font-semibold"> · {dpr.date}</span>
            </div>
            <div className="text-[12px] mt-1 text-[#5a6272]">
              {dpr.crew_size} crew · {dpr.units.join(", ").toLowerCase()} unit · status {dpr.status.replace(/_/g, " ").toLowerCase()}
            </div>
          </div>
          <div className="text-right">
            <div className="inline-block border-2 border-[#14171d] px-2 py-0.5 mb-1 display text-[13px] font-bold tracking-wide">
              WRAPPED
            </div>
            <div className="display text-2xl font-bold">WRAP {dpr.wrap}</div>
            <div className="text-[12px]">
              call {dpr.unit_call} · first shot {dpr.first_shot} · {mins(dpr.elapsed_minutes)} elapsed
            </div>
          </div>
        </header>

        {/* The four numbers a production office reads first. */}
        <section className="mt-3 grid gap-3 grid-cols-2 md:grid-cols-4 text-[11px]">
          {[
            { label: "Scenes shot", value: `${shot.length}`, note: carried.length ? `${carried.length} carried` : "nothing carried" },
            { label: "Pages", value: dpr.pages.shot_label ?? "—", note: dpr.pages.scheduled_label ? `of ${dpr.pages.scheduled_label} scheduled` : "not paginated" },
            { label: "Screen time shot", value: `${dpr.minutes_shot} min`, note: `${mins(dpr.standard_minutes)} standard day` },
            { label: "Cost in consequences", value: inr(dpr.cost.total_inr), note: "as shot" },
          ].map((s) => (
            <div key={s.label} className="border border-[#c9ced8] rounded px-3 py-2">
              <div className="font-semibold uppercase tracking-wider text-[10px] text-[#5a6272]">{s.label}</div>
              <div className="display text-xl font-bold leading-tight" style={HHMM}>{s.value}</div>
              <div className="text-[10px] text-[#5a6272]">{s.note}</div>
            </div>
          ))}
        </section>

        <section className="mt-4">
          <SheetTitle>Scenes completed</SheetTitle>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-[#5a6272] border-b border-[#c9ced8]">
                <th className="py-1 pr-2">Sc</th>
                <th className="py-1 pr-2">Set / heading</th>
                <th className="py-1 pr-2">Unit</th>
                <th className="py-1 pr-2">Shot</th>
                <th className="py-1 pr-2 text-right">Min</th>
                <th className="py-1 text-right">Pgs</th>
              </tr>
            </thead>
            <tbody>
              {shot.map((r) => (
                <tr key={r.item_id} className="border-b border-[#e6e9ef] align-top">
                  <td className="py-1.5 pr-2 display font-bold text-[14px]">{r.scene_number}</td>
                  <td className="py-1.5 pr-2">
                    {r.heading}
                    {r.location && <span className="text-[#5a6272]"> · {r.location}</span>}
                    {r.note && <div className="text-[10px] text-[#8a5a00]">{r.note}</div>}
                  </td>
                  <td className="py-1.5 pr-2 text-[10px] uppercase tracking-wider">{r.unit.toLowerCase()}</td>
                  <td className="py-1.5 pr-2 whitespace-nowrap" style={HHMM}>{r.start}–{r.end}</td>
                  <td className="py-1.5 pr-2 text-right" style={HHMM}>{r.minutes}</td>
                  <td className="py-1.5 text-right" style={HHMM}>{r.eighths !== null ? `${r.eighths}/8` : "—"}</td>
                </tr>
              ))}
              {shot.length === 0 && (
                <tr><td colSpan={6} className="py-2 text-[#5a6272] italic">No scene on this day was completed.</td></tr>
              )}
            </tbody>
          </table>
          {dpr.pages.reason && <p className="mt-1 text-[9px] text-[#5a6272]">{dpr.pages.reason}</p>}
        </section>

        {carried.length > 0 && (
          <section className="mt-4">
            <SheetTitle>Carried to another day</SheetTitle>
            <ul className="text-[12px] space-y-0.5">
              {carried.map((r) => (
                <li key={r.item_id} className="border-l-2 border-l-[#14171d] pl-2">
                  <b>Sc {r.scene_number}</b> {r.heading}
                  <span className="text-[#5a6272]"> · scheduled {r.start}–{r.end} · {r.status.toLowerCase()}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <section className="break-inside-avoid">
            <SheetTitle>What the day cost</SheetTitle>
            {dpr.cost.lines.length === 0 ? (
              <p className="text-[11px] text-[#5a6272]">
                No overtime, no carry-over and no held cast. That is a cost of zero, not an absence of information.
              </p>
            ) : (
              <table className="w-full text-[12px]">
                <tbody>
                  {dpr.cost.lines.map((l) => (
                    <tr key={l.key} className="border-b border-[#e6e9ef]">
                      <td className="py-1 pr-2">{l.label}</td>
                      <td className="py-1 text-right whitespace-nowrap" style={HHMM}>{inr(l.cost_inr)}</td>
                    </tr>
                  ))}
                  <tr className="border-t-2 border-[#14171d] font-semibold">
                    <td className="py-1 pr-2">Total</td>
                    <td className="py-1 text-right" style={HHMM}>{inr(dpr.cost.total_inr)}</td>
                  </tr>
                </tbody>
              </table>
            )}
            {dpr.cost.not_priced.length > 0 && (
              <p className="mt-1 text-[9px] text-[#5a6272]">
                Not priced — {dpr.cost.not_priced.map((n) => n.reason).join(" ")}
              </p>
            )}
          </section>

          <section className="break-inside-avoid">
            <SheetTitle>Cast worked</SheetTitle>
            {dpr.cast_worked.length === 0 ? (
              <p className="text-[11px] text-[#5a6272] italic">No performer worked a completed scene on this day.</p>
            ) : (
              <table className="w-full text-[12px]">
                <tbody>
                  {dpr.cast_worked.map((c) => (
                    <tr key={c.cast_id} className="border-b border-[#e6e9ef]">
                      <td className="py-1 pr-2 w-8 text-right" style={HHMM}>{c.cast_number ?? "—"}</td>
                      <td className="py-1 pr-2">{c.name}</td>
                      <td className="py-1 text-right text-[10px] text-[#5a6272]" style={HHMM}>Sc {c.scenes.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="mt-1 text-[9px] text-[#5a6272]">
              Read from the scenes that were completed. A performer whose scene was carried was held, not worked — the
              DOOD prices that separately.
            </p>
          </section>
        </div>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <section className="break-inside-avoid">
            <SheetTitle>Locations worked</SheetTitle>
            <ul className="text-[12px]">
              {dpr.locations.map((l) => <li key={l}>{l}</li>)}
              {dpr.locations.length === 0 && <li className="text-[#5a6272] italic">none on file</li>}
            </ul>
          </section>

          <section className="break-inside-avoid">
            <SheetTitle>Advance — tomorrow</SheetTitle>
            {dpr.advance ? (
              <div className="text-[12px]">
                <div>
                  <b>Day {dpr.advance.day_number}</b> · {dpr.advance.date} · call{" "}
                  <span style={HHMM}>{dpr.advance.unit_call}</span>
                </div>
                <div className="text-[#5a6272]">{dpr.advance.sets.join(" · ") || "no set on file"}</div>
                <ul className="mt-1">
                  {dpr.advance.scenes.map((s) => (
                    <li key={s.scene}>
                      <span style={HHMM}>{s.start}</span> — Sc {s.scene} {s.heading}
                    </li>
                  ))}
                </ul>
                {dpr.advance.note && <p className="text-[10px] text-[#5a6272] italic">{dpr.advance.note}</p>}
              </div>
            ) : (
              <p className="text-[11px] text-[#5a6272] italic">This is the last day on the schedule.</p>
            )}
          </section>
        </div>

        {/* A form with parts still to fill in, rather than a complete record that is wrong. */}
        <section className="mt-4 break-inside-avoid">
          <SheetTitle>For the production office to complete</SheetTitle>
          <div className="grid gap-2 md:grid-cols-2 text-[11px]">
            {dpr.to_be_completed.map((f) => (
              <div key={f.field} className="border border-dashed border-[#c9ced8] rounded px-2.5 py-2">
                <div className="font-semibold">{f.field}</div>
                <div className="text-[10px] text-[#5a6272]">{f.reason}</div>
              </div>
            ))}
          </div>
        </section>

        <footer className="mt-4 pt-2 border-t border-[#c9ced8] text-[10px] text-[#5a6272] break-inside-avoid">
          <p>{dpr.summary}</p>
          <p className="mt-1">{dpr.provenance}</p>
        </footer>
      </article>
    </div>
  );
}

function SheetTitle({ children }: { children: React.ReactNode }) {
  return <div className="display font-bold uppercase tracking-[.12em] text-[12px] border-b border-[#14171d] mb-1">{children}</div>;
}
