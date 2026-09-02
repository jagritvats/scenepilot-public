"use client";

import type { BasisCitation } from "@/lib/api";

export const hostname = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

/**
 * The sources behind one Basis field, as chips that link out.
 *
 * Shared rather than local because the same row of chips is what makes a dossier fact, a drift
 * notice and a researched weather hour all readable as the same kind of claim: something a named
 * source said, with the excerpt it said it in one hover away.
 */
export function Citations({ citations }: { citations: BasisCitation[] }) {
  if (citations.length === 0) return null;
  return (
    <>
      {citations.map((c) => (
        <a key={c.url} href={c.url} target="_blank" rel="noopener noreferrer" className="chip chip-parallel hover:underline" title={c.excerpts[0] || c.url}>
          {c.title || hostname(c.url)}
        </a>
      ))}
    </>
  );
}
