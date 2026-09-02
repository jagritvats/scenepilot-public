"use client";

import Link from "next/link";
import { use, useState } from "react";
import { api, type CallSheet, type InsuranceDossier } from "@/lib/api";
import { castColumn } from "@/lib/stripboard";
import { usePoll } from "@/lib/usePoll";
import { useDismissOnEscape } from "@/lib/useDismiss";
import { Kicker, LoadError } from "@/components/ui";
import { DispatchDashboard } from "@/components/DispatchDashboard";
import { OneLinerPanel } from "@/components/OneLinerPanel";

/** A printable call sheet — the document a 1st AD actually sends. Paper-styled on purpose. */
export default function CallSheetPage({ params }: { params: Promise<{ id: string; dayId: string }> }) {
  const { id, dayId } = use(params);
  const { data, error, loading } = usePoll(() => api.callSheet(id, dayId), () => false, 8000);
  const [view, setView] = useState<"after" | "before" | "side">("side");
  const [insuranceOpen, setInsuranceOpen] = useState<boolean>(false);
  const [insuranceDossier, setInsuranceDossier] = useState<InsuranceDossier | null>(null);
  const [insuranceError, setInsuranceError] = useState<string | null>(null);
  const [loadingInsurance, setLoadingInsurance] = useState<boolean>(false);

  const loadInsurance = async () => {
    try {
      setLoadingInsurance(true);
      setInsuranceError(null);
      const res = await api.getInsuranceDossier(id, dayId);
      setInsuranceDossier(res);
      setInsuranceOpen(true);
    } catch (err) {
      setInsuranceError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingInsurance(false);
    }
  };

  if (!data) return loading || !error ? <div className="card p-8 shimmer h-72" /> : <LoadError error={error} missing="Call sheet unavailable" hint="This shoot day has no call sheet to regenerate." />;
  const hasBefore = !!data.baseline;
  const mode = hasBefore ? view : "after";
  const moved = new Set((data.changeset?.changes || []).filter((c) => c.entity_type === "schedule_item").map((c) => c.label.replace("Scene ", "")));
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap print:hidden">
        <div>
          <Kicker>Call sheet · regenerated from production state</Kicker>
          <h1 className="display text-3xl font-bold">Day {data.current.day_number} — {data.current.date}</h1>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          {hasBefore && (
            <div className="flex gap-1">
              {(["before", "side", "after"] as const).map((v) => (
                <button key={v} className={`chip ${view === v ? "chip-accent" : "chip-dim"}`} onClick={() => setView(v)}>{v === "side" ? "before / after" : v}</button>
              ))}
            </div>
          )}
          <a
            href={api.exportMmsxUrl(id, dayId)}
            download
            className="btn text-xs text-accent border-accent/40 hover:border-accent"
            title="ScenePilot's own stripboard XML, shaped after Movie Magic Scheduling's exchange format. Unofficial — not written or validated by MMS."
          >
            📥 Export schedule XML (MMS-compatible, unofficial)
          </a>
          <button
            onClick={loadInsurance}
            disabled={loadingInsurance}
            className="btn text-xs text-emerald-400 border-emerald-500/40 hover:border-emerald-400"
            title="A Force Majeure claim packet compiled from this day's own record: the verified peril, the schedules the engine rejected, and what the approved recovery cost."
          >
            {loadingInsurance ? "Compiling..." : "📋 Force Majeure claim packet"}
          </button>
          {/* The rest of the day's paper, one click away — a unit carries these together. */}
          <Link href={`/projects/${id}/days/${dayId}/sides`} className="btn text-xs">Sides</Link>
          <Link href={`/projects/${id}/days/${dayId}/movement-order`} className="btn text-xs">Movement order</Link>
          <button className="btn text-xs" onClick={() => window.print()}>Print / PDF</button>
          <Link href={`/projects/${id}/days/${dayId}`} className="btn btn-ghost text-xs">Back to the day</Link>
        </div>
      </div>

      {insuranceError && (
        <div className="card px-4 py-3 text-[12px] border-bad/50 print:hidden">
          <span className="chip chip-bad mr-2">dossier unavailable</span>
          <span className="text-muted">{insuranceError}</span>
        </div>
      )}

      {insuranceOpen && insuranceDossier && (
        <InsuranceDossierModal dossier={insuranceDossier} onClose={() => setInsuranceOpen(false)} />
      )}

      {/* Without an approved recovery there is no second version to diff against — and no toggle to
          explain its absence. The sheet is not incomplete; it has simply never been rewritten. */}
      {!hasBefore && (
        <div className="card px-4 py-3 flex items-start gap-x-3 gap-y-1 flex-wrap text-[12px] print:hidden">
          <span className="chip chip-dim mt-0.5">one version</span>
          <p className="text-muted flex-1 min-w-[260px]">
            No recovery has been approved for Day {data.current.day_number}, so this is the sheet as production state stands and there is nothing to diff it against.
            Report a disruption on the day page and approve a recovery, and this page grows a before / after view with every strip the recovery moved highlighted in both.
          </p>
          <Link href={`/projects/${id}/days/${dayId}`} className="text-accent hover:underline whitespace-nowrap mt-0.5">
            Day {data.current.day_number} →
          </Link>
        </div>
      )}

      <div className="print:hidden">
        <DispatchDashboard projectId={id} dayId={dayId} />
      </div>
      {/* `print-sheets` is what makes the before/after survive a page box: the `lg:` breakpoint
          never matches A4, so the two-up is stated for print in globals.css. */}
      <div className={`print-sheets grid gap-4 ${mode === "side" ? "xl:grid-cols-2" : ""} ${insuranceOpen ? "print:hidden" : ""}`}>
        {(mode === "before" || mode === "side") && data.baseline && <Sheet sheet={data.baseline} moved={moved} dim />}
        {(mode === "after" || mode === "side") && <Sheet sheet={data.current} moved={hasBefore ? moved : new Set()} />}
      </div>

      {/* The other half of the paperwork a production circulates when a schedule moves: the call
          sheet says what this unit does tomorrow, the one-liner says what that did to the picture. */}
      <div className={insuranceOpen ? "print:hidden" : ""}>
        <OneLinerPanel projectId={id} />
      </div>
    </div>
  );
}

