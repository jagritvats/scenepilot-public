"use client";

import { useState } from "react";
import { toMin, type Disruption, type FeatureState, type ShootDay, type WeatherHour, type WeatherTimeline } from "@/lib/api";
import { Citations } from "./Citations";
import { hhmmDay, stripAxis, type StripAxis } from "./StripBoard";
import { Kicker, Spinner, StatusChip } from "./ui";

/**
 * The strip is a picture of researched hours, so its shape has to survive hours nobody researched.
 * A bar's height is the stated chance of precipitation and nothing else: an hour that came back with
 * a condition but no figure gets a fixed marker rather than an invented height, and an hour no
 * source covered is left as a gap. A dry hour and an unresearched hour must never look alike.
 */
function precipTone(pctChance: number | null) {
  if (pctChance === null) return "bg-dim/50";
  if (pctChance >= 60) return "bg-bad";
  if (pctChance >= 30) return "bg-warn";
  return "bg-ok/70";
}

function HourlyPrecip({
  timeline,
  axis,
  selected,
  onSelect,
}: {
  timeline: WeatherTimeline;
  axis: StripAxis;
  selected: WeatherHour | null;
  onSelect: (h: WeatherHour) => void;
}) {
  const hours = timeline.hours;
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] uppercase tracking-wider text-dim">Hourly precipitation · Parallel Task</span>
        <span className="chip chip-parallel">{timeline.cited_hours} of {hours.length} hour(s) cited</span>
        {timeline.replayed && (
          <span className="chip chip-dim" title="Replayed from a recorded Parallel Task run rather than researched by this deployment.">
            replayed
          </span>
        )}
      </div>

      {/* One column per researched hour, positioned on the slider's own axis. 36px of height is the
          full 100%: tall enough to read a difference, short enough not to dominate the control. */}
      <div className="relative h-[36px] w-full border-b border-line" role="group" aria-label="Hourly chance of precipitation">
        {hours.map((h) => {
          const known = h.precip_pct !== null;
          const height = known ? Math.max(3, (h.precip_pct! / 100) * 36) : 8;
          const isSel = selected?.field === h.field;
          return (
            <button
              key={h.field}
              type="button"
              onClick={() => onSelect(h)}
              style={{ left: `${axis.pct(h.start_min)}%`, width: `${(60 / axis.spanMin) * 100}%`, height: `${height}px` }}
              className={`absolute bottom-0 ${precipTone(h.precip_pct)} ${known ? "" : "border-t border-dashed border-dim"} ${
                isSel ? "outline outline-1 outline-accent" : "opacity-80 hover:opacity-100"
              }`}
              title={`${h.label} — ${h.value}${known ? "" : " (no percentage stated)"}`}
              aria-label={`${h.label}: ${h.value}`}
            />
          );
        })}
      </div>

      <div className="flex justify-between text-[9px] text-dim mono">
        <span>{hhmmDay(axis.startMin)}</span>
        <span>{hhmmDay(axis.endMin)}</span>
      </div>
    </div>
  );
}

/** What one researched hour actually said, and who said it. */
function HourDetail({ hour }: { hour: WeatherHour }) {
  return (
    <div className="rounded border border-line bg-card/60 p-2 space-y-1">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="mono text-[12px] font-bold">
          {hour.label}–{hhmmDay(hour.start_min + 60)}
        </span>
        <span className="text-[12px]">{hour.value}</span>
        {hour.confidence && <span className="chip chip-parallel ml-auto">Parallel confidence: {hour.confidence}</span>}
      </div>
      {hour.reasoning && <p className="text-[11px] text-muted">{hour.reasoning}</p>}
      <div className="flex items-center gap-1.5 flex-wrap">
        {hour.citations.length > 0 ? (
          <Citations citations={hour.citations} />
        ) : (
          <span className="text-[11px] text-dim">No source was returned for this hour on its own.</span>
        )}
      </div>
    </div>
  );
}

/**
 * Where the needle sits before the producer touches the slider. The page reads this too, so the
 * boards draw the needle at the same minute this panel prints from the moment both appear.
 */
export function defaultScrubMin(disruption: Disruption) {
  return disruption.window_start ? toMin(disruption.window_start) : 12 * 60;
}

/**
 * Drags a needle across the stripboards below. Every value here is the disruption as it was
 * reported and verified — there is no telemetry behind this panel, so it does not pretend to any.
 */
