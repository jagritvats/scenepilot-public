"use client";

import { use, useEffect, useMemo, useState } from "react";
import { api, type CoordinationAction, type ChangeSet, type Evidence, type FeatureState, type LaborRulePack, type RecoveryOption, type Resource, type ShootDayView, type SolarLightingProfile, type WeatherTimeline } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { useHashScroll } from "@/lib/useHashScroll";
import { ActivityFeed } from "@/components/ActivityFeed";
import { AgentGraph } from "@/components/AgentGraph";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { DossierPanel } from "@/components/DossierPanel";
import { MonitorPanel } from "@/components/MonitorPanel";
import { SubstitutePanel } from "@/components/SubstitutePanel";
import { ParallelUsageStrip } from "@/components/ParallelUsageStrip";
import { StripBoard } from "@/components/StripBoard";
import { InteractiveStripBoard } from "@/components/InteractiveStripBoard";
import { OptionDetail, OptionRow } from "@/components/OptionCard";
import { ActionsList, ChangeSetView } from "@/components/ChangeSetView";
import { DisruptionScrubber, defaultScrubMin } from "@/components/DisruptionScrubber";
import { MultiDayPanel } from "@/components/MultiDayPanel";
import { OperationsStrip } from "@/components/OperationsStrip";
import { CompanyMovePanel, type DayGeography } from "@/components/CompanyMovePanel";
import { CompareOptions } from "@/components/CompareOptions";
import { DayCostCard } from "@/components/DayCostCard";
import { WrapPanel } from "@/components/WrapPanel";
import { ClearancePanel } from "@/components/ClearancePanel";
import { Kicker, LoadError, Spinner, Stamp, StatusChip } from "@/components/ui";

const hrs = (h: number) => (h % 1 === 0 ? String(h) : h.toFixed(1));

/** What a reader was promised by the link they clicked, for when the section is not there yet. */
const SECTION_NAMES: Record<string, string> = {
  recovery: "Recovery options",
  multiday: "Multi-day rescue",
  stripboard: "Stripboard & timeline",
};

/** Only the terms that actually move between two packs — a row that reads the same both sides is noise. */
function packDiff(inForce: LaborRulePack, whatIf: LaborRulePack): [string, string, string][] {
  const rows: [string, string, string][] = [
    ["Standard shift", `${hrs(inForce.standard_shift_hours)} h`, `${hrs(whatIf.standard_shift_hours)} h`],
    ["Turnaround", `≥ ${hrs(inForce.minimum_turnaround_hours)} h`, `≥ ${hrs(whatIf.minimum_turnaround_hours)} h`],
    ["Lunch window", `±${inForce.lunch_window_slack_minutes} min`, `±${whatIf.lunch_window_slack_minutes} min`],
    [
      "Meal penalty",
      inForce.compounding_meal_penalties ? "compounding" : "flat",
      whatIf.compounding_meal_penalties ? "compounding" : "flat",
    ],
    [
      "Golden time",
      `after ${hrs(inForce.golden_time_threshold_hours)} h at ×${inForce.golden_time_multiplier}`,
      `after ${hrs(whatIf.golden_time_threshold_hours)} h at ×${whatIf.golden_time_multiplier}`,
    ],
    [
      "Forced call",
      inForce.forced_call_penalty_enabled ? `₹${inForce.forced_call_flat_penalty_inr.toLocaleString("en-IN")} per lead` : "no flat penalty",
      whatIf.forced_call_penalty_enabled ? `₹${whatIf.forced_call_flat_penalty_inr.toLocaleString("en-IN")} per lead` : "no flat penalty",
    ],
  ];
  return rows.filter(([, a, b]) => a !== b);
}

