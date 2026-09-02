"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import { api, type ScheduleItem, type ShootDay } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { Kicker, LoadError, Readiness, Stamp } from "@/components/ui";
import { MemoryPanel } from "@/components/MemoryPanel";
import { stripClass } from "@/components/StripBoard";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { data, error, loading, reload } = usePoll(() => api.project(id), () => false, 5000);
  const [resetting, setResetting] = useState(false);
  const [pasted, setPasted] = useState("");
  const [number, setNumber] = useState("");
  const [creating, setCreating] = useState<string | null>(null);
  // How much is waiting on a decision. Advisory badge only: a read of the same two endpoints the
  // inbox itself renders, so a stale count costs a click and never a wrong decision. Both halves,
  // because the inbox now holds both — a badge counting only fact drift would read as zero on a day
  // a monitor had raised something.
  const [pendingInbox, setPendingInbox] = useState(0);
  useEffect(() => {
    Promise.all([
      api.dossiers(id).then((d) => d.fact_changes.filter((c) => c.status === "PENDING").length).catch(() => 0),
      api.draftDisruptions(id).then((d) => d.drafts.length).catch(() => 0),
    ])
      .then(([drift, drafts]) => setPendingInbox(drift + drafts))
      .catch(() => setPendingInbox(0));
  }, [id]);
  if (!data) return loading || !error ? <div className="card p-8 shimmer h-60" /> : <LoadError error={error} missing="Project not found" />;
  const p = data.project;
  const scenes = Object.fromEntries(p.scenes.map((s) => [s.id, s]));
  const sched = new Map<string, { day: ShootDay; item: ScheduleItem }>();
  for (const d of p.shoot_days) for (const it of d.items) sched.set(it.scene_id, { day: d, item: it });

  const createAndPlan = async () => {
    if (!pasted.trim() || !number.trim()) return;
    setCreating("creating");
    try {
      const { scene } = await api.createScene(p.id, { number: number.trim(), text: pasted.trim() });
      await api.planScene(p.id, scene.id);
      router.push(`/projects/${p.id}/scenes/${scene.id}`);
    } catch (e) {
      setCreating(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-4 flex-wrap">
        <div>
          <Kicker>Production</Kicker>
          <h1 className="display text-4xl font-bold mt-1">{p.title}</h1>
          <p className="text-muted text-sm mt-1 max-w-2xl">{p.logline}</p>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <Link
            href={`/projects/${p.id}/log`}
            className="btn"
            title="Every recorded act on this production, in the order it happened — each line written by the code that performed it."
          >
            Production log
          </Link>
          <Link
            href={`/projects/${p.id}/inbox`}
            className="btn"
            title="What moved in the world since this production last looked — fact drift Parallel detected, and disruptions its monitors raised, both waiting on a producer."
          >
            Inbox
            {pendingInbox > 0 && <span className="chip chip-warn ml-1.5">{pendingInbox}</span>}
          </Link>
          <Link
            href={`/projects/${p.id}/risks`}
            className="btn"
            title="Every risk the planning runs have put on record, ordered by severity and likelihood."
          >
            Risk register
          </Link>
          <Link href={`/projects/${p.id}/screenplay#dood`} className="btn">
            DOOD Cast Matrix
          </Link>
          <Link href={`/projects/${p.id}/screenplay`} className="btn btn-primary">
            Screenplay Studio
          </Link>
          <button
            className="btn"
            disabled={resetting}
            onClick={async () => {
              if (!confirm("Reset Project Nightfall to its seeded state? Runs, evidence and changesets for this project are deleted.")) return;
              setResetting(true);
              await api.reset(p.id);
              await reload();
              setResetting(false);
            }}
          >
            Reset demo state
          </button>
        </div>
      </div>

      <section>
        <div className="flex items-baseline gap-3 mb-2">
          <h2 className="display text-2xl font-semibold">Shoot days</h2>
          <span className="text-[12px] text-muted">open a day to run the rescue workflow</span>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {p.shoot_days.map((d) => (
            <Link key={d.id} href={`/projects/${p.id}/days/${d.id}`} className={`card p-4 hover:border-accent transition ${d.day_number === 4 ? "border-accent/50" : ""}`}>
              <div className="flex items-center gap-3">
                <span className="display text-3xl font-bold">DAY {d.day_number}</span>
                <span className="text-muted text-sm">{d.date}</span>
                <span className="ml-auto"><Stamp status={d.status} /></span>
              </div>
              <div className="mt-3 flex gap-1 h-4">
                {[...d.items].sort((a, b) => a.start.localeCompare(b.start)).map((it) => (
                  <span key={it.id} className={`flex-1 rounded-sm ${stripClass(scenes[it.scene_id])}`} title={`Sc ${scenes[it.scene_id]?.number} ${it.start}–${it.end}`} />
                ))}
                {d.items.length === 0 && <span className="text-dim text-[11px]">no scenes scheduled</span>}
              </div>
              <div className="mt-2 text-[11px] text-muted">
                {d.items.length} scene{d.items.length === 1 ? "" : "s"} · call {d.unit_call} · {d.notes ? d.notes.slice(0, 70) : ""}
              </div>
              {d.day_number === 4 && <div className="mt-2 chip chip-accent">hero day</div>}
            </Link>
          ))}
        </div>
      </section>

      <MemoryPanel projectId={p.id} />

      <section>
        <div className="flex items-baseline gap-3 mb-2">
          <h2 className="display text-2xl font-semibold">Scenes</h2>
          <span className="text-[12px] text-muted">readiness comes from the planning workflow: requirements → research → Parallel evidence → plan</span>
        </div>
        <div className="card overflow-x-auto scroll-thin">
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wider text-dim">
              <tr className="border-b border-line">
                <th className="text-left px-4 py-2 font-medium">Scene</th>
                <th className="text-left px-2 py-2 font-medium">Set</th>
                <th className="text-left px-2 py-2 font-medium">Scheduled</th>
                <th className="text-left px-2 py-2 font-medium">Requirements</th>
                <th className="text-right px-4 py-2 font-medium">Readiness</th>
              </tr>
            </thead>
            <tbody>
              {[...p.scenes].sort((a, b) => Number(a.number) - Number(b.number)).map((s) => {
                const plan = p.plans[s.id];
                const sc = sched.get(s.id);
                // A scene the unit has already shot is not a scene to plan; sending a judge to a
                // "plan →" on it is the link that reads as broken.
                const done = !!sc && (sc.day.status === "WRAPPED" || sc.item.status === "COMPLETED");
                return (
                  <tr key={s.id} className="border-b border-line last:border-0 hover:bg-elev/60">
                    <td className="px-4 py-2">
                      <Link href={`/projects/${p.id}/scenes/${s.id}`} className="flex items-center gap-3">
                        <span className={`inline-block w-2 h-7 rounded-sm ${stripClass(s)}`} />
                        <span className="display font-bold text-lg w-8">{s.number}</span>
                        <span>
                          <div className="font-medium">{s.heading}</div>
                          <div className="text-[11px] text-muted">{s.synopsis}</div>
                        </span>
                        {s.id === "sc_42" && <span className="chip chip-accent">hero scene</span>}
                        {s.is_cover && <span className="chip chip-dim">cover</span>}
                      </Link>
                    </td>
                    <td className="px-2 py-2 text-muted whitespace-nowrap">{s.int_ext} · {s.time_of_day} · {s.estimated_minutes} min</td>
                    <td className="px-2 py-2 text-muted whitespace-nowrap">
                      {sc ? (
                        <>
                          Day {sc.day.day_number} {sc.item.start}–{sc.item.end}
                          {done && <span className="chip chip-dim ml-2">wrapped</span>}
                        </>
                      ) : (
                        <span className="text-dim">unscheduled</span>
                      )}
                    </td>
                    <td className="px-2 py-2 text-muted">{s.requirements.length > 0 ? s.requirements.length : <span className="text-dim">none on file</span>}</td>
                    <td className="px-4 py-2 text-right">
                      {plan ? (
                        <span className="inline-flex items-center gap-2"><Readiness score={plan.readiness_score} size={36} /></span>
                      ) : done && sc ? (
                        <Link href={`/projects/${p.id}/days/${sc.day.id}`} className="text-[12px] text-muted hover:text-accent hover:underline" title="Already shot — nothing left to plan. The record is on the day page.">
                          shot on Day {sc.day.day_number} →
                        </Link>
                      ) : (
                        <Link href={`/projects/${p.id}/scenes/${s.id}`} className="text-[12px] text-accent hover:underline">plan →</Link>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card p-4">
        <div className="flex items-baseline gap-3">
          <h2 className="display text-2xl font-semibold">Add a scene from pasted text</h2>
          <span className="text-[12px] text-muted">ingestion is normalised into a ProductionBrief; PDF/script upload can plug in here later</span>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-[120px_1fr_auto] items-start">
          <input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="Scene no." className="bg-elev border border-line rounded px-3 py-2 text-sm" />
          <textarea value={pasted} onChange={(e) => setPasted(e.target.value)} rows={3} placeholder={"EXT. MUMBAI ROOFTOP — SUNSET\n\nA motorcycle tears across adjoining rooftops…"} className="bg-elev border border-line rounded px-3 py-2 text-sm mono" />
          <button className="btn btn-primary" onClick={createAndPlan} disabled={!pasted.trim() || !number.trim() || creating === "creating"}>
            Break down & research
          </button>
        </div>
        {creating && creating !== "creating" && <div className="text-bad text-sm mt-2">{creating}</div>}
      </section>
    </div>
  );
}
