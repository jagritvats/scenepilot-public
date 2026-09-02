"use client";

import { useEffect, useMemo, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cleanExcerpt, type ExtractRun } from "@/lib/api";
import { StatusChip } from "./ui";

const norm = (s: string) => s.replace(/[#*_`>\[\]()]/g, "").replace(/\s+/g, " ").trim().toLowerCase();

/** Full page content fetched through the Parallel Extract API, with the cited excerpt highlighted. */
export function SourceViewer({ run, needle, onClose }: { run: ExtractRun; needle?: string | null; onClose?: () => void }) {
  const result = run.results[0];
  const key = useMemo(() => (needle ? norm(needle).slice(0, 80) : ""), [needle]);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current?.querySelector("[data-cited='true']");
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [run.id, key]);
  const cited = (children: React.ReactNode) => {
    if (!key) return false;
    const text = norm(String(Array.isArray(children) ? children.map((c) => (typeof c === "string" ? c : (c as { props?: { children?: unknown } })?.props?.children ?? "")).join(" ") : children));
    return text.length > 20 && (text.includes(key.slice(0, 60)) || key.includes(text.slice(0, 60)));
  };
  const mark = (Tag: "p" | "li" | "td" | "h1" | "h2" | "h3" | "blockquote") =>
    function Marked({ children }: { children?: React.ReactNode }) {
      const hit = cited(children);
      return (
        <Tag data-cited={hit ? "true" : undefined} className={hit ? "bg-accent/15 outline outline-1 outline-accent/50 rounded px-1 -mx-1" : undefined}>
          {children}
        </Tag>
      );
    };
  return (
    <div className="rounded border border-parallel/40 bg-elev mt-2">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap text-[11px]">
        <span className="chip chip-parallel">parallel · extract</span>
        <StatusChip status={run.status} />
        {run.replayed && <span className="chip chip-warn">replayed recording</span>}
        {run.purpose === "agent_extract" && <span className="chip chip-gemini">extracted by the Evidence Analyst</span>}
        <span className="mono text-dim">{run.provider_extract_id || run.id}</span>
        {run.usage.map((u) => (
          <span key={u.name} className="chip chip-dim">{u.name} ×{u.count}</span>
        ))}
        {onClose && <button onClick={onClose} className="ml-auto text-muted hover:text-fg">close</button>}
      </div>
      {run.status === "ERROR" && <div className="px-3 py-2 text-bad text-[12px]">{run.error}</div>}
      {result && (
        <div className="px-3 py-2 text-[12px]">
          <div className="flex items-baseline gap-2 flex-wrap">
            <a href={result.url} target="_blank" rel="noreferrer" className="text-info hover:underline truncate">{result.title || result.url}</a>
            {result.publish_date && <span className="mono text-dim">{result.publish_date}</span>}
            <span className="mono text-dim ml-auto">{(result.full_content || "").length.toLocaleString()} chars</span>
          </div>
          {result.excerpts.length > 0 && (
            <div className="mt-2">
              <div className="kicker">Relevant passages (Parallel)</div>
              <ul className="mt-1 space-y-1">
                {result.excerpts.slice(0, 3).map((e, i) => (
                  <li key={i} className="border-l-2 border-parallel/60 pl-2 text-muted">{cleanExcerpt(e)}</li>
                ))}
              </ul>
            </div>
          )}
          {result.full_content && result.full_content.length < 400 && (
            <div className="mt-2 text-[11px] text-warn">This page returned very little readable content ({result.full_content.length} chars) — it is probably script-rendered or gated. The passages above are what Parallel could retrieve.</div>
          )}
          {result.full_content && (
            <div ref={ref} className="mt-3 max-h-[420px] overflow-auto scroll-thin rounded border border-line bg-card p-3 prose-sm leading-relaxed [&_h1]:text-base [&_h1]:font-semibold [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:text-sm [&_h3]:font-medium [&_p]:my-1.5 [&_li]:my-0.5 [&_ul]:pl-4 [&_ol]:pl-4 [&_ul]:list-disc [&_a]:text-info [&_table]:text-[11px] [&_td]:border [&_td]:border-line [&_td]:px-1 [&_th]:border [&_th]:border-line [&_th]:px-1 [&_code]:text-[11px]">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ p: mark("p"), li: mark("li"), td: mark("td"), h1: mark("h1"), h2: mark("h2"), h3: mark("h3"), blockquote: mark("blockquote") }}>
                {result.full_content}
              </ReactMarkdown>
            </div>
          )}
        </div>
      )}
      {run.errors.length > 0 && (
        <div className="px-3 pb-2 text-[11px] text-warn">{run.errors.map((e) => `${e.url}: ${e.error_type}${e.http_status_code ? ` (${e.http_status_code})` : ""}`).join(" · ")}</div>
      )}
    </div>
  );
}
