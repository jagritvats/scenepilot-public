"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import { Empty, Kicker, Readiness, Stamp } from "@/components/ui";

const HERO_DAY = "/projects/proj_nightfall/days/day_4";

/**
 * What happens on the hero path, in the order it happens.
 *
 * Written as the promise rather than the feature: a reader who has never seen a stripboard should
 * be able to tell, before clicking, what they are about to watch and why it is hard. Every figure
 * here is one the day actually produces — they are the same numbers the demo script reads off.
 */
const HERO_BEATS = [
  { n: "1", label: "Rain arrives", detail: "13:00–17:00, verified against the outside world through Parallel Search before anything is touched." },
  { n: "2", label: "Two options are rejected", detail: "Holding the schedule leaves a motorcycle stunt 150 min in the wet; the market street's permit does not open until 13:00." },
  { n: "3", label: "One is recommended, and priced", detail: "Pull the interior cover set into the rain, defer the exterior — ₹67,500 and 60 minutes of overtime, before anyone commits." },
  { n: "4", label: "The producer approves", detail: "The board re-lays itself and the call sheet reissues white → blue, with an audit trail of who decided what, on what evidence." },
];

/** The second click, for a reader who has two more minutes. Each of these is a different argument. */
const DEEPER = [
  {
    href: "/projects/proj_nightfall/days/day_6",
    kicker: "The differentiator",
    title: "A cited statute becomes a hard constraint",
    detail: "Day 6 shoots a rooftop until 23:30. Accept the noise curfew a Parallel Task dossier found — with the statute it came from — and watch a recovery option turn red where it stands.",
  },
  {
    href: "/projects/proj_nightfall/days/day_6/sides",
    kicker: "What it refuses to invent",
    title: "Sides that print named gaps",
    detail: "Day 4's sides are complete. Day 6's are not, and say which scenes are missing and why, rather than printing a packet with holes in it.",
  },
  {
    href: "/projects/proj_nightfall/days/day_4/dpr",
    kicker: "What it refuses to produce",
    title: "A report for a day that has not happened",
    detail: "Ask Day 4 for a daily production report and it declines, naming the call sheet as the document for a day still ahead. Day 3 has wrapped, so Day 3 gets one.",
  },
];

/** The rest of the product, subordinate to the path above by design. */
const SURFACES = [
  { href: "/projects/proj_nightfall/screenplay", tone: "text-accent", label: "Screenplay Studio", detail: "Fountain / FDX, eighths, 32 element categories, DOOD" },
  { href: "/projects/proj_nightfall/scenes/sc_42", tone: "text-parallel", label: "Scene planning", detail: "Research questions, graded evidence, autonomous follow-ups" },
  { href: `${HERO_DAY}#stripboard`, tone: "text-amber-400", label: "Solar stripboard", detail: "NOAA ephemeris, union rule packs, live penalty math" },
  { href: `${HERO_DAY}/call-sheet`, tone: "text-ok", label: "Call sheet & dispatch", detail: "DGA sheet, WhatsApp / SMS log, simulated acks" },
  // The card above deliberately links the report that refuses. Without this, the working document is
  // never reachable from the home page and the refusal reads as the whole feature.
  { href: "/projects/proj_nightfall/days/day_3/dpr", tone: "text-muted", label: "Daily production report", detail: "Day 3 wrapped, so Day 3 has one — with its measured cost" },
];

