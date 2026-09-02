"use client";

import { useEffect, useState } from "react";
import { api, cleanExcerpt, fmtTime, type Evidence, type ExtractRun, type SearchRun } from "@/lib/api";
import { SourceViewer } from "./SourceViewer";
import { KindChip, StatusChip } from "./ui";

function settingsLabel(sr: SearchRun): string {
  const a = sr.advanced_settings;
  if (!a) return "Parallel defaults";
  const parts: string[] = [];
  if (a.location) parts.push(`location=${a.location}`);
  if (a.max_results) parts.push(`max_results=${a.max_results}`);
  if (a.excerpt_settings?.max_chars_per_result) parts.push(`excerpt≤${a.excerpt_settings.max_chars_per_result}`);
  if (a.source_policy?.include_domains?.length) parts.push(`include_domains=${a.source_policy.include_domains.join(",")}`);
  if (a.source_policy?.after_date) parts.push(`after=${a.source_policy.after_date}`);
  if (a.fetch_policy?.max_age_seconds) parts.push(`max_age=${a.fetch_policy.max_age_seconds}s`);
  return parts.join(" · ") || "Parallel defaults";
}

export function EvidenceDrawer({ open, onClose, searchRuns, extractRuns = [], evidence, focusSearchId, runId, title = "Evidence", onExtracted }: { open: boolean; onClose: () => void; searchRuns: SearchRun[]; extractRuns?: ExtractRun[]; evidence: Evidence[]; focusSearchId?: string | null; runId?: string | null; title?: string; onExtracted?: (xr: ExtractRun) => void }) {
  const [local, setLocal] = useState<Record<string, ExtractRun>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [openUrl, setOpenUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  useEffect(() => {
    if (open && focusSearchId) (document.getElementById(`sr-${focusSearchId}`) || document.getElementById(`xr-${focusSearchId}`))?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [open, focusSearchId]);
  if (!open) return null;
  const byUrl = new Map<string, ExtractRun>();
  for (const x of [...extractRuns, ...Object.values(local)]) if (x.urls[0] && (x.status === "OK" || x.status === "REPLAY" || !byUrl.has(x.urls[0]))) byUrl.set(x.urls[0], x);
  const byRun = new Map<string, Evidence[]>();
  for (const e of evidence) {
    const k = e.search_run_id || "?";
    byRun.set(k, [...(byRun.get(k) || []), e]);
  }
  const openSource = async (url: string, searchRunId: string, evidenceId?: string) => {
    if (openUrl === url) {
      setOpenUrl(null);
      return;
    }
    if (byUrl.has(url) || !runId) {
      setOpenUrl(url);
      return;
    }
    setBusy(url);
    try {
      const res = await api.extractSource(runId, { url, search_run_id: searchRunId, evidence_id: evidenceId });
      setLocal((m) => ({ ...m, [res.extract_run.id]: res.extract_run }));
      onExtracted?.(res.extract_run);
      setOpenUrl(url);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  const totalUsage = [...searchRuns, ...extractRuns].flatMap((r) => r.usage).reduce<Record<string, number>>((acc, u) => ({ ...acc, [u.name]: (acc[u.name] || 0) + u.count }), {});
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={title}>
      <button className="absolute inset-0 bg-black/55" onClick={onClose} aria-label="Close evidence drawer" />
      <aside className="relative w-full max-w-[680px] h-full bg-elev border-l border-line shadow-2xl flex flex-col rise">
        <div className="px-5 py-3 border-b border-line flex items-center gap-3">
          <div>
            <div className="kicker">{title}</div>
            <div className="text-sm text-muted">
              {searchRuns.length} Parallel search run{searchRuns.length === 1 ? "" : "s"} · {extractRuns.length + Object.keys(local).length} extract{extractRuns.length + Object.keys(local).length === 1 ? "" : "s"} · {evidence.length} evidence item{evidence.length === 1 ? "" : "s"}
              {Object.keys(totalUsage).length > 0 && <span className="mono text-[11px] text-dim"> · usage {Object.entries(totalUsage).map(([k, v]) => `${k}×${v}`).join(", ")}</span>}
            </div>
          </div>
          <button onClick={onClose} className="btn btn-ghost ml-auto">Close</button>
        </div>
        <div className="flex-1 overflow-auto scroll-thin p-5 space-y-5">
          {searchRuns.length === 0 && <div className="text-muted text-sm">No searches have run yet.</div>}
          {searchRuns.map((sr) => (
            <section key={sr.id} id={`sr-${sr.id}`} className={`card p-4 ${focusSearchId === sr.id ? "border-parallel" : ""}`}>
              <div className="flex items-start gap-2 flex-wrap">
                <span className="chip chip-parallel">parallel · search · {sr.mode}</span>
                <span className="chip chip-dim">{sr.purpose.replace(/_/g, " ")} · round {sr.round}</span>
                <StatusChip status={sr.status} />
                {sr.replayed && <span className="chip chip-warn">replayed recording</span>}
                <span className="ml-auto mono text-[11px] text-dim">{fmtTime(sr.started_at)}</span>
              </div>
              <div className="mt-2 text-sm">{sr.objective}</div>
              <div className="mt-1 mono text-[11px] text-muted">queries: {sr.queries.map((q) => `"${q}"`).join(" · ")}</div>
              <div className="mono text-[11px] text-dim flex flex-wrap gap-x-3">
                {sr.provider_search_id && <span>search_id {sr.provider_search_id}</span>}
                {sr.session_id && <span>session {sr.session_id}</span>}
                {sr.client_model && <span>client_model {sr.client_model}</span>}
                <span>settings: {settingsLabel(sr)}</span>
                {sr.usage.map((u) => (
                  <span key={u.name}>{u.name} ×{u.count}</span>
                ))}
              </div>
              {sr.warnings.map((w, i) => (
                <div key={i} className="mt-1 text-[11px] text-warn">⚠ {w.type}: {w.message}</div>
              ))}
              {sr.error && <div className="mt-2 text-bad text-sm">{sr.error}</div>}
              <ol className="mt-3 space-y-2">
                {sr.results.map((r, i) => {
                  const evs = (byRun.get(sr.id) || []).filter((e) => e.source_url === r.url);
                  const xr = byUrl.get(r.url);
                  return (
                    <li key={r.url + i} className="border-t border-line pt-2">
                      <div className="flex items-baseline gap-2 text-[12px]">
                        <span className="mono text-dim">#{i + 1}</span>
                        <a href={r.url} target="_blank" rel="noreferrer" className="text-info hover:underline truncate">{r.title || r.url}</a>
                        {r.publish_date && <span className="mono text-dim shrink-0">{r.publish_date}</span>}
                        <button onClick={() => openSource(r.url, sr.id, evs[0]?.id)} disabled={busy === r.url} className={`ml-auto shrink-0 text-[11px] px-2 py-0.5 rounded border ${xr ? "border-parallel/60 text-parallel" : "border-line text-muted hover:text-fg hover:border-parallel/60"}`} title="Fetch the full page through the Parallel Extract API">
                          {busy === r.url ? "extracting…" : openUrl === r.url ? "hide source" : xr ? "open source ✓" : "open source"}
                        </button>
                      </div>
                      {r.excerpts.map((ex, j) => (
                        <p key={j} className="text-[12px] text-muted mt-1 line-clamp-4">{cleanExcerpt(ex)}</p>
                      ))}
                      {evs.map((e) => (
                        <div key={e.id} className="mt-2 rounded border border-ok/30 bg-ok/5 p-2 text-[12px]">
                          <div className="flex items-center gap-2 flex-wrap">
                            <KindChip kind={e.kind} />
                            <StatusChip status={e.authority} />
                            <StatusChip status={e.freshness} />
                            {e.extract_run_id && <span className="chip chip-parallel">from extract</span>}
                            <span className="mono text-dim ml-auto">conf {Math.round(e.confidence * 100)}% · rel {Math.round(e.relevance * 100)}%</span>
                          </div>
                          <div className="mt-1 text-fg">{e.claim}</div>
                          {e.production_implication && <div className="mt-1 text-muted">→ {e.production_implication}</div>}
                          <div className="mono text-[10px] text-dim mt-1">{e.id}</div>
                        </div>
                      ))}
                      {openUrl === r.url && xr && <SourceViewer run={xr} needle={evs[0]?.excerpt || r.excerpts[0]} onClose={() => setOpenUrl(null)} />}
                    </li>
                  );
                })}
                {sr.results.length === 0 && sr.status !== "ERROR" && <li className="text-muted text-sm">No results returned.</li>}
              </ol>
            </section>
          ))}
          {extractRuns.filter((x) => x.purpose === "agent_extract").map((xr) => (
            <section key={xr.id} id={`xr-${xr.id}`} className={`card p-4 ${focusSearchId === xr.id ? "border-parallel" : ""}`}>
              <div className="flex items-start gap-2 flex-wrap">
                <span className="chip chip-parallel">parallel · extract</span>
                <span className="chip chip-gemini">called by the Evidence Analyst</span>
                <StatusChip status={xr.status} />
                <span className="ml-auto mono text-[11px] text-dim">{fmtTime(xr.started_at)}</span>
              </div>
              <div className="mt-2 text-sm">{xr.objective}</div>
              <SourceViewer run={xr} needle={evidence.find((e) => e.extract_run_id === xr.id)?.excerpt} />
            </section>
          ))}
        </div>
      </aside>
    </div>
  );
}
