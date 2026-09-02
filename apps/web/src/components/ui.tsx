import type { ClaimKind } from "@/lib/api";

export function Kicker({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`kicker ${className}`}>{children}</div>;
}

export function Stamp({ status }: { status: string }) {
  const map: Record<string, string> = {
    READY: "text-ok",
    AT_RISK: "text-bad",
    RECOVERY_PROPOSED: "text-warn",
    RECOVERED: "text-ok",
    WRAPPED: "text-dim",
  };
  return <span className={`stamp ${map[status] || "text-muted"}`}>{status.replace(/_/g, " ")}</span>;
}

export function KindChip({ kind }: { kind: ClaimKind | string }) {
  const map: Record<string, string> = { FACT: "chip-ok", INFERENCE: "chip-info", RECOMMENDATION: "chip-accent", UNKNOWN: "chip-warn" };
  return <span className={`chip ${map[kind] || "chip-dim"}`}>{kind}</span>;
}

export function StatusChip({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="chip chip-dim">pending</span>;
  const map: Record<string, string> = {
    SUPPORTED: "chip-ok", WEAK: "chip-warn", CONFLICTING: "chip-bad", MISSING: "chip-bad",
    CORROBORATED: "chip-ok", PARTIALLY_CORROBORATED: "chip-warn", UNCORROBORATED: "chip-dim", CONTRADICTED: "chip-bad",
    OK: "chip-ok", ERROR: "chip-bad", REPLAY: "chip-warn", PENDING: "chip-dim",
    CRITICAL: "chip-bad", HIGH: "chip-warn", MEDIUM: "chip-info", LOW: "chip-dim",
    RUNNING: "chip-info", COMPLETED: "chip-ok", FAILED: "chip-bad", AWAITING_APPROVAL: "chip-warn", APPLIED: "chip-ok",
    OFFICIAL: "chip-ok", NEWS: "chip-info", INDUSTRY: "chip-accent", COMMUNITY: "chip-dim", UNKNOWN: "chip-dim",
    CURRENT: "chip-ok", RECENT: "chip-info", DATED: "chip-warn",
  };
  return <span className={`chip ${map[status] || "chip-dim"}`}>{status.replace(/_/g, " ")}</span>;
}

export function Bar({ value, tone = "accent", label }: { value: number; tone?: "accent" | "ok" | "warn" | "bad" | "info"; label?: string }) {
  const color = { accent: "bg-accent", ok: "bg-ok", warn: "bg-warn", bad: "bg-bad", info: "bg-info" }[tone];
  return (
    <div className="flex items-center gap-2 text-[12px]">
      {label && <span className="w-24 lg:w-36 shrink-0 text-muted truncate">{label}</span>}
      <div className="flex-1 h-1.5 rounded bg-line overflow-hidden">
        <div className={`h-full ${color} transition-[width] duration-700`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
      </div>
      <span className="mono w-8 text-right text-muted">{Math.round(value)}</span>
    </div>
  );
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="card p-8 text-center">
      <div className="display text-lg">{title}</div>
      {hint && <p className="text-muted text-sm mt-1 max-w-md mx-auto">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Tells "the agent says this does not exist" apart from "the agent did not answer". */
export function LoadError({ error, missing, hint }: { error: string; missing: string; hint?: string }) {
  if (error.startsWith("404")) return <Empty title={missing} hint={hint || error} />;
  return <Empty title="The agent service is not answering" hint={`${error} — it is starting up or briefly unreachable. This page keeps retrying; nothing has been lost.`} />;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner inline-flex items-center gap-2 text-sm">
      <span className="spinner-dot w-2 h-2 rounded-full pulse" />
      {label}
    </span>
  );
}

export function Readiness({ score, size = 96 }: { score: number | null; size?: number }) {
  const r = (size - 10) / 2;
  const c = 2 * Math.PI * r;
  const v = score ?? 0;
  const tone = score === null ? "var(--line-strong)" : v >= 75 ? "var(--ok)" : v >= 50 ? "var(--warn)" : "var(--bad)";
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={score === null ? "Readiness not computed" : `Readiness ${score} of 100`}>
      <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--line)" strokeWidth={6} fill="none" />
      <circle cx={size / 2} cy={size / 2} r={r} stroke={tone} strokeWidth={6} fill="none" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - v / 100)} transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: "stroke-dashoffset .9s ease" }} />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" className="display" fill="var(--fg)" fontSize={size * 0.32} fontWeight={700}>
        {score === null ? "—" : score}
      </text>
    </svg>
  );
}