export function DisruptionScrubber({
  disruption,
  day,
  activeMin,
  onScrub,
  weather,
  weatherFeature,
  weatherBusy,
  weatherError,
  onResearchWeather,
}: {
  disruption: Disruption;
  /** The day whose axis the boards below are drawn on — the slider has to reach the same minutes.
   *  Optional so the panel still renders where no day is in hand; it then falls back to the old
   *  fixed 06:00–22:00, which cannot reach a single minute of a night unit's shoot. */
  day?: ShootDay | null;
  activeMin?: number;
  onScrub: (min: number) => void;
  /** null until somebody researches this day — the panel then offers the priced button instead. */
  weather?: WeatherTimeline | null;
  weatherFeature?: FeatureState | null;
  weatherBusy?: boolean;
  weatherError?: string | null;
  onResearchWeather?: () => void;
}) {
  const axis = stripAxis(day);
  const start = disruption.window_start ? toMin(disruption.window_start) : null;
  const end = disruption.window_end ? toMin(disruption.window_end) : null;
  const dryOut = end === null ? null : end + (disruption.dry_out_minutes || 0);
  const min = activeMin ?? defaultScrubMin(disruption);
  const inside = start !== null && dryOut !== null && min >= start && min <= dryOut;

  const [pickedHour, setPickedHour] = useState<string | null>(null);
  const hours = weather?.hours ?? [];
  // The needle and the strip stay in step: whichever hour the producer last touched wins, and
  // otherwise the hour the needle is currently sitting in.
  const selected = hours.find((h) => h.field === pickedHour) ?? hours.find((h) => min >= h.start_min && min < h.start_min + 60) ?? null;
  const weatherOff = !weatherFeature?.enabled;

  return (
    <section className="card p-4 space-y-3 print:hidden">
      <div className="flex items-center gap-2 flex-wrap">
        <Kicker>Disruption window · timeline scrub</Kicker>
        <span className="chip chip-bad">{disruption.type.replace(/_/g, " ")}</span>
        <span className="text-[12px] text-muted">{disruption.title}</span>
        <span className="ml-auto flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-wider text-dim">External verification · Parallel</span>
          <StatusChip status={disruption.verification_status} />
          {disruption.verification_confidence !== null && (
            <span className="mono text-[11px] text-dim">conf {Math.round(disruption.verification_confidence * 100)}%</span>
          )}
        </span>
      </div>

      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="mono text-2xl font-bold">{hhmmDay(min)} IST</span>
        {start !== null && <span className={`chip ${inside ? "chip-bad" : "chip-ok"}`}>{inside ? "inside the reported window" : "clear of the window"}</span>}
        <span className="mono text-[12px] text-muted ml-auto">
          {start !== null && end !== null ? `reported ${hhmmDay(start)}–${hhmmDay(end)}` : "no window reported"}
          {disruption.dry_out_minutes > 0 && ` · +${disruption.dry_out_minutes} min dry-out`}
        </span>
      </div>

      {weather && weather.hours.length > 0 && (
        <HourlyPrecip
          timeline={weather}
          axis={axis}
          selected={selected}
          onSelect={(h) => {
            setPickedHour(h.field);
            onScrub(h.start_min);
          }}
        />
      )}

      <input
        type="range"
        min={axis.startMin}
        max={axis.endMin}
        step={5}
        value={min}
        onChange={(e) => {
          setPickedHour(null);
          onScrub(Number(e.target.value));
        }}
        className="w-full accent-accent cursor-pointer"
        aria-label="Scrub the shoot-day timeline"
      />

      {selected && <HourDetail hour={selected} />}

      {weather?.day_summary && (
        <p className="text-[11px] text-muted">
          {weather.day_summary.value} <Citations citations={weather.day_summary.citations} />
        </p>
      )}

      {/* No timeline yet — or one that came back with a day summary and not one usable hour, which
          is what Mumbai currently returns. Both are "nothing is known hour by hour", so both keep the
          capability named, priced and timed rather than hidden. Keying this on `!weather` alone hid
          the button the moment a run answered *anything*, so a run that resolved no hour at all left
          a summary line and no way to ask again. */}
      {onResearchWeather && (!weather || !weather.hours?.length) && (
        <div className="flex items-center gap-2 flex-wrap border-t border-line pt-2">
          <button
            className="btn btn-ghost"
            onClick={onResearchWeather}
            disabled={weatherOff || weatherBusy}
            title={weatherOff ? `Disabled. Set ${weatherFeature?.env || "SCENEPILOT_PARALLEL_TASK=1"}.` : "Parallel Task API — ~$0.025, 1–5 min"}
          >
            {weatherBusy ? <Spinner /> : null} Research hourly weather · ~$0.03 · 1–5 min
          </button>
          <span className="text-[11px] text-dim">
            {weatherOff
              ? `No hourly forecast has been researched for this day. ${weatherFeature?.env || "SCENEPILOT_PARALLEL_TASK=1"} enables it.`
              : weather
                ? "The forecast that came back covers the day, not the hour: no source answered any single hour, so no hour is drawn. Research again to try for hourly detail."
                : "No hourly forecast yet. An answered hour comes back with its own sources, reasoning and confidence."}
          </span>
        </div>
      )}
      {weatherError && <p className="text-[11px] text-bad">{weatherError}</p>}

      <p className="text-[11px] text-dim">
        Moves the needle on the stripboard so you can read the schedule against the window this disruption was reported with.
        {weather && " The bars above are researched hours only — an hour no source covered is left blank rather than drawn as dry."}
      </p>
    </section>
  );
}
