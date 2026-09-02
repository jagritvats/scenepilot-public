"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { api, type ActivityEvent, type Project } from "@/lib/api";
import { kindLabel, paletteRoutes, searchProject, type SearchHit } from "@/lib/search";
import { useDismissOnEscape, useFocusTrap, useMounted } from "@/lib/useDismiss";

/**
 * Ctrl+K — every day, scene, resource, discovered fact and log line in one box.
 *
 * This films badly and demonstrates a great deal: a developer opening an unfamiliar tool reaches for
 * Ctrl+K within seconds, and whether it is there says more about build quality than any panel does.
 * It is also the fastest way to drive the app while filming.
 *
 * The index is the project document the app has already fetched, filtered in memory — see
 * `lib/search.ts` for why that is the honest choice at this size rather than a search endpoint.
 */

const KIND_TONE: Record<string, string> = {
  scene: "chip-accent",
  day: "chip-info",
  resource: "chip-dim",
  fact: "chip-parallel",
  activity: "chip-dim",
  action: "chip-ok",
};

export function CommandPalette({ projectId }: { projectId: string | null }) {
  const mounted = useMounted();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [project, setProject] = useState<Project | null>(null);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const panel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  useDismissOnEscape(open, () => setOpen(false));
  useFocusTrap(open, panel);

  // The one global shortcut in the app — bound on every project page, and deliberately not on the
  // productions index, where there is no project document to search. Binding it there would
  // preventDefault the browser's own Ctrl+K for a palette that then renders nothing.
  useEffect(() => {
    if (!projectId) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCursor(0);
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [projectId]);

  // Loaded when the palette first opens, not on every page render: it is a whole project document
  // and nobody is searching a palette they have not opened.
  useEffect(() => {
    if (!open || !projectId || project) return;
    let alive = true;
    api.project(projectId).then((r) => alive && setProject(r.project)).catch(() => {});
    api.productionLog(projectId).then((r) => alive && setEvents(r.events)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [open, projectId, project]);

  // Focus on open, without setting state in an effect (React 19 flags that as a cascading render).
  // The cursor is reset where it actually changes — on open and on every keystroke.
  useEffect(() => {
    if (open) requestAnimationFrame(() => input.current?.focus());
  }, [open]);

  const hits: SearchHit[] = useMemo(() => {
    if (!projectId) return [];
    if (query.trim().length < 2) return paletteRoutes(projectId, "day_4");
    return project ? searchProject(project, events, query) : [];
  }, [project, events, query, projectId]);

  if (!mounted || !open || !projectId) return null;

  const go = (hit: SearchHit) => {
    setOpen(false);
    setQuery("");
    if (hit.run) hit.run();
    else router.push(hit.href);
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[110] bg-black/70 backdrop-blur-sm p-4 sm:pt-[12vh] flex justify-center items-start"
      onClick={() => setOpen(false)}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="bg-zinc-950 border border-line rounded-xl w-full max-w-2xl shadow-2xl overflow-hidden"
      >
        <input
          ref={input}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setCursor(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setCursor((c) => Math.min(hits.length - 1, c + 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setCursor((c) => Math.max(0, c - 1));
            } else if (e.key === "Enter" && hits[cursor]) {
              e.preventDefault();
              go(hits[cursor]);
            }
          }}
          placeholder="Search scenes, days, resources, discovered facts, the log…"
          aria-label="Search this production"
          className="w-full bg-transparent px-4 py-3.5 text-sm outline-none border-b border-line placeholder:text-dim"
        />

        <ul className="max-h-[52vh] overflow-y-auto scroll-thin py-1" role="listbox" aria-label="Results">
          {hits.map((hit, i) => (
            <li key={`${hit.kind}:${hit.id}`}>
              <button
                type="button"
                role="option"
                aria-selected={i === cursor}
                onMouseEnter={() => setCursor(i)}
                onClick={() => go(hit)}
                className={`w-full text-left px-4 py-2 flex items-baseline gap-2 ${i === cursor ? "bg-elev" : ""}`}
              >
                <span className={`chip ${KIND_TONE[hit.kind] ?? "chip-dim"} shrink-0`}>{kindLabel(hit.kind)}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] truncate">{hit.title}</span>
                  <span className="block text-[11px] text-dim truncate">{hit.subtitle}</span>
                </span>
              </button>
            </li>
          ))}

          {hits.length === 0 && (
            <li className="px-4 py-6 text-center text-[12px] text-muted">
              {query.trim().length < 2
                ? "Type at least two characters."
                : project
                  ? `Nothing on this production matches “${query}”.`
                  : "Reading this production…"}
            </li>
          )}
        </ul>

        <div className="px-4 py-2 border-t border-line text-[10px] text-dim flex items-center gap-3 flex-wrap">
          <span><b className="mono">↑↓</b> move</span>
          <span><b className="mono">↵</b> open</span>
          <span><b className="mono">esc</b> close</span>
          <span className="ml-auto">Searches this production&apos;s own state — scenes, days, resources, facts and the log.</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
