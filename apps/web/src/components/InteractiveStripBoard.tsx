"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  inr,
  toMin,
  type Disruption,
  type FeatureState,
  type LaborRulePack,
  type Scene,
  type ScheduleItem,
  type Resource,
  type ShootDay,
  type SolarLightingProfile,
  type StripSimulationResult,
} from "@/lib/api";
import { eighthsLabel, shortName, stripToneClass } from "@/lib/stripboard";
import { DayBoard } from "./DayBoard";
import type { DayGeography } from "./CompanyMovePanel";
import { hhmmDay, minToHhmm, stripAxis } from "./StripBoard";
import { Kicker, Spinner, Stamp } from "./ui";

/** "12.5" but "10", the way every other panel prints a shift length. */
const hrs = (h: number) => (h % 1 === 0 ? String(h) : h.toFixed(1));

export function InteractiveStripBoard({
  projectId,
  day,
  baselineItems,
  scenes,
  disruption,
  scrubMin,
  scheduleLabel,
  ephemeris: ephemerisProp,
  laborPacks,
  laborPreset,
  onLaborPresetChange,
  enforcedPreset = null,
  resources,
  geography,
  deferredSceneIds = [],
  sceneDays,
  onCommitted,
}: {
  projectId: string;
  day: ShootDay;
  baselineItems: ScheduleItem[];
  scenes: Record<string, Scene>;
  disruption?: Disruption | null;
  scrubMin?: number;
  scheduleLabel?: string;
  /** Supplied by the day page so the profile is fetched once for the whole page. */
  ephemeris?: SolarLightingProfile | null;
  /** The real packs from /api/projects/{id}/labor-rules; the rule markers below are drawn from them. */
  laborPacks?: Record<string, LaborRulePack>;
  /** Owned by the day page: the operations strip prices the same pack this board simulates against,
   *  so the selection cannot be local state here without the two panels disagreeing on one page. */
  laborPreset: string;
  onLaborPresetChange: (preset: string) => void;
  /** The production's own pack. Anything else in the select is a what-if that prices this board alone. */
  enforcedPreset?: string | null;
  /** Sets and cast, for the Board view's own columns — a strip names a location, not a location id. */
  resources: Record<string, Resource>;
  /** `geography` off the shoot-day payload; the Board view's company-move banners are drawn from it. */
  geography?: DayGeography | null;
  /** Scenes this schedule does not shoot, so the Board can carry them the way a board does. */
  deferredSceneIds?: string[];
  sceneDays?: Record<string, number>;
  /** Fired after a board is committed to the day. The page owns the reload — this component only
   *  knows that the schedule it was handed is no longer the one the server holds. */
  onCommitted?: () => void;
}) {
  const [items, setItems] = useState<ScheduleItem[]>(baselineItems);
  const [fetchedEphemeris, setFetchedEphemeris] = useState<SolarLightingProfile | null>(null);
  const [simulation, setSimulation] = useState<StripSimulationResult | null>(null);
  const [simulating, setSimulating] = useState<boolean>(false);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(baselineItems[0]?.id || null);
  // The board is the default: the colour code is the one thing on this page a line producer reads
  // without being told what it is. The Gantt keeps the solar bands, the markers and the scrub needle.
  const [mode, setMode] = useState<"board" | "timeline">("board");

  /* A day the unit has already finished. Every control below that rewrites a time is inert on one —
   * day 3 wrapped at 07:15 and still offered working ±15/±30 nudges. The board, the markers and the
   * verdict all stay exactly as they are: a wrapped day is meant to be read. */
  const wrapped = day.status === "WRAPPED";
  const wrappedWhy = `Day ${day.day_number} is wrapped — this schedule is what the unit shot. There is nothing here for it to move.`;

  /* The chart's own axis, and the bounds every move on it is held to. Derived from the baseline
   * rather than from `items` so the geometry does not shift under a producer mid-nudge. */
  const axis = useMemo(() => stripAxis(day, baselineItems), [day, baselineItems]);
  const left = (m: number) => `${axis.pct(m)}%`;
  const width = (a: number, b: number) => `${axis.pct(b) - axis.pct(a)}%`;

  // The page re-computes the schedule when an option is selected and again after approval, and the
  // poll hands us a fresh array every 1.2 s with identical contents. Adopt the incoming schedule when
  // it actually differs — a reference check would throw away the producer's own nudges on every tick.
  const baselineKey = useMemo(
    () => baselineItems.map((i) => `${i.id}@${i.start}-${i.end}:${i.status}`).join("|"),
    [baselineItems],
  );
  const adopted = useRef(baselineKey);

  // What the engine would be ruling on. A verdict is only current while the key it was fetched for
  // still describes the strips on screen and the pack they are priced against.
  const itemsKey = useMemo(() => items.map((i) => `${i.id}@${i.start}-${i.end}:${i.status}`).join("|"), [items]);
  const verdictKey = `${laborPreset}::${itemsKey}`;
  const [verdictFor, setVerdictFor] = useState<string | null>(null);

  useEffect(() => {
    if (adopted.current === baselineKey) return;
    adopted.current = baselineKey;
    setItems(baselineItems);
    setSelectedItemId((prev) => (prev && baselineItems.some((i) => i.id === prev) ? prev : baselineItems[0]?.id || null));
  }, [baselineKey, baselineItems]);

  // Load Ephemeris — only when the page has not already loaded it for us
  useEffect(() => {
    if (ephemerisProp !== undefined) return;
    api
      .getEphemeris(projectId, day.id)
      .then((res) => setFetchedEphemeris(res.profile))
      .catch((err) => console.warn("Failed to load solar ephemeris", err));
  }, [projectId, day.id, ephemerisProp]);

  const ephemeris = ephemerisProp !== undefined ? ephemerisProp : fetchedEphemeris;

  // Run Simulation whenever items or preset changes
  useEffect(() => {
    let active = true;
    const debounce = setTimeout(() => {
      if (!active) return;
      setSimulating(true);  // when the call actually starts, not while we are still debouncing
      api
        .simulateStripMove(projectId, day.id, items, laborPreset)
        .then((res) => {
          if (!active) return;
          setSimulation(res);
          setVerdictFor(verdictKey);
        })
        .catch((err) => {
          console.warn("Simulation failed", err);
          if (!active) return;
          // No verdict on this schedule. The previous one is not allowed to stand in for it, and
          // holding it as "stale" forever would leave the spinner running on a call that is over.
          setSimulation(null);
          setVerdictFor(verdictKey);
        })
        .finally(() => {
          if (active) setSimulating(false);
        });
    }, 150);

    return () => {
      active = false;
      clearTimeout(debounce);
    };
  }, [projectId, day.id, items, laborPreset, verdictKey]);

  /* The 150 ms debounce plus the round trip leave a window in which the strips have already been
   * repainted — an adopted recovery, a nudge, a pack switch — and the only verdict in hand is the one
   * on the schedule they replaced. Marking a strip rejected in that window states as current a
   * finding the engine has withdrawn, and it is exactly the frame a screen capture lands on. */
  const verdictStale = simulation !== null && verdictFor !== verdictKey;

  // Both views paint the same strips and cannot disagree about which of them the engine rejects.
  const hardByItem = useMemo(() => {
    const m = new Map<string, string>();
    for (const v of simulation?.hard_violations ?? []) if (v.item_id) m.set(v.item_id, v.message);
    return m;
  }, [simulation]);

  /* Where a nudge would actually land, or null when it cannot be applied at all.
   *
   * The bounds are the day's own axis. On the fixed 06:00–22:00 one they were a trap: day 6's Sc 58
   * runs 21:00–23:30, so `END - dur` was 19:30 — already behind the strip — and every button, "+15m"
   * included, collapsed onto it and moved the scene ninety minutes *earlier*, then posted that to
   * /simulate-strip-move and came back with a TRAVEL_OVERLAP the producer never asked for. Day 3's
   * 05:45 strip had the mirror image at the bottom: "-15m" moved it later.
   *
   * A clamp that reverses the direction of the button is worse than no move, so a nudge with no room
   * left in the direction asked for is nothing at all — and the button that would do it is disabled. */
  const nudgeTarget = (it: ScheduleItem, deltaMinutes: number) => {
    const dur = toMin(it.end) - toMin(it.start);
    const cur = toMin(it.start);
    const landed = Math.max(axis.startMin, Math.min(axis.endMin - dur, cur + deltaMinutes));
    if (deltaMinutes > 0 && landed <= cur) return null;
    if (deltaMinutes < 0 && landed >= cur) return null;
    return landed;
  };

  const nudgeItem = (itemId: string, deltaMinutes: number) => {
    if (wrapped) return;
    setItems((prev) =>
      prev.map((it) => {
        if (it.id !== itemId) return it;
        const newStartMin = nudgeTarget(it, deltaMinutes);
        if (newStartMin === null) return it;
        return {
          ...it,
          start: minToHhmm(newStartMin),
          end: minToHhmm(newStartMin + (toMin(it.end) - toMin(it.start))),
        };
      })
    );
  };

  // Snaps to the day's own window, because that is the one services/schedule.py accepts a SUNSET
  // scene against; snapping to the computed astronomical window could land on a rejected slot.
  const alignToGoldenHour = (itemId: string) => {
    if (wrapped) return;
    const ghStart = toMin(day.golden_hour_dusk[0]);
    const ghEnd = toMin(day.golden_hour_dusk[1]);
    setItems((prev) =>
      prev.map((it) => {
        if (it.id !== itemId) return it;
        return {
          ...it,
          start: minToHhmm(ghStart),
          end: minToHhmm(ghEnd),
        };
      })
    );
  };

  const resetToBaseline = () => {
    setItems(baselineItems);
    setSelectedItemId((prev) => (prev && baselineItems.some((i) => i.id === prev) ? prev : baselineItems[0]?.id || null));
  };

  // Rule markers come from the selected pack itself — nothing is drawn for a rule we could not load.
  const pack = laborPacks?.[laborPreset] ?? null;
  const wrap = toMin(day.unit_call) + Math.round(day.standard_hours * 60);
  const lunchDue = pack ? toMin(day.unit_call) + Math.round(pack.lunch_due_hours * 60) : null;
  const goldenTimeMarker = pack ? toMin(day.unit_call) + Math.round(pack.golden_time_threshold_hours * 60) : null;

  /* The meal the pack states, not a meal of this component's own: when it falls due is unit call +
   * `lunch_due_hours`, and how long it has to be is `minimum_lunch_minutes` — the same two fields the
   * board's own Lunch banner prints. With no pack loaded there is nothing to derive and the control
   * is not offered, rather than inserting a break no rule asked for. */
  const insertLunchBreak = () => {
    if (!pack || lunchDue === null || wrapped) return;
    const minutes = pack.minimum_lunch_minutes;
    setItems((prev) =>
      prev.map((it) => {
        const s = toMin(it.start);
        if (s < lunchDue) return it;
        return {
          ...it,
          start: minToHhmm(s + minutes),
          end: minToHhmm(toMin(it.end) + minutes),
        };
      })
    );
  };

  /* The break is opened by moving everything from the due time back. On a day whose call puts lunch
   * due after the last strip has already started — a 16:00 call under a 6 h rule is due at 22:00 —
   * there is nothing to move, and the button would be a no-op that looks like an action. */
  const lunchShiftable = lunchDue !== null && items.some((it) => toMin(it.start) >= lunchDue);

  /* A pack marker can fall clean off this day: FWICE puts golden time at call + 18 h, which on the
   * 16:00 night unit is 10:00 the following morning. Clamped, it drew a rose line hard against the
   * right border of a chart that ends six hours earlier — a rule marker pointing at a minute that is
   * not on the axis. It is not drawn at all there; the wrap marker is always in range because the
   * axis is derived from it. */
  const onAxis = (m: number) => m >= axis.startMin && m <= axis.endMin;

  /* Marker labels hang off a zero-width line, so near the right-hand end they hang off the chart.
   * The night units put standard wrap at 04:00 +1d, a few per cent from the end of their own axis. */
  const labelSide = (m: number) => (axis.pct(m) > 80 ? "right-1" : "left-1");

  const golden = day.golden_hour_dusk;
  const goldenMinutes = toMin(golden[1]) - toMin(golden[0]);
  const goldenDiffers =
    !!ephemeris && (ephemeris.golden_hour_dusk[0] !== golden[0] || ephemeris.golden_hour_dusk[1] !== golden[1]);

  const win =
    disruption?.window_start && disruption.window_end
      ? [toMin(disruption.window_start), toMin(disruption.window_end) + (disruption.dry_out_minutes || 0)]
      : null;

  const edited = itemsKey !== baselineKey;
  const sorted = [...items].sort((a, b) => toMin(a.start) - toMin(b.start));
  const selectedItem = items.find((i) => i.id === selectedItemId);
  const selectedScene = selectedItem ? scenes[selectedItem.scene_id] : null;

  /* A scene carries `cast_ids`; the inspector describes the same strip the board's Cast column does,
   * so it resolves them against the production's own resources rather than printing the ids. The
   * column has a strip's width and prints numbers; the inspector has room, so it prints the number
   * against the name — the pairing a breakdown sheet carries, and the decode for the numbers above.
   * Billing order, with an unnumbered performer last rather than slotted into the sequence. */
  const selectedCast = (selectedScene?.cast_ids ?? [])
    .map((id) => resources[id])
    .filter((r): r is Resource => !!r)
    .sort((a, b) => (a.cast_number === null ? 1 : 0) - (b.cast_number === null ? 1 : 0) || (a.cast_number ?? 0) - (b.cast_number ?? 0));
  const castLabel = selectedCast.length
    ? selectedCast.map((r) => (r.cast_number === null ? shortName(r.name) : `${r.cast_number} ${shortName(r.name)}`)).join(", ")
    : selectedScene?.cast_ids?.length
      ? "not on file"
      : "None";

  /* Committing the board. Everything above this line is a what-if: /simulate-strip-move validates and
   * prices an arbitrary set of times and hands them back, and `resetToBaseline` is the only other
   * control, so every nudge on the headline surface of phase 2 died on reload. This is the write that
   * was missing — and it is capability-gated, so on a deployment that has not opened it the control's
   * job is to say the write exists and this deployment does not allow it. */
  const [feature, setFeature] = useState<FeatureState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[] | null>(null);
  /* The board a commit actually landed. The page owns the reload, so in the window before the fresh
   * payload arrives the strips still read as edited against a baseline one revision behind — and a
   * second click would post an identical board and come back with the server's "nothing to commit",
   * which reads on screen like the first commit having failed. */
  const [committedKey, setCommittedKey] = useState<string | null>(null);

  useEffect(() => {
    api
      .features()
      .then((f) => setFeature(f.features.commit_board))
      .catch(() => setFeature(null));
  }, []);

  const commitOff = !feature?.enabled;
  const enforcedName = enforcedPreset ? (laborPacks?.[enforcedPreset]?.name ?? enforcedPreset) : null;
  const previewName = laborPacks?.[laborPreset]?.name ?? laborPreset;
  const previewingOtherPack = !!enforcedPreset && laborPreset !== enforcedPreset;

  // What the commit would actually change, for the confirm and the title — a count of strips, not of
  // fields, because the producer moved strips and the server counts each start and end separately.
  const movedCount = useMemo(() => {
    const base = new Map(baselineItems.map((i) => [i.id, i]));
    return items.filter((i) => {
      const b = base.get(i.id);
      return !b || b.start !== i.start || b.end !== i.end;
    }).length;
  }, [items, baselineItems]);

  /* The engine's own finding, not a second opinion of it: the same `simulation` the panel below
   * prints and the strips above are marked from. Posting a board it has already rejected turns a
   * verdict that is on screen into a round trip that comes back saying the same thing. */
  const hardBlock = simulation?.hard_violations.length
    ? `The validator rejects this board: ${[...new Set(simulation.hard_violations.map((v) => v.message))].join("; ")}`
    : null;

  /* First reason that applies, and the deployment's comes first: telling a producer the day is
   * wrapped implies the commit would work on another day, which on a closed deployment is false. */
  const commitBlocked = commitOff
    ? `Committing the board is off in this deployment. Enable it with ${feature?.env || "SCENEPILOT_ALLOW_COMMIT_BOARD=1"}. ${feature?.cost ?? ""}`.trim()
    : wrapped
      ? wrappedWhy
      : committedKey === itemsKey
        ? `This board is already committed to Day ${day.day_number}.`
        : !edited
          ? "Nothing to commit — this is the schedule the day already holds. Nudge a strip and the commit goes live."
          : simulating || verdictStale
            ? "Waiting on the validator. A commit whose verdict does not describe what is on screen is a guess, so it holds until the verdict catches up."
            : !simulation
              ? "No verdict for this schedule — the validator could not be reached, so there is nothing standing behind a commit."
              : hardBlock;

  const commit = async () => {
    if (commitBlocked !== null || busy !== null) return;
    /* Named the way `resetDay` names what it destroys: the strips that move, what else is re-derived
     * from them, and the pack it will be priced under — the enforced one, never the what-if selector
     * this component owns, so a board previewed under DGA/SAG does not commit under it. */
    const under = enforcedName
      ? `Re-validated and priced under ${enforcedName}, the agreement in force${previewingOtherPack ? `, not the ${previewName} preview on screen` : ""}.`
      : "Re-validated under the agreement in force on this production.";
    const strips = `${movedCount} strip${movedCount === 1 ? "" : "s"}`;
    if (!confirm(`Commit Day ${day.day_number}'s board? ${strips} move onto the day, and its equipment and transport calls are re-derived from the new times. ${under} There is no undo for it from here.`))
      return;
    setBusy("commit");
    setCommitError(null);
    setNotes(null);
    try {
      const res = await api.commitSchedule(
        projectId,
        day.id,
        items.map((i) => ({ item_id: i.id, start: i.start, end: i.end })),
        "Hand-nudged on the interactive stripboard",
      );
      setNotes(res.notes);
      setCommittedKey(itemsKey);
      onCommitted?.();
    } catch (e) {
      /* "409: <sentence>" — the refusal names the hard constraint that stopped it, and it is the most
       * useful thing on the screen. It is printed as it arrives rather than folded into a generic
       * failure line. */
      setCommitError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="card p-5 space-y-5">
      {/* Studio Header */}
      <div className="flex items-start justify-between flex-wrap gap-4 border-b border-line pb-4">
        <div>
          <div className="flex items-center gap-2">
            <Kicker>Phase 2 Intelligence</Kicker>
            <span className="mono text-[10px] px-1.5 py-0.5 rounded bg-accent/20 text-accent font-semibold">
              Interactive Stripboard
            </span>
            {scheduleLabel && <span className="chip chip-dim">{scheduleLabel}</span>}
            {wrapped && <span className="chip chip-dim" title={wrappedWhy}>wrapped · read-only</span>}
            {edited && <span className="chip chip-warn">locally edited</span>}
          </div>
          <h2 className="display text-2xl font-bold mt-1">
            {mode === "board" ? "Production Stripboard" : "Dynamic Gantt & Solar Scheduling Studio"}
          </h2>
          <p className="text-xs text-muted mt-0.5 max-w-2xl">
            {mode === "board"
              ? "The day laid out as strips in the trade's own colour code — INT/EXT against DAY/NIGHT — with the day-break, company-move and meal banners a paper board carries."
              : "Simulate scene adjustments with astronomical solar bounds, union labor rules (DGA vs FWICE), and real-time constraint validation."}
          </p>
        </div>

        {/* Labor Preset Switcher */}
        <div className="flex items-center gap-3">
          <div>
            <div className="text-[10px] font-semibold text-dim uppercase tracking-wider">View</div>
            <div className="mt-1 inline-flex rounded overflow-hidden border border-line">
              {(["board", "timeline"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  aria-pressed={mode === m}
                  className={`px-2.5 py-1 text-xs display uppercase tracking-wider font-semibold transition-colors ${
                    mode === m ? "bg-accent text-black" : "bg-elev text-muted hover:text-foreground"
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>
          <div className="text-right">
            <div className="text-[10px] font-semibold text-dim uppercase tracking-wider">
              Union Rule Pack
            </div>
            <select
              value={laborPreset}
              onChange={(e) => onLaborPresetChange(e.target.value)}
              className="mt-0.5 bg-zinc-900 border border-line rounded px-2.5 py-1 text-xs text-foreground focus:outline-none focus:border-accent"
            >
              {laborPacks ? (
                Object.entries(laborPacks).map(([key, p]) => (
                  <option key={key} value={key}>{p.name}</option>
                ))
              ) : (
                <>
                  <option value="DGA_SAG">DGA / SAG-AFTRA (Compounding)</option>
                  <option value="FWICE_CINTAA">FWICE / CINTAA (India Norm)</option>
                </>
              )}
            </select>
            {/* The recovery options are not regenerated by this select, so it says what it governs. */}
            <div className={`mt-1 text-[10px] ${enforcedPreset && laborPreset !== enforcedPreset ? "text-warn" : "text-dim"}`}>
              {enforcedPreset && laborPreset !== enforcedPreset
                ? "what-if — prices this board only"
                : "in force on this production"}
            </div>
          </div>

          <div className="flex flex-col items-end gap-1">
            <div className="flex items-center gap-2">
              {/* Nothing on a wrapped day can be edited, so there is never anything to reset back to. */}
              <button onClick={resetToBaseline} disabled={wrapped} className="btn text-xs" title={wrapped ? wrappedWhy : undefined}>
                Reset Schedule
              </button>
              <button
                onClick={commit}
                disabled={commitBlocked !== null || busy !== null}
                className="btn btn-primary text-xs"
                title={
                  commitBlocked ??
                  `Write ${movedCount} moved strip${movedCount === 1 ? "" : "s"} onto Day ${day.day_number}.${
                    enforcedName
                      ? ` Re-validated under ${enforcedName}${previewingOtherPack ? `, not the ${previewName} preview on screen` : ""}.`
                      : ""
                  }`
                }
              >
                Commit this board
              </button>
            </div>

            {/* The reason, never the absence of the control: a closed capability that renders as a
                missing button is indistinguishable from a feature nobody built. */}
            {commitOff ? (
              <p className="text-[10px] text-muted text-right max-w-[26rem]">
                Committing the board is off in this deployment. Enable it with{" "}
                <span className="mono text-dim">{feature?.env || "SCENEPILOT_ALLOW_COMMIT_BOARD=1"}</span>. {feature?.cost}
              </p>
            ) : previewingOtherPack ? (
              /* The select above is this component's what-if; the server re-validates a commit under
                 the pack in force whatever it reads. Saying so on the control is the difference
                 between previewing a board under DGA/SAG and believing it committed under it. */
              <p className="text-[10px] text-warn text-right max-w-[26rem]">
                Commits under {enforcedName} — the agreement in force. {previewName} prices this board on screen only.
              </p>
            ) : null}
          </div>
        </div>
      </div>

      {/* What the server did, kept out of the validation panel below: that panel is the what-if
          verdict on the strips as they stand, and these are the outcome of the write. */}
      {(busy === "commit" || commitError || (notes && committedKey === itemsKey)) && (
        <div className="space-y-1">
          {busy === "commit" && <Spinner label={`Committing Day ${day.day_number}'s board`} />}
          {commitError && <p className="text-[12px] text-bad">{commitError}</p>}
          {notes && committedKey === itemsKey && (
            <p className="text-[12px] text-ok">
              Committed to Day {day.day_number}.{" "}
              <span className="text-muted">
                {notes.slice(0, 6).join(" · ")}
                {notes.length > 6 ? ` · +${notes.length - 6} more` : ""}
              </span>
            </p>
          )}
        </div>
      )}

      {/* Solar Lighting Ephemeris Bar */}
      {ephemeris && (
        <div className="bg-zinc-950/60 border border-line/70 rounded p-3 text-xs flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-4">
            <span className="font-semibold text-zinc-300 flex items-center gap-1.5">
              ☀️ <span>Astronomical Ephemeris</span>
            </span>
            <span className="text-dim">·</span>
            <span className="mono text-muted">
              Sunrise: <strong className="text-foreground">{ephemeris.sunrise}</strong>
            </span>
            <span className="text-dim">·</span>
            <span className="mono text-muted">
              Solar Noon: <strong className="text-foreground">{ephemeris.solar_noon}</strong>
            </span>
            <span className="text-dim">·</span>
            <span className="mono text-muted">
              Sunset: <strong className="text-foreground">{ephemeris.sunset}</strong>
            </span>
          </div>

          <div className="flex flex-col items-end gap-0.5">
            <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 text-[11px] font-semibold flex items-center gap-1">
              ✨ Golden Hour Dusk: {golden[0]}–{golden[1]} ({goldenMinutes}m)
            </span>
            <span className="text-[10px] text-dim">
              the window the validator enforces for SUNSET scenes
              {goldenDiffers &&
                ` · computed astronomical window ${ephemeris.golden_hour_dusk[0]}–${ephemeris.golden_hour_dusk[1]}, not enforced`}
            </span>
          </div>
        </div>
      )}

      {mode === "board" && (
        <DayBoard
          day={day}
          items={items}
          scenes={scenes}
          resources={resources}
          geography={geography}
          pack={pack}
          simulation={simulation}
          simulationStale={verdictStale}
          deferredSceneIds={deferredSceneIds}
          sceneDays={sceneDays}
          selectedItemId={selectedItemId}
          onSelect={setSelectedItemId}
        />
      )}

      {/* Gantt Stripboard Timeline */}
      <div className={`relative ${mode === "timeline" ? "" : "hidden"}`} style={{ height: 48 * Math.max(1, sorted.length) + 36 }}>
        {/* Hour Header Grid */}
        <div className="absolute inset-x-0 top-0 h-5 text-[10px] mono text-dim">
          {axis.hours.map((h) => (
            <span key={h} className="absolute -translate-x-1/2" style={{ left: left(h) }}>
              {hhmmDay(h)}
            </span>
          ))}
        </div>

        {/* Vertical Grid Lines & Highlights */}
        <div className="absolute inset-x-0 top-6 bottom-0">
          {axis.hours.map((h) => (
            <div key={h} className="absolute top-0 bottom-0 border-l border-line" style={{ left: left(h) }} />
          ))}

          {/* Golden Hour Band — the day's enforced window, not the computed astronomical one */}
          <div
            className="absolute top-0 bottom-0 bg-amber-500/15 border-x border-amber-500/40"
            style={{
              left: left(toMin(golden[0])),
              width: width(toMin(golden[0]), toMin(golden[1])),
            }}
            title={`Golden hour dusk ${golden[0]}–${golden[1]} — the window the validator enforces for SUNSET scenes`}
          >
            <span className="absolute bottom-1 right-1 text-[9px] font-bold uppercase text-amber-400 opacity-70 tracking-wider">
              Golden Hour
            </span>
          </div>

          {/* Union Lunch Due Marker */}
          {lunchDue !== null && pack && onAxis(lunchDue) && (
            <div
              className="absolute top-0 bottom-0 border-l-2 border-dashed border-blue-400/60 z-10"
              style={{ left: left(lunchDue) }}
              title={`Lunch due at ${hhmmDay(lunchDue)} — call + ${pack.lunch_due_hours} h under ${pack.name}`}
            >
              <span className={`absolute top-1 ${labelSide(lunchDue)} text-[9px] mono font-bold text-blue-400 bg-black/70 px-1 rounded`}>
                LUNCH DUE ({hhmmDay(lunchDue)})
              </span>
            </div>
          )}

          {/* Standard Wrap (Overtime) Marker */}
          <div
            className="absolute top-0 bottom-0 border-l-2 border-dashed border-amber-400/80 z-10"
            style={{ left: left(wrap) }}
            title={`Standard wrap at ${hhmmDay(wrap)} — unit call + ${hrs(day.standard_hours)} h on this day`}
          >
            <span className={`absolute top-1 ${labelSide(wrap)} text-[9px] mono font-bold text-amber-400 bg-black/70 px-1 rounded`}>
              WRAP ({hhmmDay(wrap)})
            </span>
          </div>

          {/* Golden Time Marker */}
          {goldenTimeMarker !== null && pack && onAxis(goldenTimeMarker) && (
            <div
              className="absolute top-0 bottom-0 border-l-2 border-dashed border-rose-500/80 z-10"
              style={{ left: left(goldenTimeMarker) }}
              title={`Golden time at ${hhmmDay(goldenTimeMarker)} — call + ${pack.golden_time_threshold_hours} h at ×${pack.golden_time_multiplier} under ${pack.name}`}
            />
          )}

          {/* Disruption Window */}
          {win && (
            <div
              className="absolute top-0 bottom-0 hatch z-10"
              style={{
                left: left(win[0]),
                width: width(win[0], win[1]),
              }}
              title={`${disruption?.title} (+${disruption?.dry_out_minutes} min dry-out)`}
            >
              <span className="absolute bottom-1 left-1.5 text-[10px] mono text-bad font-bold bg-black/80 px-1 rounded">
                🌧️ {disruption?.window_start}–{disruption?.window_end}
              </span>
            </div>
          )}

          {/* timeline scrub needle */}
          {scrubMin !== undefined && (
            <div
              className="absolute top-0 bottom-0 border-l-2 border-accent z-20 pointer-events-none transition-all shadow-[0_0_8px_rgba(56,189,248,0.8)]"
              style={{ left: left(scrubMin) }}
            >
              <span className="absolute -top-5 -translate-x-1/2 bg-accent text-black mono text-[9px] font-bold px-1 rounded shadow">
                {hhmmDay(scrubMin)}
              </span>
            </div>
          )}

          {/* Scene Strips */}
          {/* Same colour code as the Board — a viewer who has just learned it does not have to unlearn
              it to read the clock. The fills are light, so the strip carries the board's own dark ink
              and every affordance on it is drawn to hold against that: the ring for selection, the red
              inset for a rejected strip, opacity for the secondary text. */}
          {sorted.map((it, i) => {
            const s = scenes[it.scene_id];
            if (!s) return null;
            const isSelected = it.id === selectedItemId;
            const tone = stripToneClass(s);
            const hard = hardByItem.get(it.id);

            return (
              /* A button, not a div: in timeline mode the whole board was unreachable by keyboard,
                 while the board view beside it was navigable. Same interaction, same semantics. */
              <button
                key={it.id}
                type="button"
                aria-pressed={isSelected}
                onClick={() => setSelectedItemId(it.id)}
                title={
                  hard
                    ? `${it.start}–${it.end} Sc ${s.number} ${s.heading} — ${
                        verdictStale ? `re-validating; on the previous cut: ${hard}` : hard
                      }`
                    : `${it.start}–${it.end} Sc ${s.number} ${s.heading}`
                }
                className={`absolute rounded-[4px] cursor-pointer transition-all flex items-center justify-between px-2 text-xs font-semibold select-none ${
                  tone ?? "bg-line-strong"
                } ${hard ? (verdictStale ? "strip-risk-stale" : "strip-risk") : "strip-edge"} ${
                  isSelected
                    ? "ring-2 ring-accent ring-offset-1 ring-offset-card z-20 shadow-lg"
                    : "z-10 hover:ring-2 hover:ring-accent/50"
                }`}
                style={{
                  left: left(toMin(it.start)),
                  width: width(toMin(it.start), toMin(it.end)),
                  top: i * 48 + 4,
                  height: 40,
                  color: tone ? "var(--strip-ink)" : "var(--fg)",
                }}
              >
                <div className="flex items-center gap-2 truncate">
                  <span className="display font-bold text-sm">SC {s.number}</span>
                  {it.unit && it.unit !== "MAIN" && (
                    <span className="shrink-0 rounded bg-black/20 px-1 text-[9px] font-bold uppercase" title={`${it.unit.toLowerCase()} unit`}>
                      {it.unit}
                    </span>
                  )}
                  <span className="truncate text-[11px] opacity-75">
                    {s.heading.replace(/^INT\.\s*|^EXT\.\s*/i, "")}
                  </span>
                </div>

                <div className="flex items-center gap-1.5 mono text-[10px] shrink-0 opacity-80">
                  <span>
                    {it.start}–{it.end}
                  </span>
                  {typeof s.eighths === "number" && (
                    <span className="px-1 rounded bg-black/15" title={`${s.eighths}/8 of a page`}>
                      {eighthsLabel(s.eighths)}
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Selected Strip Control & Simulation Status Panel */}
      <div className="grid grid-cols-1 md:grid-cols-12 gap-4 pt-2 border-t border-line">
        {/* Selected Strip Actions */}
        <div className="md:col-span-6 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-dim uppercase tracking-wider">
              Selected Scene Controls
            </span>
            {selectedItem && (
              <span className="mono text-xs text-foreground font-bold">
                {selectedItem.start} – {selectedItem.end} ({toMin(selectedItem.end) - toMin(selectedItem.start)} min)
              </span>
            )}
          </div>

          {selectedItem && selectedScene ? (
            <div className="card p-3 space-y-3 bg-zinc-900/60 border-accent/40">
              <div className="flex items-center justify-between">
                <div>
                  <div className="display text-base font-bold">
                    Scene {selectedScene.number}: {selectedScene.heading}
                  </div>
                  <div className="text-xs text-muted">
                    {selectedScene.int_ext} · {selectedScene.time_of_day} ·{" "}
                    <span title={selectedCast.length ? selectedCast.map((r) => r.name).join(" · ") : undefined}>Cast: {castLabel}</span>
                  </div>
                </div>
                {typeof selectedScene.eighths === "number" && (
                  <span
                    className="mono text-xs px-2 py-0.5 rounded bg-zinc-800 text-foreground font-bold"
                    title={`${selectedScene.eighths}/8 of a page`}
                  >
                    {eighthsLabel(selectedScene.eighths)} pgs
                  </span>
                )}
              </div>

              {/* Timing Nudge Controls */}
              <div className="flex items-center gap-2 flex-wrap">
                {[-30, -15, 15, 30].map((delta) => {
                  const target = nudgeTarget(selectedItem, delta);
                  return (
                    <button
                      key={delta}
                      onClick={() => nudgeItem(selectedItem.id, delta)}
                      disabled={wrapped || target === null}
                      className="btn text-xs py-1 px-2.5"
                      title={
                        wrapped
                          ? wrappedWhy
                          : target === null
                            ? delta > 0
                              ? `Sc ${selectedScene.number} already ends at ${hhmmDay(axis.endMin)}, the end of this day. There is nothing later to move it into.`
                              : `Sc ${selectedScene.number} already starts at ${hhmmDay(axis.startMin)}, the start of this day. There is nothing earlier to move it into.`
                            : `Move Sc ${selectedScene.number} to ${hhmmDay(target)}`
                      }
                    >
                      {delta < 0 ? `◀ ${delta}m` : `+${delta}m ▶`}
                    </button>
                  );
                })}

                {selectedScene.time_of_day === "SUNSET" && (
                  <button
                    onClick={() => alignToGoldenHour(selectedItem.id)}
                    disabled={wrapped}
                    className="btn btn-primary text-xs py-1 px-2.5"
                    title={wrapped ? wrappedWhy : `Snap to ${golden[0]}–${golden[1]}, the day's enforced golden-hour window`}
                  >
                    ✨ Snap to Golden Hour
                  </button>
                )}
              </div>

              {pack && lunchDue !== null && (
                <div className="flex items-center gap-2 pt-1">
                  <button
                    onClick={insertLunchBreak}
                    disabled={wrapped || !lunchShiftable}
                    className="text-xs text-blue-400 hover:text-blue-300 underline disabled:text-dim disabled:no-underline disabled:cursor-not-allowed"
                    title={
                      wrapped
                        ? wrappedWhy
                        : lunchShiftable
                          ? `${pack.minimum_lunch_minutes} min is the minimum meal break under ${pack.name}, due at unit call + ${pack.lunch_due_hours} h. Everything from ${hhmmDay(lunchDue)} moves back by that much.`
                          : `Lunch falls due at ${hhmmDay(lunchDue)} — unit call + ${pack.lunch_due_hours} h under ${pack.name} — but no scene on this day starts at or after it, so there is nothing to move back to open the break.`
                    }
                  >
                    + Insert {pack.minimum_lunch_minutes}-min lunch break at {hhmmDay(lunchDue)}
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="card p-4 text-center text-xs text-muted">
              {wrapped
                ? `Click a scene strip on the timeline above to read it. ${wrappedWhy}`
                : "Click a scene strip on the timeline above to adjust timings."}
            </div>
          )}
        </div>

        {/* Real-Time Constraint Validation & Cost Waterfall */}
        <div className="md:col-span-6 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-dim uppercase tracking-wider">
              Live Validation & Union Penalties
            </span>
            {(simulating || verdictStale) && <Spinner label="Validating..." />}
          </div>

          {/* Same rule as the strips: while the verdict below belongs to the schedule this one
              replaced, it is shown as receding rather than as the engine's finding on what is here. */}
          <div
            className={`card p-3 space-y-2.5 bg-zinc-900/60 transition-opacity ${verdictStale ? "opacity-50" : ""}`}
            title={verdictStale ? "Re-validating — this is the verdict on the previous cut of the schedule." : undefined}
          >
            {simulation ? (
              <>
                <div className="flex items-center justify-between border-b border-line/60 pb-2">
                  <div className="flex items-center gap-2">
                    <Stamp status={simulation.valid ? "READY" : "AT_RISK"} />
                    <span className="text-xs font-semibold">
                      {simulation.valid
                        ? "Feasible Schedule"
                        : `${simulation.hard_violations.length} Hard Violation(s)`}
                    </span>
                  </div>

                  <div className="text-right">
                    <span className="text-[11px] text-muted">Penalty Exposure:</span>{" "}
                    <strong
                      className={`mono text-xs ${
                        simulation.total_penalty_cost_inr > 0 ? "text-warn" : "text-ok"
                      }`}
                    >
                      {inr(simulation.total_penalty_cost_inr)}
                    </strong>
                  </div>
                </div>

                {/* Hard Violations */}
                {simulation.hard_violations.length > 0 && (
                  <div className="space-y-1">
                    {simulation.hard_violations.map((hv, idx) => (
                      <div
                        key={idx}
                        className="p-2 rounded bg-rose-500/15 border border-rose-500/40 text-rose-300 text-xs font-medium"
                      >
                        ⛔ {hv.message}
                      </div>
                    ))}
                  </div>
                )}

                {/* Soft Violations & Labor Penalties */}
                {simulation.soft_violations.length > 0 ? (
                  <div className="space-y-1">
                    {simulation.soft_violations.map((sv, idx) => (
                      <div
                        key={idx}
                        className="p-1.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-300 text-[11px]"
                      >
                        ⚠️ {sv.message}
                      </div>
                    ))}
                  </div>
                ) : (
                  simulation.valid && (
                    <div className="text-xs text-ok font-medium p-1">
                      ✓ No union meal penalties or turnaround violations detected.
                    </div>
                  )
                )}
              </>
            ) : simulating || verdictFor === null ? (
              <div className="text-xs text-muted p-2">Running validation checks...</div>
            ) : (
              <div className="text-xs text-muted p-2">
                No verdict for this schedule — the validator could not be reached. Nothing is marked on the strips rather
                than carrying the last one forward.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
