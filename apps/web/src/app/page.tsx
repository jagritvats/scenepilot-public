"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { Empty, Kicker, Readiness, Stamp } from "@/components/ui";

export default function ProjectsPage() {
  const { data, error, loading } = usePoll(() => api.projects(), () => false, 5000);
  return (
    <div className="space-y-6">
      <div>
        <Kicker>Productions</Kicker>
        <h1 className="display text-4xl font-bold mt-1">Control room</h1>
        <p className="text-muted mt-1 max-w-2xl text-sm">
          Every production here has structured scenes, resources and shoot days. ScenePilot plans scenes against live web evidence and rescues shoot days when the real world changes.
        </p>
      </div>

      {/* Hackathon Showcase Banner */}
      <div className="card p-6 bg-gradient-to-r from-accent/10 via-zinc-950 to-parallel/10 border-accent/40 space-y-4">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="mono text-[10px] px-2 py-0.5 rounded bg-accent/20 text-accent font-semibold">
                Google Cloud Hackathon
              </span>
              <span className="mono text-[10px] px-2 py-0.5 rounded bg-parallel/20 text-parallel font-semibold">
                Parallel Partner Track
              </span>
              <span className="mono text-[10px] px-2 py-0.5 rounded bg-ok/20 text-ok font-semibold">
                509 Tests Passing
              </span>
            </div>
            <h2 className="display text-2xl font-bold mt-2">
              ScenePilot — Feature Showcase & Quick Tour
            </h2>
            <p className="text-xs text-muted mt-1 max-w-3xl">
              Judges & reviewers: click any showcase shortcut below to jump directly into the live vertical slice.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 pt-2">
          <Link
            href="/projects/proj_nightfall/screenplay"
            className="card p-3 bg-zinc-900/60 hover:border-accent transition group"
          >
            <div className="text-[10px] uppercase font-bold text-accent">Phase 1</div>
            <div className="text-xs font-bold text-foreground group-hover:text-accent mt-0.5">
              Screenplay Studio
            </div>
            <div className="text-[11px] text-dim mt-1">
              Fountain parser, 8ths estimate, 32 element categories, DOOD matrix
            </div>
          </Link>

          <Link
            href="/projects/proj_nightfall/scenes/sc_42"
            className="card p-3 bg-zinc-900/60 hover:border-accent transition group"
          >
            <div className="text-[10px] uppercase font-bold text-parallel">Planning</div>
            <div className="text-xs font-bold text-foreground group-hover:text-accent mt-0.5">
              Scene 42 Planning
            </div>
            <div className="text-[11px] text-dim mt-1">
              Parallel Search, autonomous follow-ups, graded evidence
            </div>
          </Link>

          {/* Both Phase cards below open Day 4; the hash is what makes them different pages to a
              reader, landing one on the board and the other on the rescue workflow. */}
          <Link
            href="/projects/proj_nightfall/days/day_4#stripboard"
            className="card p-3 bg-zinc-900/60 hover:border-accent transition group"
          >
            <div className="text-[10px] uppercase font-bold text-amber-400">Phase 2</div>
            <div className="text-xs font-bold text-foreground group-hover:text-accent mt-0.5">
              Solar Stripboard
            </div>
            <div className="text-[11px] text-dim mt-1">
              NOAA ephemeris, DGA meal penalties, live nudging
            </div>
          </Link>

          <Link
            href="/projects/proj_nightfall/days/day_4#multiday"
            className="card p-3 bg-zinc-900/60 hover:border-accent transition group"
          >
            <div className="text-[10px] uppercase font-bold text-bad">Phase 3</div>
            <div className="text-xs font-bold text-foreground group-hover:text-accent mt-0.5">
              Multi-Day Rescue
            </div>
            <div className="text-[11px] text-dim mt-1">
              Rain disruption, constraint rejections, Day 5/6 ripple
            </div>
          </Link>

          <Link
            href="/projects/proj_nightfall/days/day_4/call-sheet"
            className="card p-3 bg-zinc-900/60 hover:border-accent transition group"
          >
            <div className="text-[10px] uppercase font-bold text-ok">Phase 3</div>
            <div className="text-xs font-bold text-foreground group-hover:text-accent mt-0.5">
              Field Dispatch
            </div>
            <div className="text-[11px] text-dim mt-1">
              DGA Call Sheet, WhatsApp/SMS dispatch log, simulated acks
            </div>
          </Link>
        </div>
      </div>

      {error && (
        <Empty
          title="Waking the production agent"
          hint="The control room has not reached the agent service yet — a cold start is the usual cause. This page keeps retrying on its own."
          action={<div className="mono text-[11px] text-dim">{error}</div>}
        />
      )}
      {loading && !data && <div className="card p-8 shimmer h-40" />}
      <div className="grid gap-4 md:grid-cols-2">
        {data?.map((p) => (
          <Link key={p.id} href={`/projects/${p.id}`} className="card p-5 hover:border-accent transition block">
            <div className="flex items-start gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h2 className="display text-2xl font-bold">{p.title}</h2>
                  {p.synthetic && <span className="chip chip-dim">fictional</span>}
                </div>
                <p className="text-sm text-muted mt-1">{p.logline}</p>
                <div className="mt-3 text-[12px] text-muted">
                  {p.scene_count} scenes · {p.shoot_day_count} shoot days · {p.base_city}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {p.shoot_days.map((d) => (
                    <span key={d.id} className="inline-flex items-center gap-2 rounded border border-line px-2 py-1 text-[11px]">
                      <span className="display font-semibold">DAY {d.day_number}</span>
                      <span className="text-dim">{d.date}</span>
                      <Stamp status={d.status} />
                    </span>
                  ))}
                </div>
              </div>
              <div className="text-center">
                <Readiness score={p.avg_readiness} size={84} />
                <div className="text-[10px] text-dim mt-1 max-w-[104px] mx-auto leading-tight">
                  {p.avg_readiness === null ? "no scene planned yet — open one to plan it" : "avg readiness"}
                </div>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
