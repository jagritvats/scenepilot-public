"use client";

import { useEffect, useState } from "react";
import { api, type AgentGraph as Graph } from "@/lib/api";

/**
 * The pipeline, as the engine actually holds it.
 *
 * This is not an illustration of the orchestrator — it is `GET /api/agent-graph`, which serialises
 * the same `google.adk.workflow.Workflow` object the run executes. Rename a node in Python and it
 * is renamed here; delete a stage and it stops being drawn. That is the only reason a diagram like
 * this is worth putting in a product: a picture can be out of date, a projection cannot.
 *
 * Node names are the same strings a run reports as its stage, so the live node can be highlighted
 * without a second mapping to keep in sync.
 */

/** Node order comes from the graph's own edge order, so the row reads in pipeline order. */
function useGraph(name: string) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [runtime, setRuntime] = useState<string>("");
  useEffect(() => {
    let live = true;
    api
      .agentGraph()
      .then((c) => {
        if (!live) return;
        setGraph(c.graphs.find((g) => g.name === name) ?? null);
        setRuntime(c.runtime);
      })
      .catch(() => setGraph(null));
    return () => {
      live = false;
    };
  }, [name]);
  return { graph, runtime };
}

export function AgentGraph({
  name,
  stage,
  status,
  aside,
}: {
  name: string;
  stage?: string | null;
  /** The run's status, so a finished run reads as finished rather than parked on its last node. */
  status?: string | null;
  aside?: React.ReactNode;
}) {
  const { graph, runtime } = useGraph(name);
  if (!graph) return null;

  const names = graph.nodes.map((n) => n.name);
  const at = stage ? names.indexOf(stage) : -1;
  const finished = status === "COMPLETED" || status === "APPLIED";
  const failed = status === "FAILED";
  const routed = graph.edges.filter((e) => e.route);
  // The back edge — one that returns to a node the row has already passed — is the loop.
  const back = graph.edges.find((e) => names.indexOf(e.to) >= 0 && names.indexOf(e.to) < names.indexOf(e.from));

  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-2.5 border-b border-line flex items-center justify-between gap-3 flex-wrap">
        <div className="kicker">Pipeline · ADK Workflow</div>
        {aside}
        <span className="mono text-[10px] text-dim" title={`${runtime} — served by GET /api/agent-graph, so this is the graph that runs`}>
          {graph.name}
        </span>
      </div>

      <div className="p-3">
        <ol className="flex flex-wrap items-center gap-x-1 gap-y-1.5">
          {graph.nodes.map((n, i) => {
            const live = !finished && i === at;
            const done = finished || (at >= 0 && i < at);
            const terminal = graph.terminal.includes(n.name);
            return (
              <li key={n.name} className="flex items-center gap-1">
                <span
                  title={n.description}
                  className={
                    "rounded px-1.5 py-0.5 text-[11px] border " +
                    (failed && i === at
                      ? "border-bad bg-bad/10 text-bad"
                      : live
                        ? "border-accent bg-accent/10 text-accent"
                        : done
                          ? "border-line text-muted"
                          : "border-line/60 text-dim")
                  }
                >
                  {live && <span className="mr-1 animate-pulse">●</span>}
                  {n.name.replace(/_/g, " ")}
                  {terminal && <span className="ml-1 text-dim" title="Terminal node — nothing downstream of it exists">■</span>}
                </span>
                {i < graph.nodes.length - 1 && <span className="text-dim text-[11px]">→</span>}
              </li>
            );
          })}
        </ol>

        {routed.length > 0 && (
          <div className="mt-2.5 space-y-1 text-[11px] text-dim">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="uppercase tracking-[0.14em]">Conditional</span>
              {routed.map((e) => (
                <span key={`${e.from}-${e.to}`} className="mono">
                  {e.from} —{e.route}→ {e.to}
                </span>
              ))}
            </div>
            {back && (
              <p>
                <span className="mono">
                  {back.from} → {back.to}
                </span>{" "}
                closes the cycle — research → evaluate → research again, bounded, and taken only while a question is still unsupported.
              </p>
            )}
          </div>
        )}
        {routed.length === 0 && graph.terminal.length > 0 && (
          <p className="mt-2 text-[11px] text-dim">
            <span className="mono">{graph.terminal.join(", ")}</span> is terminal: applying a change is not a step the pipeline may take on its own.
          </p>
        )}
      </div>
    </div>
  );
}
