/* Strip-board conventions, shared by every view that paints a strip.
 *
 * The colour code is the industry one and the CSS custom properties in globals.css already carry it:
 * white INT/DAY, yellow EXT/DAY, blue INT/NIGHT, green EXT/NIGHT, plus ScenePilot's own dusk amber
 * for the golden-hour scenes the validator holds to a window. */


export type BoardTimeKey = "DAY" | "NIGHT" | "DUSK" | "DAWN";

/**
 * The three fields a strip is coloured and labelled from.
 *
 * Structural rather than `Scene`, because the one-liner reads the same three off its own rows and
 * carrying a whole `Scene` there just to paint a swatch would mean the two documents could drift.
 * `Scene` satisfies this by construction.
 */
export interface StripScene {
  int_ext: "INT" | "EXT";
  time_of_day: string;
  heading: string;
}

export interface BoardTimeOfDay {
  /** null = the scene's day/night is genuinely not on file; nothing here guesses one. */
  key: BoardTimeKey | null;
  /** Which real field the answer came from — `Scene.time_of_day`, or the slugline when that is ANY. */
  source: "time_of_day" | "heading" | null;
  /** What the D/N column prints. */
  label: string;
  /** Why it prints that, for the cell's tooltip. Null when `time_of_day` simply said so. */
  note: string | null;
}

const LABEL: Record<BoardTimeKey, string> = { DAY: "D", NIGHT: "N", DUSK: "DUSK", DAWN: "DAWN" };

/* Only tokens that name a time of day without ambiguity. EVENING, CONTINUOUS, LATER and MOMENTS
 * LATER are deliberately absent: a board that reads them has decided something the draft did not. */
const SLUG_TIME: Record<string, BoardTimeKey> = {
  DAY: "DAY",
  MORNING: "DAY",
  AFTERNOON: "DAY",
  NIGHT: "NIGHT",
  SUNSET: "DUSK",
  DUSK: "DUSK",
  DAWN: "DAWN",
  SUNRISE: "DAWN",
};

const SLUG_TAIL = /(?:^|[\s.,\-–—])(DAY|MORNING|AFTERNOON|NIGHT|SUNSET|DUSK|DAWN|SUNRISE)\s*$/;

/**
 * The day/night a strip is coloured and labelled by.
 *
 * `Scene.time_of_day` is the authority wherever it commits to one. `ANY` is not a fifth time of day
 * — the domain uses it for "this scene can shoot at any hour", which is how a stage interior gets
 * scheduled at noon while playing as night (Sc 62, `INT. APARTMENT — NIGHT`). Painting those white
 * would put a day interior on the board for a scene the script calls night, so the D/N falls back to
 * where a real board has always read it: the slugline. Marked as heading-derived wherever it shows.
 */
export function boardTimeOfDay(scene: StripScene | undefined): BoardTimeOfDay {
  const tod = (scene?.time_of_day || "").toUpperCase();
  if (tod === "DAY" || tod === "NIGHT") return { key: tod, source: "time_of_day", label: LABEL[tod], note: null };
  if (tod === "SUNSET") return { key: "DUSK", source: "time_of_day", label: "DUSK", note: "SUNSET — held to the day's golden-hour dusk window by the validator." };
  if (tod === "DAWN") return { key: "DAWN", source: "time_of_day", label: "DAWN", note: "DAWN — the morning golden-hour window." };
  if (tod === "ANY" && scene) {
    const match = SLUG_TAIL.exec(scene.heading.toUpperCase());
    const key = match ? SLUG_TIME[match[1]] : undefined;
    if (key) {
      return {
        key,
        source: "heading",
        label: LABEL[key],
        note: `time_of_day is ANY — the scene is unconstrained for scheduling, so it carries no day/night of its own. D/N read from the slugline: "${scene.heading}".`,
      };
    }
    return { key: null, source: null, label: "ANY", note: "time_of_day is ANY and the slugline names no time of day — the board leaves D/N unset rather than choose one." };
  }
  return { key: null, source: null, label: tod || "—", note: tod ? null : "This scene carries no time of day." };
}

/** The `.strip-*` background class for a scene, or null when its day/night is not on file. */
export function stripToneClass(scene: StripScene | undefined): string | null {
  if (!scene) return null;
  const { key } = boardTimeOfDay(scene);
  if (key === null) return null;
  if (key === "DUSK" || key === "DAWN") return "strip-dusk";
  const night = key === "NIGHT";
  if (scene.int_ext === "EXT") return night ? "strip-ext-night" : "strip-ext-day";
  return night ? "strip-int-night" : "strip-int-day";
}

