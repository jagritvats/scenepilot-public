"use client";

import { useEffect, useState } from "react";
import {
  api,
  inr,
  toMin,
  type CompletionRow,
  type DayCompletion,
  type FeatureState,
  type Scene,
  type ScheduleItem,
  type ShootDay,
} from "@/lib/api";
import { eighthsLabel } from "@/lib/stripboard";
import { Kicker, Spinner } from "./ui";

/**
 * Wrapping a day — the verb that turns a plan into a record.
 *
 * `ShootDayStatus.WRAPPED` and `ScheduleItemStatus.COMPLETED` were written by nothing but the seed,
 * so `day_completion` computed a per-scene record on every day payload that no screen could read,
 * `day_cost` carried a `record` branch it could never take, and the DPR issued for exactly one day —
 * the one the seed ships wrapped. Three shipped features were waiting on this write.
 *
 * The form is deliberately the whole day rather than a selection. The server refuses a body that
 * leaves a strip unaccounted for, and it is right to: `day_completion` reads anything short of
 * COMPLETED as outstanding, so an omission would quietly record a scene as carried and charge the
 * day this production's carry-over for it. Putting every strip on screen, defaulted to SHOT at its
 * scheduled end, means that refusal can never be reached — carrying is the deliberate act, and
 * shooting the day as planned is one click.
 */

type Outcome = "SHOT" | "CARRIED";

interface Row {
  outcome: Outcome;
  actualEnd: string;
  note: string;
}

/** HH may pass 23: this production's night units write past-midnight as "28:00" and `to_minutes` on
 *  the server accepts it, so a pattern tightened to 00–23 would reject the days a wrap exists for. */
