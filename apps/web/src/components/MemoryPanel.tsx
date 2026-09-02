"use client";

import { useEffect, useState } from "react";
import { api, fmtTime, type FeatureState, type MemoryEntry, type MemoryView } from "@/lib/api";
import { Kicker, Spinner } from "./ui";

/**
 * Production brain — what Parallel has learned about this shoot.
 *
 * Task, Monitor and FindAll runs created in the same `memory_scope_key` accumulate into one scope;
 * this panel reads that scope back and lets the producer forget what has gone stale — one entry, or
 * the whole scope when a production is finished and should stop colouring the next one. Reads are
 * explicit: nothing here fires on page load, because Memory is a live server-side call.
 */

const KIND_LABEL: Record<string, string> = { task: "location dossier", monitor: "monitor", findall: "entity search" };

function EntryRow({ e, onEvict, busy }: { e: MemoryEntry; onEvict: () => void; busy: boolean }) {
  return (
    <li className="rounded border border-line bg-elev p-2.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="chip chip-parallel">{KIND_LABEL[e.kind] || e.kind}</span>
        {e.status && <span className="chip chip-dim">{e.status}</span>}
        {e.matched_count !== null && <span className="chip chip-dim">{e.matched_count} matches</span>}
        <span className="mono text-dim text-[11px]">{e.ref_id}</span>
        {e.updated_at && <span className="mono text-dim text-[11px]">{fmtTime(e.updated_at)}</span>}
        <button className="btn btn-ghost ml-auto text-[11px]" disabled={busy} onClick={onEvict} title="Forget this run in Parallel's memory. The run itself is not deleted.">
          Mark stale
        </button>
      </div>
      <div className="mt-1 font-medium">{e.input_excerpt}</div>
      {e.output_excerpt && <p className="mt-0.5 text-[12px] text-muted whitespace-pre-line">{e.output_excerpt}</p>}
      {e.event_ids.length > 0 && <div className="mt-1 mono text-[11px] text-dim">{e.event_ids.length} matched event{e.event_ids.length === 1 ? "" : "s"}</div>}
    </li>
  );
}