export default function ShootDayPage({ params }: { params: Promise<{ id: string; dayId: string }> }) {
  const { id, dayId } = use(params);
  const { data, error, loading, reload } = usePoll(() => api.shootDay(id, dayId), (d) => !!d?.run && (d.run.status === "RUNNING" || d.run.status === "PENDING"), 1200);
  const [selected, setSelected] = useState<string | null>(null);
  // Up to two options pinned for side-by-side comparison; a third pin replaces the oldest, so the
  // control never needs an error state. Cleared whenever the option list itself is replaced.
  const [pins, setPins] = useState<string[]>([]);
  const togglePin = (optionId: string) =>
    setPins((prev) => (prev.includes(optionId) ? prev.filter((x) => x !== optionId) : [...prev, optionId].slice(-2)));
  const [drawer, setDrawer] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [localApplied, setApplied] = useState<{ changeset: ChangeSet; actions: CoordinationAction[] } | null>(null);
  const [view, setView] = useState<"before" | "after">("after");
  const [manualOpen, setManualOpen] = useState(false);
  const [interactiveMode, setInteractiveMode] = useState(true);
  const [scrubMin, setScrubMin] = useState<number | undefined>(undefined);
  // Fetched once here and handed down: the stripboard used to pull the same ephemeris a second time.
  const [ephemeris, setEphemeris] = useState<SolarLightingProfile | null>(null);
  const [laborPacks, setLaborPacks] = useState<{ active_preset: string; presets: Record<string, LaborRulePack> } | null>(null);
  // Owned here, not by the stripboard: the operations strip and the board's own simulation price this
  // one pack, so switching to DGA cannot leave one panel quoting DGA meal penalties beside another
  // quoting FWICE ones. It is a *what-if* selector — see `enforcedPack` below.
  const [laborPreset, setLaborPreset] = useState<string | null>(null);

  // The researched hourly forecast behind the scrubber, and the flag that decides whether it can be
  // researched at all. Both are read once: a Task run is paid, so nothing here fires on render.
  const [weather, setWeather] = useState<WeatherTimeline | null>(null);
  const [taskFeature, setTaskFeature] = useState<FeatureState | null>(null);
  const [weatherBusy, setWeatherBusy] = useState(false);
  const [weatherError, setWeatherError] = useState<string | null>(null);

  useEffect(() => {
    api.getEphemeris(id, dayId).then((r) => setEphemeris(r.profile)).catch((e) => console.warn("solar ephemeris unavailable", e));
  }, [id, dayId]);
  useEffect(() => {
    api.weatherTimeline(id, dayId).then((r) => setWeather(r.timeline)).catch((e) => console.warn("weather timeline unavailable", e));
  }, [id, dayId]);
  useEffect(() => {
    api.features().then((f) => setTaskFeature(f.features.task ?? null)).catch(() => setTaskFeature(null));
  }, []);

  const researchWeather = async () => {
    setWeatherBusy(true);
    setWeatherError(null);
    try {
      setWeather((await api.researchWeather(id, dayId)).timeline);
    } catch (e) {
      setWeatherError(e instanceof Error ? e.message : String(e));
    } finally {
      setWeatherBusy(false);
    }
  };
  useEffect(() => {
    api
      .getLaborRules(id)
      .then((r) => {
        setLaborPacks(r);
        setLaborPreset((prev) => prev ?? r.active_preset);
      })
      .catch((e) => console.warn("labor rule packs unavailable", e));
  }, [id]);

  const run = data?.run || null;
  const rescue = run?.rescue || null;
  const running = run?.status === "RUNNING" || run?.status === "PENDING";
  const options = useMemo(() => rescue?.options || [], [rescue]);
  const sel = options.find((o) => o.id === selected) || options.find((o) => o.id === rescue?.recommended_option_id) || options[0];
  // Pins are ids, and a new run replaces the option list — so resolve them against the *current*
  // options every render. A pin whose option is gone simply stops resolving.
  const pinned = pins.map((pid) => options.find((o) => o.id === pid)).filter((o): o is RecoveryOption => !!o);
  const pinnedPair = pinned.length === 2 ? ([pinned[0], pinned[1]] as const) : null;
  const evidence: Evidence[] = useMemo(() => rescue?.evidence || [], [rescue]);

  const applied = localApplied ?? (run?.status === "APPLIED" && rescue?.changeset ? { changeset: rescue.changeset, actions: rescue.actions } : null);

  // Deep links land on the section they name. `#recovery` and `#multiday` only exist once a
  // disruption has been reported, so both fall back to the picker that starts that workflow.
  //
  // `fellBackFrom` is what stops that being a lie by omission. A reader clicking "Multi-Day Rescue"
  // arrived at a card headed "Disruption" offering three fixtures, mid-page, with the day's own
  // header scrolled off — indistinguishable from a broken link, which is what it was reported as.
  const { fellBackFrom } = useHashScroll(!!data, { recovery: "disruption", multiday: "disruption" });
  const missingSection = fellBackFrom ? SECTION_NAMES[fellBackFrom] ?? fellBackFrom : null;

  if (!data) return loading || !error ? <div className="card p-8 shimmer h-72" /> : <LoadError error={error} missing="Shoot day not found" />;
  const { day, scenes, disruption } = data;
  // A day the unit has already finished. Nothing on it can be rescued, monitored or re-timed.
  const wrapped = day.status === "WRAPPED";
  // The recorded wrap where a day has one, and only otherwise the last strip's end. A carried strip
  // wins a `max(end)` and would date the day from a scene nobody shot.
  const cameraWrap = day.camera_wrap ?? (day.items.length ? day.items.map((i) => i.end).sort().slice(-1)[0] : null);
  const baseline = rescue?.baseline?.length ? rescue.baseline : day.items;
  const affected = rescue?.impact?.directly_affected_item_ids || [];
  const isApplied = run?.status === "APPLIED";
  const awaiting = run?.status === "AWAITING_APPROVAL";
  // the boards draw the needle wherever the scrubber reads, including before it is first dragged
  const needleMin = disruption ? scrubMin ?? defaultScrubMin(disruption) : undefined;
  // `geography` and `company_move_cost` are on the payload the engine sends; narrowed here, not in lib/api.ts
  const geography = (data as ShootDayView & { geography?: DayGeography }).geography ?? null;
  const extraMoveCostInr = (day as typeof day & { company_move_cost?: number }).company_move_cost;
  // Two different packs, and the difference is the whole point of keeping them apart.
  //
  // `enforcedPack` is `active_pack(project)` on the server — derived from where the production shoots
  // — and it is the pack the recovery options beside were generated, validated and priced under. It
  // never follows the dropdown, because the option list is not regenerated when the dropdown moves.
  //
  // `activePack` is whatever the stripboard's selector reads. It genuinely governs the board's live
  // simulation (`/simulate-strip-move` is sent the preset) and the operations strip that follows it,
  // and nothing else. Anywhere it is shown it is labelled as the what-if it is.
  const enforcedPreset = laborPacks?.active_preset ?? null;
  const enforcedPack = laborPacks && enforcedPreset ? laborPacks.presets[enforcedPreset] ?? null : null;
  const activePreset = laborPreset ?? enforcedPreset;
  const activePack = laborPacks && activePreset ? laborPacks.presets[activePreset] ?? null : null;
  const packIsEnforced = !laborPacks || activePreset === laborPacks.active_preset;
  const whatIfPack = packIsEnforced ? null : activePack;
  // Everything this day's own strips call — what a manually reported disruption can name as the
  // thing it takes out. Locations come off the items, cast and equipment off the scenes on them.
  const bookableOnDay = [
    ...new Set([
      ...day.items.map((i) => i.location_id || scenes[i.scene_id]?.location_id),
      ...day.items.flatMap((i) => scenes[i.scene_id]?.cast_ids || []),
      ...day.items.flatMap((i) => scenes[i.scene_id]?.equipment_ids || []),
    ]),
  ]
    .map((rid) => (rid ? data.resources[rid] : null))
    .filter((r): r is Resource => !!r);

  const openSearch = (sid: string) => {
    setFocus(sid);
    setDrawer(true);
  };
  const revert = async () => {
    if (!run) return;
    const reason = window.prompt("Why is this recovery being reverted? It goes on the record with your name.", "the disruption cleared");
    if (reason === null) return;
    setBusy("revert");
    try {
      await api.revertRecovery(run.id, reason.trim() || "no reason given");
      setApplied(null);
      setSelected(null);
      setPins([]);
      reload();
    } catch (e) {
      console.warn("revert failed", e);
      window.alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const report = async (body: Record<string, unknown>) => {
    setBusy("report");
    setApplied(null);
    setSelected(null);
    setPins([]);
    try {
      await api.reportDisruption(id, dayId, body);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  // The way out of a recommendation nobody can take. `approve` refuses an option that is not
  // feasible, and while a disruption is live the picker and the manual form are both hidden — so
  // before this existed a day whose every option was rejected could only be freed by resetting the
  // whole production. Worded like `revert`, because it is the same kind of act: a producer decision
  // that goes on the record under their name.
  const standDown = async () => {
    if (!run) return;
    const reason = window.prompt("Why is this recovery being stood down? It goes on the record with your name.", "we are shooting through it");
    if (reason === null) return;
    setBusy("stand-down");
    try {
      await api.standDown(run.id, reason.trim() || "no reason given");
      setApplied(null);
      setSelected(null);
      setPins([]);
      await reload();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const approve = async () => {
    if (!run || !sel) return;
    setBusy("approve");
    try {
      const res = await api.approve(run.id, sel.id);
      setApplied({ changeset: res.changeset, actions: res.actions });
      setView("after");
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  // Styled like the buttons beside it and confirmed by a sentence that named nothing, this discarded
  // the whole production: every run, every accepted fact, every change set, the activity log. It also
  // did not care whether a rescue was mid-flight. The confirm now counts what is about to go, the way
  // the project page's own reset does.
  const resetDay = async () => {
    const losing = [
      options.length ? `${options.length} recovery option${options.length === 1 ? "" : "s"} on this day` : null,
      data.changesets.length ? `${data.changesets.length} change set${data.changesets.length === 1 ? "" : "s"}` : null,
      data.activity.length ? `${data.activity.length} logged event${data.activity.length === 1 ? "" : "s"}` : null,
    ].filter(Boolean);
    const detail = losing.length ? ` You lose ${losing.join(", ")}, plus every accepted fact and Parallel run on the production.` : "";
    if (!confirm(`Reset Project Nightfall to its seeded state?${detail} This cannot be undone.`)) return;
    setBusy("reset");
    await api.reset(id);
    setApplied(null);
    setSelected(null);
    setPins([]);
    await reload();
    setBusy(null);
  };

  // which schedule the board shows
  const boardItems = isApplied ? (view === "before" ? baseline : day.items) : sel && (awaiting || running) && view === "after" && sel.feasible ? sel.schedule : baseline;
  const boardDeferred = isApplied ? (view === "before" ? [] : (rescue?.changeset ? baseline.filter((b) => !day.items.some((i) => i.scene_id === b.scene_id)).map((b) => b.scene_id) : [])) : sel && awaiting && view === "after" ? sel.deferred_scene_ids : [];
  const boardLabel = isApplied ? (view === "before" ? "before recovery" : "after recovery") : sel && (awaiting || running) && view === "after" && sel.feasible ? `preview option ${sel.label}` : "today";

  return (
    <div className="space-y-5">
      {/* header */}
      <div className="flex items-start gap-5 flex-wrap">
        <div>
          <Kicker>{day.date} · unit call {day.unit_call} · {day.crew_size} crew · overtime after {day.standard_hours} h at ₹{day.overtime_rate_per_hour.toLocaleString("en-IN")}/h</Kicker>
          <h1 className="display text-5xl font-bold mt-1 leading-none">SHOOT DAY {day.day_number}</h1>
        </div>
        <div className="mt-2"><Stamp status={day.status} /></div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <a href={`/projects/${id}/days/${dayId}/call-sheet`} className="btn">Call sheet</a>
          <a href={api.exportMmsxUrl(id, dayId)} download className="btn" title="Download this day's stripboard, breakdown sheets and cast as MMS-compatible XML (unofficial — ScenePilot's own schema, not a file written by Movie Magic Scheduling)">Export XML</a>
          <button className="btn" onClick={() => setDrawer(true)} disabled={!run}>Evidence ({evidence.length})</button>
          {/* Also gated on `running`: `busy` only tracks this page's own actions, so a rescue in
              flight could be thrown away by a button that looked idle. */}
          <button className="btn" onClick={resetDay} disabled={busy !== null || running}>Reset demo state</button>
        </div>
      </div>

      <OperationsStrip
        day={day}
        items={boardItems}
        scenes={scenes}
        scheduleLabel={boardLabel}
        disruption={disruption}
        scrubMin={scrubMin}
        ephemeris={ephemeris}
        pack={activePack}
        packIsEnforced={packIsEnforced}
        enforcedPackName={enforcedPack?.name ?? null}
      />

      {/* disruption banner / trigger */}
      {wrapped && (!disruption || isApplied) ? (
        <section id="disruption" className="card p-4 scroll-mt-20">
          <div className="flex items-center gap-2 flex-wrap">
            <Kicker>Disruption</Kicker>
            <span className="chip chip-dim">not offered — this day is wrapped</span>
          </div>
          <p className="text-sm text-muted mt-2 max-w-3xl">
            Day {day.day_number} is wrapped{cameraWrap ? <>; its last strip ended at <span className="mono">{cameraWrap}</span></> : null}. The rescue workflow exists to
            rewrite a schedule that has not happened yet — it moves strips, defers scenes, re-times transport and re-calls equipment. There is nothing here for it to
            move, and pricing a recovery for a finished day would invent a cost the production never paid.
          </p>
          <p className="text-[12px] text-dim mt-2 max-w-3xl">
            The day is still fully readable. The stripboard below is what was shot, the call sheet is the document the unit worked from, and the location dossiers
            underneath were researched against this date.
          </p>
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            {/* Offered only here, because a DPR only exists for a day that has finished. */}
            <a href={`/projects/${id}/days/${dayId}/dpr`} className="btn btn-primary">Daily production report</a>
            <a href={`/projects/${id}/days/${dayId}/call-sheet`} className="btn">Day {day.day_number} call sheet</a>
            <a href={`/projects/${id}`} className="btn btn-ghost">Days still ahead →</a>
          </div>
        </section>
      ) : !disruption || isApplied ? (
        <section id="disruption" className="card p-4 scroll-mt-20">
          <div className="flex items-center gap-3 flex-wrap">
            <Kicker>{isApplied ? "Report another disruption" : "Disruption"}</Kicker>
            <span className="text-[12px] text-muted">Deterministic fixtures keep the demo reliable; external context is still verified live through Parallel.</span>
            <button className="ml-auto text-[12px] text-accent hover:underline" onClick={() => setManualOpen((v) => !v)}>{manualOpen ? "hide manual entry" : "enter a disruption manually"}</button>
          </div>
          {missingSection && (
            <p className="mt-2 text-[12px] text-warn">
              {missingSection} does not exist on this day yet — it appears once a disruption has been reported and the
              rescue has run. This is where that starts.
            </p>
          )}
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            {data.fixtures.map((f) => {
              // A fixture the day cannot feel is shown, disabled, with the reason on it. Dropping it
              // would be worse: the list is short enough that a missing card reads as a bug, and the
              // reason is the more useful half — "Day 6 calls no telescopic crane" says something
              // about the day. Offering it live was worse still, which is what used to happen: Day 6
              // answered a crane fault with a scored recovery on a unit that carries no crane.
              const off = !f.applicable;
              return (
                <button
                  key={f.id}
                  className={`text-left rounded-lg border p-3 transition ${off ? "border-line opacity-45 cursor-not-allowed" : `hover:border-accent ${f.id === "rain_pm" ? "border-accent/50" : "border-line"}`}`}
                  onClick={() => report({ fixture_id: f.id })}
                  disabled={off || busy !== null || running}
                  title={f.not_applicable_reason ?? undefined}
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`chip ${off ? "chip-dim" : "chip-warn"}`}>{f.type.replace(/_/g, " ")}</span>
                    {f.id === "rain_pm" && !off && <span className="chip chip-accent">hero scenario</span>}
                    {off && <span className="chip chip-dim">cannot reach this day</span>}
                  </div>
                  <div className="font-medium mt-1.5">{f.title}</div>
                  <div className="text-[12px] text-muted mt-0.5 line-clamp-2">{f.not_applicable_reason ?? f.description}</div>
                </button>
              );
            })}
          </div>
          {manualOpen && <ManualDisruption onSubmit={report} disabled={busy !== null || running} onDay={bookableOnDay} />}
        </section>
      ) : (
        <section id="disruption" className={`card p-4 border-l-4 scroll-mt-20 ${disruption.verification_status === "CONTRADICTED" ? "border-l-ok" : "border-l-bad"}`}>
          <div className="flex items-start gap-4 flex-wrap">
            <div className="flex-1 min-w-[260px]">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="chip chip-bad">{disruption.type.replace(/_/g, " ")}</span>
                <span className="display text-2xl font-bold">{disruption.title}</span>
                {disruption.synthetic && <span className="chip chip-dim">fixture</span>}
              </div>
              <p className="text-sm text-muted mt-1">{disruption.description}</p>
            </div>
            <div className="min-w-[280px] max-w-[460px]">
              <div className="flex items-center gap-2">
                <span className="text-[11px] uppercase tracking-wider text-dim">External verification · Parallel</span>
                {disruption.verification_status ? <StatusChip status={disruption.verification_status} /> : running ? <Spinner label="checking" /> : <span className="chip chip-dim">n/a</span>}
                {disruption.verification_confidence !== null && <span className="mono text-[11px] text-dim">conf {Math.round((disruption.verification_confidence || 0) * 100)}%</span>}
              </div>
              {disruption.verification_summary && <p className="text-[12px] text-muted mt-1">{disruption.verification_summary}</p>}
              {disruption.search_run_ids.length > 0 && (
                <button className="mt-1 text-[12px] text-parallel underline decoration-dotted" onClick={() => openSearch(disruption.search_run_ids[0])}>
                  {disruption.search_run_ids.length} Parallel search run{disruption.search_run_ids.length === 1 ? "" : "s"} · {evidence.length} evidence
                </button>
              )}
            </div>
          </div>
        </section>
      )}

      {/* Watching a day that is already behind the production is the same false promise as rescuing one. */}
      {/* `!wrapped` alone. It used to copy the picker's condition verbatim, so the whole panel —
          the only place monitor drafts are ever shown, and the only Confirm/Dismiss on them —
          unmounted the moment a disruption went live, taking any draft sitting in it with it. It is
          also the only way to put a day under watch, which a producer wants *most* while a rescue is
          being decided. Confirming a draft mid-decision would start a second run the page reads
          instead of the one on screen, so the button is disabled while one awaits approval; the
          server refuses it too (409). */}
      {!wrapped && (
        <MonitorPanel
          projectId={id}
          dayId={dayId}
          disabled={busy !== null || running || awaiting}
          onChanged={() => reload()}
        />
      )}

      <DossierPanel
        projectId={id}
        locationIds={[...new Set(day.items.map((i) => i.location_id || scenes[i.scene_id]?.location_id).filter((x): x is string => !!x))]}
        dayId={day.id}
        dayNumber={day.day_number}
        disabled={busy !== null || running}
        onChanged={() => reload()}
      />

      <SubstitutePanel
        projectId={id}
        dayId={dayId}
        resources={[...new Set(day.items.flatMap((i) => scenes[i.scene_id]?.equipment_ids || []))].map((rid) => data.resources[rid]).filter(Boolean)}
        disabled={busy !== null || running}
        onChanged={() => reload()}
      />

      <ParallelUsageStrip usage={data.parallel_usage} onOpen={() => setDrawer(true)} />

      {/* Who this day calls that nobody has booked onto it. Renders nothing when there is nothing to
          clear, so it stays out of the way on a day that is fully cleared — which every seeded day is. */}
      <ClearancePanel
        projectId={id}
        dayId={dayId}
        day={day}
        pendingClearance={data.pending_clearance}
        disabled={busy !== null || running}
        onChanged={() => reload()}
      />

      {/* Closing the day out, and — once it is closed — the record of what it delivered. Given
          `day.items` rather than `boardItems`: the board may be previewing a recovery option, and a
          wrap is about the schedule the day actually holds. It switches to the record view itself on
          a wrapped day, so it needs no gate here. */}
      <WrapPanel
        projectId={id}
        dayId={dayId}
        day={day}
        items={day.items}
        scenes={scenes}
        completion={data.completion}
        disabled={busy !== null || running || awaiting}
        onChanged={() => reload()}
      />

      {disruption && (
        <DisruptionScrubber
          disruption={disruption}
          day={day}
          activeMin={needleMin}
          onScrub={setScrubMin}
          weather={weather}
          weatherFeature={taskFeature}
          weatherBusy={weatherBusy}
          weatherError={weatherError}
          onResearchWeather={wrapped ? undefined : researchWeather}
        />
      )}

      {/* Multi-Day Cascading Horizon & Pickup Day Synthesis */}
      {(boardDeferred.length > 0 || (sel?.deferred_scene_ids && sel.deferred_scene_ids.length > 0)) && (
        <div id="multiday" className="scroll-mt-20">
          <MultiDayPanel
            projectId={id}
            dayId={dayId}
            deferredSceneIds={boardDeferred.length > 0 ? boardDeferred : (sel?.deferred_scene_ids || [])}
            scenes={scenes}
          />
        </div>
      )}

      {/* `xl:` not `lg:` — at 1024 the rail snapped on and cut the main column to 549px, a 42%
          loss from one pixel of viewport. The rail is worth having only where there is room for it. */}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="space-y-5 min-w-0">
          {/* strip board header, before/after diff & mode toggle.
              `id` is what lets the guided tour and the trailer runbook point at this section rather
              than at the top of the page — two tour steps used to share one URL and land in the same
              place, which teaches nothing on the second click. */}
          <div id="stripboard" className="flex items-center gap-3 flex-wrap scroll-mt-20">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-dim">
              Stripboard & Timeline
            </span>
            {(awaiting || isApplied) && (
              <div className="flex gap-1">
                <button className={`chip ${view === "before" ? "chip-accent" : "chip-dim"}`} onClick={() => setView("before")}>before</button>
                <button className={`chip ${view === "after" ? "chip-accent" : "chip-dim"}`} onClick={() => setView("after")}>after</button>
              </div>
            )}
            <button
              onClick={() => setInteractiveMode(!interactiveMode)}
              className="ml-auto text-xs text-accent hover:underline flex items-center gap-1 font-semibold"
            >
              {interactiveMode ? "Switch to Classic View" : "⚡ Open Interactive Gantt & Solar Studio"}
            </button>
          </div>

          {interactiveMode ? (
            <InteractiveStripBoard
              projectId={id}
              day={day}
              baselineItems={boardItems}
              scenes={scenes}
              disruption={disruption}
              scrubMin={needleMin}
              scheduleLabel={boardLabel}
              ephemeris={ephemeris}
              laborPacks={laborPacks?.presets}
              laborPreset={activePreset ?? "DGA_SAG"}
              onLaborPresetChange={setLaborPreset}
              onCommitted={() => reload()}
              enforcedPreset={enforcedPreset}
              resources={data.resources}
              geography={geography}
              deferredSceneIds={boardDeferred}
          sceneDays={data.scene_days}
            />
          ) : (
            <StripBoard
              day={day}
              items={boardItems}
              scenes={scenes}
              disruption={disruption}
              affectedItemIds={isApplied || ((awaiting || running) && view === "after" && sel?.feasible) ? [] : affected}
              deferredSceneIds={boardDeferred}
              ghost={(awaiting || isApplied) && view === "after" ? baseline : undefined}
              title={`Strip board · ${boardLabel}`}
              scrubMin={needleMin}
            />
          )}

          {geography && (
            <CompanyMovePanel
              geography={geography}
              changeset={applied?.changeset ?? null}
              applied={isApplied}
              boardOnBaseline={isApplied && view === "before"}
              extraMoveCostInr={extraMoveCostInr}
            />
          )}

          {/* impact */}
          {rescue?.impact && !isApplied && (
            <section className="card p-4">
              <div className="flex items-center gap-2 flex-wrap">
                <Kicker>Impact analysis</Kicker>
                <span className="chip chip-accent">deterministic</span>
                <span className="text-[12px] text-muted">{rescue.impact.summary}</span>
              </div>
              <div className="mt-3 grid gap-4 md:grid-cols-3 text-[12px]">
                <div>
                  <div className="text-dim uppercase tracking-wider text-[10px] mb-1">Violated requirements</div>
                  <ul className="space-y-1">
                    {rescue.impact.violated_requirements.map((v, i) => (
                      <li key={i}><span className="display font-semibold mr-1">Sc {scenes[v.scene_id]?.number}</span><span className="text-muted">{v.reason}</span></li>
                    ))}
                  </ul>
                </div>
                <div>
                  <div className="text-dim uppercase tracking-wider text-[10px] mb-1">Implicated resources</div>
                  <div className="flex flex-wrap gap-1">
                    {rescue.impact.implicated_resource_ids.map((r) => (
                      <span key={r} className="chip chip-dim">{data.resources[r]?.name.split(" (")[0].split(" — ")[0]}</span>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="text-dim uppercase tracking-wider text-[10px] mb-1">Mobility</div>
                  <ul className="space-y-1">
                    {rescue.impact.immovable.map((m) => (
                      <li key={m.item_id}><span className="chip chip-warn mr-1">pinned</span><span className="display font-semibold">Sc {scenes[m.scene_id]?.number}</span> <span className="text-muted">{m.reason}</span></li>
                    ))}
                    {rescue.impact.movable.map((m) => (
                      <li key={m.item_id}><span className="chip chip-ok mr-1">movable</span><span className="display font-semibold">Sc {scenes[m.scene_id]?.number}</span> <span className="text-muted">{m.reason.split(";")[0]}</span></li>
                    ))}
                    {rescue.impact.cover_scene_ids.map((c) => (
                      <li key={c}><span className="chip chip-info mr-1">cover</span><span className="display font-semibold">Sc {scenes[c]?.number}</span> <span className="text-muted">{scenes[c]?.heading}</span></li>
                    ))}
                  </ul>
                </div>
              </div>
            </section>
          )}

          {/* options */}
          {options.length > 0 && !isApplied && (
            <section id="recovery" className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] scroll-mt-20">
              <div className="card p-4 space-y-2 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <Kicker>Recovery options</Kicker>
                  <span className="text-[12px] text-muted">{options.filter((o) => o.feasible).length} feasible · {options.filter((o) => !o.feasible).length} rejected</span>
                  {/* Named on the options themselves, so the pack cannot be inferred from a card across the page. */}
                  {enforcedPack && <span className="chip chip-dim" title="Every score and rupee figure below was produced under this pack. The stripboard's rule-pack selector does not regenerate them.">validated under {enforcedPack.name}</span>}
                </div>
                {options.map((o) => (
                  <div key={o.id} className="flex items-stretch gap-1.5">
                    <div className="flex-1 min-w-0">
                      <OptionRow o={o} selected={sel?.id === o.id} recommended={o.id === rescue?.recommended_option_id} onSelect={() => { setSelected(o.id); setView("after"); }} scenes={scenes} />
                    </div>
                    {/* Beside the row, never inside it — OptionRow's own root is a button. */}
                    <button
                      className={`chip self-center shrink-0 ${pins.includes(o.id) ? "chip-accent" : "chip-dim"}`}
                      onClick={() => togglePin(o.id)}
                      title="Pin to compare — two pinned options open the side-by-side"
                    >
                      {pins.includes(o.id) ? "pinned" : "compare"}
                    </button>
                  </div>
                ))}
                {rescue?.recommendation_rationale && (
                  <div className="pt-2 border-t border-line">
                    <div className="kicker mb-1">Why the recommendation wins</div>
                    <p className="text-[13px] text-fg/90 leading-relaxed">{rescue.recommendation_rationale}</p>
                  </div>
                )}
              </div>
              <div className="card p-4">
                {sel ? <OptionDetail o={sel} facts={data.location_facts} /> : <div className="text-muted text-sm">Select an option.</div>}
                {awaiting && sel && (
                  <div className="mt-5 space-y-2">
                    <div className="flex items-center gap-3 flex-wrap">
                      <button className="btn btn-primary" onClick={approve} disabled={!sel.feasible || busy !== null}>
                        {sel.feasible ? `Approve recovery ${sel.label}` : `Option ${sel.label} cannot be approved`}
                      </button>
                      {/* The other answer, and until it existed there was none. A day whose every option
                          is rejected leaves the button above permanently unclickable, and reporting a
                          different disruption is hidden while this one is live — so the only way out of
                          this screen was resetting the entire production. */}
                      <button className="btn btn-ghost" onClick={standDown} disabled={busy !== null}>
                        {busy === "stand-down" ? <Spinner /> : null} Stand down — take none of it
                      </button>
                    </div>
                    <p className="text-[12px] text-muted">
                      Nothing changes until a producer approves. Approval generates a ChangeSet and applies it with an audit
                      trail; standing down ends the rescue, hands Day {day.day_number} back untouched and keeps the options
                      above on the record as what was offered and declined.
                    </p>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Full width, below the two-column recovery section: two OptionDetails side by side need
              the page, and an inline section stays linkable and printable where a modal would not. */}
          {pinnedPair && !isApplied && (
            <CompareOptions
              a={pinnedPair[0]}
              b={pinnedPair[1]}
              baseline={baseline}
              day={day}
              scenes={scenes}
              facts={data.location_facts}
              projectId={id}
              dayId={dayId}
              onClose={() => setPins([])}
            />
          )}

          {running && options.length === 0 && (
            <div className="card p-6 flex items-center gap-3">
              <Spinner label={`Working · ${run?.stage.replace(/_/g, " ")}`} />
              <span className="text-[12px] text-muted">verify via Parallel → impact → candidates → deterministic validation → ranking → Gemini proposals & explanation</span>
            </div>
          )}
          {/* The other end of the graph. A disruption that turns out to touch nothing is an answer,
              and this is where it is given — the pipeline used to reach the same conclusion, report
              "0 scheduled scene(s) directly affected", and then recommend moving two scenes anyway. */}
          {rescue?.no_impact_reason && !running && (
            <section className="card p-4 border-l-4 border-l-ok">
              <div className="flex items-center gap-2 flex-wrap">
                <Kicker>Nothing to recover</Kicker>
                <span className="chip chip-ok">schedule stands</span>
                <span className="chip chip-dim">deterministic</span>
              </div>
              <p className="mt-2 text-sm text-fg/90 max-w-3xl leading-relaxed">{rescue.no_impact_reason}</p>
              <p className="mt-2 text-[12px] text-dim max-w-3xl">
                The report is on the record and the day is not held against it — Day {day.day_number} keeps the status and the
                call sheet it had. Report another disruption above if something else has moved.
              </p>
            </section>
          )}
          {run?.status === "FAILED" && (
            <div className="card p-4 border-bad text-sm">
              <div className="text-bad font-medium">Rescue run failed: {run.error}</div>
              {/* The day is handed back when a run fails, so this is a dead card and not a dead end —
                  worth saying, because it used to be the latter: the day stayed AT_RISK under a
                  disruption with no picker, no options and nothing to click but Reset. */}
              <p className="mt-1.5 text-[12px] text-muted">
                Day {day.day_number} was released — its status and schedule are untouched, and the disruption can be reported again above.
              </p>
            </div>
          )}

          {/* applied */}
          {applied && (
            <div className="space-y-5 rise">
              <ChangeSetView cs={applied.changeset} />
              {/* Reversibility is most of what separates a tool a producer trusts from a demo. The
                  original approval stays on the record; this is its own audit-trailed event. */}
              {run?.status === "APPLIED" && (
                <div className="card p-3 flex items-center gap-2 flex-wrap">
                  <span className="text-[12px] text-muted flex-1 min-w-[240px]">
                    Approved and applied. If the world changes again, this can be rolled back to the schedule it
                    replaced — the approval stays on the record, with the revert recorded against it.
                  </span>
                  <button className="btn btn-ghost text-xs" disabled={busy !== null} onClick={revert}>
                    {busy === "revert" ? <Spinner /> : null} Revert this recovery
                  </button>
                </div>
              )}
              <ActionsList actions={applied.actions} />
            </div>
          )}
          {!applied && data.changesets.length > 0 && !running && (
            <div className="space-y-5">
              {data.changesets.map((cs) => <ChangeSetView key={cs.id} cs={cs} />)}
            </div>
          )}

          {/* call sheet extras */}
          <section className="grid gap-4 md:grid-cols-2">
            <div className="card p-4">
              <Kicker>Equipment calls</Kicker>
              <table className="mt-2 w-full text-[12px]">
                <tbody>
                  {day.equipment_calls.map((c) => (
                    <tr key={c.resource_id} className="border-t border-line">
                      <td className="py-1">{data.resources[c.resource_id]?.name}</td>
                      <td className="py-1 mono text-right">{c.call_time}</td>
                    </tr>
                  ))}
                  {day.equipment_calls.length === 0 && (
                    <tr>
                      <td className="text-dim py-1">
                        no equipment call times on this day&apos;s sheet
                        {day.items.some((i) => (scenes[i.scene_id]?.equipment_ids || []).length > 0) && (
                          <span className="text-muted"> — though its scenes do carry equipment, so nothing here says when it arrives</span>
                        )}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="card p-4">
              <Kicker>Transport</Kicker>
              <table className="mt-2 w-full text-[12px]">
                <tbody>
                  {day.transport.map((t) => (
                    <tr key={t.id} className="border-t border-line">
                      <td className="py-1">{data.resources[t.vehicle_id]?.name} → {t.to_location_id ? data.resources[t.to_location_id]?.name.split(" — ")[0] : "?"}</td>
                      <td className="py-1 mono text-right">dep {t.departure}</td>
                    </tr>
                  ))}
                  {day.transport.length === 0 && <tr><td className="text-dim py-1">no company moves</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <div className="space-y-5">
          {/* Shown before a run too: the pipeline a disruption will be put through is worth seeing cold. */}
          <AgentGraph name="scenepilot_rescue" stage={run?.stage} status={run?.status} />
          <ActivityFeed events={data.activity} live={running} onOpenSearch={openSearch} />
          {/* Sits directly above the pack its projection was priced under, so the figure and the
              agreement behind it are read together. */}
          <DayCostCard card={data.day_cost} />
          {/* The rules the options beside were validated under — never the dropdown's selection. */}
          <section className="card p-4">
            <div className="flex items-center gap-2 flex-wrap">
              <Kicker>Rules in force</Kicker>
              {enforcedPack && <span className="chip chip-accent">{enforcedPack.name}</span>}
            </div>
            <p className="mt-1 text-[11px] text-dim">
              The agreement this production works under, and the one every recovery option on this page was generated, validated and priced against.
            </p>
            <ul className="mt-2 text-[12px] text-muted space-y-1">
              <li><b className="text-fg">Shift &amp; overtime</b> — {day.standard_hours} h standard day from unit call; overtime beyond it at ₹{day.overtime_rate_per_hour.toLocaleString("en-IN")}/h for the crew{enforcedPack ? ` (the pack's own standard shift is ${enforcedPack.standard_shift_hours} h)` : ""}.</li>
              {enforcedPack && <li><b className="text-fg">Lunch</b> — a {enforcedPack.minimum_lunch_minutes}-min break due {enforcedPack.lunch_due_hours} h after call, ±{enforcedPack.lunch_window_slack_minutes} min; otherwise a {enforcedPack.compounding_meal_penalties ? "compounding" : "flat"} meal penalty.</li>}
              {enforcedPack && <li><b className="text-fg">Turnaround</b> — at least {enforcedPack.minimum_turnaround_hours} h rest before the next day&apos;s unit call.</li>}
              <li><b className="text-fg">Light</b> — DAY scenes inside usable daylight; SUNSET scenes must hit the day&apos;s golden-hour window {day.golden_hour_dusk[0]}–{day.golden_hour_dusk[1]}, computed from the NOAA solar ephemeris for {day.date}.</li>
              <li><b className="text-fg">Hard limits</b> — cast, location and equipment availability windows, permit windows, travel time between locations, accepted external rules, disruption exposure incl. dry-out.</li>
            </ul>

            {whatIfPack && enforcedPack && (
              <div className="mt-3 pt-2.5 border-t border-line">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="chip chip-warn">what-if</span>
                  <span className="text-[12px] text-muted">{whatIfPack.name} — selected on the stripboard below</span>
                </div>
                <p className="mt-1 text-[12px] text-muted">
                  It re-prices the stripboard simulation and the day-operations strip, and nothing else. The options,
                  scores and rupee figures on this page were produced under {enforcedPack.name} and are not regenerated
                  by this selector.
                </p>
                {packDiff(enforcedPack, whatIfPack).length > 0 && (
                  <ul className="mt-1.5 text-[12px] text-muted space-y-0.5">
                    {packDiff(enforcedPack, whatIfPack).map(([label, enforcedValue, whatIfValue]) => (
                      <li key={label}>
                        <b className="text-fg">{label}</b> — <span className="mono">{enforcedValue}</span> in force,{" "}
                        <span className="mono text-warn">{whatIfValue}</span> under the what-if
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>
          <ConstraintsPanel resources={Object.values(data.resources)} dayId={day.id} dayNumber={day.day_number} />
        </div>
      </div>

      <EvidenceDrawer open={drawer} onClose={() => setDrawer(false)} searchRuns={data.search_runs} extractRuns={data.extract_runs} evidence={evidence} focusSearchId={focus} runId={run?.id} title={`Evidence · Day ${day.day_number}`} onExtracted={() => reload()} />
    </div>
  );
}

const prettyDayId = (d: string) => (/^day_\d+$/.test(d) ? `Day ${d.slice(4)}` : d);

/**
 * The hard windows the validator holds this day's schedule against. An empty list is not a quiet
 * list — it is the reason a day can have its own seeded schedule rejected — so it says which days
 * the windows on file *do* name.
 */
function ConstraintsPanel({ resources, dayId, dayNumber }: { resources: Resource[]; dayId: string; dayNumber: number }) {
  const bookable = resources.filter((r) => ["CAST", "LOCATION", "EQUIPMENT"].includes(r.type));
  const onThisDay = bookable.filter((r) => r.availability.some((a) => a.shoot_day_id === dayId));
  const elsewhere = [...new Set(bookable.flatMap((r) => r.availability.map((a) => a.shoot_day_id)).filter((d): d is string => !!d && d !== dayId))].sort();
  const windowless = bookable.filter((r) => r.availability.length === 0);
  return (
    <section className="card p-4">
      <div className="flex items-center gap-2 flex-wrap">
        <Kicker>Constraints on this day</Kicker>
        {onThisDay.length === 0 && <span className="chip chip-warn">none declared</span>}
      </div>
      {onThisDay.length === 0 ? (
        <div className="mt-2 text-[12px] space-y-1.5">
          <p className="text-muted">
            No cast member, location or piece of equipment on this production declares an availability window for Day {dayNumber}.
            {elsewhere.length > 0 && <> Every window on file names {elsewhere.map(prettyDayId).join(elsewhere.length === 2 ? " or " : ", ")}.</>}
          </p>
          <p className="text-dim">
            Availability is a hard constraint: a resource with no window for a day has nothing the validator can hold a schedule against, and reads to it as
            unavailable here. That is a gap in the production data, not a quiet day — anything the rescue workflow proposes for Day {dayNumber} will say so.
          </p>
        </div>
      ) : (
        <ul className="mt-2 text-[12px] space-y-1">
          {onThisDay.map((r) => {
            const a = r.availability.find((x) => x.shoot_day_id === dayId)!;
            return (
              <li key={r.id} className="flex gap-2">
                <span className="chip chip-dim w-24 justify-center">{r.type}</span>
                <span className="flex-1 truncate" title={r.name}>
                  {r.cast_number !== null && (
                    <span className="mono text-dim mr-1.5" title="Cast number — the same key on the board, the call sheet, the DOOD and the dispatch.">
                      {r.cast_number}
                    </span>
                  )}
                  {r.name}
                </span>
                <span className="mono text-muted">{a.start}–{a.end}</span>
                {r.weather_sensitive && <span className="chip chip-warn">wx</span>}
              </li>
            );
          })}
        </ul>
      )}
      {windowless.length > 0 && (
        <p className="mt-2 pt-2 border-t border-line text-[11px] text-dim">
          No window on any day: {windowless.map((r) => r.name).join(", ")}.
        </p>
      )}
    </section>
  );
}

/**
 * Report something the fixtures do not cover.
 *
 * The form used to send four fields — type, title, and a window — and nothing else. `scene_exposed`
 * has exactly four branches that can return true and every one of them needs `affects_exteriors`, a
 * named resource or a named location, so six of the seven types here were guaranteed no-ops: the
 * impact panel reported nothing affected and the engine offered a repack anyway, and for TRANSPORT
 * and REGULATORY it spent a real Parallel search verifying the report first. Only WEATHER, which
 * sets `affects_exteriors` on its own, ever bit.
 *
 * So the form now asks what a 1st AD would have said in the same breath as "the truck is late" —
 * which truck. The server refuses the empty shape outright; this is the half that makes that refusal
 * something a producer never has to see.
 */
function ManualDisruption({
  onSubmit,
  disabled,
  onDay,
}: {
  onSubmit: (b: Record<string, unknown>) => void;
  disabled: boolean;
  /** Only what this day has on it — cast, equipment and locations its own strips call. A picker
   *  listing the whole production would offer resources whose selection could not move a strip. */
  onDay: Resource[];
}) {
  const [type, setType] = useState("WEATHER");
  const [title, setTitle] = useState("");
  const [start, setStart] = useState("13:00");
  const [end, setEnd] = useState("17:00");
  const [affected, setAffected] = useState<string[]>([]);

  const weather = type === "WEATHER";
  const isLocation = (rid: string) => onDay.find((r) => r.id === rid)?.type === "LOCATION";
  const locations = onDay.filter((r) => r.type === "LOCATION");
  const others = onDay.filter((r) => r.type !== "LOCATION");
  const toggle = (rid: string) => setAffected((prev) => (prev.includes(rid) ? prev.filter((x) => x !== rid) : [...prev, rid]));
  // Weather names its own exposure; everything else has to name something, or the report cannot
  // reach a scene and the server will say so.
  const ready = !!title.trim() && (weather || affected.length > 0);

  return (
    <div className="mt-3 space-y-2">
      <div className="grid gap-2 md:grid-cols-[160px_1fr_90px_90px_auto] items-center">
        <select value={type} onChange={(e) => { setType(e.target.value); setAffected([]); }} className="bg-elev border border-line rounded px-2 py-2 text-sm">
          {["WEATHER", "CAST_UNAVAILABLE", "LOCATION_UNAVAILABLE", "EQUIPMENT_FAILURE", "TRANSPORT", "REGULATORY", "OTHER"].map((t) => <option key={t}>{t}</option>)}
        </select>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What happened? e.g. Thunderstorm warning 14:00–16:00" className="bg-elev border border-line rounded px-3 py-2 text-sm" />
        <input value={start} onChange={(e) => setStart(e.target.value)} className="bg-elev border border-line rounded px-2 py-2 text-sm mono" />
        <input value={end} onChange={(e) => setEnd(e.target.value)} className="bg-elev border border-line rounded px-2 py-2 text-sm mono" />
        <button
          className="btn"
          disabled={disabled || !ready}
          title={ready ? undefined : weather ? "Say what happened" : "Say what happened, and what it takes out"}
          onClick={() =>
            onSubmit({
              type,
              title,
              description: title,
              window_start: start,
              window_end: end,
              affects_exteriors: weather,
              affects_resource_ids: affected.filter((rid) => !isLocation(rid)),
              affects_location_ids: affected.filter(isLocation),
              dry_out_minutes: weather ? 30 : 0,
            })
          }
        >
          Report
        </button>
      </div>
      {weather ? (
        <p className="text-[11px] text-dim">
          Weather is held against every exterior scheduled inside the window, plus a 30-minute dry-out. Nothing to name.
        </p>
      ) : (
        <div className="rounded-lg border border-line p-2.5">
          <div className="text-[11px] uppercase tracking-wider text-dim mb-1.5">
            What does it take out? <span className="normal-case tracking-normal text-muted">— a disruption that names nothing cannot reach a scene</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {[...others, ...locations].map((r) => (
              <button
                key={r.id}
                onClick={() => toggle(r.id)}
                className={`chip ${affected.includes(r.id) ? "chip-warn" : "chip-dim"}`}
                title={`${r.type.toLowerCase()} · ${r.name}`}
              >
                {r.name.split(" (")[0].split(" — ")[0]}
              </button>
            ))}
            {onDay.length === 0 && <span className="text-[12px] text-dim">Nothing on this day is bookable, so only weather can be reported against it.</span>}
          </div>
        </div>
      )}
    </div>
  );
}