const TIME = /^\d{1,2}:[0-5]\d$/;
const asMinutes = (value: string) => (TIME.test(value.trim()) ? toMin(value.trim()) : null);
const hhmm = (minutes: number) => `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
const hoursLabel = (minutes: number) => `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} m`;
const NOT_A_TIME = "Use HH:MM on the day's own clock — hours past midnight count on, so 01:00 the next morning is 25:00.";

export interface WrapPanelProps {
  projectId: string;
  dayId: string;
  day: ShootDay;
  /** The day's own committed strips — `day.items`, never a previewed or simulated board. The server
   *  matches this list against the day it holds and refuses anything that does not account for it. */
  items: ScheduleItem[];
  scenes: Record<string, Scene>;
  completion: DayCompletion | null;
  disabled?: boolean;
  onChanged?: () => void;
}

export function WrapPanel(props: WrapPanelProps) {
  return props.day.status === "WRAPPED" ? (
    <DayRecord projectId={props.projectId} dayId={props.dayId} day={props.day} completion={props.completion} />
  ) : (
    <WrapForm {...props} />
  );
}

function WrapForm({ projectId, dayId, day, items, scenes, disabled, onChanged }: WrapPanelProps) {
  const [feature, setFeature] = useState<FeatureState | null>(null);
  const [rows, setRows] = useState<Record<string, Row>>({});
  const [wrapAt, setWrapAt] = useState<string | null>(null);
  // Held so the panel shows the record it just made without waiting for the parent's next poll — and
  // so a mount that passes no `onChanged` still leaves the producer looking at the result.
  const [done, setDone] = useState<{ day: ShootDay; completion: DayCompletion | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.features().then((f) => setFeature(f.features.wrap)).catch(() => setFeature(null));
  }, []);

  // Defaulted per row at read time rather than seeded into state: `items` is a fresh array on every
  // payload and the day page polls itself every few seconds, so re-seeding on it would wipe a
  // half-filled form while the producer was still typing into it.
  const defaultRow = (item: ScheduleItem): Row => ({ outcome: "SHOT", actualEnd: item.end, note: "" });
  const rowOf = (item: ScheduleItem): Row => rows[item.id] ?? defaultRow(item);
  const set = (item: ScheduleItem, patch: Partial<Row>) =>
    setRows((prev) => ({ ...prev, [item.id]: { ...(prev[item.id] ?? defaultRow(item)), ...patch } }));

  const shot = items.filter((i) => rowOf(i).outcome === "SHOT");
  const carried = items.filter((i) => rowOf(i).outcome === "CARRIED");
  const sceneNumber = (item: ScheduleItem) => scenes[item.scene_id]?.number ?? item.scene_id;

  /** The end the strip will actually carry — the wrap writes an actual end onto `item.end` itself. */
  const endOf = (item: ScheduleItem) => asMinutes(rowOf(item).actualEnd) ?? toMin(item.end);
  const lastShotEnd = shot.reduce((max, i) => Math.max(max, endOf(i)), -1);

  const rowProblem = (item: ScheduleItem): string | null => {
    const row = rowOf(item);
    if (row.outcome !== "SHOT") return null;
    const typed = row.actualEnd.trim();
    if (!typed) return null; // optional — an unstated end leaves the strip at its scheduled one
    const end = asMinutes(typed);
    if (end === null) return `${typed} is not a time. ${NOT_A_TIME}`;
    if (end <= toMin(item.start)) return `Sc ${sceneNumber(item)} cannot have ended at ${typed}; it started at ${item.start}.`;
    return null;
  };

  // Follows the last completed scene until the producer states one, so the refusal below can only be
  // reached deliberately — and is then shown here rather than left to arrive as a 409.
  const cameraWrap = wrapAt ?? (lastShotEnd >= 0 ? hhmm(lastShotEnd) : "");
  const wrapMinutes = asMinutes(cameraWrap);
  const wrapProblem =
    shot.length === 0 || !cameraWrap.trim()
      ? null
      : wrapMinutes === null
        ? `${cameraWrap} is not a time. ${NOT_A_TIME}`
        : wrapMinutes < lastShotEnd
          ? `The camera cannot have wrapped at ${cameraWrap} on a day whose last completed scene ran to ${hhmm(lastShotEnd)}.`
          : null;

  const off = !feature?.enabled;
  const blocked = items.length === 0 || wrapProblem !== null || items.some((i) => rowProblem(i) !== null);
  const frozen = off || !!disabled || busy !== null;

  const submit = async () => {
    const carriedDetail = carried.length
      ? ` ${carried.map((i) => `Sc ${sceneNumber(i)}`).join(", ")} ${carried.length === 1 ? "carries" : "carry"} to another day, and this day is charged the carry-over for ${carried.length === 1 ? "it" : "them"}.`
      : "";
    const wrapPhrase = shot.length && cameraWrap.trim() ? `, camera wrap ${cameraWrap.trim()}` : "";
    if (
      !confirm(
        `Wrap Day ${day.day_number}? ${shot.length} shot, ${carried.length} carried${wrapPhrase}.${carriedDetail} ` +
          "A wrapped day is a record: it cannot be rescued, re-timed or wrapped again. This cannot be undone.",
      )
    )
      return;
    setBusy("wrap");
    setError(null);
    try {
      const result = await api.wrapDay(projectId, dayId, {
        items: items.map((i) => {
          const row = rowOf(i);
          const typed = row.actualEnd.trim();
          return {
            item_id: i.id,
            outcome: row.outcome,
            // Only ever sent for a strip that was shot. The server records an actual end by moving
            // the item's own end, and a carried strip never ran to one.
            actual_end: row.outcome === "SHOT" && typed && typed !== i.end ? typed : null,
            note: row.note.trim() || null,
          };
        }),
        // A day that shot nothing has no camera wrap, and recording the hard wrap for one would
        // invent a time the unit never reached — the engine leaves it null for the same reason.
        camera_wrap: shot.length ? cameraWrap.trim() || null : null,
      });
      setDone({ day: result.day, completion: result.completion });
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  if (done) return <DayRecord projectId={projectId} dayId={dayId} day={done.day} completion={done.completion} />;

  return (
    <section className="card p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Kicker>Closing the day out</Kicker>
        <span className="text-[12px] text-muted">
          Every strip is either in the can or carried to another day. Wrapping writes that down: the day becomes a record, its cost is read off what happened
          instead of projected, and the daily production report can finally issue.
        </span>
        {Object.keys(rows).length > 0 && (
          <button
            className="btn btn-ghost ml-auto text-[11px]"
            disabled={frozen}
            title="Put every strip back to shot at its scheduled end and drop the notes."
            onClick={() => {
              setRows({});
              setWrapAt(null);
            }}
          >
            Back to the day as scheduled
          </button>
        )}
      </div>

      {off && (
        <p className="mt-3 text-[12px] text-muted">
          Wrapping a day is off in this deployment. Enable with <span className="mono text-dim">{feature?.env || "SCENEPILOT_ALLOW_WRAP=1"}</span>. {feature?.cost}
        </p>
      )}

      {items.length === 0 ? (
        <p className="mt-3 text-[12px] text-muted">
          Day {day.day_number} has nothing scheduled on it. A day with no strips has no delivery to record, and wrapping it would leave a day reporting neither a
          plan nor a result.
        </p>
      ) : (
        <ul className="mt-3 grid gap-2 text-[13px]">
          {items.map((item) => {
            const scene = scenes[item.scene_id];
            const row = rowOf(item);
            const problem = rowProblem(item);
            const isCarried = row.outcome === "CARRIED";
            return (
              <li key={item.id} className={`rounded border p-2.5 ${isCarried ? "border-warn/60 bg-warn/5" : "border-line bg-elev"}`}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="mono text-dim shrink-0">Sc {scene?.number ?? item.scene_id}</span>
                  <span className="font-medium truncate max-w-[24rem]" title={scene?.heading}>
                    {scene?.heading ?? "scene not on this production"}
                  </span>
                  <span className="mono text-[11px] text-dim">
                    {item.start}–{item.end}
                  </span>
                  {typeof scene?.eighths === "number" && <span className="chip chip-dim">{eighthsLabel(scene.eighths)} pgs</span>}
                  <span className="ml-auto flex items-center gap-1.5">
                    <button
                      className={`text-[11px] ${isCarried ? "btn btn-ghost" : "btn btn-primary"}`}
                      disabled={frozen}
                      title="In the can. The strip is marked completed and counts towards what the day delivered."
                      onClick={() => set(item, { outcome: "SHOT" })}
                    >
                      Shot
                    </button>
                    <button
                      className={`text-[11px] ${isCarried ? "btn btn-primary" : "btn btn-ghost"}`}
                      disabled={frozen}
                      title="Hand the strip to another day. It stays on this day as outstanding and is priced as a carry-over."
                      onClick={() => set(item, { outcome: "CARRIED" })}
                    >
                      Carried
                    </button>
                  </span>
                </div>

                <div className="mt-1.5 flex items-center gap-2 flex-wrap text-[12px]">
                  {isCarried ? (
                    <span className="text-muted shrink-0">Carried — it stays on Day {day.day_number} as outstanding, and has to land on another day.</span>
                  ) : (
                    <>
                      <span className="text-dim shrink-0">ran to</span>
                      <input
                        value={row.actualEnd}
                        onChange={(e) => set(item, { actualEnd: e.target.value })}
                        placeholder={item.end}
                        disabled={frozen}
                        className="bg-elev border border-line rounded px-2 py-1 mono w-20 disabled:opacity-50"
                        title={`Scheduled ${item.start}–${item.end}. Leave it as scheduled unless the strip actually ran long or short.`}
                      />
                    </>
                  )}
                  <input
                    value={row.note}
                    onChange={(e) => set(item, { note: e.target.value })}
                    placeholder={isCarried ? "why it carried (optional)" : "note for the report (optional)"}
                    disabled={frozen}
                    className="bg-elev border border-line rounded px-2 py-1 flex-1 min-w-[12rem] disabled:opacity-50"
                  />
                </div>

                {problem && <p className="mt-1 text-[12px] text-warn">{problem}</p>}
              </li>
            );
          })}
        </ul>
      )}

      {items.length > 0 && (
        <>
          <div className="mt-3 flex items-center gap-2 flex-wrap border-t border-line pt-3 text-[12px]">
            <span className="text-dim shrink-0">camera wrap</span>
            <input
              value={cameraWrap}
              onChange={(e) => setWrapAt(e.target.value)}
              disabled={frozen || shot.length === 0}
              placeholder={day.hard_wrap}
              className="bg-elev border border-line rounded px-2 py-1 mono w-20 disabled:opacity-50"
            />
            <span className="text-muted">
              {shot.length === 0
                ? "Nothing is marked shot, so there is no camera wrap to record."
                : `Follows the last completed scene until you state one. Day ${day.day_number} hard-wraps at ${day.hard_wrap}.`}
            </span>
            <button
              className="btn btn-primary ml-auto"
              disabled={frozen || blocked}
              title={off ? `Disabled. Set ${feature?.env || "SCENEPILOT_ALLOW_WRAP=1"}.` : "Irreversible — a wrapped day is a record"}
              onClick={submit}
            >
              Wrap Day {day.day_number} · {shot.length} shot, {carried.length} carried
            </button>
          </div>

          {wrapProblem && <p className="mt-2 text-[12px] text-warn">{wrapProblem}</p>}

          <p className="mt-2 text-[11px] text-dim">
            One way. A wrapped day cannot be rescued, re-timed or wrapped again — the rescue workflow rewrites a schedule that has not happened yet, and a
            finished day has nothing left on it to move.
          </p>
        </>
      )}

      {busy && (
        <div className="mt-3">
          <Spinner label={`Closing Day ${day.day_number} out`} />
        </div>
      )}
      {error && <p className="mt-3 text-[12px] text-bad">{error}</p>}
    </section>
  );
}

function SectionLabel({ children, note }: { children: React.ReactNode; note?: string }) {
  return (
    <div className="mt-3 mb-1.5 flex items-baseline gap-2 flex-wrap">
      <span className="text-[10px] uppercase tracking-[0.14em] text-dim">{children}</span>
      {note && <span className="text-[11px] text-dim">{note}</span>}
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded border border-line bg-elev p-2">
      <div className="text-[10px] uppercase tracking-[0.14em] text-dim">{label}</div>
      <div className="mono mt-0.5 text-[13px]">{value}</div>
      {note && <div className="mt-0.5 text-[10px] text-dim">{note}</div>}
    </div>
  );
}

function RecordRow({ row, carried }: { row: CompletionRow; carried: boolean }) {
  return (
    <li className="flex items-baseline gap-2 border-b border-line/60 py-1 text-[12px] last:border-0">
      <span className={`chip ${carried ? "chip-warn" : "chip-ok"} shrink-0`}>{carried ? "carried" : "in the can"}</span>
      <span className="mono text-dim shrink-0">Sc {row.scene_number}</span>
      <span className="min-w-0 flex-1 truncate" title={row.note ? `${row.heading} — ${row.note}` : row.heading}>
        {row.heading}
      </span>
      {row.location && <span className="hidden shrink-0 max-w-[10rem] truncate text-dim lg:inline">{row.location}</span>}
      <span className="mono text-muted shrink-0">
        {row.start}–{row.end}
      </span>
      <span className="mono w-16 shrink-0 text-right text-dim">{row.minutes} min</span>
      <span className="mono w-14 shrink-0 text-right text-dim">{row.eighths !== null ? `${eighthsLabel(row.eighths)} pg` : "—"}</span>
    </li>
  );
}

/** What the day delivered, read off its own record. Nothing here is projected — that is the whole of
 *  the distinction the day-cost card draws between its `record` and `projected` bases. */
function DayRecord({ projectId, dayId, day, completion }: { projectId: string; dayId: string; day: ShootDay; completion: DayCompletion | null }) {
  return (
    <section className="card p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Kicker>What Day {day.day_number} delivered</Kicker>
        <span className="text-[12px] text-muted">
          Read off the day&rsquo;s own record rather than projected from a schedule. This is what the daily production report is built from.
        </span>
        <a href={`/projects/${projectId}/days/${dayId}/dpr`} className="btn btn-primary ml-auto text-[11px]" title="Only ever issued for a wrapped day">
          Daily production report
        </a>
      </div>

      {!completion ? (
        <p className="mt-3 text-[12px] text-muted">
          Day {day.day_number} is wrapped, but this payload carries no completion record. The engine derives one from the day&rsquo;s own strips, so a day whose
          strips have since been taken off it has nothing left to report.
        </p>
      ) : (
        <>
          <p className="mt-3 text-[13px]">{completion.summary}</p>

          <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-6">
            <Stat label="unit call" value={completion.unit_call} />
            <Stat label="first shot" value={completion.first_shot} />
            <Stat label="camera wrap" value={completion.wrap} />
            <Stat label="elapsed" value={hoursLabel(completion.elapsed_minutes)} note={`standard ${hoursLabel(completion.standard_minutes)}`} />
            <Stat
              label="overtime"
              value={completion.overtime_minutes ? hoursLabel(completion.overtime_minutes) : "none"}
              note={completion.overtime_minutes ? inr(completion.overtime_cost_inr) : "inside the standard day"}
            />
            <Stat
              label="shot"
              value={`${completion.minutes_shot} min`}
              note={completion.eighths_shot !== null ? `${eighthsLabel(completion.eighths_shot)} pages` : "not paginated"}
            />
          </div>

          <div className="mt-2 flex items-baseline gap-3 flex-wrap border-t border-line pt-2 text-[12px]">
            <span className="text-dim">overtime {inr(completion.overtime_cost_inr)}</span>
            <span className="text-dim">carry-over {inr(completion.carry_over_cost_inr)}</span>
            {completion.locations.length > 0 && <span className="max-w-[24rem] truncate text-dim">{completion.locations.join(" · ")}</span>}
            <span className="ml-auto">
              what the day cost <span className="mono text-[15px] font-bold">{inr(completion.cost_inr)}</span>
            </span>
          </div>

          {completion.scenes_completed.length > 0 && (
            <>
              <SectionLabel note="what the unit actually got">In the can · {completion.scenes_completed.length}</SectionLabel>
              <ul className="rounded border border-line bg-elev px-2.5">
                {completion.scenes_completed.map((row) => (
                  <RecordRow key={row.item_id} row={row} carried={false} />
                ))}
              </ul>
            </>
          )}

          {completion.scenes_carried.length > 0 && (
            <>
              <SectionLabel note="outstanding — these still have to land on another day">Carried · {completion.scenes_carried.length}</SectionLabel>
              <ul className="rounded border border-warn/40 bg-warn/5 px-2.5">
                {completion.scenes_carried.map((row) => (
                  <RecordRow key={row.item_id} row={row} carried />
                ))}
              </ul>
            </>
          )}

          {completion.units.length > 1 && <p className="mt-2 text-[11px] text-dim">Units on this day: {completion.units.join(", ")}.</p>}
        </>
      )}
    </section>
  );
}
