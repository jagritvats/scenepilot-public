"use client";

import { useMemo, useRef, type ReactNode } from "react";
import {
  toMin,
  type ConstraintViolation,
  type LaborRulePack,
  type Resource,
  type Scene,
  type ScheduleItem,
  type ShootDay,
  type StripSimulationResult,
} from "@/lib/api";
import { boardDate, boardTimeOfDay, castColumn, continuityChains, eighthsLabel, shortName, stripToneClass } from "@/lib/stripboard";
import type { DayGeography } from "./CompanyMovePanel";

/* A production stripboard, not a Gantt: one strip per scheduled scene, coloured by the trade's own
 * INT/EXT × DAY/NIGHT code, separated by the black day-break banners a physical board uses and
 * interrupted by the two things that consume a day without being a scene — the company move and the
 * meal. Nothing here is time-proportional; that is the Timeline view's job.
 *
 * Every cell reads production state. Where the state does not carry what a real board would print,
 * the cell says so instead of filling in something plausible — see the legend at the foot. */

/** One grid, used by the column header and every strip, so the columns cannot drift apart. */
const COLS =
  "grid-cols-[54px_40px_46px_minmax(112px,1.05fr)_minmax(148px,1.7fr)_minmax(92px,0.85fr)_52px_98px]";
/** Below this the board scrolls inside its own frame rather than crushing the scene number. */
const MIN_W = "min-w-[700px]";

const minutesBetween = (from: string, to: string) => toMin(to) - toMin(from);

function pageTotal(scenes: Scene[]) {
  const paginated = scenes.filter((s) => typeof s.eighths === "number");
  return {
    eighths: paginated.reduce((n, s) => n + (s.eighths as number), 0),
    paginated: paginated.length,
    missing: scenes.length - paginated.length,
  };
}

/** What the day banner prints for the day's pages — or why it prints nothing. */
function pagesLabel(scenes: Scene[]): { text: string; title: string } {
  const { eighths, paginated, missing } = pageTotal(scenes);
  if (!paginated) {
    return {
      text: "pages not on file",
      title: "No scene on this day carries a page count. Eighths reach a scene from the screenplay parser; until a draft is uploaded and synced there is no pagination to total.",
    };
  }
  return {
    text: `${eighthsLabel(eighths)} pgs${missing ? ` of ${paginated}/${paginated + missing} sc` : ""}`,
    title: missing
      ? `${eighthsLabel(eighths)} pages across the ${paginated} scene(s) that carry a page count. ${missing} scene(s) on this day are not paginated and are not in the total.`
      : `${eighthsLabel(eighths)} pages across all ${paginated} scene(s) on the day.`,
  };
}

interface Move {
  fromName: string;
  toName: string;
  wrapAt: string;
  nextShotAt: string;
  km: number | null;
  travelMinutes: number | null;
  departure: string | null;
  vehicleName: string | null;
}

/* A move is two consecutive strips at two different sets — read off the schedule the board is
 * actually showing, so a previewed recovery loses its move on screen the moment it is selected.
 * The distance and the production's travel allowance are properties of the location pair, so they
 * are taken from `geography` whenever it holds that pair. The departure is a property of *this*
 * schedule, so it is only shown when the persisted leg is still the one between these two scenes. */
function moveBetween(
  a: ScheduleItem,
  b: ScheduleItem,
  scenes: Record<string, Scene>,
  resources: Record<string, Resource>,
  geography: DayGeography | null | undefined,
): Move | null {
  const from = a.location_id ?? scenes[a.scene_id]?.location_id ?? null;
  const to = b.location_id ?? scenes[b.scene_id]?.location_id ?? null;
  if (!from || !to || from === to) return null;
  const pair = geography?.moves.find((m) => m.from_location_id === from && m.to_location_id === to) ?? null;
  const sameLeg =
    pair && pair.after_scene === scenes[a.scene_id]?.number && pair.before_scene === scenes[b.scene_id]?.number
      ? pair
      : null;
  return {
    fromName: shortName(resources[from]?.name ?? pair?.from_name ?? from),
    toName: shortName(resources[to]?.name ?? pair?.to_name ?? to),
    wrapAt: a.end,
    nextShotAt: b.start,
    km: pair?.straight_line_km ?? null,
    travelMinutes: pair?.travel_minutes ?? null,
    departure: sameLeg?.departure ?? null,
    vehicleName: sameLeg?.vehicle_name ?? null,
  };
}