export default function ProjectsPage() {
  const { data, error, loading } = usePoll(() => api.projects(), () => false, 5000);
  return (
    <div className="space-y-6">
      {/* ------------------------------------------------------------------ */}
      {/* The thesis, in the product's voice. A reader arriving from a link   */}
      {/* has thirty seconds to learn what this is; the hackathon it was      */}
      {/* built for is not what they need in them.                           */}
      {/* ------------------------------------------------------------------ */}
      <header className="pt-1">
        <Kicker>ScenePilot · production control room</Kicker>
        <h1 className="display text-4xl sm:text-5xl font-bold mt-1 leading-[1.05]">
          Gemini proposes.<span className="text-accent"> Code decides.</span>
        </h1>
        <p className="text-muted mt-3 max-w-3xl">
          An AI 1st assistant director that plans shoot days against live web evidence and rescues them when the real
          world moves. Every constraint holding this schedule up traces to a cited source, a permit window or a
          computed sun — and where the production genuinely does not hold a fact, the screen says so instead of
          filling the gap.
        </p>
      </header>

      {/* ------------------------------------------------------------------ */}
      {/* One front door, not five. The hero path is the argument; everything */}
      {/* below it is evidence that the argument generalises.                 */}
      {/* ------------------------------------------------------------------ */}
      <section className="card overflow-hidden border-accent/40">
        <div className="bg-gradient-to-r from-accent/12 via-transparent to-parallel/10 p-5 sm:p-6 space-y-5">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <Kicker className="text-accent">Start here · about 60 seconds</Kicker>
              <h2 className="display text-2xl sm:text-3xl font-bold mt-1">
                Shoot Day 4, Mumbai. It starts raining at one.
              </h2>
              <p className="text-sm text-muted mt-1 max-w-2xl">
                Four scenes, a motorcycle stunt, a sunset rooftop jump, and a market street whose police permit opens
                at 13:00. Watch what a constraint engine does that a chat window cannot.
              </p>
            </div>
            <Link
              href={HERO_DAY}
              className="shrink-0 display font-bold text-base tracking-wide uppercase px-5 py-2.5 rounded bg-accent text-[color:var(--strip-ink)] hover:brightness-110 transition"
            >
              Open Day 4 →
            </Link>
          </div>

          <ol className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {HERO_BEATS.map((b) => (
              <li key={b.n} className="card bg-elev/60 p-3">
                <div className="flex items-baseline gap-2">
                  <span className="display font-bold text-accent text-sm">{b.n}</span>
                  <span className="text-xs font-bold text-foreground">{b.label}</span>
                </div>
                <p className="text-[11px] text-dim mt-1.5 leading-relaxed">{b.detail}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* The second click. Three arguments, each a different one: what the   */}
      {/* web can bind, and the two things the product declines to fake.      */}
      {/* ------------------------------------------------------------------ */}
      <section className="space-y-3">
        <Kicker>If you have another two minutes</Kicker>
        <div className="grid gap-3 lg:grid-cols-3">
          {DEEPER.map((d) => (
            <Link key={d.href} href={d.href} className="card p-4 hover:border-accent transition group">
              <div className="text-[10px] uppercase font-bold text-parallel tracking-wider">{d.kicker}</div>
              <div className="display text-lg font-bold mt-1 group-hover:text-accent transition">{d.title}</div>
              <p className="text-[11px] text-dim mt-1.5 leading-relaxed">{d.detail}</p>
            </Link>
          ))}
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* The rest of the product. Deliberately the smallest thing on the     */}
      {/* page: five equal doors is the same as no door at all, and the two   */}
      {/* sections above are the ones worth a reader's first minute.          */}
      {/* ------------------------------------------------------------------ */}
      <section className="space-y-3">
        <Kicker>Everywhere else</Kicker>
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
          {SURFACES.map((s) => (
            <Link key={s.href} href={s.href} className="card p-3 hover:border-accent transition group">
              <div className={`text-xs font-bold ${s.tone} group-hover:text-accent transition`}>{s.label}</div>
              <div className="text-[11px] text-dim mt-1 leading-relaxed">{s.detail}</div>
            </Link>
          ))}
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* The production itself.                                              */}
      {/* ------------------------------------------------------------------ */}
      {error && (
        <Empty
          title="Waking the production agent"
          hint="The control room has not reached the agent service yet — a cold start is the usual cause. This page keeps retrying on its own."
          action={<div className="mono text-[11px] text-dim">{error}</div>}
        />
      )}
      {loading && !data && <div className="card p-8 shimmer h-40" />}
      <section className="space-y-3">
        <Kicker>Productions</Kicker>
        <div className="grid gap-4 grid-cols-[minmax(0,1fr)] md:grid-cols-2">
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
                {/*
                  Readiness is a score over *planned* scenes, so on an instance where nobody has run
                  a planning pass yet there is genuinely nothing to draw. The dial used to render
                  anyway — an empty ring, at the visual centre of the first card a reader sees,
                  saying nothing except that something was missing. The honest sentence was already
                  there underneath it; this keeps the sentence and drops the hole.
                */}
                <div className="text-center shrink-0 w-[104px]">
                  {p.avg_readiness === null ? (
                    <>
                      <div className="display text-3xl font-bold text-dim leading-none">
                        {p.shoot_days.filter((d) => d.status === "WRAPPED").length}
                        <span className="text-dim/60">/{p.shoot_day_count}</span>
                      </div>
                      <div className="text-[10px] text-dim mt-1.5 leading-tight">
                        days wrapped · readiness appears once a scene has been planned
                      </div>
                    </>
                  ) : (
                    <>
                      <Readiness score={p.avg_readiness} size={84} />
                      <div className="text-[10px] text-dim mt-1 leading-tight">avg readiness</div>
                    </>
                  )}
                </div>
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* Provenance, last. It is context for the reader, not the pitch.      */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex flex-wrap items-center gap-2 pt-1 text-[11px] text-dim">
        <span className="mono text-[10px] px-2 py-0.5 rounded bg-accent/15 text-accent font-semibold">Google Cloud Agentic Cinema</span>
        <span className="mono text-[10px] px-2 py-0.5 rounded bg-parallel/15 text-parallel font-semibold">Parallel partner track</span>
        <span className="mono text-[10px] px-2 py-0.5 rounded bg-ok/15 text-ok font-semibold">614 Tests Passing</span>
        <span>Google ADK · Gemini 3.5 Flash · Parallel Search, Extract, Task, FindAll, Memory, Monitor · Next.js 16</span>
      </div>
    </div>
  );
}
