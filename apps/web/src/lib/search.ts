import type { Project, ActivityEvent } from "@/lib/api";

/**
 * Searching this production's own state.
 *
 * Deliberately client-side. Everything searchable — scenes, resources, discovered facts, shoot days,
 * monitors — already arrives on the single project document the page has fetched, and the activity
 * feed is one more call the log page already makes. A `/search` endpoint would be new surface
 * carrying no new truth, and a round trip per keystroke for nine scenes would be slower than the
 * filter it replaced.
 *
 * Two rules keep the results honest:
 *
 * 1. **Fields are named, never swept.** `Scene.script_text` and a brief's `raw_text` are whole pages
 *    of prose; including them would make every query match everything and rank the noise first.
 * 2. **A result links to somewhere that exists.** Every hit carries a real route. A fact belongs to a
 *    location, and a location is only reachable through a day that books it — so a fact whose
 *    location no day books links to the project rather than to a page that would 404.
 */

export type SearchKind = "scene" | "day" | "resource" | "fact" | "activity" | "action";

export interface SearchHit {
  kind: SearchKind;
  id: string;
  title: string;
  subtitle: string;
  href: string;
  /** Lower sorts first. Set by how directly the query matched, not by entity type. */
  rank: number;
  /** Present on the three demo actions; the palette runs these instead of navigating. */
  run?: () => void;
}

const KIND_LABEL: Record<SearchKind, string> = {
  scene: "Scene",
  day: "Shoot day",
  resource: "Resource",
  fact: "Discovered fact",
  activity: "Log entry",
  action: "Action",
};

export const kindLabel = (k: SearchKind) => KIND_LABEL[k];