export function DayBoard({
  day,
  items,
  scenes,
  resources,
  geography,
  pack,
  simulation,
  simulationStale = false,
  deferredSceneIds = [],
  selectedItemId,
  onSelect,
  sceneDays,
}: {
  day: ShootDay;
  items: ScheduleItem[];
  scenes: Record<string, Scene>;
  resources: Record<string, Resource>;
  geography?: DayGeography | null;
  /** The pack in force on the board — the meal banner applies its rule, not a hardcoded lunch. */
  pack?: LaborRulePack | null;
  /** The engine's verdict on exactly the schedule below; strips are marked from it, never from the UI's own arithmetic. */
  simulation?: StripSimulationResult | null;
  /** True while `simulation` is the verdict on a *previous* cut of the schedule and a re-validation is
   *  in flight. The markers hold their place rather than flicker, but they fade and say why: a
   *  rejection drawn at full strength is a claim the engine has not made about the strips below. */
  simulationStale?: boolean;
  deferredSceneIds?: string[];
  selectedItemId?: string | null;
  onSelect?: (itemId: string) => void;
  /** scene_id → day number, so a chain marker can say where the rest of the chain shoots. */
  sceneDays?: Record<string, number>;
}) {
  const sorted = useMemo(() => [...items].sort((a, b) => toMin(a.start) - toMin(b.start)), [items]);
  const stripRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  // Which strips are tied together by continuity, and where the rest of each chain shoots.
  const chains = useMemo(
    () => continuityChains(Object.values(scenes), new Map(Object.entries(sceneDays ?? {}))),
    [scenes, sceneDays],
  );
  const dayScenes = sorted.map((i) => scenes[i.scene_id]).filter((s): s is Scene => !!s);
  const pages = pagesLabel(dayScenes);
  /* The Cast column, resolved against the production's own resources. Computed once per strip here so
   * the legend can say which notation the column is actually in rather than describing both. */
  const castOf = (scene: Scene) => castColumn(scene.cast_ids.map((c) => resources[c]).filter((r): r is Resource => !!r));
  const castCells = dayScenes.map(castOf);
  const numberedStrips = castCells.filter((c) => c.numbered).length;
  const namedStrips = castCells.filter((c) => !c.numbered && !!c.text).length;
  const cameraWrap = sorted.length ? sorted[sorted.length - 1].end : null;

  const hardByItem = new Map<string, ConstraintViolation>();
  for (const v of simulation?.hard_violations ?? []) if (v.item_id) hardByItem.set(v.item_id, v);
  const softByItem = new Map<string, ConstraintViolation>();
  for (const v of simulation?.soft_violations ?? []) if (v.item_id) softByItem.set(v.item_id, v);

  /* The meal, under the same rule services/callsheet.py applies: the first gap between strips that
   * covers the pack's minimum break inside its window around unit call + lunch-due. No pack loaded,
   * or no gap that qualifies, and the board prints no meal — the penalty exposure is the
   * simulation's to report, not a banner's to imply. */
  const lunchDue = pack ? toMin(day.unit_call) + Math.round(pack.lunch_due_hours * 60) : null;
  let mealAfter = -1;
  if (pack && lunchDue !== null) {
    for (let i = 0; i < sorted.length - 1; i++) {
      const open = toMin(sorted[i].end);
      const close = toMin(sorted[i + 1].start);
      const covered = Math.min(close, lunchDue + pack.lunch_window_slack_minutes) - Math.max(open, lunchDue - pack.lunch_window_slack_minutes);
      if (covered >= pack.minimum_lunch_minutes) {
        mealAfter = i;
        break;
      }
    }
  }

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto scroll-thin -mx-1 px-1">
        {/* A listbox, not a stack of buttons: arrows move between strips and only the selected one
            is a tab stop, so tabbing past an eight-strip board costs one press rather than eight. */}
        <div
          className={`${MIN_W} space-y-[3px]`}
          role="listbox"
          aria-label={`Stripboard for day ${day.day_number}`}
          onKeyDown={(e) => {
            if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
            e.preventDefault();
            const at = sorted.findIndex((i) => i.id === selectedItemId);
            const next =
              e.key === "Home" ? 0
              : e.key === "End" ? sorted.length - 1
              : e.key === "ArrowDown" ? Math.min(sorted.length - 1, at < 0 ? 0 : at + 1)
              : Math.max(0, at < 0 ? 0 : at - 1);
            const target = sorted[next];
            if (!target) return;
            onSelect?.(target.id);
            // Move real focus with the selection, or the focus ring and the selection ring desync —
            // and on a narrow viewport this is what scrolls the off-frame columns into view.
            stripRefs.current[target.id]?.focus();
          }}
        >
          {/* column header */}
          <div className={`grid ${COLS} items-center pb-1 text-[9px] uppercase tracking-[0.14em] text-dim border-b border-line`}>
            <span className="px-2">Sc</span>
            <span className="px-2">I/E</span>
            <span className="px-2">D/N</span>
            <span className="px-2">Set</span>
            <span className="px-2">Description</span>
            <span
              className="px-2"
              title={
                numberedStrips > 0
                  ? "Cast numbers — the production's billing order, the same key the DOOD, the call sheet and the dispatch carry. Hover a strip for the names."
                  : "Cast names — no strip on this day carries a cast number on every performer."
              }
            >
              Cast
            </span>
            <span className="px-2 text-right">Pgs</span>
            <span className="px-2 text-right">Scheduled</span>
          </div>

          <DayBanner>
            <span className="display font-bold text-[14px] tracking-[0.16em]">DAY {day.day_number}</span>
            <span className="mono text-[11px] opacity-70">{boardDate(day.date)}</span>
            <span className="mono text-[11px] opacity-70">call {day.unit_call}</span>
            <span className="ml-auto mono text-[11px]" title={pages.title}>
              {sorted.length} sc · {pages.text}
            </span>
          </DayBanner>

          {sorted.length === 0 && (
            <div className="px-3 py-4 text-[12px] text-muted">Nothing is scheduled on this day — the board has no strips to lay.</div>
          )}

          {sorted.map((it, i) => {
            const scene = scenes[it.scene_id];
            if (!scene) return null;
            const tod = boardTimeOfDay(scene);
            const tone = stripToneClass(scene);
            const setId = it.location_id ?? scene.location_id ?? null;
            const set = setId ? resources[setId]?.name ?? null : null;
            const cast = castOf(scene);
            const hard = hardByItem.get(it.id);
            const soft = softByItem.get(it.id);
            const selected = it.id === selectedItemId;
            const next = sorted[i + 1];
            const move = next ? moveBetween(it, next, scenes, resources, geography) : null;

            return (
              <div key={it.id} className="space-y-[3px]">
                <button
                  type="button"
                  ref={(el) => {
                    stripRefs.current[it.id] = el;
                  }}
                  role="option"
                  aria-selected={selected}
                  tabIndex={selected || (!selectedItemId && i === 0) ? 0 : -1}
                  onClick={() => onSelect?.(it.id)}
                  title={`Sc ${scene.number} · ${scene.heading} · ${it.start}–${it.end}`}
                  className={`grid ${COLS} w-full items-center h-[34px] text-left rounded-[3px] transition-shadow ${
                    tone ?? "bg-line-strong"
                  } ${hard ? (simulationStale ? "strip-risk-stale" : "strip-risk") : ""} ${
                    selected ? "ring-2 ring-accent ring-offset-1 ring-offset-card" : ""
                  }`}
                  style={{ color: tone ? "var(--strip-ink)" : "var(--fg)" }}
                >
                  <span className="px-2 display font-bold text-[15px] leading-none truncate">{scene.number}</span>
                  <span className="px-2 mono text-[10px] font-semibold">{scene.int_ext}</span>
                  <span
                    className={`px-2 mono text-[10px] font-semibold ${tod.source === "heading" ? "underline decoration-dotted underline-offset-2" : ""}`}
                    title={tod.note ?? undefined}
                  >
                    {tod.label}
                  </span>
                  <span className="px-2 text-[11px] font-medium truncate" title={set ?? undefined}>
                    {set ? shortName(set) : <span className="opacity-50">no set on file</span>}
                  </span>
                  <span className="px-2 text-[11px] truncate flex items-center gap-1">
                    {it.status === "MOVED" && <Pill>moved</Pill>}
                    {scene.is_cover && <Pill>cover</Pill>}
                    {/* Only off the main unit: a chip on every strip of an all-MAIN day says nothing. */}
                    {(() => {
                      const chain = chains.get(it.scene_id);
                      if (!chain) return null;
                      const elsewhere = chain.dayNumbers.filter((n) => n !== day.day_number);
                      return (
                        <Pill
                          title={
                            `Continuity group "${chain.group}" — ${chain.sceneIds.length} scenes must match. ` +
                            (elsewhere.length
                              ? `The rest shoots Day ${elsewhere.join(", Day ")}. Carrying this scene off the day splits the chain further.`
                              : "The whole chain shoots today.")
                          }
                        >
                          ⛓ {chain.group}{elsewhere.length ? ` · d${elsewhere.join(",")}` : ""}
                        </Pill>
                      );
                    })()}
                    {it.unit && it.unit !== "MAIN" && (
                      <Pill title={`Shot by the ${it.unit.toLowerCase()} unit. Cast and equipment booked by two units at once is a hard conflict.`}>
                        {it.unit.toLowerCase()}
                      </Pill>
                    )}
                    {hard && (
                      <Pill
                        tone="bad"
                        stale={simulationStale}
                        title={
                          simulationStale
                            ? `Re-validating this schedule — the engine has not ruled on it yet. On the previous cut: ${hard.message}`
                            : hard.message
                        }
                      >
                        rejected
                      </Pill>
                    )}
                    {!hard && soft && (
                      <Pill
                        stale={simulationStale}
                        title={
                          simulationStale
                            ? `Re-validating this schedule — the engine has not ruled on it yet. On the previous cut: ${soft.message}`
                            : soft.message
                        }
                      >
                        note
                      </Pill>
                    )}
                    <span className="truncate opacity-90">{scene.synopsis || scene.heading}</span>
                  </span>
                  <span
                    className={`px-2 truncate opacity-90 ${cast.numbered ? "mono text-[11px] font-semibold tracking-wide" : "text-[11px]"}`}
                    title={cast.title || undefined}
                  >
                    {cast.text || <span className="opacity-50">no cast</span>}
                  </span>
                  <span className="px-2 mono text-[10px] text-right" title={typeof scene.eighths === "number" ? `${scene.eighths}/8 of a page` : "This scene is not paginated — no page count has reached it from a screenplay."}>
                    {typeof scene.eighths === "number" ? eighthsLabel(scene.eighths) : <span className="opacity-40">—</span>}
                  </span>
                  <span className="px-2 mono text-[10px] text-right whitespace-nowrap">
                    {it.start}–{it.end}
                  </span>
                </button>

                {i === mealAfter && pack && lunchDue !== null && (
                  <Banner
                    label="Lunch"
                    tone="info"
                    title={`A ${pack.minimum_lunch_minutes}-min break is due at ${hhmm(lunchDue)} — unit call + ${pack.lunch_due_hours} h under ${pack.name}, ±${pack.lunch_window_slack_minutes} min. This gap covers it.`}
                  >
                    {sorted[i].end}–{sorted[i + 1].start} · {minutesBetween(sorted[i].end, sorted[i + 1].start)} min · due {hhmm(lunchDue)} under {pack.name}
                  </Banner>
                )}

                {move && (
                  <Banner label="Company move" tone="accent" title={geography?.distance_basis}>
                    {move.fromName} → {move.toName} · wrap {move.wrapAt}, next shot {move.nextShotAt} (
                    {minutesBetween(move.wrapAt, move.nextShotAt)} min)
                    {move.km !== null && <> · {move.km} km straight line</>}
                    {move.travelMinutes !== null && <> · {move.travelMinutes} min travel allowed</>}
                    {move.km === null && move.travelMinutes === null && (
                      <span title="This day's geography covers the moves in the schedule on file. Nothing on the payload measures this pair, so the board states the move and leaves the distance blank.">
                        {" "}
                        · no distance on file for this pair
                      </span>
                    )}
                    {move.departure && (
                      <>
                        {" "}
                        · {move.vehicleName ?? "unit"} departs {move.departure}
                      </>
                    )}
                  </Banner>
                )}
              </div>
            );
          })}

          {deferredSceneIds.length > 0 && (
            <Banner label="Carried over" tone="warn" title="Not shot on this day under the schedule the board is showing.">
              {deferredSceneIds
                .map((sid) => scenes[sid])
                .filter((s): s is Scene => !!s)
                .map((s) => `Sc ${s.number} ${s.heading}`)
                .join(" · ")}
            </Banner>
          )}

          {sorted.length > 0 && (
            <DayBanner>
              <span className="display font-bold text-[13px] tracking-[0.16em]">END OF DAY {day.day_number}</span>
              <span className="ml-auto mono text-[11px] opacity-70">
                camera wrap {cameraWrap} · {sorted.length} sc · {pages.text}
              </span>
            </DayBanner>
          )}
        </div>
      </div>

      {/* legend */}
      <div className="pt-2 border-t border-line text-[10px] text-dim space-y-1.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <Swatch v="--strip-int-day">INT day</Swatch>
          <Swatch v="--strip-ext-day">EXT day</Swatch>
          <Swatch v="--strip-int-night">INT night</Swatch>
          <Swatch v="--strip-ext-night">EXT night</Swatch>
          <Swatch v="--strip-dusk">golden hour (SUNSET / DAWN)</Swatch>
          <span className="ml-auto">
            red outline = the live validation rejects this strip{simulationStale ? " · faded while the engine re-validates" : ""}
          </span>
        </div>
        <p>
          <b className="text-muted">D/N underlined</b> — the scene&apos;s <span className="mono">time_of_day</span> is{" "}
          <span className="mono">ANY</span> (it can shoot at any hour), so the board reads day/night off its slugline, as a
          paper board does. <b className="text-muted">Cast</b>{" "}
          {numberedStrips === 0 && namedStrips === 0 && (
            <>is empty down the board — no scene scheduled on this day carries a performer.</>
          )}
          {numberedStrips === 0 && namedStrips > 0 && (
            <>
              prints the production&apos;s own cast names: no strip here has a cast number on every performer, and a
              half-numbered cell would be a notation no board uses.
            </>
          )}
          {numberedStrips > 0 && (
            <>
              prints cast numbers, in billing order — the trade&apos;s own column, and the key the DOOD, the call sheet and
              the dispatch join on. Hover a strip for the names behind them
              {namedStrips > 0 && (
                <>
                  ; {namedStrips} strip{namedStrips === 1 ? "" : "s"} fall back to names, because the production has not
                  numbered every performer in {namedStrips === 1 ? "it" : "them"}
                </>
              )}
              .
            </>
          )}{" "}
          <b className="text-muted">Pgs</b> are eighths of a page and read{" "}
          <span className="mono">—</span> on any scene the screenplay parser has not paginated. A{" "}
          <b className="text-muted">unit pill</b> appears only on strips shot by something other than the main unit; the
          validator treats one performer or one piece of kit booked by two units at the same hour as a hard conflict.
        </p>
      </div>
    </div>
  );
}