export function MemoryPanel({ projectId }: { projectId: string }) {
  const [feature, setFeature] = useState<FeatureState | null>(null);
  const [data, setData] = useState<MemoryView | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .features()
      .then((f) => setFeature(f.features.memory))
      .catch(() => setFeature(null));
  }, []);

  const recall = async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.memory(projectId, query));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const evict = async (e: MemoryEntry) => {
    if (!confirm(`Forget this ${KIND_LABEL[e.kind] || e.kind} in Parallel's memory?\n\n${e.input_excerpt}\n\nThe underlying run is not deleted.`)) return;
    setBusy(true);
    try {
      await api.evictMemory(projectId, e.kind, e.ref_id);
      setData(await api.memory(projectId, query));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // Per-entry eviction was the only way out, which left the scope itself unreachable — a producer
  // who wanted Parallel to stop reasoning from a whole stale production had to evict a page at a
  // time and could never be sure they had got everything. The confirm counts what is on screen and
  // says what survives, the way the per-entry one already does for a single run.
  const forget = async () => {
    const known = data?.read.status === "OK" ? data.read.entries.length : 0;
    const warning = [
      "Forget everything Parallel has learned about this production?",
      `This clears the whole memory scope${data ? ` — ${data.scope_key}` : ""}, not one entry${known ? `; ${known} entr${known === 1 ? "y is" : "ies are"} readable in it right now` : ""}.`,
      "The Task, Monitor and FindAll runs themselves are not deleted, and nothing already accepted as a production fact changes. Only Parallel's recall of this shoot goes, and it cannot be undone.",
    ].join("\n\n");
    if (!confirm(warning)) return;
    setBusy(true);
    setError(null);
    try {
      await api.forgetMemory(projectId);
      // Read straight back rather than clearing local state: an empty scope the server confirms is
      // worth more than a blank panel, and it is the same live call the Recall button makes.
      setData(await api.memory(projectId, query));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const off = !feature?.enabled;
  const read = data?.read;
  const writes = data?.writes_memory;
  const nothingWritesYet = writes && !writes.monitors && !writes.task && !writes.findall;

  return (
    <section className="card p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Kicker>Production brain · Parallel Memory</Kicker>
        <span className="text-[12px] text-muted">
          Every Task, Monitor and FindAll run for this production writes into one memory scope. Read it back here — and forget what has gone stale.
        </span>
        {data && <span className="mono text-[11px] text-dim">{data.scope_key}</span>}
      </div>

      <div className="mt-3 flex items-center gap-2 flex-wrap">
        <input
          value={query}
          onChange={(ev) => setQuery(ev.target.value)}
          onKeyDown={(ev) => ev.key === "Enter" && !off && !busy && recall()}
          placeholder="What should Parallel recall? (blank = most recent)"
          disabled={off}
          className="bg-elev border border-line rounded px-2 py-1.5 text-[13px] flex-1 min-w-[16rem] disabled:opacity-50"
        />
        <button
          className="btn btn-primary"
          disabled={off || busy}
          title={off ? `Disabled. Set ${feature?.env || "SCENEPILOT_PARALLEL_MEMORY=1"} to enable.` : "Live read of Parallel's memory for this production — no per-call cost"}
          onClick={recall}
        >
          Recall from Parallel
        </button>
        <button
          className="btn btn-ghost"
          disabled={off || busy}
          title={off ? `Disabled. Set ${feature?.env || "SCENEPILOT_PARALLEL_MEMORY=1"} to enable.` : "Clear the whole scope at Parallel. The runs stay; only what Parallel recalls of them goes."}
          onClick={forget}
        >
          Forget this production&rsquo;s scope
        </button>
      </div>

      {off && (
        <p className="mt-3 text-[12px] text-muted">
          Parallel Memory is off in this deployment. Enable it with <span className="mono text-dim">{feature?.env || "SCENEPILOT_PARALLEL_MEMORY=1"}</span>
          {feature?.requires_key && <> and a <span className="mono text-dim">PARALLEL_API_KEY</span></>}. It has no per-call cost, but it is a live call, so it never runs on its own.
        </p>
      )}

      {busy && !data && <div className="mt-3"><Spinner label="reading Parallel memory" /></div>}
      {error && <p className="mt-3 text-[12px] text-bad">{error}</p>}

      {read?.status === "UNAVAILABLE" && (
        <p className="mt-3 text-[12px] text-muted">Memory is unavailable: {read.error}. Memory lives on Parallel&rsquo;s side, so — unlike Search and Extract — there is nothing recorded to replay.</p>
      )}
      {read?.status === "ERROR" && <p className="mt-3 text-[12px] text-bad">Parallel memory read failed: {read.error}</p>}

      {read?.status === "OK" && read.entries.length === 0 && (
        <p className="mt-3 text-[12px] text-muted">
          Nothing in this scope yet.{" "}
          {nothingWritesYet
            ? "Memory is written by Task, Monitor and FindAll runs — create live monitors for a shoot day, or run a location dossier, and what they learn will show up here."
            : "Runs that write to memory have not produced anything for this query yet."}
        </p>
      )}

      {read?.status === "OK" && read.entries.length > 0 && (
        <ul className="mt-3 grid gap-2 text-[13px]">
          {read.entries.map((e) => (
            <EntryRow key={`${e.kind}:${e.ref_id}`} e={e} busy={busy} onEvict={() => evict(e)} />
          ))}
        </ul>
      )}

      {data && data.recent.length > 1 && (
        <div className="mt-3 text-[11px] text-dim">
          {data.recent.length} recall{data.recent.length === 1 ? "" : "s"} this session · every read is persisted and shows in the activity feed
        </div>
      )}
    </section>
  );
}
