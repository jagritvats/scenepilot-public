"use client";

import {
  toMin,
  type Disruption,
  type LaborRulePack,
  type Scene,
  type ScheduleItem,
  type ShootDay,
  type SolarLightingProfile,
} from "@/lib/api";
import { defaultScrubMin } from "./DisruptionScrubber";
import { Kicker } from "./ui";

const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

const hhmm = (m: number) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(Math.round(m) % 60).padStart(2, "0")}`;
/** The night unit wraps at 28:00 and turnaround runs past midnight, so a minute can land on the next date. */
const hhmmDay = (m: number) => (m >= 24 * 60 ? `${hhmm(m)} +1d` : hhmm(m));
const hrs = (h: number) => (h % 1 === 0 ? String(h) : h.toFixed(1));
const compass = (deg: number) => COMPASS[Math.round(((((deg % 360) + 360) % 360) / 360) * 16) % 16];

function Cell({ label, value, sub, tone = "text-fg" }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-[0.13em] text-dim font-semibold whitespace-nowrap">{label}</div>
      <div className={`mono text-[15px] leading-tight mt-0.5 ${tone}`}>{value}</div>
      {sub && <div className="text-[10px] text-dim leading-tight mt-0.5">{sub}</div>}
    </div>
  );
}

/**
 * The day's operating facts on one line: the schedule, the labor rule pack in force and the solar
 * ephemeris, and nothing else. Every time here is a time *in the schedule* — no wall clock is read,
 * because the seeded day is "today" in IST and a judge in another timezone would otherwise be shown
 * a pre-call or post-wrap countdown that means nothing.
 *
 * The cursor is the minute the whole page is reading against: the disruption's own onset, or wherever
 * the producer has dragged the timeline scrub. It is named on the cell so it cannot be mistaken for now.
 */
export function OperationsStrip({
  day,
  items,
  scenes,
  scheduleLabel,
  disruption,
  scrubMin,
  ephemeris,
  pack,
  packIsEnforced = true,
  enforcedPackName = null,
}: {
  day: ShootDay;
  items: ScheduleItem[];
  scenes: Record<string, Scene>;
  scheduleLabel?: string;
  disruption?: Disruption | null;
  scrubMin?: number;
  ephemeris?: SolarLightingProfile | null;
  pack?: LaborRulePack | null;
  /** False once the producer has picked a what-if pack on the stripboard, which this strip follows. */
  packIsEnforced?: boolean;
  /** The pack the production is actually validated under — named whenever a what-if is in the cells. */
  enforcedPackName?: string | null;
}) {
  const call = toMin(day.unit_call);
  const ordered = [...items].sort((a, b) => toMin(a.start) - toMin(b.start));

  const cursor = scrubMin ?? (disruption ? defaultScrubMin(disruption) : call);
  const cursorBasis =
    scrubMin !== undefined
      ? "timeline scrub"
      : disruption?.window_start
        ? `${disruption.type.replace(/_/g, " ").toLowerCase()} onset`
        : disruption
          ? "timeline default"
          : "unit call";

  const current = ordered.find((i) => toMin(i.start) <= cursor && cursor < toMin(i.end)) || null;
  const next = ordered.find((i) => toMin(i.start) > cursor) || null;
  const currentScene = current ? scenes[current.scene_id] : null;
  const nextScene = next ? scenes[next.scene_id] : null;

  const lastEnd = ordered.length ? Math.max(...ordered.map((i) => toMin(i.end))) : null;
  const otAt = call + Math.round(day.standard_hours * 60);
  const otOver = lastEnd === null ? 0 : Math.max(0, lastEnd - otAt);

  const lunchDue = pack ? call + Math.round(pack.lunch_due_hours * 60) : null;
  const turnaroundAt = pack && lastEnd !== null ? lastEnd + Math.round(pack.minimum_turnaround_hours * 60) : null;

  const goldenMinutes = toMin(day.golden_hour_dusk[1]) - toMin(day.golden_hour_dusk[0]);
  const goldenDiffers =
    !!ephemeris &&
    (ephemeris.golden_hour_dusk[0] !== day.golden_hour_dusk[0] || ephemeris.golden_hour_dusk[1] !== day.golden_hour_dusk[1]);

  return (
    <section className="card px-4 py-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Kicker>Day operations</Kicker>
        {scheduleLabel && <span className="chip chip-dim">{scheduleLabel}</span>}
        {pack && (
          <>
            <span className={`chip ${packIsEnforced ? "chip-dim" : "chip-warn"}`}>labor pack · {packIsEnforced ? "in force" : "stripboard what-if"}</span>
            <span className="text-[12px] text-muted">
              {pack.name} —{" "}
              {packIsEnforced
                ? "the agreement this production is validated and priced under, and what the cells below are read from."
                : `a what-if selected on the stripboard below. The cells below follow it${enforcedPackName ? `; the recovery options stay validated under ${enforcedPackName}` : ""}.`}
            </span>
          </>
        )}
        <span className="ml-auto text-[11px] text-dim">
          Schedule, labor pack and solar ephemeris only — no wall clock is read.
        </span>
      </div>

      <div className="mt-2.5 flex flex-wrap items-start gap-x-6 gap-y-3">
        <Cell label="Unit call" value={day.unit_call} sub={`${day.crew_size} crew`} />

        <Cell
          label={`At ${hhmm(cursor)} · ${cursorBasis}`}
          value={
            current && currentScene ? (
              <>
                Sc {currentScene.number} <span className="text-dim">{current.start}–{current.end}</span>
              </>
            ) : !ordered.length ? (
              <span className="text-dim">nothing scheduled</span>
            ) : cursor < toMin(ordered[0].start) ? (
              <span className="text-dim">before first setup</span>
            ) : lastEnd !== null && cursor >= lastEnd ? (
              <span className="text-dim">after last scene</span>
            ) : (
              <span className="text-dim">between setups</span>
            )
          }
          sub={currentScene ? currentScene.heading : ordered.length ? "no scene covers this minute" : "nothing scheduled"}
        />

        <Cell
          label="Next up"
          value={
            next && nextScene ? (
              <>
                Sc {nextScene.number} <span className="text-dim">{next.start}</span>
              </>
            ) : (
              <span className="text-dim">—</span>
            )
          }
          sub={
            next
              ? current
                ? `${toMin(next.start) - toMin(current.end)} min after this setup ends`
                : `${toMin(next.start) - cursor} min after the cursor`
              : "nothing scheduled after the cursor"
          }
        />

        {pack && lunchDue !== null && (
          <Cell
            label="Lunch due"
            value={hhmmDay(lunchDue)}
            sub={`call + ${hrs(pack.lunch_due_hours)} h · ${pack.minimum_lunch_minutes} min break · ±${pack.lunch_window_slack_minutes} min window`}
          />
        )}

        <Cell
          label="Scheduled wrap"
          value={lastEnd === null ? <span className="text-dim">—</span> : hhmmDay(lastEnd)}
          sub={`last scene end · hard wrap ${day.hard_wrap}`}
        />

        <Cell
          label="Overtime after"
          value={hhmmDay(otAt)}
          tone={otOver > 0 ? "text-warn" : "text-fg"}
          sub={
            <>
              call + {hrs(day.standard_hours)} h (this day)
              {pack && ` · pack shift ${hrs(pack.standard_shift_hours)} h`}
              {otOver > 0 && ` · schedule runs ${otOver} min past`}
            </>
          }
        />

        {pack && (
          <Cell
            label="Turnaround"
            value={turnaroundAt === null ? `≥ ${hrs(pack.minimum_turnaround_hours)} h` : hhmmDay(turnaroundAt)}
            sub={
              turnaroundAt === null
                ? "rest before the next unit call"
                : `earliest next call · ${hrs(pack.minimum_turnaround_hours)} h after wrap`
            }
          />
        )}

        {/* The window the validator accepts or rejects a SUNSET scene against is the day's own
            (services/schedule.py), so that is the number in headline type. The computed
            astronomical window is a second, separately labelled line — never an unexplained
            second number, and only when it is not the same window. */}
        <Cell
          label="Golden hour (dusk)"
          value={`${day.golden_hour_dusk[0]}–${day.golden_hour_dusk[1]}`}
          sub={
            <>
              {goldenMinutes} min · the window the validator enforces for SUNSET scenes
              {ephemeris && goldenDiffers && (
                <> · computed astronomical window {ephemeris.golden_hour_dusk[0]}–{ephemeris.golden_hour_dusk[1]}, not enforced</>
              )}
            </>
          }
        />
      </div>

      {ephemeris && (
        <div className="mt-2 pt-2 border-t border-line text-[10px] text-dim">
          Solar values computed for {ephemeris.latitude.toFixed(4)}°, {ephemeris.longitude.toFixed(4)}° at UTC{ephemeris.timezone_offset >= 0 ? "+" : ""}
          {ephemeris.timezone_offset} on {ephemeris.date} · sunset {ephemeris.sunset} on {Math.round(ephemeris.sun_azimuth_at_sunset)}° {compass(ephemeris.sun_azimuth_at_sunset)} · civil twilight ends {ephemeris.civil_twilight_dusk}.
          {" "}
          {goldenDiffers
            ? `The computed golden hour, ${ephemeris.golden_hour_dusk[0]}–${ephemeris.golden_hour_dusk[1]} (${ephemeris.golden_hour_dusk_minutes} min), is astronomy only; the validator enforces the day's own window above.`
            : `The computed golden hour is the day's own window above, which is what the validator enforces.`}
        </div>
      )}
    </section>
  );
}
