import type { ExternalRule } from "@/lib/api";

/**
 * Find the words in a cited excerpt that state the window a rule holds.
 *
 * The engine stores a rule ("no work 22:00–06:00") and, separately, the excerpts Parallel cited. It
 * does not store which characters said so — so rather than adding a field and a migration for
 * something derivable, this re-derives it.
 *
 * **This asks a different question from `services/dossier.py`, deliberately.** That parser decides
 * whether a *rule may exist at all*, so it is narrow on purpose: a window it misreads becomes a hard
 * constraint that rejects real schedules. This one decides whether a sentence *states the window the
 * rule already holds*, which is only ever a claim about where to draw a mark. So it recognises more
 * spellings of the same clock — the statute Parallel actually cites reads "from 10.00 p.m. to 6.00
 * a.m.", which no schedule-affecting parser needs to understand, and which is exactly the sentence a
 * producer should see marked before accepting it.
 *
 * The contract that keeps that safe: a match is returned **only** when the text resolves to exactly
 * the window the rule holds. Recognising a new notation can therefore never move a highlight onto
 * text that says something else; it can only stop one being missed. Anything unmatched renders
 * unmarked, because a mark in the wrong place is a claim the source did not make.
 */

// "22:00-06:00", "22.00 to 06.00"
const RANGE = /(\d{1,2})[:.](\d{2})\s*(?:-|–|—|to|until|till)\s*(\d{1,2})[:.](\d{2})/g;
// "10 pm – 6 am", "10.00 p.m. to 6.00 a.m.", "10:30pm-6am" — optional minutes, dots and spacing.
const RANGE_AMPM =
  /(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?\s?m\.?\s*(?:-|–|—|to|until|till)\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?\s?m\.?/gi;
const PROHIBITED = /\b(prohibit\w*|forbidden|banned|not permitted|not allowed|no-fly|no fly|disallowed)\b/i;

const hhmm = (h: number, m: number) => `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
const to24 = (h: string, meridiem: string) => (Number(h) % 12) + (meridiem.toLowerCase().startsWith("p") ? 12 : 0);

export interface ClauseMatch {
  start: number;
  end: number;
}

export function findRuleClause(excerpt: string, rule: ExternalRule | null | undefined): ClauseMatch | null {
  if (!excerpt || !rule) return null;

  if (rule.kind === "TIME_WINDOW_BAN") {
    if (!rule.window_start || !rule.window_end) return null;

    for (const m of excerpt.matchAll(RANGE)) {
      const [h1, m1, h2, m2] = m.slice(1, 5).map(Number);
      // The same sanity check the backend applies before it will believe a range.
      if (h1 >= 24 || h2 >= 24 || m1 >= 60 || m2 >= 60) continue;
      if (hhmm(h1, m1) === rule.window_start && hhmm(h2, m2) === rule.window_end) {
        return { start: m.index!, end: m.index! + m[0].length };
      }
    }

    for (const m of excerpt.matchAll(RANGE_AMPM)) {
      const [, h1, min1, ap1, h2, min2, ap2] = m;
      const start = hhmm(to24(h1, ap1), Number(min1 ?? 0));
      const end = hhmm(to24(h2, ap2), Number(min2 ?? 0));
      if (start === rule.window_start && end === rule.window_end) {
        return { start: m.index!, end: m.index! + m[0].length };
      }
    }
    return null;
  }

  if (rule.kind === "ACTIVITY_BAN") {
    const m = PROHIBITED.exec(excerpt);
    return m ? { start: m.index, end: m.index + m[0].length } : null;
  }

  return null;
}