/** Page count the way a board writes it: eighths of a page, whole pages carried out front. */
export function eighthsLabel(eighths: number): string {
  const whole = Math.floor(eighths / 8);
  const rest = eighths % 8;
  if (whole && rest) return `${whole} ${rest}/8`;
  if (whole) return `${whole}`;
  return `${rest}/8`;
}

/** "Aarav Mehta (Rider / lead)" → "Aarav Mehta"; "Rooftop A — Sitara Mills" → "Rooftop A". */
export const shortName = (name: string) => name.split(" — ")[0].split(" (")[0].trim();

/** A performer as every board-side view holds one: the production's name, and its cast number if any. */
export interface CastMember {
  name: string;
  cast_number: number | null;
}

export interface CastColumn {
  /** What the cell prints: cast numbers, or names when the numbering does not cover the scene. */
  text: string;
  /** True while `text` is numbers, so a caller can caption the column it is drawing. */
  numbered: boolean;
  /** Full names for the tooltip — decoded against their numbers wherever the cell prints numbers. */
  title: string;
}

/**
 * The cast column a strip prints.
 *
 * The trade convention is numbers: "1, 2" fits a strip where "Aarav Mehta, Meera Iyer" does not, and
 * the number is the key the DOOD, the call sheet and the dispatch all join on. It holds only while
 * every performer in the scene carries one — a cell reading "1, 2, —" would be inventing a notation
 * for a gap in the production's own numbering, so one unnumbered performer drops the cell to names.
 * Numbers print in billing order, which is the order a board reads them in, not the order the scene
 * happens to list its `cast_ids`.
 */
export function castColumn(cast: CastMember[]): CastColumn {
  const numbered = cast.length > 0 && cast.every((c) => typeof c.cast_number === "number");
  const ordered = numbered ? [...cast].sort((a, b) => (a.cast_number as number) - (b.cast_number as number)) : cast;
  return {
    text: ordered.map((c) => (numbered ? String(c.cast_number) : shortName(c.name))).join(", "),
    numbered,
    title: ordered.map((c) => (numbered ? `${c.cast_number} — ${c.name}` : c.name)).join(" · "),
  };
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** `2026-09-01` → `Mon 1 Sep 2026`. Formatted by hand so the server and the browser cannot disagree. */
export function boardDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (Number.isNaN(d.getTime())) return iso;
  return `${WEEKDAYS[d.getUTCDay()]} ${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}


/**
 * Which scenes are chained to which by continuity, and where the rest of the chain shoots.
 *
 * `continuity_group` has been on every scene since the seed and drawn nowhere. It matters because
 * the engine already prices splitting one: deferring a scene whose group-mates shoot today is a soft
 * violation worth 15 creative points, and a producer looking at the board had no way to see which
 * strips were tied together before making that trade.
 *
 * Cross-day is the interesting case and the one this reports: on this production the rooftop chase
 * runs Day 4 → Day 6, so "already split across days" is the schedule's normal state, not an error.
 * The marker says which day the rest of the chain is on rather than implying something is wrong.
 */
export interface ContinuityChain {
  group: string;
  /** Every scene in the group, whether or not it is scheduled. */
  sceneIds: string[];
  /** Day numbers the group shoots across, ascending. Empty for an unscheduled group. */
  dayNumbers: number[];
  /** True when the group is spread over more than one day — a fact, not a fault. */
  crossesDays: boolean;
}

export function continuityChains(
  scenes: { id: string; continuity_group?: string | null }[],
  dayOfScene: Map<string, number>,
): Map<string, ContinuityChain> {
  const byGroup = new Map<string, string[]>();
  for (const scene of scenes) {
    const group = scene.continuity_group;
    if (!group) continue;
    byGroup.set(group, [...(byGroup.get(group) ?? []), scene.id]);
  }

  const out = new Map<string, ContinuityChain>();
  for (const [group, sceneIds] of byGroup) {
    // A group of one is not a chain; nothing can be split from itself.
    if (sceneIds.length < 2) continue;
    const dayNumbers = [...new Set(sceneIds.map((id) => dayOfScene.get(id)).filter((d): d is number => d !== undefined))].sort((a, b) => a - b);
    const chain: ContinuityChain = { group, sceneIds, dayNumbers, crossesDays: dayNumbers.length > 1 };
    for (const id of sceneIds) out.set(id, chain);
  }
  return out;
}
