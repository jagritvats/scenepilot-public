"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type ParallelUsage } from "@/lib/api";
import { CommandPalette } from "./CommandPalette";
import { HackathonTourModal } from "./HackathonTourModal";
import { ParallelConsoleModal, type HealthWithFeatures } from "./ParallelConsoleModal";

/** The leaf crumb for `/projects/:id/<section>`, by the section segment that names it. */
const SECTIONS: Record<string, string> = {
  scenes: "Scene readiness",
  days: "Shoot day",
  screenplay: "Screenplay studio",
  log: "Production log",
  risks: "Risk register",
  inbox: "Inbox",
};

export function TopBar() {
  const [health, setHealth] = useState<HealthWithFeatures | null>(null);
  // What this production has spent on Parallel, read only while the console is open — it is a
  // whole-session question, and polling it behind a closed modal would be a request per 15s for
  // a number nobody is looking at.
  const [spend, setSpend] = useState<ParallelUsage | null>(null);
  const [err, setErr] = useState(false);
  const [tourOpen, setTourOpen] = useState(false);
  const [consoleOpen, setConsoleOpen] = useState(false);
  // Everything waiting on a producer in the inbox — pending fact drift plus monitor drafts. Both
  // kinds, because they are one queue to the person who has to answer them, and a badge that
  // counted only drift would go on reading zero while a monitor sat on Day 6. The count carries the
  // production it was counted for, so crossing to another one shows nothing rather than the last
  // one's number.
  const [unread, setUnread] = useState<{ project: string; count: number } | null>(null);
  const path = usePathname();
  useEffect(() => {
    let alive = true;
    const tick = () => api.health().then((h) => alive && (setHealth(h), setErr(false))).catch(() => alive && setErr(true));
    tick();
    const t = setInterval(tick, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  const crumbs = path.split("/").filter(Boolean);
  const projectId = crumbs[0] === "projects" ? crumbs[1] : null;
  const section = projectId ? SECTIONS[crumbs[2]] : undefined;
  // Advisory only, and read on navigation rather than on a timer: it is the same two endpoints the
  // inbox itself renders, so a stale count costs a click and never a wrong decision — and a number
  // in the corner is not worth a request every fifteen seconds. Re-read on every route change so
  // deciding something in the inbox and walking back out does not leave the badge lying.
  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    Promise.all([api.dossiers(projectId), api.draftDisruptions(projectId)])
      .then(([d, dd]) => alive && setUnread({ project: projectId, count: d.fact_changes.filter((c) => c.status === "PENDING").length + dd.drafts.length }))
      .catch(() => alive && setUnread({ project: projectId, count: 0 }));
    return () => {
      alive = false;
    };
  }, [projectId, path]);
  const waiting = unread && unread.project === projectId ? unread.count : 0;
  useEffect(() => {
    if (!consoleOpen || !projectId) return;
    let alive = true;
    api.parallelSpend(projectId).then((r) => alive && setSpend(r.usage)).catch(() => alive && setSpend(null));
    return () => {
      alive = false;
    };
  }, [consoleOpen, projectId]);
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-bg/90 backdrop-blur">
      <div className="max-w-[1480px] mx-auto px-3 sm:px-5 h-14 flex items-center gap-2 sm:gap-4">
        <Link href="/" className="display text-[19px] sm:text-[22px] font-bold tracking-wide leading-none shrink-0">
          SCENE<span className="text-accent">PILOT</span>
        </Link>
        {/* A phone fits one crumb, and the one worth keeping is the leaf — where you are. A
            left-aligned scroll strip shows the opposite: "Productions", with the current page off
            the right edge and sliced mid-word by the scroll box. So the trail appears only at lg,
            where the row has the width to hold all of it — the same breakpoint at which the
            buttons on the right get their labels back — and below that the leaf stands alone and
            ellipsizes rather than being cut. Shrinking the whole chain instead would just give
            three crumbs reading "Pr… / Scr…". */}
        <nav className="flex items-center gap-1 text-[13px] text-muted overflow-x-auto scroll-thin whitespace-nowrap min-w-0">
          <Link href="/" className={`px-2 py-1 rounded hover:text-fg shrink-0 ${projectId ? "hidden lg:block" : ""} ${crumbs.length === 0 ? "text-fg" : ""}`}>Productions</Link>
          {projectId && (
            <>
              <span className="text-dim shrink-0 hidden lg:inline">/</span>
              <Link
                href={`/projects/${projectId}`}
                className={`px-2 py-1 rounded hover:text-fg truncate min-w-0 ${section ? "hidden lg:block" : ""} ${crumbs.length === 2 ? "text-fg" : ""}`}
              >
                Project Nightfall
              </Link>
            </>
          )}
          {section && (
            <>
              <span className="text-dim shrink-0 hidden lg:inline">/</span>
              <span className="px-2 py-1 text-fg truncate min-w-0">{section}</span>
            </>
          )}
        </nav>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {/* The full phrase is what a producer needs to read, but it is also the widest thing in
              this row — on a phone it is the difference between the bar fitting and the page
              scrolling sideways, so below md only the word that carries the meaning stays. */}
          {err && (
            <span className="chip chip-bad" title="The agent service is not reachable">
              <span className="hidden md:inline">agent service</span>offline
            </span>
          )}
          {!health && !err && (
            <>
              <span className="chip chip-dim" title="Asking the agent service how it is configured">checking…</span>
              <span className="hidden 2xl:inline-flex chip chip-dim">gemini · …</span>
              <span className="hidden xl:inline-flex chip chip-dim">parallel · …</span>
            </>
          )}
          {health && (
            <>
              <span className={`chip ${health.mode === "live" ? "chip-ok" : "chip-warn"}`} title={health.mode === "live" ? "Calling Gemini and Parallel live" : "Replaying recorded responses from earlier live runs"}>
                {health.mode === "live" ? "live" : "replay"}
              </span>
              <span className={`hidden 2xl:inline-flex chip ${health.gemini_configured || health.mode === "replay" ? "chip-gemini" : "chip-dim"}`} title={health.adk}>
                gemini · {health.gemini_model}
              </span>
              <span className={`hidden xl:inline-flex chip ${health.parallel_configured || health.mode === "replay" ? "chip-parallel" : "chip-dim"}`} title={`Parallel ${(health.parallel_apis || ["search"]).join(" + ")} API · client_model ${health.parallel_client_model} · one session per run`}>
                parallel · {(health.parallel_apis || ["search"]).join("+")}
              </span>
            </>
          )}
          {/* Same gating as the palette below: both read one production, and the inbox is where the
              outside world's two ways in — drift and monitor drafts — wait for an answer. It stays
              visible at zero, because "nothing is waiting" is the reassurance, and hiding it would
              leave the inbox reachable only from the project page. */}
          {projectId && (
            <Link
              href={`/projects/${projectId}/inbox`}
              className={`hidden sm:inline-flex chip ${waiting ? "chip-warn" : "chip-dim"} hover:text-fg cursor-pointer text-xs py-1 px-2.5 shrink-0 ${crumbs[2] === "inbox" ? "text-fg" : ""}`}
              title={waiting ? `${waiting} decision${waiting === 1 ? "" : "s"} waiting — fact drift and monitor-detected disruptions` : "Fact drift and monitor-detected disruptions, production-wide. Nothing is waiting."}
            >
              inbox{waiting > 0 ? ` · ${waiting}` : ""}
            </Link>
          )}
          {/* The palette searches one production's document, so outside a production there is
              nothing to open — an affordance that does nothing reads as a broken build. */}
          {projectId && (
            <button
              onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }))}
              className="hidden sm:inline-flex chip chip-dim hover:text-fg cursor-pointer text-xs py-1 px-2.5 shrink-0"
              title="Search scenes, days, resources, facts and the log"
            >
              ⌘K search
            </button>
          )}
          {/* Both of these are demo front doors, so neither is allowed to disappear on a phone the
              way the chips above do — they collapse to their glyph instead. The label returns at lg,
              where there is room for it beside the breadcrumbs; the title and aria-label carry the
              name the rest of the time. */}
          <button
            onClick={() => setConsoleOpen(true)}
            className="chip chip-parallel hover:brightness-110 font-semibold cursor-pointer text-xs py-1 px-2 lg:px-2.5 shadow-sm transition shrink-0"
            title="What ScenePilot uses each Parallel API for, and which are enabled here"
            aria-label="Parallel Console"
          >
            🌐<span className="hidden lg:inline">Parallel Console</span>
          </button>
          <button
            onClick={() => setTourOpen(true)}
            className="chip chip-accent hover:brightness-110 font-semibold cursor-pointer text-xs py-1 px-2 lg:px-2.5 shadow-sm transition shrink-0"
            title="Open Hackathon Guided Tour"
            aria-label="Hackathon Tour"
          >
            ⚡<span className="hidden lg:inline">Hackathon Tour</span>
          </button>
        </div>
      </div>
      <CommandPalette projectId={projectId} />
      <HackathonTourModal
        isOpen={tourOpen}
        onClose={() => setTourOpen(false)}
        onOpenConsole={() => setConsoleOpen(true)}
      />
      <ParallelConsoleModal
        isOpen={consoleOpen}
        onClose={() => setConsoleOpen(false)}
        health={health}
        usage={spend}
      />
    </header>
  );
}