function Sheet({ sheet, moved, dim = false }: { sheet: CallSheet; moved: Set<string>; dim?: boolean }) {
  /* The schedule block names its cast; the cast block numbers the same performers. Joining them here
   * is the same join the board makes, and it is what lets the schedule print "1, 2, 4" the way a call
   * sheet does — never a number this document has not itself published in the table below. */
  const castNumberByName = new Map(sheet.cast.map((c) => [c.name, c.cast_number] as const));
  const rev = sheet.revision;
  const solar = sheet.solar;
  return (
    /* The page is tinted to its revision colour. On an original that is #ffffff and nothing looks
     * different, which is the point: a white sheet is what an unrevised call sheet is. */
    <article
      className={`print-tint text-[#14171d] rounded-md shadow-xl p-6 print:shadow-none print:p-3 print:rounded-none print:border print:border-[#c9ced8] print:opacity-100 ${dim ? "opacity-90" : ""}`}
      style={{ fontFamily: "var(--font-plex-sans)", backgroundColor: rev.hex }}
    >
      <header className="flex items-start gap-4 border-b-2 border-[#14171d] pb-3">
        <div className="flex-1">
          <div className="text-[11px] tracking-[.18em] uppercase text-[#5a6272]">{sheet.production}{sheet.synthetic ? " · fictional production" : ""} · call sheet</div>
          <div className="display text-3xl font-bold leading-none mt-1">
            DAY {sheet.day_number}
            <span
              className="text-[#5a6272] font-semibold"
              title={`This production's schedule runs to Day ${sheet.day_of_total}. ScenePilot holds Day ${sheet.days_held.join(", Day ")}.`}
            >
              {" "}of {sheet.day_of_total}
            </span>
            <span className="text-[#5a6272] font-semibold"> · {sheet.date}</span>
          </div>
          <div className="text-[12px] mt-1 text-[#5a6272]">{sheet.crew_size} crew · status {sheet.status.replace(/_/g, " ").toLowerCase()}</div>
        </div>
        <div className="text-right">
          <div className="text-[11px] uppercase tracking-wider text-[#5a6272]">{sheet.label}</div>
          {/* The line an AD reads before the date. On a reissue it is the whole status of the
              document — "you are holding blue pages" — so it is set beside the unit call, not in
              a corner, and it is boxed in ink so it survives a monochrome print. */}
          <div
            className="inline-block border-2 border-[#14171d] px-2 py-0.5 mb-1 display text-[13px] font-bold tracking-wide"
            title={rev.is_original ? "The original issue of this sheet. Nothing has been approved against it yet." : `Reissued ${rev.index} time(s) — one per approved recovery on this day. A production reprints on the next colour each time, and the unit reads the colour before the date.`}
          >
            {rev.label}
          </div>
          <div className="display text-2xl font-bold">UNIT CALL {sheet.unit_call}</div>
          <div className="text-[12px]">first shot {sheet.first_shot ?? "—"} · est. wrap <b>{sheet.estimated_wrap}</b> (standard {sheet.standard_wrap})</div>
        </div>
      </header>

      {/* Sun and weather, in the place a call sheet carries them: above the schedule, because the
          schedule below is what they constrain. */}
      <section className="mt-3 grid gap-3 md:grid-cols-2 text-[11px]">
        <div className="border border-[#c9ced8] rounded px-3 py-2">
          <div className="font-semibold uppercase tracking-wider text-[10px] mb-1">Sun</div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5" style={{ fontFamily: "var(--font-plex-mono)" }}>
            <span className="text-[#5a6272]">Sunrise</span><span className="text-right font-semibold">{solar.sunrise}</span>
            <span className="text-[#5a6272]">Sunset</span><span className="text-right font-semibold">{solar.sunset}</span>
            <span className="text-[#5a6272]">Civil twilight</span><span className="text-right">{solar.civil_twilight_dawn} / {solar.civil_twilight_dusk}</span>
            <span className="text-[#5a6272]">Golden (dusk)</span><span className="text-right">{solar.golden_hour_dusk[0]}–{solar.golden_hour_dusk[1]}</span>
          </div>
          <div className="text-[9px] text-[#5a6272] mt-1">{solar.source}</div>
        </div>
        <div className="border border-[#c9ced8] rounded px-3 py-2">
          <div className="font-semibold uppercase tracking-wider text-[10px] mb-1">Weather</div>
          {!sheet.weather.reported ? (
            <p className="text-[#5a6272] italic">{sheet.weather.reason}</p>
          ) : (
            <div className="space-y-1">
              <div className="font-semibold">{sheet.weather.headline}</div>
              {sheet.weather.window && (
                <div style={{ fontFamily: "var(--font-plex-mono)" }}>
                  {sheet.weather.window.start}–{sheet.weather.window.end}
                  {sheet.weather.window.dry_out_minutes > 0 && (
                    <span className="text-[#5a6272]"> · +{sheet.weather.window.dry_out_minutes} min dry-out, clear {sheet.weather.window.clear_at}</span>
                  )}
                </div>
              )}
              {sheet.weather.verification && (
                <div className="text-[#5a6272]">
                  External check: {sheet.weather.verification.status.replace(/_/g, " ").toLowerCase()}
                  {sheet.weather.verification.confidence_pct !== null && <> · {sheet.weather.verification.confidence_pct}% confidence</>}
                </div>
              )}
              {sheet.weather.sources.map((s) => (
                <a key={s.url} href={s.url} target="_blank" rel="noreferrer" className="block text-[10px] text-[#1c4f8b] hover:underline truncate print:text-[#14171d]">
                  {s.title || s.url}
                </a>
              ))}
            </div>
          )}
        </div>
      </section>

      {sheet.advisories.length > 0 && (
        <section className="mt-3 border border-[#e2b93b] bg-[#fff7de] rounded px-3 py-2 text-[12px] print:bg-transparent">
          <div className="font-semibold uppercase tracking-wider text-[11px]">Advisories</div>
          <ul className="list-disc pl-4 mt-1 space-y-0.5">
            {sheet.advisories.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-4">
        <SheetTitle>Shooting schedule</SheetTitle>
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wider text-[#5a6272] border-b border-[#c9ced8]">
              <th className="py-1 w-3" title="A margin asterisk marks every line the last approved recovery moved — the mark a revised page carries, and the one that survives a black-and-white print." />
              <th className="py-1 pr-2">Time</th><th className="py-1 pr-2">Sc</th><th className="py-1 pr-2">Set</th><th className="py-1 pr-2">D/N</th>
              <th className="py-1 pr-2" title="Cast numbers wherever the production numbers every performer in the scene — decoded in Cast &amp; Staggered Prep below.">Cast</th>
              <th className="py-1 pr-2 text-right" title="Script pages in eighths, the unit a schedule is measured in.">Pgs</th>
              <th className="py-1">Location</th>
            </tr>
          </thead>
          <tbody>
            {sheet.schedule.map((r) => {
              const cast = castColumn(r.cast.map((name) => ({ name, cast_number: castNumberByName.get(name) ?? null })));
              const changed = moved.has(r.scene);
              return (
                /* Changed rows carry three marks, deliberately: the tint for the screen, and the
                   asterisk plus the ink rule for paper, because a background never prints. */
                <tr key={r.scene + r.start} className={`border-b border-[#e6e9ef] align-top ${changed ? "bg-[#fff1cc] print:bg-transparent" : ""}`}>
                  <td className={`py-1.5 display font-bold text-[13px] leading-none ${changed ? "border-l-2 border-l-[#14171d]" : ""}`} title={changed ? "Moved by the last approved recovery." : undefined}>
                    {changed ? "*" : ""}
                  </td>
                  <td className="py-1.5 pr-2 whitespace-nowrap" style={{ fontFamily: "var(--font-plex-mono)" }}>{r.start}–{r.end}</td>
                  <td className="py-1.5 pr-2 display font-bold text-[14px]">{r.scene}</td>
                  <td className="py-1.5 pr-2">
                    {r.heading}
                    {r.cover && <span className="ml-1 text-[10px] uppercase tracking-wider text-[#8a5a00]">cover set</span>}
                    {r.status === "MOVED" && <span className="ml-1 text-[10px] uppercase tracking-wider text-[#8a5a00]">moved</span>}
                    {r.unit && r.unit !== "MAIN" && <span className="ml-1 text-[10px] uppercase tracking-wider text-[#1c4f8b]">{r.unit.toLowerCase()} unit</span>}
                  </td>
                  <td className="py-1.5 pr-2 whitespace-nowrap">{r.int_ext} · {r.time_of_day}</td>
                  <td className={`py-1.5 pr-2 ${cast.numbered ? "mono font-semibold" : ""}`} title={cast.title || undefined}>
                    {r.cast.length > 0 ? cast.text : <span className="text-[#5a6272]">no cast</span>}
                  </td>
                  <td className="py-1.5 pr-2 text-right whitespace-nowrap" style={{ fontFamily: "var(--font-plex-mono)" }}>
                    {r.pages ?? <span className="text-[#5a6272]" title="This scene carries no page count.">—</span>}
                  </td>
                  <td className="py-1.5">{r.location.trim() && r.location !== "—" ? r.location.split(" — ")[0] : <span className="text-[#5a6272]">no set on file</span>}</td>
                </tr>
              );
            })}
            {sheet.schedule.length === 0 && (
              <tr>
                <td colSpan={8} className="py-2 text-[#5a6272]">No scene is scheduled on this day.</td>
              </tr>
            )}
          </tbody>
          {sheet.schedule.length > 0 && (
            <tfoot>
              <tr className="border-t-2 border-[#14171d] text-[11px]">
                <td />
                <td className="py-1 pr-2 uppercase tracking-wider text-[#5a6272]" colSpan={5}>
                  {sheet.pages.scene_count} scene{sheet.pages.scene_count === 1 ? "" : "s"}
                </td>
                <td className="py-1 pr-2 text-right font-bold whitespace-nowrap" style={{ fontFamily: "var(--font-plex-mono)" }}>
                  {sheet.pages.total_label ?? <span className="font-normal text-[#5a6272]" title={sheet.pages.reason ?? undefined}>no total</span>}
                </td>
                <td className="py-1 text-[#5a6272]">{sheet.pages.total_label ? "pages" : ""}</td>
              </tr>
            </tfoot>
          )}
        </table>
        {sheet.pages.unpriced_scenes.length > 0 && (
          <p className="mt-1 text-[9px] text-[#5a6272]">
            No day total is stated: Sc {sheet.pages.unpriced_scenes.join(", ")} carr{sheet.pages.unpriced_scenes.length === 1 ? "ies" : "y"} no
            page count, and a partial total would understate the day.
          </p>
        )}
      </section>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <section>
          <SheetTitle>Cast & Staggered Prep (PU / H-MU / Ready / On Set)</SheetTitle>
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-left text-[9px] uppercase tracking-wider text-[#5a6272] border-b border-[#c9ced8]">
                <th className="py-1 w-8">Cast #</th>
                <th className="py-1">Performer</th>
                <th className="py-1">Pickup (PU)</th>
                <th className="py-1">H/MU</th>
                <th className="py-1">On Set</th>
                <th className="py-1">Wrap</th>
              </tr>
            </thead>
            <tbody>
              {sheet.cast.map((c) => (
                <tr key={c.name} className="border-b border-[#e6e9ef]">
                  <td className="py-1 pr-1 align-top mono font-bold text-[12px]">
                    {c.cast_number ?? <span className="text-[#5a6272] font-normal" title="This performer carries no cast number on the production.">—</span>}
                  </td>
                  <td className="py-1 pr-1 font-medium">
                    {c.name}
                    {c.note && <span className="block text-[9px] text-[#8a5a00]">{c.note}</span>}
                    <span className="block text-[9px] text-[#5a6272]">Sc {c.scenes.join(", ")}</span>
                  </td>
                  <td className="py-1 pr-1 whitespace-nowrap mono text-[10px] text-amber-800 font-semibold">{c.pickup || c.call}</td>
                  <td className="py-1 pr-1 whitespace-nowrap mono text-[10px]">{c.hmu || c.call}</td>
                  <td className="py-1 pr-1 whitespace-nowrap mono text-[10px] font-bold text-emerald-800">{c.on_set || c.call}</td>
                  <td className="py-1 whitespace-nowrap mono text-[10px] text-[#5a6272]">{c.wrap}</td>
                </tr>
              ))}
              {sheet.cast.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-2 text-[#5a6272]">
                    No performer is attached to any scene on this day, so there is no cast call and no staggered prep to publish.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          {sheet.cast.length > 0 && (
            <p className="mt-1 text-[9px] text-[#5a6272]">
              Rows are in first-shot order, so the staggered pickups read straight down the column. <b>Cast #</b> is the
              production&apos;s billing number — the same one the schedule above prints, and the one carried on the board,
              the DOOD and the dispatch — which is why the number, not the row position, identifies a performer.
            </p>
          )}
        </section>
        <section>
          <SheetTitle>Equipment calls</SheetTitle>
          <table className="w-full text-[12px]">
            <tbody>
              {sheet.equipment.map((e) => (
                <tr key={e.name} className="border-b border-[#e6e9ef]">
                  <td className="py-1 pr-2">{e.name}</td>
                  <td className="py-1 whitespace-nowrap text-right" style={{ fontFamily: "var(--font-plex-mono)" }}>{e.call}</td>
                </tr>
              ))}
              {sheet.equipment.length === 0 && (
                <tr><td className="py-1 text-[#5a6272]">no equipment call times on this day</td></tr>
              )}
            </tbody>
          </table>
          <SheetTitle className="mt-3">Transport</SheetTitle>
          <table className="w-full text-[12px]">
            <tbody>
              {sheet.transport.map((t, i) => (
                <tr key={i} className="border-b border-[#e6e9ef]">
                  <td className="py-1 pr-2">{t.vehicle}: {t.from.split(" — ")[0]} → {t.to.split(" — ")[0]}</td>
                  <td className="py-1 whitespace-nowrap text-right" style={{ fontFamily: "var(--font-plex-mono)" }}>dep {t.departure}</td>
                </tr>
              ))}
              {sheet.transport.length === 0 && <tr><td className="py-1 text-[#5a6272]">no company moves</td></tr>}
            </tbody>
          </table>
        </section>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <section>
          <SheetTitle>Meals</SheetTitle>
          <div className="text-[12px]">
            Lunch {sheet.meals.lunch.time} · {sheet.meals.lunch.count} heads
            {!sheet.meals.lunch.scheduled_gap && <span className="text-[#a63a2e]"> — no 30-min gap scheduled</span>}
          </div>
          <div className="text-[12px]">{sheet.meals.dinner.time ? `Dinner at wrap ${sheet.meals.dinner.time} · ${sheet.meals.dinner.count} heads` : "No dinner (wrap before 19:00)"}</div>
        </section>
        <section>
          <SheetTitle>Locations</SheetTitle>
          <ul className="text-[12px] space-y-1">
            {sheet.locations.map((l) => (
              <li key={l.name}>
                <b>{l.name}</b> · {l.window}
                {l.note && <span className="text-[#8a5a00]"> · {l.note}</span>}
                {l.contact && <span className="block text-[#5a6272]">contact: {l.contact}</span>}
              </li>
            ))}
            {sheet.locations.length === 0 && (
              <li className="text-[#5a6272]">No scene on this day names a set, so the sheet carries no location, no access window and no site contact.</li>
            )}
          </ul>
        </section>
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <section className="break-inside-avoid">
          <SheetTitle>Departments &amp; radio</SheetTitle>
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-left text-[9px] uppercase tracking-wider text-[#5a6272] border-b border-[#c9ced8]">
                <th className="py-1 w-8" title="The channel this department is raised on. Two departments share a channel where the production put them on one.">Ch</th>
                <th className="py-1">Department</th>
                <th className="py-1">Head</th>
              </tr>
            </thead>
            <tbody>
              {sheet.departments.map((d) => (
                <tr key={d.department} className="border-b border-[#e6e9ef]">
                  <td className="py-1 pr-1 mono font-bold text-[12px]">
                    {d.channel ?? <span className="text-[#5a6272] font-normal" title="No radio channel is on file for this department.">—</span>}
                  </td>
                  <td className="py-1 pr-1">
                    {d.department}
                    {d.safety_critical && <span className="ml-1 text-[9px] uppercase tracking-wider text-[#8a5a00]">safety</span>}
                  </td>
                  <td className="py-1">
                    {d.name}
                    {d.contact && <span className="block text-[9px] text-[#5a6272]">{d.contact}</span>}
                  </td>
                </tr>
              ))}
              {sheet.departments.length === 0 && (
                <tr><td colSpan={3} className="py-2 text-[#5a6272]">No department is implicated by this day&apos;s schedule.</td></tr>
              )}
            </tbody>
          </table>
          <p className="mt-1 text-[9px] text-[#5a6272]">
            Departments are on this list because a scene today carries equipment they own — the same mapping the
            coordination engine notifies against, so the channel list and the dispatch cannot name different people.
          </p>
        </section>

        <section className="break-inside-avoid">
          <SheetTitle>Safety</SheetTitle>
          <div className="text-[12px] font-semibold">{sheet.safety.meeting_note}</div>
          {sheet.safety.hazards.length > 0 && (
            <ul className="mt-1 text-[11px] space-y-1">
              {sheet.safety.hazards.map((h) => (
                <li key={h.item + h.why}>
                  <b>{h.item}</b>
                  {h.owner && <span className="text-[#5a6272]"> · {h.owner}</span>}
                  <span className="block text-[#5a6272]">{h.why}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-2 text-[11px]">
            <div className="font-semibold uppercase tracking-wider text-[10px] text-[#5a6272]">Nearest emergency department</div>
            {sheet.safety.hospitals.entries.length === 0 ? (
              <p className="text-[#5a6272] italic">{sheet.safety.hospitals.reason}</p>
            ) : (
              <ul className="space-y-1 mt-0.5">
                {sheet.safety.hospitals.entries.map((h) => (
                  <li key={h.location}>
                    <span className="text-[#5a6272]">{h.location.split(" — ")[0]}:</span> <b>{h.value}</b>
                    {h.source_url && (
                      <a href={h.source_url} target="_blank" rel="noreferrer" className="ml-1 text-[10px] text-[#1c4f8b] hover:underline print:hidden">
                        source ↗
                      </a>
                    )}
                    <span className="block text-[9px] text-[#5a6272]">
                      From this set&apos;s Parallel location dossier{h.confidence ? ` · ${h.confidence} confidence` : ""}. Confirm with the production office before the unit travels.
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {sheet.safety.hospitals.sets_without_one.length > 0 && (
              <p className="mt-1 text-[9px] text-[#5a6272]">
                No hospital on file for {sheet.safety.hospitals.sets_without_one.map((s) => s.split(" — ")[0]).join(", ")} — that set&apos;s
                dossier has not returned one.
              </p>
            )}
          </div>
          <ul className="mt-2 text-[10px] text-[#5a6272] space-y-0.5">
            {sheet.safety.standing_notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </section>
      </div>

      <section className="mt-4 break-inside-avoid">
        <SheetTitle>Advance schedule</SheetTitle>
        {!sheet.advance ? (
          <p className="text-[11px] text-[#5a6272]">
            Day {sheet.day_number} is the last day on this schedule, so there is nothing to advance to.
          </p>
        ) : (
          <div className="text-[11px]">
            <div className="font-semibold">
              Day {sheet.advance.day_number} · {sheet.advance.date} · unit call{" "}
              <span style={{ fontFamily: "var(--font-plex-mono)" }}>{sheet.advance.unit_call}</span>
              {sheet.advance.sets.length > 0 && <span className="text-[#5a6272]"> · {sheet.advance.sets.map((s) => s.split(" — ")[0]).join(", ")}</span>}
            </div>
            {sheet.advance.scenes.length > 0 ? (
              <div className="text-[#5a6272] mt-0.5">
                {sheet.advance.scenes.map((s) => `Sc ${s.scene} ${s.heading}`).join(" · ")}
              </div>
            ) : (
              <div className="text-[#5a6272] mt-0.5 italic">{sheet.advance.note}</div>
            )}
          </div>
        )}
      </section>

      {sheet.notes && <p className="mt-3 text-[11px] text-[#5a6272]">{sheet.notes}</p>}

      <footer className="mt-4 pt-2 border-t border-[#c9ced8] break-inside-avoid">
        <div className="grid gap-4 md:grid-cols-2 text-[10px]">
          <div>
            <div className="uppercase tracking-wider text-[#5a6272]">Prepared by</div>
            {sheet.signatures.prepared_by ? (
              <>
                <div className="font-semibold text-[11px]">{sheet.signatures.prepared_by.name}</div>
                <div className="text-[#5a6272]">{sheet.signatures.prepared_by.role}</div>
              </>
            ) : (
              <div className="text-[#5a6272] italic">{sheet.signatures.prepared_by_reason}</div>
            )}
          </div>
          <div>
            <div className="uppercase tracking-wider text-[#5a6272]">Approved by</div>
            {sheet.signatures.approved_by ? (
              <>
                <div className="font-semibold text-[11px]">{sheet.signatures.approved_by}</div>
                {sheet.signatures.approved_at_utc && (
                  <div className="text-[#5a6272]">{new Date(sheet.signatures.approved_at_utc).toLocaleString()}</div>
                )}
              </>
            ) : (
              <div className="text-[#5a6272] italic">{sheet.signatures.approved_reason}</div>
            )}
          </div>
        </div>
        <div className="mt-2 text-[10px] text-[#5a6272]">
          {sheet.signatures.generated_by}. Rows marked <span className="display font-bold">*</span> in the margin changed in the last
          approved recovery.
        </div>
      </footer>
    </article>
  );
}

function SheetTitle({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`display font-bold uppercase tracking-[.12em] text-[12px] border-b border-[#14171d] mb-1 ${className}`}>{children}</div>;
}

/* --------------------------------------------------------------------------
   Force Majeure claim packet.

   An underwriter reads a weather claim in three passes — was the peril real,
   did the production try to work rather than idle, and what did that cost — so
   the modal is those three sections in that order, and nothing renders unless
   the API sent state behind it. The last block is the point of the whole
   document: the rows a production insurance claim needs and ScenePilot does not
   hold are printed as named blanks, never as a plausible number.
   -------------------------------------------------------------------------- */

const inr = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `₹${n.toLocaleString("en-IN")}`);

const CLAIM_TONE: Record<string, string> = {
  MITIGATION_APPLIED: "chip-ok",
  AWAITING_PRODUCER_DECISION: "chip-warn",
  PERIL_REPORTED: "chip-warn",
  NO_PERIL_ON_RECORD: "chip-dim",
};

const VERIFY_TONE: Record<string, string> = {
  CORROBORATED: "chip-ok",
  PARTIALLY_CORROBORATED: "chip-warn",
  UNCORROBORATED: "chip-dim",
  CONTRADICTED: "chip-bad",
};

function InsuranceDossierModal({ dossier, onClose }: { dossier: InsuranceDossier; onClose: () => void }) {
  useDismissOnEscape(true, onClose);   // rendered only while open, so `true` is the open state
  const peril = dossier.peril_evidence;
  const mitigation = dossier.proof_of_mitigation;
  const cost = dossier.cost_delta;
  const constraints = dossier.constraints_on_record;
  return (
    /* Printable: an underwriter reads this on paper. `print-paper` repaints the subtree in the call
       sheet's ink and lifts it out of the fixed, viewport-height scroll box a modal needs on screen
       and a printer cannot follow past its first page. */
    <div className="print-paper print-portrait fixed inset-0 z-50 flex items-start justify-center bg-black/80 backdrop-blur-sm p-4 overflow-y-auto" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Force majeure claim packet"
        className="card max-w-4xl w-full p-6 space-y-5 max-h-[92vh] overflow-y-auto scroll-thin my-6 print:max-w-none print:my-0 print:border-0"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start gap-4 border-b border-line pb-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`chip ${CLAIM_TONE[dossier.claim_status] || "chip-dim"}`}>{String(dossier.claim_status).replace(/_/g, " ")}</span>
              <span className="mono text-[10px] text-dim">{dossier.claim_type}</span>
            </div>
            <h2 className="display text-2xl font-bold mt-1">Force Majeure claim packet — Day {dossier.shoot_day.day_number}</h2>
            <div className="mono text-[11px] text-dim mt-0.5 break-all">
              {dossier.dossier_id} · {dossier.shoot_day.date} · compiled {new Date(dossier.generated_at_utc).toLocaleString()}
            </div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-fg text-sm font-bold px-2 py-1 shrink-0 print:hidden">✕ Close</button>
        </header>

        <p className="text-[12px] text-muted leading-relaxed">{dossier.summary}</p>
        <p className="text-[11px] text-dim italic">{dossier.notice}</p>

        <DossierSection n={1} title="Peril evidence">
          {!peril ? (
            <Blank>No disruption is on record for this shoot day, so there is no peril to evidence and no claim to make.</Blank>
          ) : (
            <div className="space-y-3">
              <div className="p-3 rounded bg-elev border border-line space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="chip chip-dim">{peril.peril.type.replace(/_/g, " ")}</span>
                  <span className="display font-bold text-[15px]">{peril.peril.title}</span>
                </div>
                <div className="text-[11px] text-muted">
                  {peril.peril.window_start && peril.peril.window_end ? (
                    <>
                      Window <b className="mono">{peril.peril.window_start}–{peril.peril.window_end}</b>
                      {peril.peril.dry_out_minutes ? <> · +{peril.peril.dry_out_minutes} min dry-out</> : null} ·{" "}
                    </>
                  ) : null}
                  reported via {peril.peril.reported_via}
                  {peril.peril.reported_at_utc ? <> on {new Date(peril.peril.reported_at_utc).toLocaleString()}</> : null}
                </div>
                <div className="text-[11px] text-dim">{peril.peril.description}</div>
              </div>

              <div className="p-3 rounded bg-elev border border-line space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <Kicker>Verification · {peril.verification.verified_by}</Kicker>
                  {peril.verification.status ? (
                    <span className={`chip ${VERIFY_TONE[peril.verification.status] || "chip-dim"}`}>{peril.verification.status.replace(/_/g, " ")}</span>
                  ) : (
                    <span className="chip chip-dim">not applicable</span>
                  )}
                  {peril.verification.confidence_pct !== null && (
                    <span className="mono text-[11px] text-dim">{peril.verification.confidence_pct}% confidence</span>
                  )}
                </div>
                {peril.verification.summary && <div className="text-[11px] text-muted">{peril.verification.summary}</div>}
                <div className="mono text-[10px] text-dim">
                  {peril.verification.searches_run} search run(s) · {peril.verification.sources_returned} source(s) returned ·{" "}
                  {peril.verification.findings_retained} finding(s) retained
                </div>
              </div>

              {peril.analyst_findings.length > 0 && (
                <div className="space-y-1.5">
                  <Kicker>Findings retained from those sources</Kicker>
                  {peril.analyst_findings.map((f, i: number) => (
                    <div key={i} className="p-2.5 rounded bg-elev border border-line space-y-0.5">
                      <div className="text-[12px] text-fg">{f.claim}</div>
                      {f.excerpt && <div className="text-[11px] text-dim italic">“{f.excerpt}”</div>}
                      <SourceLine url={f.source_url} title={f.source_title} date={f.publish_date} tail={`${f.authority.toLowerCase()} · ${f.freshness.toLowerCase()}`} />
                    </div>
                  ))}
                </div>
              )}

              {peril.certified_sources.length > 0 && (
                <div className="space-y-1.5">
                  <Kicker>Sources as returned — every query exactly as sent</Kicker>
                  {peril.certified_sources.map((s) => (
                    <div key={s.search_run_id} className="p-2.5 rounded bg-elev border border-line space-y-1">
                      <div className="flex items-start gap-2 flex-wrap">
                        <span className="chip chip-parallel">{s.provider} {s.mode}</span>
                        {s.replayed && <span className="chip chip-dim">replayed</span>}
                        <span className="mono text-[10px] text-dim">{s.search_run_id}</span>
                      </div>
                      <div className="text-[11px] text-muted">{s.objective}</div>
                      <div className="flex gap-1 flex-wrap">
                        {s.queries.map((q: string) => (
                          <span key={q} className="mono text-[10px] px-1.5 py-0.5 rounded bg-card border border-line text-dim">{q}</span>
                        ))}
                      </div>
                      <ul className="space-y-1 pt-0.5">
                        {s.results.map((r, i: number) => (
                          <li key={i} className="border-l border-line pl-2">
                            <SourceLine url={r.url} title={r.title} date={r.publish_date} />
                            {r.excerpt && <div className="text-[11px] text-dim line-clamp-3">{r.excerpt}</div>}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </DossierSection>

        <DossierSection n={2} title="Proof of mitigation">
          {!mitigation ? (
            <Blank>No recovery has been run for this day, so there is no record of the production trying to work around anything.</Blank>
          ) : (
            <div className="space-y-3">
              <div className="text-[12px] text-muted">
                <b className="text-fg">{mitigation.alternatives_evaluated}</b> schedule(s) evaluated by the deterministic constraint engine;{" "}
                <b className="text-bad">{mitigation.rejected_by_hard_constraint}</b> rejected outright by a hard constraint.
              </div>

              {mitigation.rejected_alternatives.length > 0 && (
                <div className="space-y-1.5">
                  <Kicker>Rejected as infeasible — and why</Kicker>
                  {mitigation.rejected_alternatives.map((r) => (
                    <div key={r.label} className="p-2.5 rounded bg-elev border border-bad/30 space-y-1">
                      <div className="flex items-baseline gap-2">
                        <span className="chip chip-bad">option {r.label}</span>
                        <span className="text-[12px] text-fg">{r.title}</span>
                      </div>
                      <ul className="space-y-0.5">
                        {r.violations.map((v, i: number) => (
                          <li key={i} className="text-[11px] text-muted">
                            <span className="mono text-[10px] text-bad mr-1">{v.kind.replace(/_/g, " ").toLowerCase()}</span>
                            {v.description}
                            {v.evidence_url && (
                              <a href={v.evidence_url} target="_blank" rel="noreferrer" className="ml-1 text-accent hover:underline mono text-[10px]">
                                source ↗
                              </a>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}

              {mitigation.selected_option && (
                <div className="p-3 rounded bg-elev border border-ok/30 space-y-2">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="chip chip-ok">option {mitigation.selected_option.label}</span>
                    <span className="display font-bold text-[15px]">{mitigation.selected_option.title}</span>
                    {mitigation.selected_option.score && (
                      <span className="mono text-[11px] text-dim">score {mitigation.selected_option.score.total}/100</span>
                    )}
                  </div>
                  {mitigation.selected_option.explanation && (
                    <div className="text-[11px] text-muted">{mitigation.selected_option.explanation}</div>
                  )}
                  <table className="w-full text-[11px]">
                    <tbody>
                      {mitigation.selected_option.schedule.map((row, i: number) => (
                        <tr key={i} className="border-b border-line/60">
                          <td className="py-1 pr-2 mono whitespace-nowrap text-dim">{row.start}–{row.end}</td>
                          <td className="py-1 pr-2 display font-bold">Sc {row.scene_number}</td>
                          <td className="py-1 pr-2 text-muted">{row.heading}</td>
                          <td className="py-1 text-dim">{row.location}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {mitigation.selected_option.carried_over.length > 0 && (
                    <div className="text-[11px] text-warn">
                      Carried over: {mitigation.selected_option.carried_over.map((s: string) => `Sc ${s}`).join(", ")}
                    </div>
                  )}
                </div>
              )}

              {mitigation.alternatives_not_selected.length > 0 && (
                <div className="text-[11px] text-dim">
                  Feasible but not chosen:{" "}
                  {mitigation.alternatives_not_selected.map((a) => `${a.label} (${inr(a.extra_cost_inr)}, score ${a.score_total})`).join(" · ")}
                </div>
              )}

              {mitigation.decision ? (
                <div className="p-2.5 rounded bg-elev border border-line text-[12px]">
                  <span className="text-ok font-semibold">Approved by {mitigation.decision.approved_by}</span>{" "}
                  <span className="text-dim">on {new Date(mitigation.decision.approved_at_utc).toLocaleString()}</span>
                  <div className="text-[11px] text-muted mt-0.5">{mitigation.decision.summary}</div>
                  <div className="mono text-[10px] text-dim mt-0.5">{mitigation.decision.changeset_id}</div>
                </div>
              ) : (
                <Blank>No producer approval is on record yet, so this option is a recommendation and not a decision.</Blank>
              )}
            </div>
          )}
        </DossierSection>

        <DossierSection n={3} title="Cost delta">
          {cost.mitigation_cost_inr === null ? (
            <Blank>No recovery schedule has been selected for this day, so there is no mitigation cost on record to report.</Blank>
          ) : (
            <div className="space-y-2">
              <div className="flex items-baseline gap-3 flex-wrap">
                <span className="display text-2xl font-bold text-accent">{inr(cost.mitigation_cost_inr)}</span>
                <span className="text-[11px] text-dim">
                  cost of the {cost.basis} mitigation · {cost.overtime_minutes} min overtime at {inr(cost.rates.overtime_per_hour_inr)}/h ·
                  carry-over {inr(cost.rates.carry_over_per_scene_inr)}/scene
                </span>
              </div>
              <table className="w-full text-[11px]">
                <tbody>
                  {cost.line_items.map((li, i: number) => (
                    <tr key={i} className="border-b border-line/60">
                      <td className="py-1 pr-2 mono text-[10px] text-dim whitespace-nowrap">{li.kind.replace(/_/g, " ").toLowerCase()}</td>
                      <td className="py-1 pr-2 text-muted">{li.description}</td>
                      <td className="py-1 text-right mono whitespace-nowrap">{inr(li.amount_inr)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {cost.unpriced_constraints.length > 0 && (
                <div className="text-[11px] text-dim">
                  Not priced by the engine: {cost.unpriced_constraints.map((v) => v.description).join("; ")}
                </div>
              )}
              {cost.alternatives_priced.length > 0 && (
                <div className="text-[11px] text-dim">
                  Every schedule considered:{" "}
                  {cost.alternatives_priced
                    .map((a) => `${a.label} ${inr(a.extra_cost_inr)}${a.feasible ? "" : " (infeasible)"}${a.selected ? " ←" : ""}`)
                    .join(" · ")}
                </div>
              )}
            </div>
          )}
        </DossierSection>

        {constraints.length > 0 && (
          <DossierSection title="Accepted external constraints on record">
            <div className="space-y-1.5">
              {constraints.map((c) => (
                <div key={c.fact_id} className="p-2.5 rounded bg-elev border border-line space-y-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="chip chip-parallel">{c.binding}</span>
                    <span className="text-[12px] text-fg font-semibold">{c.label}: {c.value}</span>
                    <span className="text-[11px] text-dim">{c.location}</span>
                  </div>
                  <div className="text-[11px] text-dim">
                    Found by {c.discovered_by}, accepted by <b className="text-muted">{c.accepted_by}</b>
                    {c.accepted_at_utc ? <> on {new Date(c.accepted_at_utc).toLocaleString()}</> : null}
                  </div>
                  {c.citations.map((cit, i: number) => (
                    <div key={i}>
                      <SourceLine url={cit.url} title={cit.title} />
                      {cit.excerpt && <div className="text-[11px] text-dim italic">“{cit.excerpt}”</div>}
                    </div>
                  ))}
                  {c.current_schedule_violations.map((v, i: number) => (
                    <div key={i} className="text-[11px] text-bad">Committed schedule breaks this: {v.description}</div>
                  ))}
                  {c.rejected_schedules.map((r, i: number) => (
                    <div key={i} className="text-[11px] text-muted">Option {r.option_label} rejected: {r.message}</div>
                  ))}
                </div>
              ))}
            </div>
          </DossierSection>
        )}

        <DossierSection title="Not in production state — for the producer to complete">
          <div className="space-y-1">
            {cost.not_in_production_state.map((row) => (
              <div key={row.field} className="flex items-baseline gap-2 text-[11px] border-b border-dashed border-line pb-1">
                <span className="text-muted w-56 shrink-0">{row.label}</span>
                <span className="mono text-dim">—————</span>
                <span className="text-dim flex-1">{row.why}</span>
              </div>
            ))}
          </div>
        </DossierSection>

        <footer className="pt-3 border-t border-line flex items-center gap-3 flex-wrap">
          <span className="text-[10px] text-dim flex-1">
            Compiled from persisted production state only: {Object.values(dossier.provenance).join("; ")}.
          </span>
          <button onClick={onClose} className="btn btn-primary text-xs">Close</button>
        </footer>
      </div>
    </div>
  );
}

function DossierSection({ n, title, children }: { n?: number; title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <Kicker>{n ? `${n} · ` : ""}{title}</Kicker>
      {children}
    </section>
  );
}

function Blank({ children }: { children: React.ReactNode }) {
  return <div className="p-2.5 rounded border border-dashed border-line text-[11px] text-dim">{children}</div>;
}

function SourceLine({ url, title, date, tail }: { url: string; title?: string | null; date?: string | null; tail?: string }) {
  return (
    <a href={url} target="_blank" rel="noreferrer" className="block text-[11px] text-accent hover:underline truncate">
      {title || url}
      {date && <span className="text-dim ml-1">· {date}</span>}
      {tail && <span className="text-dim ml-1">· {tail}</span>}
    </a>
  );
}
