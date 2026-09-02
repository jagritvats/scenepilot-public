import type { ConstraintViolation, ExternalRule, LocationFact } from "@/lib/api";
import { findRuleClause } from "@/lib/ruleClause";

/**
 * Why an option was rejected, traced back to the page that said so.
 *
 * The cost waterfall answers "what does this cost?"; this answers "who says?". Every link is real
 * state: a Parallel Task run produced a fact, the fact carried a citation, a producer accepted it,
 * and the deterministic engine turned it into this rejection. Four links, no interpretation.
 */

const hostname = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

/**
 * A cited excerpt with the clause the rule was parsed from marked inside it.
 *
 * This is the difference between "a source says something about this location" and "*these words*
 * are why your option is red". When the clause cannot be located the excerpt is rendered plainly and
 * clamped as before — never a guessed highlight, and never a claim about which sentence bound.
 */
export function ExcerptWithClause({ excerpt, rule }: { excerpt: string; rule?: ExternalRule | null }) {
  const hit = findRuleClause(excerpt, rule);
  if (!hit) {
    return <p className="mt-0.5 border-l-2 border-parallel/50 pl-2 text-[11px] text-muted line-clamp-3">{excerpt}</p>;
  }
  return (
    <p className="mt-0.5 border-l-2 border-parallel/50 pl-2 text-[11px] text-muted">
      {excerpt.slice(0, hit.start)}
      <mark className="rounded bg-accent/20 px-0.5 text-inherit outline outline-1 outline-accent/50">{excerpt.slice(hit.start, hit.end)}</mark>
      {excerpt.slice(hit.end)}
    </p>
  );
}

function Link({ n, label, children, last = false }: { n: number; label: string; children: React.ReactNode; last?: boolean }) {
  return (
    <li className="relative pl-6 pb-2 last:pb-0">
      <span className="absolute left-0 top-0.5 flex h-4 w-4 items-center justify-center rounded-full border border-parallel/60 bg-elev text-[9px] font-bold text-parallel">{n}</span>
      {!last && <span className="absolute left-2 top-5 bottom-0 w-px bg-parallel/30" aria-hidden="true" />}
      <div className="text-[10px] uppercase tracking-wider text-dim">{label}</div>
      <div className="text-[12px]">{children}</div>
    </li>
  );
}

export function ProvenanceChain({ violation, fact }: { violation: ConstraintViolation; fact?: LocationFact }) {
  const citation = fact?.citations?.[0];
  const url = citation?.url || violation.evidence_url;
  if (!fact && !url) return null;
  const rule = fact?.rule;
  return (
    <div className="mt-1.5 rounded border border-parallel/40 bg-parallel/5 p-2.5">
      <div className="kicker mb-1.5">Why this was rejected</div>
      <ol className="space-y-0">
        <Link n={1} label="Parallel Task research">
          {fact ? (
            <span>
              Location dossier <span className="mono text-[11px] text-dim">{fact.task_run_id}</span>
              {fact.confidence && <span className="text-muted"> · Parallel confidence {fact.confidence}</span>}
            </span>
          ) : (
            <span className="text-muted">a dossier run for this location</span>
          )}
        </Link>

        <Link n={2} label="cited source">
          {url ? (
            <a href={url} target="_blank" rel="noopener noreferrer" className="text-info hover:underline">
              {citation?.title || hostname(url)}
            </a>
          ) : (
            <span className="text-muted">no citation recorded</span>
          )}
          {citation?.excerpts?.[0] && <ExcerptWithClause excerpt={citation.excerpts[0]} rule={rule} />}
        </Link>

        <Link n={3} label="accepted as a constraint">
          {fact ? (
            <span>
              <span className="font-medium">{fact.value}</span>
              {rule && (
                <span className="ml-1 chip chip-parallel">
                  {rule.kind === "TIME_WINDOW_BAN" ? `no work ${rule.window_start}–${rule.window_end}` : `no ${rule.activity}`}
                </span>
              )}
              {fact.accepted_by && <span className="text-dim"> · accepted by {fact.accepted_by}</span>}
            </span>
          ) : (
            <span className="text-muted">an accepted external rule</span>
          )}
        </Link>

        <Link n={4} label="this option" last>
          <span className="text-bad">{violation.message}</span>
        </Link>
      </ol>
    </div>
  );
}
