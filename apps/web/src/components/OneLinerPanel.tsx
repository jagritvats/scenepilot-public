"use client";

import { useEffect, useState } from "react";
import { api, inr, type OneLiner, type OneLinerDay, type OneLinerRow, type OneLinerView } from "@/lib/api";
import { boardDate, stripToneClass } from "@/lib/stripboard";
import { Kicker } from "./ui";

/**
 * The one-liner: the whole shoot on one page, one line a scene.
 *
 * This is the document a producer circulates when a schedule moves, and the reason is geometric —
 * the entire before and after fits side by side on a single sheet, which no board or Gantt manages.
 * So it prints as paper, in the same ink the call sheet above it uses, and the change is marked the
 * way a revised page marks one: an asterisk in the margin, not a colour that a printer drops.
 *
 * Every column here is somewhere else in the product. That is the point: the day banner a stripboard
 * prints, the scene number and slugline, the cast numbers the call sheet leads with, and the page
 * eighths the board totals — all on one row, so the shape of the picture is legible at a glance.
 */
export function OneLinerPanel({ projectId }: { projectId: string }) {
  const [view, setView] = useState<OneLinerView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<boolean>(false);
  const [showBefore, setShowBefore] = useState<boolean>(true);

  useEffect(() => {
    let active = true;
    api
      .oneLiner(projectId)
      .then((res) => active && setView(res))
      .catch((e) => active && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, [projectId]);

  if (error) {
    return (
      <div className="card px-4 py-3 text-[12px] border-bad/50 print:hidden">
        <span className="chip chip-bad mr-2">one-liner unavailable</span>
        <span className="text-muted">{error}</span>
      </div>
    );
  }
  if (!view) return <div className="card h-24 shimmer print:hidden" />;

  const moved = new Set(view.moves.map((m) => m.scene));
  const hasBefore = view.baseline !== null;

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap print:hidden">
        <div>
          <Kicker>One-liner · the condensed shoot order</Kicker>
          <h2 className="display text-xl font-bold">
            {view.current.scene_count} scenes across {view.current.days.length} days
            {view.current.total_label && <span className="text-muted font-semibold"> · {view.current.total_label} pages</span>}
          </h2>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {hasBefore && open && (
            <button className={`chip ${showBefore ? "chip-accent" : "chip-dim"}`} onClick={() => setShowBefore(!showBefore)}>
              {showBefore ? "hide before" : "show before"}
            </button>
          )}
          <button className="btn text-xs" onClick={() => setOpen(!open)}>
            {open ? "Hide one-liner" : "Show one-liner"}
          </button>
        </div>
      </div>

      {view.moves.length > 0 && (
        <p className="text-[12px] text-muted print:hidden">
          The approved recovery moved{" "}
          {view.moves.map((m, i) => (
            <span key={m.scene}>
              {i > 0 && ", "}
              <b className="text-foreground">Sc {m.scene}</b>{" "}
              {m.carried_out ? (
                <span className="text-warn">out of Day {m.from_day} entirely</span>
              ) : m.from_day !== m.to_day ? (
                <>from Day {m.from_day} to Day {m.to_day}</>
              ) : (
                <>from {m.from_slot} to {m.to_slot}</>
              )}
            </span>
          ))}
          . Those rows carry a margin asterisk below.
        </p>
      )}

      {open && (
        <div className={`grid gap-4 ${hasBefore && showBefore ? "xl:grid-cols-2" : ""} print-sheets`}>
          {hasBefore && showBefore && view.baseline && (
            <Sheet one={view.baseline} title="Before the approved recovery" moved={moved} dim />
          )}
          <Sheet one={view.current} title="Current schedule" moved={hasBefore ? moved : new Set()} />
        </div>
      )}
    </section>
  );
}

function Sheet({ one, title, moved, dim = false }: { one: OneLiner; title: string; moved: Set<string>; dim?: boolean }) {
  return (
    <article
      className={`print-tint bg-white text-[#14171d] rounded-md shadow-xl p-5 print:shadow-none print:p-3 print:rounded-none print:border print:border-[#c9ced8] print:opacity-100 ${dim ? "opacity-90" : ""}`}
      style={{ fontFamily: "var(--font-plex-sans)" }}
    >
      <header className="border-b-2 border-[#14171d] pb-2 mb-2 flex items-baseline gap-3 flex-wrap">
        <div className="flex-1 min-w-[180px]">
          <div className="text-[10px] tracking-[.18em] uppercase text-[#5a6272]">{one.production} · one-liner</div>
          <div className="display text-lg font-bold leading-none mt-0.5">{title}</div>
        </div>
        <div className="text-right text-[11px]">
          <div className="display text-base font-bold">
            {one.total_label ?? <span className="text-[#5a6272] text-[11px] font-normal">pages not totalled</span>}
          </div>
          <div className="text-[#5a6272]">{one.scene_count} scenes</div>
        </div>
      </header>

      {one.unpriced_reason && <p className="text-[9px] text-[#5a6272] mb-2">{one.unpriced_reason}</p>}

      <div className="space-y-2.5">
        {one.days.map((day) => (
          <div key={day.shoot_day_id} className="space-y-2.5">
            {/* The gap between two days, made legible. The turnaround rule is already enforced in
                the validator; this is where a producer can actually see it. */}
            {day.rest_before && <TurnaroundBar rest={day.rest_before} />}
            <DayBlock day={day} moved={moved} />
          </div>
        ))}
      </div>
    </article>
  );
}

/**
 * Rest between one day's camera wrap and the next day's call, against the agreement in force.
 *
 * Ink-ruled rather than tinted: a bar whose only signal is a background colour says nothing on
 * paper, and this document is printed. A breach is stated in words as well as weight.
 */
function TurnaroundBar({ rest }: { rest: NonNullable<OneLinerDay["rest_before"]> }) {
  return (
    <div
      className={`flex items-baseline gap-2 flex-wrap border-y px-2 py-0.5 text-[9px] uppercase tracking-wider ${
        rest.breach ? "border-[#a63a2e] text-[#a63a2e] font-semibold" : "border-[#c9ced8] text-[#5a6272]"
      }`}
      title={`Wrapped ${rest.from_wrap}, back in at ${rest.to_call}. ${rest.pack} sets a minimum turnaround of ${rest.required_label}.`}
    >
      <span>Turnaround</span>
      <span style={{ fontFamily: "var(--font-plex-mono)" }}>
        {rest.from_wrap} → {rest.to_call} · {rest.hours_label} rest
      </span>
      <span className="ml-auto">
        {rest.breach
          ? `${Math.floor(rest.deficit_minutes / 60)}h${String(rest.deficit_minutes % 60).padStart(2, "0")} under the ${rest.required_label} minimum — a forced call is owed`
          : `${rest.required_label} minimum met`}
      </span>
    </div>
  );
}

function DayBlock({ day, moved }: { day: OneLinerDay; moved: Set<string> }) {
  return (
    <div className="break-inside-avoid">
      {/* The black day-break banner a stripboard prints, in the same ink. */}
      <div className="bg-[#14171d] text-white px-2 py-1 flex items-baseline gap-2 flex-wrap text-[10px] uppercase tracking-wider print-tint">
        <span className="display font-bold text-[12px] tracking-normal">Day {day.day_number}</span>
        <span className="opacity-80">{boardDate(day.date)}</span>
        <span className="opacity-80">call {day.unit_call}</span>
        <span className="ml-auto opacity-90">
          {day.scene_count} sc · {day.total_label ?? "pages n/a"}
          {day.company_moves > 0 && ` · ${day.company_moves} company move${day.company_moves === 1 ? "" : "s"}`}
          {/* Two divisions on numbers the board already holds: what today shoots against what this
              production averages over the days that carry a scene. */}
          {day.velocity?.average_label && (
            <span
              title={
                `Averaged over the ${day.velocity.days_counted} day(s) that carry a scene — a day with nothing ` +
                `scheduled is not a slow day. ${day.velocity.days_wrapped} of them ${day.velocity.days_wrapped === 1 ? "has" : "have"} ` +
                `been shot; the rest are still a plan, which is why this is the scheduled average and not a delivered one.`
              }
            >
              {" · "}{day.velocity.delta_label === "on the production average"
                ? `on the ${day.velocity.average_label}/day scheduled avg`
                : `${day.velocity.delta_label?.replace("the average", `the ${day.velocity.average_label}/day scheduled avg`)}`}
            </span>
          )}
          {/* Absent on the baseline sheet by construction — history is not re-priced. */}
          {day.cost && (
            <span title={day.cost.basis === "record" ? "As shot, from this day's own record." : "Projected from the current schedule."}>
              {" · "}≈{inr(day.cost.total_inr)}
            </span>
          )}
        </span>
      </div>
      {day.scenes.length === 0 ? (
        <p className="text-[10px] text-[#5a6272] px-2 py-1">Nothing is scheduled on this day.</p>
      ) : (
        <table className="w-full text-[11px]">
          <tbody>
            {day.scenes.map((row) => (
              <Row key={row.item_id} row={row} changed={moved.has(row.scene)} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Row({ row, changed }: { row: OneLinerRow; changed: boolean }) {
  /* The strip colour, reused from the board so a scene is the same colour in both places. On paper
     it is a 3px edge rather than a fill: a full strip of colour costs ink and reads worse than the
     text beside it, but the code itself is the fastest thing on the page to scan. */
  const tone = stripToneClass(row);
  return (
    <tr className={`border-b border-[#e6e9ef] align-top ${changed ? "bg-[#fff1cc] print:bg-transparent" : ""}`}>
      <td className={`py-1 w-3 display font-bold text-[12px] leading-none ${changed ? "border-l-2 border-l-[#14171d]" : ""}`} title={changed ? "Moved by the approved recovery." : undefined}>
        {changed ? "*" : ""}
      </td>
      <td className="py-1 pl-1 pr-1 w-1.5">
        <span className={`block w-1.5 h-4 rounded-sm border border-[#c9ced8] print-tint ${tone ?? ""}`} title={tone ? undefined : "This scene's day/night is not on file, so the board leaves it uncoloured."} />
      </td>
      <td className="py-1 pr-2 whitespace-nowrap text-[10px] text-[#5a6272]" style={{ fontFamily: "var(--font-plex-mono)" }}>{row.start}</td>
      <td className="py-1 pr-2 display font-bold text-[13px] whitespace-nowrap">{row.scene}</td>
      <td className="py-1 pr-2">
        {row.heading}
        {row.cover && <span className="ml-1 text-[9px] uppercase tracking-wider text-[#8a5a00]">cover</span>}
        {row.unit && row.unit !== "MAIN" && <span className="ml-1 text-[9px] uppercase tracking-wider text-[#1c4f8b]">{row.unit.toLowerCase()} unit</span>}
        {row.synopsis && <span className="block text-[9px] text-[#5a6272]">{row.synopsis}</span>}
      </td>
      <td className="py-1 pr-2 whitespace-nowrap text-[10px]" style={{ fontFamily: "var(--font-plex-mono)" }}
          title={row.cast.map((c) => (c.cast_number ? `${c.cast_number} — ${c.name}` : c.name)).join(" · ") || undefined}>
        {row.cast.length === 0
          ? <span className="text-[#5a6272]">—</span>
          : row.cast.every((c) => c.cast_number !== null)
            ? row.cast.map((c) => c.cast_number).join(", ")
            : row.cast.map((c) => c.name.split(" (")[0].split(" — ")[0]).join(", ")}
      </td>
      <td className="py-1 pr-2 text-right whitespace-nowrap" style={{ fontFamily: "var(--font-plex-mono)" }}>
        {row.pages ?? <span className="text-[#5a6272]" title="This scene carries no page count.">—</span>}
      </td>
      <td className="py-1 text-[10px] text-[#5a6272] whitespace-nowrap">{row.location?.split(" — ")[0] ?? "no set"}</td>
    </tr>
  );
}
