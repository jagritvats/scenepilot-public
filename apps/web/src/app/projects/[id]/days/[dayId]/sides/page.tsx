"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { api, type SidesPacket } from "@/lib/api";
import { Kicker, LoadError } from "@/components/ui";

/**
 * Sides — the day's pages, in the order the day shoots them.
 *
 * Set in the screenplay's own shape (mono type, centred character cues, indented speech) but in
 * paper ink, because these get printed and carried. A scene the Studio holds no text for prints as a
 * named gap with its reason: an actor must never be handed a packet that looks complete and is
 * missing their scene.
 */

const MONO = { fontFamily: "var(--font-plex-mono)" } as const;

export default function SidesPage({ params }: { params: Promise<{ id: string; dayId: string }> }) {
  const { id, dayId } = use(params);
  const [sides, setSides] = useState<SidesPacket | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.sides(id, dayId).then((r) => alive && setSides(r.sides)).catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [id, dayId]);

  if (!sides) return error ? <LoadError error={error} missing="Shoot day not found" /> : <div className="card p-8 shimmer h-72" />;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap print:hidden">
        <div>
          <Kicker>Sides</Kicker>
          <h1 className="display text-3xl font-bold">Day {sides.day_number} pages</h1>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <span className={`chip ${sides.complete ? "chip-ok" : "chip-warn"}`}>
            {sides.scenes_with_text} of {sides.scene_count} scene{sides.scene_count === 1 ? "" : "s"} supplied
          </span>
          <Link href={`/projects/${id}/days/${dayId}/call-sheet`} className="btn text-xs">Call sheet</Link>
          <button className="btn text-xs" onClick={() => window.print()}>Print / PDF</button>
          <Link href={`/projects/${id}/days/${dayId}`} className="btn btn-ghost text-xs">Back to the day</Link>
        </div>
      </div>

      <article
        className="print-portrait print-tint bg-white text-[#14171d] rounded-md shadow-xl p-6 print:shadow-none print:p-3 print:rounded-none print:border print:border-[#c9ced8]"
        style={{ fontFamily: "var(--font-plex-sans)" }}
      >
        <header className="flex items-start gap-4 border-b-2 border-[#14171d] pb-3">
          <div className="flex-1">
            <div className="text-[11px] tracking-[.18em] uppercase text-[#5a6272]">
              {sides.production}{sides.fictional ? " · fictional production" : ""} · sides
            </div>
            <div className="display text-3xl font-bold leading-none mt-1">
              SIDES — DAY {sides.day_number}
              <span className="text-[#5a6272] font-semibold"> of {sides.day_of_total}</span>
              <span className="text-[#5a6272] font-semibold"> · {sides.date}</span>
            </div>
          </div>
          <div className="text-right">
            <div className="display text-2xl font-bold">CALL {sides.unit_call}</div>
            <div className="text-[12px] text-[#5a6272]">
              {sides.scene_count} scene{sides.scene_count === 1 ? "" : "s"} in shooting order
            </div>
          </div>
        </header>

        {sides.coverage_note && (
          <p className="mt-3 border border-[#e2b93b] bg-[#fff7de] rounded px-3 py-2 text-[11px] print:bg-transparent">
            {sides.coverage_note}
          </p>
        )}

        {sides.scenes.map((scene) => (
          <section key={scene.scene_id} className="mt-5 break-inside-avoid">
            {/* The scene bar, in the board's ink: the number a unit calls it by, and when. */}
            <div className="bg-[#14171d] text-white px-2 py-1 flex items-baseline gap-2 flex-wrap text-[10px] uppercase tracking-wider print-tint">
              <span className="display font-bold text-[13px] tracking-normal">Sc {scene.scene_number}</span>
              <span className="opacity-80">{scene.int_ext} · {scene.time_of_day}</span>
              {scene.unit !== "MAIN" && <span className="opacity-80">{scene.unit.toLowerCase()} unit</span>}
              <span className="ml-auto opacity-90" style={MONO}>
                {scene.start}–{scene.end}
                {scene.eighths !== null && ` · ${scene.eighths}/8`}
              </span>
            </div>

            <div className="border border-t-0 border-[#c9ced8] px-4 py-3">
              <div className="text-[12px] font-bold tracking-wider" style={MONO}>{scene.heading}</div>
              {scene.location && <div className="text-[10px] text-[#5a6272]">{scene.location}</div>}
              {scene.cast.length > 0 && (
                <div className="text-[10px] text-[#5a6272] mt-0.5" style={MONO}>
                  {scene.cast.map((c) => (c.cast_number ? `${c.cast_number} ${c.name}` : c.name)).join(" · ")}
                </div>
              )}

              {scene.has_text ? (
                <div className="mt-3 text-[12px] leading-relaxed" style={MONO}>
                  {scene.action_text && <p className="whitespace-pre-line">{scene.action_text}</p>}
                  {scene.dialogue.map((line, i) => (
                    <div key={i} className="mt-3">
                      <div className="text-center font-bold tracking-wider">{line.character}</div>
                      {line.parenthetical && (
                        <div className="text-center text-[11px] text-[#5a6272] italic">({line.parenthetical})</div>
                      )}
                      <p className="max-w-md mx-auto">{line.text}</p>
                    </div>
                  ))}
                  {!scene.action_text && scene.dialogue.length === 0 && (
                    <p className="text-[#5a6272] italic">The draft holds this scene&apos;s heading but no action or dialogue.</p>
                  )}
                </div>
              ) : (
                /* A named gap, never an empty page. */
                <div className="mt-3 border border-dashed border-[#c9ced8] px-3 py-4 text-center">
                  <div className="display font-bold uppercase tracking-[.12em] text-[11px]">No pages on file</div>
                  <p className="mt-1 text-[11px] text-[#5a6272] max-w-xl mx-auto">{scene.gap_reason}</p>
                </div>
              )}
            </div>
          </section>
        ))}

        {sides.scenes.length === 0 && (
          <p className="mt-4 text-[12px] text-[#5a6272] italic">Nothing is scheduled on this day, so there are no sides to print.</p>
        )}

        <footer className="mt-5 pt-2 border-t border-[#c9ced8] text-[9px] text-[#5a6272] break-inside-avoid">
          {sides.provenance}
        </footer>
      </article>
    </div>
  );
}
