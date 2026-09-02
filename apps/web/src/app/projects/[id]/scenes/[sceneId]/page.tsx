"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import { api, inr, type Evidence, type Project, type Resource, type ScheduleItem, type ShootDay, type RecalledEntry } from "@/lib/api";
import { eighthsLabel } from "@/lib/stripboard";
import { usePoll } from "@/lib/usePoll";
import { ActivityFeed } from "@/components/ActivityFeed";
import { AgentGraph } from "@/components/AgentGraph";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { ParallelUsageStrip } from "@/components/ParallelUsageStrip";
import { Kicker, KindChip, LoadError, Readiness, Spinner, Stamp, StatusChip } from "@/components/ui";

/** One line of the scene sheet. `value` is real state or nothing — never a placeholder that reads like data. */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-[0.14em] text-dim">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

const Unset = ({ children }: { children: React.ReactNode }) => <span className="text-dim">{children}</span>;

export default function ScenePage({ params }: { params: Promise<{ id: string; sceneId: string }> }) {
  const { id, sceneId } = use(params);
  const { data, error, loading, reload } = usePoll(() => api.scene(id, sceneId), (d) => !!d?.run && (d.run.status === "RUNNING" || d.run.status === "PENDING"), 1500);
  const [drawer, setDrawer] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // Putting a planned scene onto a day. The engine recomputes against the target day's live
  // schedule and either writes a ChangeSet or refuses with a sentence, so the control picks a day
  // and never a time — a time typed here would be a proposal the commit path throws away anyway.
  const [placeDay, setPlaceDay] = useState("");
  const [placing, setPlacing] = useState(false);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const [placed, setPlaced] = useState<{ summary: string; dayId: string; dayNumber: number; overtime: number; notes: string[] } | null>(null);
  const [useMemory, setUseMemory] = useState(false);
  const [memoryOn, setMemoryOn] = useState(false);
  // The scene payload knows nothing about where the scene sits in the schedule or what its resource
  // ids name. Both are already on the project, and both are what makes an unplanned scene readable.
  const [project, setProject] = useState<Project | null>(null);
  useEffect(() => {
    api.features().then((f) => setMemoryOn(!!f.features.memory?.enabled)).catch(() => setMemoryOn(false));
  }, []);
  useEffect(() => {
    api.project(id).then((r) => setProject(r.project)).catch(() => setProject(null));
  }, [id]);
  const evidence: Evidence[] = useMemo(() => data?.run?.planning?.evidence || [], [data]);
  const recalled: RecalledEntry[] = useMemo(() => (data as { recalled?: RecalledEntry[] })?.recalled ?? [], [data]);
  const evById = useMemo(() => Object.fromEntries(evidence.map((e) => [e.id, e])), [evidence]);
  if (!data) return loading || !error ? <div className="card p-8 shimmer h-60" /> : <LoadError error={error} missing="Scene not found" />;
  const { scene, plan, run } = data;
  const running = run?.status === "RUNNING" || run?.status === "PENDING";
  const questions = run?.planning?.questions || [];
  const placement: { day: ShootDay; item: ScheduleItem } | null =
    (project?.shoot_days || [])
      .map((d) => ({ day: d, item: d.items.find((i) => i.scene_id === scene.id) }))
      .find((x): x is { day: ShootDay; item: ScheduleItem } => !!x.item) ?? null;
  const resourceById: Record<string, Resource> = Object.fromEntries((project?.resources || []).map((r) => [r.id, r]));
  // A wrapped day refuses a placement outright, so offering one would be the page inviting a click
  // the engine is going to reject.
  const placeableDays = (project?.shoot_days || []).filter((d) => d.status !== "WRAPPED");
  // "Already shot" is read off the production, never assumed from a synopsis.
  const wrapped = !!placement && (placement.day.status === "WRAPPED" || placement.item.status === "COMPLETED");
  const hasScript = scene.script_text.trim().length > 0;
  const setLocation = scene.location_id ? resourceById[scene.location_id] : undefined;
  // A performer is listed the way every other document on this production keys them: the cast number
  // against the name, in billing order, with an unnumbered performer last and unnumbered on screen.
  const castMembers = scene.cast_ids
    .map((c) => ({ id: c, name: resourceById[c]?.name || c, cast_number: resourceById[c]?.cast_number ?? null }))
    .sort((a, b) => (a.cast_number === null ? 1 : 0) - (b.cast_number === null ? 1 : 0) || (a.cast_number ?? 0) - (b.cast_number ?? 0));
  const equipmentNames = scene.equipment_ids.map((e) => resourceById[e]?.name || e);
  const openSearch = (sid: string) => {
    setFocus(sid);
    setDrawer(true);
  };
  const start = async () => {
    setStarting(true);
    try {
      await api.planScene(id, sceneId, text ?? undefined, useMemory);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };
  const place = async () => {
    if (!placeDay) return;
    setPlacing(true);
    setPlaceError(null);
    try {
      const r = await api.commitPlacement(id, placeDay, sceneId);
      setPlaced({ summary: r.changeset.summary, dayId: r.day.id, dayNumber: r.day.day_number, overtime: r.added_overtime_cost_inr, notes: r.notes });
      // The scene sheet reads its placement off the project, so the row above this one is wrong
      // until the production is re-read — and "Not on any shoot day" under a landed ChangeSet is
      // the page contradicting itself.
      setProject((await api.project(id)).project);
    } catch (e) {
      setPlaceError(e instanceof Error ? e.message : String(e));
    } finally {
      setPlacing(false);
    }
  };
  const cite = (ids: string[]) =>
    ids.length > 0 && (
      <span className="inline-flex flex-wrap gap-1 ml-1 align-middle">
        {ids.map((eid) => (
          <button key={eid} onClick={() => openSearch(evById[eid]?.search_run_id || "")} className="mono text-[10px] px-1 rounded bg-ok/10 text-ok hover:bg-ok/20" title={evById[eid]?.claim}>
            {eid.replace("ev_", "ev·")}
          </button>
        ))}
      </span>
    );

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-5 flex-wrap">
        <div className="flex-1 min-w-[280px]">
          <Kicker>
            Scene readiness · {scene.int_ext} · {scene.time_of_day} · scheduled {scene.estimated_minutes} min
            {scene.estimated_minutes_breakdown && scene.estimated_minutes_breakdown !== scene.estimated_minutes && (
              <span className={Math.abs(scene.estimated_minutes_breakdown - scene.estimated_minutes) >= 30 ? "text-warn" : ""}> · breakdown estimate ≈{scene.estimated_minutes_breakdown} min</span>
            )}
          </Kicker>
          <h1 className="display text-4xl font-bold mt-1">
            <span className="text-accent mr-3">{scene.number}</span>
            {scene.heading}
          </h1>
          <p className="text-muted text-sm mt-1 max-w-3xl">{scene.synopsis}</p>
        </div>
        <div className="flex items-center gap-4">
          {plan && (
            <div className="text-center">
              <Readiness score={plan.readiness_score} />
              <div className="text-[10px] text-dim mt-1">readiness (heuristic)</div>
            </div>
          )}
          <div className="flex flex-col gap-2">
            {/* A wrapped scene is not a thing to plan. The primary action is the record of the day it was shot. */}
            {wrapped && placement && !run ? (
              <>
                <Link href={`/projects/${id}/days/${placement.day.id}`} className="btn btn-primary">
                  Open Day {placement.day.day_number}
                </Link>
                <Link href={`/projects/${id}/days/${placement.day.id}/call-sheet`} className="btn">
                  Day {placement.day.day_number} call sheet
                </Link>
              </>
            ) : (
              <>
                <button className="btn btn-primary" onClick={start} disabled={running || starting}>
                  {plan ? "Re-run planning" : "Break down & research"}
                </button>
                <label className={`flex items-center gap-1.5 text-[11px] ${memoryOn ? "text-muted" : "text-dim"}`} title={memoryOn ? "Read this production's Parallel memory first, so the planner does not re-ask what earlier dossiers already answered" : "Parallel Memory is off in this deployment (SCENEPILOT_PARALLEL_MEMORY=1)"}>
                  <input type="checkbox" checked={useMemory} disabled={!memoryOn || running || starting} onChange={(e) => setUseMemory(e.target.checked)} />
                  start from prior research
                </label>
              </>
            )}
            <button className="btn" onClick={() => setDrawer(true)} disabled={!run}>
              Evidence ({evidence.length})
            </button>
          </div>
        </div>
      </div>

      {/* The pipeline, read off the ADK graph the engine runs rather than listed here by hand. */}
      <AgentGraph
        name="scenepilot_planning"
        stage={run?.stage}
        status={run?.status}
        aside={
          <span className="text-[12px] text-muted">
            {running && <Spinner label={`running · ${run?.stage}`} />}
            {run?.status === "FAILED" && <span className="text-bad">failed: {run.error}</span>}
            {run?.status === "COMPLETED" && run.planning && (
              <span>
                {run.planning.search_run_ids.length} Parallel searches · {run.planning.follow_up_rounds} follow-up round{run.planning.follow_up_rounds === 1 ? "" : "s"} · {evidence.length} evidence items
                {run.planning.used_memory && <span> · reused {run.planning.memory_entries_used} remembered run{run.planning.memory_entries_used === 1 ? "" : "s"}</span>}
              </span>
            )}
          </span>
        }
      />

      <ParallelUsageStrip usage={data.parallel_usage} onOpen={() => setDrawer(true)} />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="space-y-5 min-w-0">
          {/* What this run stood on. Memory's whole value is the second time — a count told a
              producer that reuse happened and nothing about what was reused, or which earlier run
              paid for it. */}
          {recalled.length > 0 && (
            <section className="card p-4 space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <Kicker>Recalled from the production brain</Kicker>
                <span className="chip chip-parallel">Parallel Memory</span>
                <span className="ml-auto text-[11px] text-dim mono truncate max-w-[200px]">scope {recalled[0].scope_key}</span>
              </div>
              <p className="text-[11px] text-muted">
                This planning run started from research this production had already paid for, rather than from nothing.
              </p>
              <ul className="space-y-1.5">
                {recalled.map((r) => (
                  <li key={`${r.memory_read_id}:${r.ref_id}`} className="rounded border border-line bg-elev p-2 text-[12px] space-y-0.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="chip chip-dim">{r.kind_label}</span>
                      <span className="text-muted min-w-0 truncate">
                        recalled: {r.origin ? r.origin.label : r.input_excerpt.slice(0, 60)}
                      </span>
                      <span className="ml-auto mono text-[10px] text-dim truncate max-w-[160px]" title={r.ref_id}>{r.ref_id}</span>
                    </div>
                    {r.excerpt && <p className="text-muted line-clamp-2">{r.excerpt}</p>}
                    {r.origin_note && <p className="text-[10px] text-dim">{r.origin_note}</p>}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {/* Every value here is on the production already; none of it needs a run. It is the difference
              between a scene page that has "nothing on it" and one that simply has not been planned. */}
          {project && (
            <section className="card p-4">
              <div className="flex items-center gap-2 flex-wrap">
                <Kicker>Scene sheet</Kicker>
                <span className="text-[12px] text-muted">what the scene is booked against — read from the production, not from any run</span>
              </div>
              <dl className="mt-3 grid gap-x-5 gap-y-3 sm:grid-cols-2 text-[13px]">
                <Field label="Set">
                  {setLocation ? (
                    <>
                      {setLocation.name}
                      {setLocation.contact && <div className="text-[11px] text-dim">contact: {setLocation.contact}</div>}
                    </>
                  ) : (
                    <Unset>No location on file for this scene — it is not tied to a set, so nothing on the page can check a permit, a window or a company move.</Unset>
                  )}
                </Field>
                <Field label="Scheduled">
                  {placement ? (
                    <>
                      Day {placement.day.day_number} · {placement.day.date} · <span className="mono">{placement.item.start}–{placement.item.end}</span>
                      <div className="mt-1 flex items-center gap-2">
                        <span className={`chip ${wrapped ? "chip-dim" : "chip-ok"}`}>{placement.day.status.replace(/_/g, " ")}</span>
                        <Link href={`/projects/${id}/days/${placement.day.id}`} className="text-[11px] text-accent hover:underline">day {placement.day.day_number} →</Link>
                      </div>
                    </>
                  ) : (
                    <Unset>Not on any shoot day. It is in the script and in this list, but no unit has been asked to shoot it.</Unset>
                  )}
                </Field>
                <Field label={`Cast (${scene.cast_ids.length})`}>
                  {castMembers.length > 0 ? (
                    <ul className="space-y-0.5">
                      {castMembers.map((m) => (
                        <li key={m.id}>
                          {m.cast_number !== null && (
                            <span
                              className="mono text-dim mr-1.5"
                              title="Cast number — what this performer is called on the board, the call sheet, the DOOD and the dispatch."
                            >
                              {m.cast_number}
                            </span>
                          )}
                          {m.name}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <Unset>No performer is attached to this scene, so it raises no cast call and no hold day.</Unset>
                  )}
                </Field>
                <Field label={`Equipment (${scene.equipment_ids.length})`}>
                  {equipmentNames.length > 0 ? (
                    <ul className="space-y-0.5">{equipmentNames.map((n) => <li key={n}>{n}</li>)}</ul>
                  ) : (
                    <Unset>No equipment booked against this scene.</Unset>
                  )}
                </Field>
              </dl>
              {(scene.eighths || scene.continuity_group || scene.is_cover) && (
                <div className="mt-3 pt-2 border-t border-line flex items-center gap-2 flex-wrap text-[11px]">
                  {scene.eighths ? (
                    <span className="chip chip-dim" title={`${scene.eighths}/8 of a page, as the board totals it.`}>
                      {eighthsLabel(scene.eighths)} pgs
                    </span>
                  ) : null}
                  {scene.continuity_group && <span className="chip chip-dim">continuity · {scene.continuity_group}</span>}
                  {scene.is_cover && <span className="chip chip-info">cover set</span>}
                </div>
              )}
            </section>
          )}

          {/* Everything above this point de-risks a scene nobody has asked a unit to shoot. Eight of
              nine scenes on this production reach a day through the seed and the ninth only as a
              by-product of a rescue option, so a scene taken through readiness, evidence and
              candidates had no way onto the board at all. */}
          {project && !placement && (
            <section className="card p-4">
              <div className="flex items-center gap-2 flex-wrap">
                <Kicker>Schedule this scene</Kicker>
                <span className="text-[12px] text-muted">pick the day; the engine finds the slot and validates the whole day against it</span>
              </div>
              {placeableDays.length === 0 ? (
                <p className="mt-2 text-[12px] text-muted">
                  Every shoot day on this production has wrapped. A scene cannot be added to a day that has already been
                  shot, so this scene needs a day the schedule does not have yet.
                </p>
              ) : (
                <>
                  <p className="mt-2 text-[13px] text-muted max-w-2xl">
                    The start time is not yours to choose: the placement is recomputed against the target day&apos;s current
                    schedule, its light, its bookings and the labour pack in force. It lands as an applied ChangeSet, or the
                    day refuses and says what it could not fit.
                  </p>
                  <div className="mt-3 flex items-center gap-2 flex-wrap">
                    <select
                      value={placeDay}
                      onChange={(e) => setPlaceDay(e.target.value)}
                      className="bg-elev border border-line rounded px-2 py-1.5 text-[13px]"
                      disabled={placing}
                    >
                      <option value="">Choose a shoot day…</option>
                      {placeableDays.map((d) => (
                        <option key={d.id} value={d.id}>
                          Day {d.day_number} · {d.date} · {d.items.length} scene{d.items.length === 1 ? "" : "s"} · {d.status.replace(/_/g, " ").toLowerCase()}
                        </option>
                      ))}
                    </select>
                    <button className="btn btn-primary" onClick={place} disabled={placing || !placeDay}>
                      {placing ? <Spinner label="placing…" /> : "Place on this day"}
                    </button>
                    <span className="text-[11px] text-dim">
                      Writes to the board immediately and is recorded as a producer decision — there is no proposal step.
                    </span>
                  </div>
                </>
              )}
              {placeError && <p className="mt-3 text-[12px] text-bad">{placeError}</p>}
              {placed && (
                <div className="mt-3 rounded border border-ok/50 bg-ok/5 p-2.5 text-[12px] space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="chip chip-ok">ChangeSet applied</span>
                    <span>{placed.summary}</span>
                    <Link href={`/projects/${id}/days/${placed.dayId}`} className="ml-auto text-accent hover:underline">
                      Day {placed.dayNumber} →
                    </Link>
                  </div>
                  <p className="text-muted">
                    {placed.overtime > 0
                      ? `It puts the day ${inr(placed.overtime)} into overtime.`
                      : "It fits inside the standard day — no overtime."}
                  </p>
                  {placed.notes.length > 0 && (
                    <ul className="list-disc pl-5 text-dim">
                      {placed.notes.map((n) => <li key={n}>{n}</li>)}
                    </ul>
                  )}
                </div>
              )}
            </section>
          )}

          {/* A scene that has already been shot. Planning it de-risks nothing, so the page stops offering to. */}
          {!run && wrapped && placement && (
            <section className="card p-4 border-l-4 border-l-line-strong">
              <div className="flex items-center gap-2 flex-wrap">
                <Kicker>Already shot</Kicker>
                <Stamp status={placement.day.status} />
              </div>
              <h2 className="display text-2xl font-bold mt-2">
                Scene {scene.number} was shot on Day {placement.day.day_number}.
              </h2>
              <p className="text-sm text-muted mt-1.5 max-w-2xl">
                It ran <span className="mono">{placement.item.start}–{placement.item.end}</span> on {placement.day.date}. Breakdown and research exist to de-risk a
                scene before the unit rolls, and there is nothing left here to de-risk. What the day actually was — its call, its crew, its equipment — is on the day
                page and on the call sheet the unit worked from.
              </p>
              {placement.day.notes && (
                <p className="text-[12px] text-dim mt-2 border-l-2 border-line pl-2.5">{placement.day.notes}</p>
              )}
              <div className="mt-3 flex items-center gap-3 flex-wrap">
                <Link href={`/projects/${id}/days/${placement.day.id}`} className="btn">Day {placement.day.day_number}</Link>
                <Link href={`/projects/${id}/days/${placement.day.id}/call-sheet`} className="btn">Call sheet</Link>
                <button className="text-[12px] text-accent hover:underline disabled:text-dim" onClick={start} disabled={starting}>
                  break it down anyway →
                </button>
              </div>
              <p className="text-[11px] text-dim mt-2 max-w-2xl">
                Planning still runs on a wrapped scene. It spends a Gemini breakdown and real Parallel searches, and changes nothing about a day that is behind the production.
              </p>
            </section>
          )}

          {/* Scheduled or not, this scene has no pages. The textarea is the right affordance; the empty box was not. */}
          {!run && !wrapped && !hasScript && (
            <section className="card p-4">
              <div className="flex items-center gap-2 flex-wrap">
                <Kicker>Scene input</Kicker>
                <span className="chip chip-dim">no script pages</span>
              </div>
              <p className="text-sm text-muted mt-2 max-w-2xl">
                The production carries this scene&apos;s slug line, its set, its cast and how long it is booked for — but not a word of its text. The breakdown agent reads
                script text, so it has nothing to read. Paste the scene below, or ingest the draft in the Screenplay Studio and it lands here.
              </p>
              <textarea
                className="mt-3 w-full bg-elev border border-line rounded px-3 py-2 text-sm mono"
                rows={7}
                value={text ?? scene.script_text}
                onChange={(e) => setText(e.target.value)}
                placeholder={`${scene.heading}\n\n`}
              />
              <div className="mt-2 flex items-center gap-3 flex-wrap">
                <Link href={`/projects/${id}/screenplay`} className="text-[12px] text-accent hover:underline">Screenplay Studio →</Link>
                <span className="text-[12px] text-muted">Break down &amp; research reads this box. Left empty it falls back to the scene&apos;s one-line synopsis — a real Gemini breakdown and real Parallel searches, run against a sentence.</span>
              </div>
            </section>
          )}

          {!run && !wrapped && hasScript && (
            <section className="card p-4">
              <Kicker>Scene input</Kicker>
              <textarea className="mt-2 w-full bg-elev border border-line rounded px-3 py-2 text-sm mono" rows={7} value={text ?? scene.script_text} onChange={(e) => setText(e.target.value)} />
              <p className="text-[12px] text-muted mt-2">Gemini extracts structured requirements, plans research questions, ScenePilot runs real Parallel searches, grades the evidence, follows up where it is weak, and writes a grounded plan.</p>
              <p className="text-[11px] text-dim mt-1.5">Nothing has been run for this scene yet. Planning spends a Gemini breakdown and several Parallel searches, so it is never started on your behalf.</p>
            </section>
          )}

          {/* The page loses six sections without a plan. Say which six, rather than letting them silently vanish. */}
          {!plan && !running && (
            <section className="rounded-lg border border-dashed border-line p-4">
              <div className="flex items-center gap-2 flex-wrap">
                <Kicker>Plan</Kicker>
                <span className="chip chip-dim">not generated</span>
              </div>
              <p className="text-sm text-muted mt-2 max-w-2xl">
                {wrapped
                  ? "Six sections of this page are written by the planning workflow, and it has never been asked to run against a scene the production has already shot."
                  : "Six sections of this page are written by the planning workflow. Until it runs they are not drawn at all — a blank recommendation reads worse than none."}
              </p>
              <ul className="mt-2.5 text-[12px] text-dim grid gap-1 sm:grid-cols-2">
                <li>· A readiness score and the four components behind it</li>
                <li>· The recommended approach and its reasoning</li>
                <li>· Candidate approaches, with pros and cons</li>
                <li>· Graded facts and inferences, each citing its evidence</li>
                <li>· Risks with severity, likelihood and mitigations</li>
                <li>· The questions the research could not close</li>
              </ul>
            </section>
          )}

          {plan && (
            <section className="card p-4 border-accent/40">
              <div className="flex items-center gap-2">
                <Kicker>Recommended approach</Kicker>
                <KindChip kind="RECOMMENDATION" />
              </div>
              <p className="mt-2 text-sm leading-relaxed">{plan.recommendation}</p>
              {plan.readiness && (
                <ul className="mt-3 text-[11px] text-dim mono space-y-0.5">
                  {plan.readiness.explanation.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {plan && plan.candidates.length > 0 && (
            <section>
              <div className="kicker mb-2">Candidate approaches</div>
              <div className="grid gap-3 md:grid-cols-2">
                {plan.candidates.map((c) => (
                  <div key={c.id} className={`card p-4 ${c.id === plan.recommended_candidate_id ? "border-accent" : ""}`}>
                    <div className="flex items-center gap-2">
                      <div className="font-medium">{c.title}</div>
                      {c.id === plan.recommended_candidate_id && <span className="chip chip-accent">recommended</span>}
                    </div>
                    <p className="text-[12px] text-muted mt-1">{c.description}</p>
                    <div className="mt-2 grid grid-cols-2 gap-2 text-[12px]">
                      <ul className="space-y-0.5">{c.pros.map((x) => <li key={x} className="text-ok">+ {x}</li>)}</ul>
                      <ul className="space-y-0.5">{c.cons.map((x) => <li key={x} className="text-warn">− {x}</li>)}</ul>
                    </div>
                    {cite(c.evidence_ids)}
                  </div>
                ))}
              </div>
            </section>
          )}

          {plan && (
            <section className="grid gap-3 md:grid-cols-2">
              <div className="card p-4">
                <div className="flex items-center gap-2"><Kicker>Facts</Kicker><KindChip kind="FACT" /></div>
                <ul className="mt-2 text-[13px] space-y-1.5">
                  {plan.key_facts.length === 0 && <li className="text-dim">No fully grounded facts — see unknowns.</li>}
                  {plan.key_facts.map((f) => {
                    const m = f.match(/^(.*)\s\[(.*)\]$/);
                    return (
                      <li key={f}>
                        {m ? m[1] : f}
                        {m && cite(m[2].split(",").map((x) => x.trim()).filter(Boolean))}
                      </li>
                    );
                  })}
                </ul>
              </div>
              <div className="card p-4">
                <div className="flex items-center gap-2"><Kicker>Inferences</Kicker><KindChip kind="INFERENCE" /></div>
                <ul className="mt-2 text-[13px] space-y-1.5 text-muted">
                  {plan.inferences.map((x) => <li key={x}>{x}</li>)}
                </ul>
              </div>
            </section>
          )}

          {plan && plan.risks.length > 0 && (
            <section className="card p-4">
              <Kicker>Risks</Kicker>
              <ul className="mt-2 divide-y divide-line">
                {plan.risks.map((r) => (
                  <li key={r.id} className="py-2.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <StatusChip status={r.severity} />
                      <span className="font-medium text-sm">{r.title}</span>
                      <KindChip kind={r.kind} />
                      <span className="ml-auto mono text-[11px] text-dim">likelihood {Math.round(r.likelihood * 100)}% · confidence {Math.round(r.confidence * 100)}%</span>
                    </div>
                    <p className="text-[12px] text-muted mt-1">{r.description}{cite(r.evidence_ids)}</p>
                    {r.mitigations.length > 0 && (
                      <ul className="mt-1 text-[12px] pl-4 list-disc space-y-0.5">
                        {r.mitigations.map((m) => <li key={m}>{m}</li>)}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {plan && plan.unresolved.length > 0 && (
            <section className="card p-4">
              <div className="flex items-center gap-2"><Kicker>Unresolved</Kicker><KindChip kind="UNKNOWN" /></div>
              <ul className="mt-2 text-[13px] space-y-1.5">
                {plan.unresolved.map((u) => (
                  <li key={u.question}>
                    <span className="text-warn">?</span> {u.question}
                    {u.why_it_matters && <span className="text-dim"> — {u.why_it_matters}</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="card p-4">
            <div className="flex items-center gap-2">
              <Kicker>Requirements</Kicker>
              <span className="text-[12px] text-muted">
                {scene.requirements.length} · {scene.requirements.length === 0 ? "none on file" : run ? "extracted by Gemini" : "seeded (synthetic)"}
              </span>
            </div>
            <table className="mt-2 w-full text-[12px]">
              <tbody>
                {scene.requirements.length === 0 && (
                  <tr className="border-t border-line">
                    <td className="py-2.5 text-muted">
                      {run
                        ? "The breakdown ran and returned no requirements for this scene."
                        : wrapped
                        ? "None were seeded for this scene and no breakdown has run. Requirements are what a research plan is built from, and a wrapped scene has nothing left to research."
                        : "None were seeded for this scene and no breakdown has run. Requirements are what the research plan is built from: run the breakdown and Gemini extracts them from the script text, each quoting the line it came from."}
                    </td>
                  </tr>
                )}
                {scene.requirements.map((r) => (
                  <tr key={r.id} className="border-t border-line align-top">
                    <td className="py-1.5 pr-2 whitespace-nowrap"><span className="chip chip-dim">{r.category}</span></td>
                    <td className="py-1.5 pr-2 whitespace-nowrap"><StatusChip status={r.importance} /></td>
                    <td className="py-1.5">
                      {r.description}
                      {r.source_ref && <span className="text-dim"> — “{r.source_ref}”</span>}
                      {r.weather_sensitive && <span className="chip chip-warn ml-1">weather</span>}
                    </td>
                    <td className="py-1.5 pl-2 mono text-[10px] text-dim whitespace-nowrap">{r.id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {scene.breakdown_elements && scene.breakdown_elements.length > 0 && (
            <section className="card p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Kicker>Extracted Production Elements</Kicker>
                  <span className="text-[12px] text-muted">
                    {scene.breakdown_elements.length} elements · CreativeBreakdownAgent
                  </span>
                </div>
                <Link
                  href={`/projects/${id}/screenplay`}
                  className="text-xs text-accent hover:underline"
                >
                  Screenplay Studio →
                </Link>
              </div>
              <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                {scene.breakdown_elements.map((elem) => (
                  <div
                    key={elem.id}
                    className="p-2.5 rounded border border-line bg-elev/60 space-y-1"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-foreground">{elem.name}</span>
                      <span className="mono text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-dim">
                        {elem.category}
                      </span>
                    </div>
                    {elem.description && (
                      <p className="text-[11px] text-muted">{elem.description}</p>
                    )}
                    {elem.safety_notes && (
                      <p className="text-[10px] text-red-400 font-medium">⚠️ {elem.safety_notes}</p>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        <div className="space-y-5">
          <section className="card p-4">
            <div className="flex items-center gap-2">
              <Kicker>Research plan</Kicker>
              <span className="text-[12px] text-muted">{questions.length} questions</span>
            </div>
            <ul className="mt-2 space-y-3">
              {questions.length === 0 && (
                <li>
                  {running ? (
                    <span className="text-dim text-sm">Planning research…</span>
                  ) : (
                    <>
                      <div className="text-dim text-sm">No research has been run for this scene.</div>
                      <p className="text-[12px] text-muted mt-1">
                        {wrapped
                          ? "The workflow writes each question it decides to ask here, then grades the answer against the Parallel searches that produced it. It has not been pointed at a scene that is already shot."
                          : "The workflow writes each question it decides to ask here, grades the answer against the Parallel searches that produced it, and re-asks the ones that come back weak."}
                      </p>
                    </>
                  )}
                </li>
              )}
              {questions.map((q) => (
                <li key={q.id} className="border-t border-line pt-2">
                  <div className="flex items-start gap-2">
                    <StatusChip status={q.status} />
                    <span className="text-[13px]">{q.question}</span>
                  </div>
                  <div className="mt-1 text-[11px] text-dim mono">
                    {q.search_run_ids.length} search run{q.search_run_ids.length === 1 ? "" : "s"} · {q.evidence_ids.length} evidence
                    {q.search_run_ids.map((sid) => (
                      <button key={sid} onClick={() => openSearch(sid)} className="ml-2 text-parallel underline decoration-dotted">view</button>
                    ))}
                  </div>
                  {q.assessment && <p className="mt-1 text-[12px] text-muted">{q.assessment}</p>}
                </li>
              ))}
            </ul>
          </section>
          <ActivityFeed events={data.activity} live={running} onOpenSearch={openSearch} />
        </div>
      </div>

      <EvidenceDrawer open={drawer} onClose={() => setDrawer(false)} searchRuns={data.search_runs} extractRuns={data.extract_runs} evidence={evidence} focusSearchId={focus} runId={run?.id} title={`Evidence · Scene ${scene.number}`} onExtracted={() => reload()} />
    </div>
  );
}