const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

function DayBanner({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-3 h-[30px] px-3 rounded-[3px] bg-black text-fg border border-black">{children}</div>
  );
}

const BANNER_TONE: Record<string, { bg: string; border: string; text: string }> = {
  accent: { bg: "var(--accent-soft)", border: "var(--accent)", text: "var(--accent)" },
  info: { bg: "var(--info-soft)", border: "var(--info)", text: "var(--info)" },
  warn: { bg: "var(--warn-soft)", border: "var(--warn)", text: "var(--warn)" },
};

function Banner({
  label,
  tone,
  title,
  children,
}: {
  label: string;
  tone: "accent" | "info" | "warn";
  title?: string;
  children: ReactNode;
}) {
  const t = BANNER_TONE[tone];
  return (
    <div
      className="flex items-center gap-2.5 min-h-[26px] px-3 py-1 rounded-[3px] text-[11px]"
      style={{ background: t.bg, boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${t.border} 35%, transparent)` }}
      title={title}
    >
      <span className="display font-bold uppercase tracking-[0.14em] text-[11px] shrink-0" style={{ color: t.text }}>
        {label}
      </span>
      <span className="text-muted truncate">{children}</span>
    </div>
  );
}

function Pill({
  children,
  tone,
  title,
  stale = false,
}: {
  children: ReactNode;
  tone?: "bad";
  title?: string;
  stale?: boolean;
}) {
  return (
    <span
      className={`shrink-0 text-[9px] font-bold uppercase tracking-wider px-1 rounded-sm ${
        tone === "bad" ? "bg-black/80 text-bad" : "bg-black/15"
      } ${stale ? "opacity-40" : ""}`}
      title={title}
    >
      {children}
    </span>
  );
}

function Swatch({ v, children }: { v: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1">
      <i className="inline-block w-3.5 h-2 rounded-sm" style={{ background: `var(${v})` }} />
      {children}
    </span>
  );
}
