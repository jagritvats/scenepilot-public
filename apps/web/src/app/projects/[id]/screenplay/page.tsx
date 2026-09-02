"use client";

import { use, useCallback, useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { api, inr, type CastDOODEntry, type DoodDelta, type DoodTotals, type DoodView, type ParsedSceneData } from "@/lib/api";
import { Empty, Kicker, Spinner } from "@/components/ui";
import { useDismissOnEscape } from "@/lib/useDismiss";
import { watchHash } from "@/lib/useHashScroll";

const HERO_FOUNTAIN_SAMPLE = `Title: Project Nightfall
Credit: Written by
Author: ScenePilot Operations
Draft date: 2026-08-29

EXT. MUMBAI ROOFTOP — SUNSET #42#

A motorcycle tears across adjoining rooftops.
A drone follows while fireworks explode over the skyline.

AARAV
(into helmet radio)
Package is secured. Exfil route is hot!

Rain begins as the rider jumps to an adjacent building. The tires skid dangerously on the wet concrete.

INT. APARTMENT KITCHEN — MORNING #27#

Zoya sifts through burner phones on the granite counter.

ZOYA
(whispering to herself)
He didn't make the call.

She hides the SIM card inside the spice container.

EXT. MARKET STREET — DAY #48#

Inspector Dalvi pushes through a dense crowd of fruit vendors and shoppers.
Sirens wail in the distance.

DALVI
Block the southern exit! Nobody leaves this bazaar!

Sixty background extras scatter in panic as vegetable crates collapse.

EXT. SERVICE ALLEY — DAY #31#

Steam rises from kitchen vents. Aarav pulls Zoya into the shadow behind an oil drum.

AARAV
Keep your head down. They've tapped the local towers.

INT. APARTMENT — DAY #19#

Dalvi stands by the shattered window overlooking the alley.

DALVI
Tell me where he hid the ledger, Zoya. Before the rain washes everything away.
`;

/**
 * The 32 breakdown categories, coloured by the department that has to deliver the element — the way
 * a paper breakdown sheet is banded — rather than by 32 unrelated hues nobody can hold in their head.
 *
 * The category names are `BREAKDOWN_CATEGORIES` in
 * `services/agent/scenepilot/domain/breakdown_models.py`, which is the only list of them: the Gemini
 * output schema and the prompt are built from that tuple too. A category added there and not given a
 * department here falls to the neutral tone below and still renders its own name, so the failure mode
 * is a grey chip, never a missing one.
 */
const DEPARTMENTS: { id: string; label: string; tone: string; categories: string[] }[] = [
  {
    id: "performers",
    label: "Cast & background",
    tone: "bg-blue-500/15 text-blue-300 border-blue-500/30",
    categories: ["CAST", "BACKGROUND_ATMOSPHERE", "EXTRAS", "STAND_INS"],
  },
  {
    id: "stunts",
    label: "Stunts",
    tone: "bg-rose-500/15 text-rose-300 border-rose-500/40 font-semibold",
    categories: ["STUNTS", "STUNT_RIGGING"],
  },
  {
    id: "art",
    label: "Art, props & set",
    tone: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    categories: ["PROPS", "SET_DRESSING", "GREENERY", "ART_DEPARTMENT", "VEHICLES", "ANIMALS", "ANIMAL_WRANGLER", "LIVESTOCK"],
  },
  {
    id: "performer_facing",
    label: "Wardrobe, hair & makeup",
    tone: "bg-pink-500/15 text-pink-300 border-pink-500/30",
    categories: ["WARDROBE", "MAKEUP", "HAIR", "SPECIAL_EFFECTS_MAKEUP"],
  },
  {
    id: "effects",
    label: "Effects",
    tone: "bg-violet-500/15 text-violet-300 border-violet-500/30",
    categories: ["SFX", "MECHANICAL_EFFECTS", "OPTICAL_EFFECTS", "VFX"],
  },
  {
    id: "technical",
    label: "Camera, lighting & sound",
    tone: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    categories: ["CAMERA", "SPECIAL_EQUIPMENT", "LIGHTING", "SOUND", "MUSIC"],
  },
  {
    id: "logistics",
    label: "Logistics & admin",
    tone: "bg-zinc-500/15 text-zinc-300 border-zinc-500/40",
    categories: ["SECURITY", "ADDITIONAL_LABOR", "MISCELLANEOUS", "NOTES"],
  },
  {
    // Its own band, not folded into logistics: SAFETY is where a scene's stop conditions attach, and
    // it is the one category a 1st AD must never scan past.
    id: "safety",
    label: "Safety",
    tone: "bg-red-500/20 text-red-300 border-red-500/50 font-bold",
    categories: ["SAFETY"],
  },
];

const NEUTRAL_TONE = "bg-zinc-800 text-zinc-300 border-zinc-700";
const DEPARTMENT_BY_CATEGORY: Record<string, { id: string; label: string; tone: string }> = Object.fromEntries(
  DEPARTMENTS.flatMap((d) => d.categories.map((c) => [c, { id: d.id, label: d.label, tone: d.tone }]))
);

/**
 * `#dood` in the URL opens the cast matrix, so the project page can link straight at it.
 *
 * The subscription is `watchHash` rather than a bare `hashchange` listener because a `<Link>` to a
 * fragment on the page you are already on navigates through Next's pushState and fires no event at
 * all — the URL would say `#dood` while the screenplay tab stayed up.
 */
const subscribeHash = (onChange: () => void) => watchHash(onChange);

export default function ScreenplayPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const hash = useSyncExternalStore(subscribeHash, () => window.location.hash, () => "");
  /* A tab picked by hand outranks the URL, but only until the URL is asked again — so the pick
   * records the hash it was made against instead of being cleared from an effect. Following a link
   * to `#dood` from this page has to beat a tab clicked a minute ago, or the link reads as dead. */
  const [picked, setPicked] = useState<{ tab: "screenplay" | "dood"; atHash: string } | null>(null);
  const activeTab = picked?.atHash === hash ? picked.tab : hash === "#dood" ? "dood" : "screenplay";
  const [scenes, setScenes] = useState<ParsedSceneData[]>([]);
  const [selectedIdx, setSelectedIdx] = useState<number>(0);
  const [doodEntries, setDoodEntries] = useState<CastDOODEntry[]>([]);
  const [shootDays, setShootDays] = useState<{ id: string; day_number: number; date: string }[]>([]);
  /* The codes the engine actually emits, and the ones it does not — sent by the API rather than
   * written here, so the legend cannot claim a code the matrix never produces. */
  const [doodCodes, setDoodCodes] = useState<Record<string, string>>({});
  const [unmodelledCodes, setUnmodelledCodes] = useState<Record<string, string>>({});
  const [doodDelta, setDoodDelta] = useState<DoodDelta | null>(null);
  const [doodTotals, setDoodTotals] = useState<DoodTotals | null>(null);
  const [unlinkedCharacters, setUnlinkedCharacters] = useState<DoodView["unlinked_characters"]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [uploading, setUploading] = useState<boolean>(false);
  const [breakingDown, setBreakingDown] = useState<boolean>(false);
  const [showUploadModal, setShowUploadModal] = useState<boolean>(false);
  const [customText, setCustomText] = useState<string>("");
  useDismissOnEscape(showUploadModal, () => setShowUploadModal(false));

  /* One place the DOOD payload is unpacked. It was unpacked in two, and the two had already
   * drifted — a field added to the response reached the matrix on load and not after a breakdown. */
  const applyDood = useCallback((res: DoodView) => {
    setDoodEntries(res.entries || []);
    setShootDays(res.shoot_days || []);
    setDoodCodes(res.codes || {});
    setUnmodelledCodes(res.unmodelled_codes || {});
    setDoodTotals(res.totals ?? null);
    setUnlinkedCharacters(res.unlinked_characters || []);
    setDoodDelta(res.delta ?? null);
  }, []);

  const refreshData = useCallback(async () => {
    try {
      setLoading(true);
      const [scenesRes, doodRes] = await Promise.all([
        api.getScreenplayScenes(id).catch(() => ({ scenes: [], count: 0 })),
        api.getDOOD(id).catch(
          (): DoodView => ({
            project_id: id, entries: [], shoot_days: [], codes: {}, unmodelled_codes: {},
            totals: { performers: 0, performers_engaged: 0, work_days: 0, hold_days: 0, engaged_days: 0, hold_cost_inr: null, unpriced_performers: [], labor_pack: "", drop_pickup_minimum_days: null, releasable_days: 0 },
            unlinked_characters: [], delta: null,
          })
        ),
      ]);
      setScenes(scenesRes.scenes || []);
      applyDood(doodRes);
    } finally {
      setLoading(false);
    }
  }, [id, applyDood]);

  useEffect(() => {
    void refreshData();
  }, [refreshData]);

  const handleUpload = async (contentToUpload: string) => {
    setUploading(true);
    try {
      await api.uploadScreenplay(id, contentToUpload, "auto", true);
      setShowUploadModal(false);
      await refreshData();
      setSelectedIdx(0);
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  const handleRunBreakdown = async (sceneNumber: string) => {
    setBreakingDown(true);
    try {
      const res = await api.breakdownSceneElements(id, `sc_${sceneNumber}`);
      // Update local scene elements, stop conditions, and continuity notes
      setScenes((prev) =>
        prev.map((s) =>
          s.scene_number === sceneNumber
            ? {
                ...s,
                elements: res.elements || [],
                stop_conditions: res.stop_conditions || [],
                continuity_notes: res.continuity_notes || [],
              }
            : s
        )
      );
      // Refresh DOOD matrix in background
      api.getDOOD(id).then((doodRes) => {
        applyDood(doodRes);
      }).catch(() => {});
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    } finally {
      setBreakingDown(false);
    }
  };

  const currentScene = scenes[selectedIdx];

  const categoryColor = (cat: string) => DEPARTMENT_BY_CATEGORY[cat]?.tone || NEUTRAL_TONE;

  // Which departments this scene actually calls — the legend for the colours below, drawn from the
  // elements on screen rather than printed as a static key of eight bands most scenes never use.
  const departmentsCalled = DEPARTMENTS.map((d) => ({
    ...d,
    count: (currentScene?.elements || []).filter((e) => DEPARTMENT_BY_CATEGORY[e.category]?.id === d.id).length,
  })).filter((d) => d.count > 0);
  const unbanded = (currentScene?.elements || []).filter((e) => !DEPARTMENT_BY_CATEGORY[e.category]).length;

  // A day nobody works and nobody holds. Blank down the whole column reads as missing data unless the
  // matrix says otherwise — and the reason is on the schedule, not in the matrix.
  const blankDays = shootDays.filter((d) => doodEntries.every((c) => !c.day_status[d.id] || c.day_status[d.id] === "-"));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Link href={`/projects/${id}`} className="text-muted hover:text-foreground text-xs uppercase tracking-wider">
              ← Back to Project
            </Link>
          </div>
          <Kicker className="mt-1">Creative Intelligence & Breakdown</Kicker>
          <h1 className="display text-4xl font-bold mt-1">Screenplay Studio</h1>
          <p className="text-sm text-muted mt-1 max-w-2xl">
            Automated Fountain/FDX ingestion, eighths-of-a-page estimation, Gemini extraction across 32 element categories, and Day-Out-Of-Days (DOOD) cast matrix.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => handleUpload(HERO_FOUNTAIN_SAMPLE)}
            disabled={uploading}
            className="btn btn-primary text-xs"
          >
            {uploading ? <Spinner label="Ingesting..." /> : "Load Hero Screenplay (Fountain)"}
          </button>
          <button
            onClick={() => setShowUploadModal(true)}
            className="btn text-xs"
          >
            Upload / Paste Script
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-2 border-b border-line pb-2">
        <button
          onClick={() => setPicked({ tab: "screenplay", atHash: hash })}
          className={`px-4 py-2 text-sm font-medium rounded transition ${
            activeTab === "screenplay" ? "bg-card border border-accent text-foreground" : "text-muted hover:text-foreground"
          }`}
        >
          Screenplay Reader & AI Breakdown ({scenes.length} scenes)
        </button>
        <button
          onClick={() => setPicked({ tab: "dood", atHash: hash })}
          className={`px-4 py-2 text-sm font-medium rounded transition ${
            activeTab === "dood" ? "bg-card border border-accent text-foreground" : "text-muted hover:text-foreground"
          }`}
        >
          Day-Out-Of-Days (DOOD) Matrix ({doodEntries.length} cast)
        </button>
      </div>

      {loading && scenes.length === 0 && (
        <div className="card p-12 text-center shimmer">
          <Spinner label="Loading Screenplay Studio..." />
        </div>
      )}

      {/* Screenplay & Breakdown Tab */}
      {activeTab === "screenplay" && (
        <div>
          {scenes.length === 0 ? (
            <Empty
              title="No Screenplay Loaded"
              hint="Load the Project Nightfall hero Fountain script to view scenes, 8ths, and element breakdowns."
              action={
                <button
                  onClick={() => handleUpload(HERO_FOUNTAIN_SAMPLE)}
                  disabled={uploading}
                  className="btn btn-primary"
                >
                  Load Hero Screenplay
                </button>
              }
            />
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
              {/* Scene Sidebar List */}
              <div className="lg:col-span-4 card p-3 space-y-2 max-h-[780px] overflow-y-auto">
                <div className="text-[11px] font-semibold uppercase tracking-wider text-dim px-2 mb-1">
                  Screenplay Scenes ({scenes.length})
                </div>
                {scenes.map((s, idx) => (
                  <button
                    key={s.scene_number}
                    onClick={() => setSelectedIdx(idx)}
                    className={`w-full text-left p-2.5 rounded border transition block ${
                      idx === selectedIdx
                        ? "border-accent bg-accent/10"
                        : "border-line/60 hover:border-line bg-card/40"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="display font-bold text-sm text-foreground">
                        SC {s.scene_number}
                      </span>
                      <span className="mono text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300">
                        {s.eighths}/8 pg
                      </span>
                    </div>
                    <div className="text-xs font-semibold text-muted truncate mt-0.5">
                      {s.heading}
                    </div>
                    <div className="flex items-center gap-2 text-[10px] text-dim mt-1">
                      <span>{s.int_ext}</span>
                      <span>·</span>
                      <span>{s.time_of_day}</span>
                      {s.elements && s.elements.length > 0 && (
                        <>
                          <span>·</span>
                          <span className="text-ok font-medium">
                            {s.elements.length} elements
                          </span>
                        </>
                      )}
                    </div>
                  </button>
                ))}
              </div>

              {/* Reader & Breakdown View */}
              <div className="lg:col-span-8 space-y-4">
                {currentScene && (
                  <div className="card p-6 space-y-5">
                    {/* Scene Metadata Header */}
                    <div className="flex items-start justify-between flex-wrap gap-3 border-b border-line pb-4">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="display text-3xl font-bold text-accent">
                            {currentScene.scene_number}
                          </span>
                          <h2 className="display text-2xl font-bold">
                            {currentScene.heading}
                          </h2>
                        </div>
                        <div className="flex items-center gap-3 text-xs text-muted mt-1">
                          <span className="mono font-semibold text-foreground">
                            Length: {currentScene.eighths}/8 page
                          </span>
                          <span>·</span>
                          <span>Pages {currentScene.page_start}–{currentScene.page_end}</span>
                          <span>·</span>
                          <span>{currentScene.int_ext}</span>
                          <span>·</span>
                          <span>{currentScene.time_of_day}</span>
                        </div>
                      </div>

                      <button
                        onClick={() => handleRunBreakdown(currentScene.scene_number)}
                        disabled={breakingDown}
                        className="btn btn-primary text-xs"
                      >
                        {breakingDown ? (
                          <Spinner label="Analyzing..." />
                        ) : (
                          `Run AI Breakdown (Sc ${currentScene.scene_number})`
                        )}
                      </button>
                    </div>

                    {/* Formatted Screenplay Reader */}
                    <div className="bg-zinc-950/80 border border-line rounded p-6 font-mono text-[13px] leading-relaxed max-h-[340px] overflow-y-auto space-y-3">
                      <div className="font-bold text-accent tracking-wider">
                        {currentScene.heading}
                      </div>

                      {currentScene.action_text && (
                        <p className="text-zinc-300 whitespace-pre-line">
                          {currentScene.action_text}
                        </p>
                      )}

                      {!currentScene.action_text && (currentScene.dialogue?.length ?? 0) === 0 && (
                        <p className="text-dim">
                          The parser found this slug line and nothing under it — no action, no dialogue. Everything below reads the scene text, so it has nothing to work from.
                        </p>
                      )}

                      {currentScene.dialogue && currentScene.dialogue.length > 0 && (
                        <div className="space-y-3 pt-2">
                          {currentScene.dialogue.map((d, dIdx) => (
                            <div key={dIdx} className="space-y-0.5">
                              <div className="text-center font-bold text-zinc-100 tracking-wider">
                                {d.character}
                              </div>
                              {d.parenthetical && (
                                <div className="text-center text-zinc-400 italic text-[11px]">
                                  ({d.parenthetical})
                                </div>
                              )}
                              <div className="max-w-md mx-auto text-zinc-200">
                                {d.text}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Extracted Element Breakdown */}
                    <div className="space-y-3 pt-2">
                      <div className="flex items-center justify-between">
                        <h3 className="display text-lg font-bold">
                          Extracted Production Elements ({currentScene.elements?.length || 0})
                        </h3>
                        <span className="text-[11px] text-muted">
                          Semantically extracted by CreativeBreakdownAgent
                        </span>
                      </div>

                      {departmentsCalled.length > 0 && (
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="text-[10px] uppercase tracking-[0.14em] text-dim">Departments called</span>
                          {departmentsCalled.map((d) => (
                            <span key={d.id} className={`px-1.5 py-0.5 rounded border text-[10px] uppercase tracking-wider ${d.tone}`}>
                              {d.label} · {d.count}
                            </span>
                          ))}
                          {unbanded > 0 && (
                            <span className={`px-1.5 py-0.5 rounded border text-[10px] uppercase tracking-wider ${NEUTRAL_TONE}`}>
                              unbanded · {unbanded}
                            </span>
                          )}
                        </div>
                      )}

                      {(!currentScene.elements || currentScene.elements.length === 0) ? (
                        <div className="p-4 rounded border border-dashed border-line text-xs text-muted space-y-1.5">
                          <div className="text-foreground font-semibold">No breakdown has run for Sc {currentScene.scene_number}.</div>
                          <p>
                            Parsing the script is free and has already happened — the slug line, the {currentScene.eighths}/8 page count and the text above all come from
                            it. The 32 element categories do not: they come from a Gemini pass over this scene that has to be asked for, one scene at a time.
                          </p>
                          <p className="text-dim">
                            Run it and this space fills with the props, vehicles, stunts, effects and equipment named or implied in the text — each banded by the
                            department that has to deliver it — plus the scene&apos;s safety stop conditions and its continuity resets.
                          </p>
                        </div>
                      ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                          {currentScene.elements.map((elem) => (
                            <div
                              key={elem.id}
                              className={`p-2.5 rounded border text-xs flex items-start gap-2.5 ${categoryColor(
                                elem.category
                              )}`}
                            >
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="font-bold truncate">{elem.name}</span>
                                  {elem.count > 1 && (
                                    <span className="mono text-[10px] px-1 rounded bg-black/40">
                                      ×{elem.count}
                                    </span>
                                  )}
                                  {elem.implied && (
                                    <span className="text-[9px] uppercase tracking-wider px-1 rounded bg-black/30 text-dim">
                                      implied
                                    </span>
                                  )}
                                </div>
                                {elem.description && (
                                  <p className="text-[11px] opacity-80 mt-0.5 truncate">
                                    {elem.description}
                                  </p>
                                )}
                                {elem.safety_notes && (
                                  <p className="text-[10px] text-red-300 font-semibold mt-1">
                                    ⚠️ {elem.safety_notes}
                                  </p>
                                )}
                              </div>
                              <span className="text-[9px] uppercase tracking-wider font-semibold opacity-70 shrink-0">
                                {elem.category}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Mandatory Safety Stop Conditions */}
                      {currentScene.stop_conditions && currentScene.stop_conditions.length > 0 && (
                        <div className="p-3.5 rounded-lg bg-red-950/30 border border-red-500/40 space-y-2 mt-3">
                          <div className="flex items-center gap-2">
                            <span className="w-2 h-2 rounded-full bg-red-400 animate-pulse" />
                            <span className="text-xs font-bold text-red-400 uppercase tracking-wide">
                              Mandatory Safety Stop Conditions ({currentScene.stop_conditions.length})
                            </span>
                          </div>
                          <ul className="list-disc pl-4 text-xs text-red-200/90 space-y-1">
                            {currentScene.stop_conditions.map((sc, idx) => (
                              <li key={idx}>{sc}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Continuity & Reset Notes */}
                      {currentScene.continuity_notes && currentScene.continuity_notes.length > 0 && (
                        <div className="p-3.5 rounded-lg bg-sky-950/30 border border-sky-500/40 space-y-2 mt-3">
                          <div className="flex items-center gap-2">
                            <span className="w-2 h-2 rounded-full bg-sky-400" />
                            <span className="text-xs font-bold text-sky-400 uppercase tracking-wide">
                              Continuity & Turnaround Reset Notes ({currentScene.continuity_notes.length})
                            </span>
                          </div>
                          <ul className="list-disc pl-4 text-xs text-sky-200/90 space-y-1">
                            {currentScene.continuity_notes.map((cn, idx) => (
                              <li key={idx}>{cn}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Day-Out-Of-Days (DOOD) Tab */}
      {activeTab === "dood" && (
        <div className="card p-6 space-y-4">
          <div>
            <h2 className="display text-2xl font-bold">Cast Day-Out-Of-Days (DOOD) Matrix</h2>
            <p className="text-sm text-muted mt-0.5">
              Work and hold schedule across all shoot days, in cast-number order — the same number the board, the call
              sheet and the dispatch join on. Union rules require paying hold days (<strong className="text-amber-400">H</strong>) between active calls.
            </p>
          </div>

          {/* What the approved recovery did to the cast schedule. A cost delta in the aggregate is
              an abstraction; a named performer and the rate on their own contract is the sentence a
              producer reacts to — and it is the half of the rain's cost that the schedule hides. */}
          {doodDelta && doodDelta.changes.length > 0 && (
            <div className="card p-4 border-l-4 border-l-amber-500 space-y-2">
              <div className="flex items-baseline gap-2 flex-wrap">
                <Kicker>Before / after the approved recovery</Kicker>
                {doodDelta.headline && <span className="text-sm font-semibold text-amber-300">{doodDelta.headline}</span>}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs text-left">
                  <thead className="text-dim uppercase tracking-wider text-[10px] border-b border-line">
                    <tr>
                      <th className="py-1.5 pr-2 w-8">#</th>
                      <th className="py-1.5 pr-2">Performer</th>
                      <th className="py-1.5 pr-2 text-center">Work</th>
                      <th className="py-1.5 pr-2 text-center">Hold</th>
                      <th className="py-1.5 pr-2 text-right">Day rate</th>
                      <th className="py-1.5 text-right">Added hold cost</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {doodDelta.changes.map((c) => (
                      <tr key={c.cast_id}>
                        <td className="py-1.5 pr-2 mono font-bold">{c.cast_number ?? <span className="text-dim font-normal">—</span>}</td>
                        <td className="py-1.5 pr-2 text-foreground">{c.name}</td>
                        <td className="py-1.5 pr-2 text-center mono">
                          <span className="text-dim">{c.work_days_before}</span>
                          <span className="text-zinc-600 mx-1">→</span>
                          <span className={c.work_days_after < c.work_days_before ? "text-bad font-bold" : "text-ok"}>{c.work_days_after}</span>
                        </td>
                        <td className="py-1.5 pr-2 text-center mono">
                          <span className="text-dim">{c.hold_days_before}</span>
                          <span className="text-zinc-600 mx-1">→</span>
                          <span className={c.hold_days_gained > 0 ? "text-amber-400 font-bold" : "text-dim"}>{c.hold_days_after}</span>
                        </td>
                        <td className="py-1.5 pr-2 text-right mono text-dim">
                          {c.day_rate_inr !== null ? inr(c.day_rate_inr) : <span title={c.unpriced_reason ?? undefined}>not on file</span>}
                        </td>
                        <td className="py-1.5 text-right mono font-bold text-amber-400">
                          {c.added_hold_cost_inr !== null ? inr(c.added_hold_cost_inr) : <span className="text-dim font-normal">—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {doodDelta.unpriced_performers.length > 0 && (
                <p className="text-[11px] text-muted">
                  {doodDelta.unpriced_performers.join(", ")} gained a hold day the production states no rate for, so it is
                  counted above and not priced — the total below is therefore a floor, not the whole cost.
                </p>
              )}
              {doodDelta.total_added_hold_cost_inr !== null && (
                <p className="text-[11px] text-dim">
                  Total added retention across the cast: <b className="text-amber-400">{inr(doodDelta.total_added_hold_cost_inr)}</b>, at each
                  performer&apos;s own contracted rate.
                </p>
              )}
            </div>
          )}

          {doodEntries.length === 0 ? (
            <Empty
              title="No cast to schedule"
              hint="The matrix is built from the production's cast resources and the scenes they are attached to. This production has none of one or the other, so there is no work day, no hold day and nothing to hold against a union rate."
            />
          ) : (
            <div className="overflow-x-auto border border-line rounded">
              <table className="w-full text-xs text-left">
                <thead className="bg-zinc-900/80 text-dim uppercase tracking-wider text-[10px] border-b border-line">
                  <tr>
                    <th
                      className="p-3 font-semibold text-center w-10"
                      title="The performer's cast number — the production's billing order, and what this matrix is sorted by."
                    >
                      #
                    </th>
                    <th className="p-3 font-semibold">Cast Member</th>
                    <th className="p-3 font-semibold text-center">Work</th>
                    <th className="p-3 font-semibold text-center">Hold</th>
                    <th
                      className="p-3 font-semibold text-center"
                      title="First call to last, inclusive — the days the production is engaged for. Work + hold, which is the comparison a UPM is making."
                    >
                      Total
                    </th>
                    {shootDays.map((d) => (
                      <th key={d.id} className="p-3 font-semibold text-center border-l border-line/40">
                        <div>DAY {d.day_number}</div>
                        <div className="text-[9px] text-dim">{d.date.slice(5)}</div>
                      </th>
                    ))}
                    <th className="p-3 font-semibold">Advisory</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {doodEntries.map((c) => (
                    <tr key={c.cast_id} className="hover:bg-zinc-900/40 transition">
                      <td className="p-3 text-center mono font-bold text-foreground">
                        {c.cast_number ?? (
                          <span
                            className="text-dim font-normal"
                            title="This performer carries no cast number, so the matrix lists them after every numbered one rather than handing them a position that would read as a number they do not have."
                          >
                            —
                          </span>
                        )}
                      </td>
                      <td className="p-3 font-medium text-foreground whitespace-nowrap">
                        {c.name}
                      </td>
                      <td className="p-3 text-center mono font-bold text-ok">
                        {c.total_work_days}
                      </td>
                      <td className={`p-3 text-center mono font-bold ${c.total_hold_days > 0 ? "text-amber-400" : "text-dim"}`}>
                        {c.total_hold_days}
                      </td>
                      <td
                        className="p-3 text-center mono font-bold text-foreground"
                        title={c.day_rate_inr ? `${c.total_engaged_days} day(s) engaged at ${inr(c.day_rate_inr)}/day.` : "No day rate is on file for this performer."}
                      >
                        {c.total_engaged_days}
                      </td>
                      {shootDays.map((d) => {
                        const code = c.day_status[d.id] || "-";
                        const isHold = code === "H";
                        const isWork = code.includes("W");
                        return (
                          <td
                            key={d.id}
                            className={`p-3 text-center mono font-bold border-l border-line/40 ${
                              isHold
                                ? "bg-amber-500/10 text-amber-400"
                                : isWork
                                ? "bg-blue-500/10 text-blue-400"
                                : "text-zinc-600"
                            }`}
                          >
                            {code}
                          </td>
                        );
                      })}
                      <td className="p-3 text-xs" title={c.drop_pickup?.note || undefined}>
                        {/* A hold day is only ever priced at the performer's own contracted rate.
                            Without one the days are still counted — that is a fact about the
                            schedule — and the cost is reported as absent rather than defaulted. */}
                        {c.hold_day_cost_warning ? (
                          <span className="text-amber-400 font-semibold" title={c.warning_message || ""}>
                            ⚠️ {c.total_hold_days} hold days
                            {c.estimated_hold_cost_inr !== null ? ` (~${inr(c.estimated_hold_cost_inr)})` : " · no day rate on file, not priced"}
                          </span>
                        ) : c.total_hold_days > 0 ? (
                          <span className="text-muted">
                            {c.total_hold_days} hold day{c.total_hold_days === 1 ? "" : "s"}
                            {c.estimated_hold_cost_inr !== null ? ` · ~${inr(c.estimated_hold_cost_inr)}` : " · no day rate on file, not priced"}
                          </span>
                        ) : c.total_work_days === 0 ? (
                          <span className="text-dim">Not on the schedule — no scene they are attached to is on a shoot day</span>
                        ) : (
                          <span className="text-dim">No hold days between calls</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
                {/* The bottom line, and the ratio between its two halves. A cast worked 8 days out
                    of 10 engaged is a cast being paid a fifth of the time to do nothing, and no
                    individual row above says that. */}
                {doodTotals && doodTotals.performers_engaged > 0 && (
                  <tfoot className="border-t-2 border-line bg-zinc-900/60">
                    <tr className="text-xs">
                      <td className="p-3" />
                      <td className="p-3 font-semibold text-dim uppercase tracking-wider text-[10px]">
                        {doodTotals.performers_engaged} performer{doodTotals.performers_engaged === 1 ? "" : "s"} engaged
                      </td>
                      <td className="p-3 text-center mono font-bold text-ok">{doodTotals.work_days}</td>
                      <td className={`p-3 text-center mono font-bold ${doodTotals.hold_days > 0 ? "text-amber-400" : "text-dim"}`}>
                        {doodTotals.hold_days}
                      </td>
                      <td className="p-3 text-center mono font-bold text-foreground">{doodTotals.engaged_days}</td>
                      <td colSpan={shootDays.length} className="p-3 text-dim">
                        {doodTotals.work_days} of {doodTotals.engaged_days} engaged days are shooting days
                      </td>
                      <td className="p-3">
                        {doodTotals.hold_cost_inr !== null ? (
                          <span className="text-amber-400 font-semibold">
                            {inr(doodTotals.hold_cost_inr)} in hold days
                            {doodTotals.unpriced_performers.length > 0 && (
                              <span
                                className="text-dim font-normal"
                                title={`${doodTotals.unpriced_performers.join(", ")} carry no day rate, so their hold days are counted here and not priced. This figure is a floor.`}
                              >
                                {" "}
                                + {doodTotals.unpriced_performers.length} unpriced
                              </span>
                            )}
                          </span>
                        ) : (
                          <span className="text-dim">no hold cost on file</span>
                        )}
                      </td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}

          {/* Drop and pickup: the only lever a production has against hold-day cost, and whether it
              exists at all is a term of the agreement rather than a scheduling choice. */}
          {doodTotals && doodTotals.hold_days > 0 && (
            <div className="card p-3 text-[11px] flex items-start gap-3 flex-wrap">
              <span className={`chip mt-0.5 ${doodTotals.releasable_days > 0 ? "chip-ok" : "chip-dim"}`}>
                D / P {doodTotals.releasable_days > 0 ? "available" : "unavailable"}
              </span>
              <p className="text-muted flex-1 min-w-[280px]">
                {doodTotals.drop_pickup_minimum_days === null ? (
                  <>
                    <b className="text-foreground">{doodTotals.labor_pack}</b> models no drop-and-pickup provision, so all{" "}
                    <b className="text-amber-400">{doodTotals.hold_days} hold day{doodTotals.hold_days === 1 ? "" : "s"}</b> on this
                    matrix are paid. Under this agreement a performer engaged across a gap is held through it, and no amount of
                    rescheduling changes that — which is why the hold column is a budget line and not a scheduling artefact.
                  </>
                ) : doodTotals.releasable_days > 0 ? (
                  <>
                    <b className="text-ok">{doodTotals.releasable_days}</b> of {doodTotals.hold_days} hold day(s) sit in a run long
                    enough to release under <b className="text-foreground">{doodTotals.labor_pack}</b>&apos;s{" "}
                    {doodTotals.drop_pickup_minimum_days}-day drop-and-pickup. Advisory only: re-engaging a released performer is a
                    producer&apos;s decision, so nothing here is netted off the cost above.
                  </>
                ) : (
                  <>
                    <b className="text-foreground">{doodTotals.labor_pack}</b> allows a drop and pickup only from{" "}
                    {doodTotals.drop_pickup_minimum_days} days. No hold run on this schedule is that long, so none of the{" "}
                    {doodTotals.hold_days} hold day(s) can be released.
                  </>
                )}
              </p>
            </div>
          )}

          {/* A casting gap, reported as one. This used to be folded silently into the matrix as work
              days — a day on a UPM's budget asserted from a language model's read of the draft. */}
          {unlinkedCharacters.length > 0 && (
            <div className="card p-3 text-[11px] flex items-start gap-3 flex-wrap">
              <span className="chip chip-warn mt-0.5">not cast</span>
              <p className="text-muted flex-1 min-w-[280px]">
                The breakdown names{" "}
                {unlinkedCharacters.map((c, i) => (
                  <span key={c.character}>
                    {i > 0 && ", "}
                    <b className="text-foreground">{c.character}</b>{" "}
                    <span className="text-dim">(Sc {c.scenes.join(", ")}{c.scheduled ? ", scheduled" : ""})</span>
                  </span>
                ))}{" "}
                {unlinkedCharacters.length === 1 ? "as a character" : "as characters"} with no performer attached, so{" "}
                {unlinkedCharacters.length === 1 ? "it does" : "they do"} not appear above. The matrix counts the production&apos;s own
                casting only — a work day inferred from a draft is a day on the budget nobody scheduled.
              </p>
            </div>
          )}

          {doodEntries.length > 0 && (
            <div className="space-y-1.5 text-[11px]">
              {/* Both lists come from the API. The engine is the authority on what it emits, so a
                  code can never appear in this legend that the matrix does not actually produce. */}
              <div className="flex items-center gap-x-3 gap-y-1 flex-wrap text-dim">
                <span className="uppercase tracking-[0.14em]">Codes</span>
                {Object.entries(doodCodes).map(([code, meaning]) => (
                  <span key={code}>
                    <b className={`mono ${code === "H" ? "text-amber-400" : "text-blue-400"}`}>{code}</b>{" "}
                    {meaning.split(" — ")[0].toLowerCase()}
                    {meaning.includes(" — ") && <span className="text-zinc-600"> — {meaning.split(" — ")[1]}</span>}
                  </span>
                ))}
                <span><b className="mono">-</b> outside this performer&apos;s span</span>
              </div>
              {Object.keys(unmodelledCodes).length > 0 && (
                <p className="text-muted">
                  <span className="uppercase tracking-[0.14em] text-dim mr-2">Not on this matrix</span>
                  A full DOOD also carries{" "}
                  {Object.entries(unmodelledCodes).map(([code, why], i, all) => (
                    <span key={code}>
                      <b className="mono text-zinc-500">{code}</b> <span className="text-dim">({why.split(" — ")[1]})</span>
                      {i < all.length - 2 ? ", " : i === all.length - 2 ? " and " : ". "}
                    </span>
                  ))}
                  ScenePilot holds no state behind any of them, so it emits none of them rather than printing a day
                  nobody booked.
                </p>
              )}
              {doodEntries.some((c) => c.cast_number === null) && (
                <p className="text-muted">
                  {doodEntries.filter((c) => c.cast_number === null).length} performer(s) carry no cast number and read{" "}
                  <span className="mono">—</span> under <b>#</b>. They are listed after every numbered performer, by name:
                  an unnumbered performer is not the next number down, and the row order does not imply they are.
                </p>
              )}
              {blankDays.length > 0 && (
                <p className="text-muted">
                  {blankDays.map((d) => `Day ${d.day_number}`).join(" and ")} {blankDays.length === 1 ? "is" : "are"} blank down the whole column: no scene scheduled
                  {blankDays.length === 1 ? " that day" : " those days"} has a performer attached, so nobody is called and nobody is held. An empty cell here is a fact
                  about the schedule, not a gap in the matrix.
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Upload / Paste Modal */}
      {showUploadModal && (
        // The only overlay in the app that closed on neither Escape nor a backdrop click.
        <div
          className="fixed inset-0 bg-black/75 z-50 flex items-center justify-center p-4"
          onClick={() => setShowUploadModal(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Upload or paste a screenplay"
            className="card p-6 w-full max-w-2xl space-y-4 bg-zinc-900 border-accent"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="display text-xl font-bold">Upload / Paste Screenplay</h3>
              <button
                onClick={() => setShowUploadModal(false)}
                aria-label="Close the upload dialog"
                className="text-muted hover:text-foreground text-sm"
              >
                ✕
              </button>
            </div>
            <p className="text-xs text-muted">
              Paste standard Fountain format or Final Draft XML content. Scenes will be automatically extracted, paginated in eighths, and linked to the project.
            </p>
            <textarea
              value={customText}
              onChange={(e) => setCustomText(e.target.value)}
              rows={12}
              placeholder="EXT. MUMBAI ROOFTOP — SUNSET #42#&#10;&#10;A motorcycle tears across adjoining rooftops..."
              className="w-full bg-zinc-950 border border-line rounded p-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent"
            />
            <div className="flex justify-end gap-3 pt-2">
              <button
                onClick={() => setShowUploadModal(false)}
                className="btn text-xs"
              >
                Cancel
              </button>
              <button
                onClick={() => handleUpload(customText)}
                disabled={!customText.trim() || uploading}
                className="btn btn-primary text-xs"
              >
                {uploading ? <Spinner label="Parsing..." /> : "Parse & Import Screenplay"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