/** 0 for a prefix hit, 1 for a word-start hit, 2 for anything else, -1 for no match. */
function score(haystack: string | null | undefined, needle: string): number {
  if (!haystack) return -1;
  const h = haystack.toLowerCase();
  const at = h.indexOf(needle);
  if (at < 0) return -1;
  if (at === 0) return 0;
  return /\s|[-–—/·,(]/.test(h[at - 1]) ? 1 : 2;
}

/** The best score across several fields. */
function best(needle: string, ...fields: (string | null | undefined)[]): number {
  let out = -1;
  for (const f of fields) {
    const s = score(f, needle);
    if (s >= 0 && (out < 0 || s < out)) out = s;
  }
  return out;
}

export function searchProject(
  project: Project,
  events: ActivityEvent[],
  query: string,
  limit = 24,
): SearchHit[] {
  const needle = query.trim().toLowerCase();
  if (needle.length < 2) return [];
  const pid = project.id;
  const hits: SearchHit[] = [];

  // Which day books a scene, so a scene result can point at the board that shows it.
  const dayOfScene = new Map<string, { id: string; day_number: number }>();
  for (const day of project.shoot_days) {
    for (const item of day.items) dayOfScene.set(item.scene_id, { id: day.id, day_number: day.day_number });
  }
  // Which day books a location, for the same reason on a fact.
  const dayOfResource = new Map<string, { id: string; day_number: number }>();
  for (const day of project.shoot_days) {
    for (const item of day.items) {
      const loc = item.location_id ?? project.scenes.find((s) => s.id === item.scene_id)?.location_id;
      if (loc && !dayOfResource.has(loc)) dayOfResource.set(loc, { id: day.id, day_number: day.day_number });
    }
  }

  for (const scene of project.scenes) {
    const rank = best(needle, `sc ${scene.number}`, scene.number, scene.heading, scene.synopsis, scene.continuity_group);
    if (rank < 0) continue;
    const day = dayOfScene.get(scene.id);
    hits.push({
      kind: "scene",
      id: scene.id,
      title: `Sc ${scene.number} — ${scene.heading}`,
      subtitle: day ? `Day ${day.day_number}${scene.is_cover ? " · cover set" : ""}` : "not scheduled",
      href: `/projects/${pid}/scenes/${scene.id}`,
      rank,
    });
  }

  for (const day of project.shoot_days) {
    const rank = best(needle, `day ${day.day_number}`, day.date, day.status, day.notes);
    if (rank < 0) continue;
    hits.push({
      kind: "day",
      id: day.id,
      title: `Day ${day.day_number} — ${day.date}`,
      subtitle: `${day.items.length} scene(s) · call ${day.unit_call} · ${day.status.toLowerCase().replace(/_/g, " ")}`,
      href: `/projects/${pid}/days/${day.id}`,
      rank,
    });
  }

  for (const resource of project.resources) {
    const rank = best(needle, resource.name, resource.type, resource.locality);
    if (rank < 0) continue;
    const day = dayOfResource.get(resource.id);
    hits.push({
      kind: "resource",
      id: resource.id,
      title: resource.name,
      subtitle: `${resource.type.toLowerCase()}${resource.cast_number ? ` · cast ${resource.cast_number}` : ""}`,
      href: day ? `/projects/${pid}/days/${day.id}` : `/projects/${pid}`,
      rank,
    });
  }

  for (const fact of project.location_facts) {
    const rank = best(needle, fact.label, fact.value, fact.key);
    if (rank < 0) continue;
    const day = dayOfResource.get(fact.resource_id);
    hits.push({
      kind: "fact",
      id: fact.id,
      title: `${fact.label}: ${fact.value}`,
      subtitle: `${fact.binding.toLowerCase()}${fact.accepted ? " · accepted" : fact.rejected ? " · rejected" : " · not yet decided"}`,
      href: day ? `/projects/${pid}/days/${day.id}` : `/projects/${pid}`,
      rank,
    });
  }

  for (const event of events) {
    const rank = best(needle, event.message);
    if (rank < 0) continue;
    hits.push({
      kind: "activity",
      id: event.id,
      title: event.message,
      subtitle: `${event.kind} · ${new Date(event.ts).toLocaleString()}`,
      href: `/projects/${pid}/log`,
      rank,
    });
  }

  hits.sort((a, b) => a.rank - b.rank || a.title.localeCompare(b.title));
  return hits.slice(0, limit);
}

/** The places and demo actions a palette offers before anything is typed. */
export function paletteRoutes(projectId: string, dayId: string): SearchHit[] {
  return [
    { kind: "day", id: "hero", title: "Shoot Day 4 — the hero day", subtitle: "the board, the disruption, the recovery", href: `/projects/${projectId}/days/${dayId}`, rank: 0 },
    { kind: "day", id: "callsheet", title: "Call sheet", subtitle: `Day ${dayId.replace("day_", "")} · the document the unit works from`, href: `/projects/${projectId}/days/${dayId}/call-sheet`, rank: 0 },
    { kind: "day", id: "sides", title: "Sides", subtitle: "the day's pages, in shooting order", href: `/projects/${projectId}/days/${dayId}/sides`, rank: 0 },
    { kind: "day", id: "movement", title: "Movement order", subtitle: "the day's transport", href: `/projects/${projectId}/days/${dayId}/movement-order`, rank: 0 },
    { kind: "day", id: "log", title: "Production log", subtitle: "who decided what, on what evidence", href: `/projects/${projectId}/log`, rank: 0 },
    { kind: "day", id: "inbox", title: "Inbox", subtitle: "fact drift and monitor-raised disruptions waiting on a decision", href: `/projects/${projectId}/inbox`, rank: 0 },
    { kind: "day", id: "risks", title: "Risk register", subtitle: "severity × likelihood, across the production", href: `/projects/${projectId}/risks`, rank: 0 },
    { kind: "day", id: "screenplay", title: "Screenplay Studio", subtitle: "the draft, the breakdown, the DOOD", href: `/projects/${projectId}/screenplay`, rank: 0 },
  ];
}
