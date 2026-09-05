"use client";

import { toMin, type Change, type ChangeSet } from "@/lib/api";
import { Kicker } from "./ui";

/** `geography` on the shoot-day payload (services/geo.py). Narrowed here rather than in lib/api.ts. */
export interface DayLocation {
  id: string;
  name: string;
  kind: string | null;
  locality: string | null;
  latitude: number | null;
  longitude: number | null;
  order: number;
  scene_numbers: string[];
  first_start: string;
  last_end: string;
}

export interface CompanyMove {
  from_location_id: string;
  from_name: string;
  from_latitude: number | null;
  from_longitude: number | null;
  to_location_id: string;
  to_name: string;
  to_latitude: number | null;
  to_longitude: number | null;
  straight_line_km: number | null;
  travel_minutes: number | null;
  after_scene: string;
  before_scene: string;
  wrap_at: string;
  next_shot_at: string;
  departure: string | null;
  transport_leg_id: string | null;
  vehicle_id: string | null;
  vehicle_name: string | null;
}

export interface DayGeography {
  locations: DayLocation[];
  moves: CompanyMove[];
  move_count: number;
  total_straight_line_km: number | null;
  total_travel_minutes: number | null;
  locations_missing_coordinates: { id: string; name: string }[];
  distance_basis: string;
  travel_minutes_basis: string;
  coordinates_basis: string;
}

/* ---- projection: the real coordinates, drawn to scale, north up ---- */
const EARTH_RADIUS_KM = 6371.0088; // IUGG mean radius — the same constant services/geo.py uses
const DEG = Math.PI / 180;
const VB_W = 340;
const VB_H = 300;
const FOOTER = 30; // reserved for the scale bar and the north arrow
const INNER = 228; // user units the longer axis of the real extent is drawn across
const NARROW = 120; // below this drawn width the geometry is left-anchored to leave room for labels
const CURVE = 15; // user units a leg bows off its chord so overlapping legs stay tellable apart
const PIN_CLEAR = 8; // user units a leg stops short of the pin it joins
const SCALE_STEPS_KM = [0.5, 1, 2, 5, 10, 20, 50, 100, 200];

type Node = DayLocation & { latitude: number; longitude: number; x: number; y: number };

function project(locations: DayLocation[]): { nodes: Node[]; unitsPerKm: number } | null {
  const placed = locations.filter((l): l is DayLocation & { latitude: number; longitude: number } => l.latitude !== null && l.longitude !== null);
  if (placed.length < 2) return null;

  const lat0 = placed.reduce((s, l) => s + l.latitude, 0) / placed.length;
  const kmPerDegLat = EARTH_RADIUS_KM * DEG;
  const kmPerDegLon = kmPerDegLat * Math.cos(lat0 * DEG);
  const lon0 = placed.reduce((s, l) => s + l.longitude, 0) / placed.length;

  const km = placed.map((l) => ({ l, kx: (l.longitude - lon0) * kmPerDegLon, ky: -(l.latitude - lat0) * kmPerDegLat }));
  const minKx = Math.min(...km.map((k) => k.kx));
  const maxKx = Math.max(...km.map((k) => k.kx));
  const minKy = Math.min(...km.map((k) => k.ky));
  const maxKy = Math.max(...km.map((k) => k.ky));

  const spanKm = Math.max(maxKx - minKx, maxKy - minKy, 0.2);
  const unitsPerKm = INNER / spanKm;
  const w = (maxKx - minKx) * unitsPerKm;
  const h = (maxKy - minKy) * unitsPerKm;
  const ox = w <= NARROW ? 44 : (VB_W - w) / 2;
  const oy = (VB_H - FOOTER - h) / 2;

  return {
    nodes: km.map(({ l, kx, ky }) => ({ ...l, x: ox + (kx - minKx) * unitsPerKm, y: oy + (ky - minKy) * unitsPerKm })),
    unitsPerKm,
  };
}

const setName = (name: string) => name.split(" — ")[0];
const gapMinutes = (m: CompanyMove) => toMin(m.next_shot_at) - toMin(m.wrap_at);

function MoveMap({ locations, moves }: { locations: DayLocation[]; moves: CompanyMove[] }) {
  const projected = project(locations);
  if (!projected) return null;
  const { nodes, unitsPerKm } = projected;
  const at = new Map(nodes.map((n) => [n.id, n]));

  const bar = SCALE_STEPS_KM.filter((s) => s * unitsPerKm <= INNER * 0.45).pop() ?? SCALE_STEPS_KM[0];

  return (
    <svg viewBox={`0 0 ${VB_W} ${VB_H}`} className="w-full" style={{ height: VB_H }} role="img" aria-label="Company move geometry drawn from the locations' real coordinates">
      <defs>
        <marker id="cm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)" />
        </marker>
      </defs>

      {/* scale bar and north arrow, kept out of the drawing area */}
      <g transform={`translate(14 ${VB_H - 11})`}>
        <path d={`M 0 0 L ${(bar * unitsPerKm).toFixed(1)} 0`} stroke="var(--fg-dim)" strokeWidth="1.5" />
        <path d="M 0 -4 L 0 4" stroke="var(--fg-dim)" strokeWidth="1.5" />
        <path d={`M ${(bar * unitsPerKm).toFixed(1)} -4 L ${(bar * unitsPerKm).toFixed(1)} 4`} stroke="var(--fg-dim)" strokeWidth="1.5" />
        <text x={(bar * unitsPerKm + 8).toFixed(1)} y="3.5" fontSize="9" fill="var(--fg-dim)" className="mono">{bar} km</text>
      </g>
      <g transform={`translate(${VB_W - 20} ${VB_H - 4})`}>
        <path d="M 0 0 L 0 -18" stroke="var(--line-strong)" strokeWidth="1" />
        <path d="M -3.5 -13 L 0 -22 L 3.5 -13 z" fill="var(--line-strong)" />
        <text x="-8" y="-2" textAnchor="end" fontSize="9" fill="var(--fg-dim)">N</text>
      </g>

      {moves.map((m, i) => {
        const a = at.get(m.from_location_id);
        const b = at.get(m.to_location_id);
        if (!a || !b) return null;
        const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
        const off = (i % 2 === 0 ? 1 : -1) * CURVE;
        const cx = (a.x + b.x) / 2 - ((b.y - a.y) / len) * off;
        const cy = (a.y + b.y) / 2 + ((b.x - a.x) / len) * off;
        // pull both ends clear of the pins so the arrowhead is not hidden under the circle it points at
        const inLen = Math.hypot(cx - a.x, cy - a.y) || 1;
        const outLen = Math.hypot(b.x - cx, b.y - cy) || 1;
        const sx = a.x + ((cx - a.x) / inLen) * PIN_CLEAR;
        const sy = a.y + ((cy - a.y) / inLen) * PIN_CLEAR;
        const ex = b.x - ((b.x - cx) / outLen) * (PIN_CLEAR + 4);
        const ey = b.y - ((b.y - cy) / outLen) * (PIN_CLEAR + 4);
        // the label sits on the side the leg bows to, so two legs over the same ground do not collide
        const lx = (a.x + 2 * cx + b.x) / 4 + (i % 2 === 0 ? -7 : 7);
        const ly = (a.y + 2 * cy + b.y) / 4;
        return (
          <g key={`${m.from_location_id}-${m.to_location_id}-${i}`}>
            {/* dashed, because this is a great circle between two locality centres and not a road */}
            <path d={`M ${sx.toFixed(1)} ${sy.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${ex.toFixed(1)} ${ey.toFixed(1)}`} fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeOpacity="0.75" strokeDasharray="5 3" markerEnd="url(#cm-arrow)" />
            <text x={lx.toFixed(1)} y={ly.toFixed(1)} textAnchor={i % 2 === 0 ? "end" : "start"} fontSize="9" fill="var(--accent)" className="mono">
              {m.straight_line_km === null ? "?" : `${m.straight_line_km} km`}
            </text>
          </g>
        );
      })}

      {nodes.map((n) => {
        const flip = n.x > VB_W * 0.55;
        const lx = flip ? n.x - 13 : n.x + 13;
        const anchor = flip ? "end" : "start";
        return (
          <g key={n.id}>
            <circle cx={n.x} cy={n.y} r="7" fill="var(--bg-card)" stroke="var(--fg-muted)" strokeWidth="1.5" />
            <text x={n.x} y={n.y + 3} textAnchor="middle" fontSize="9" fontWeight="700" fill="var(--fg)" className="mono">{n.order}</text>
            {/* one line per pin: two locations 2 km apart are 20 units apart here, and a taller
                label block would overlap its neighbour's. The rest is in the legend below. */}
            <text x={lx} y={n.y + 3} textAnchor={anchor} fontSize="11" fill="var(--fg)">{setName(n.name)}</text>
          </g>
        );
      })}
    </svg>
  );
}

/**
 * A company move is the whole unit relocating between locations mid-day; it eats shooting time.
 * Everything drawn here comes off the day's own schedule and the `geography` block the engine
 * publishes: real locality coordinates, a great-circle distance that is never called a driving
 * distance, and the production's own travel times — never a distance divided by an assumed speed.
 */
export function CompanyMovePanel({
  geography,
  changeset,
  applied,
  boardOnBaseline,
  extraMoveCostInr,
}: {
  geography: DayGeography;
  changeset?: ChangeSet | null;
  applied?: boolean;
  /** The stripboard is showing the pre-recovery schedule. This panel is server-computed from the
   *  day's stored (applied) schedule and cannot follow that toggle, so it has to say so. */
  boardOnBaseline?: boolean;
  extraMoveCostInr?: number;
}) {
  const { locations, moves } = geography;
  // Returning null here deleted the panel outright on a day whose strips carry no set — the reader
  // could not tell an absent feature from an absent location. Say which it is.
  if (locations.length === 0) {
    return (
      <section className="card p-4">
        <div className="flex items-center gap-2 flex-wrap">
          <Kicker>Company move</Kicker>
          <span className="chip chip-dim">no geometry</span>
          <span className="text-[12px] text-muted">Nothing on this day&apos;s schedule names a set, so there is no position to plot and no move to price.</span>
        </div>
        <p className="mt-2 text-[11px] text-dim max-w-3xl">
          This panel is drawn from the locations on the day&apos;s own schedule items and the coordinates on file for them. A day whose scenes have no location
          assigned reaches it empty — which also means the validator has no travel time to charge and no company move to count against a recovery here.
        </p>
        {geography.locations_missing_coordinates.length > 0 && (
          <p className="mt-1 text-[11px] text-warn">
            Locations with no coordinates on file: {geography.locations_missing_coordinates.map((l) => l.name).join(", ")}.
          </p>
        )}
      </section>
    );
  }

  /* A transport Change is keyed by DESTINATION, not by leg id: `derive_transport` renumbers legs
     positionally (leg_1, leg_2, …) and `apply_changeset` regenerates the whole list, so the ids a
     changeset carries do not survive the recovery that produced it. `Change.label` is
     `f"{vehicle} → {to_name}"` (services/changeset.py), and both halves are `Resource.name` — the
     same strings this payload puts in `vehicle_name` and `to_name`. Join on that.

     Only once the recovery is applied, though. `geography` is server-computed from the day's stored
     schedule, so between the approval click and the reload that refreshes it this panel holds the
     new changeset over the old geometry — and a row would read "departs 15:50 · was 15:50 → 16:30",
     annotating a move against itself. Until the two agree, the diff is not drawn at all. */
  const transportChanges: Change[] = applied
    ? (changeset?.changes || []).filter((c) => c.entity_type === "transport" && c.field === "departure")
    : [];
  const moveKey = (m: CompanyMove) => (m.vehicle_name ? `${m.vehicle_name} → ${m.to_name}` : null);

  // A day that returns to a location it has already left has two moves under one key; the diff
  // itself collapses those, so the change cannot be pinned to one of them and none is annotated.
  const keyCount = new Map<string, number>();
  for (const m of moves) {
    const k = moveKey(m);
    if (k) keyCount.set(k, (keyCount.get(k) ?? 0) + 1);
  }
  const retimed = new Map(transportChanges.filter((c): c is Change & { after: string } => c.after !== null).map((c) => [c.label, c]));
  const rows = moves.map((m) => {
    const k = moveKey(m);
    return { m, change: k && keyCount.get(k) === 1 ? retimed.get(k) : undefined };
  });
  const movedCount = rows.filter((r) => r.change && r.change.before !== null).length;
  const addedCount = rows.filter((r) => r.change && r.change.before === null).length;
  // a leg the recovery removed: it must not also be one of the moves still on the board
  const dropped = transportChanges.filter((c) => c.after === null && !keyCount.has(c.label));
  const orderOf = new Map(locations.map((l) => [l.id, l.order]));

  if (moves.length === 0) {
    return (
      <section className="card p-4">
        <div className="flex items-center gap-2 flex-wrap">
          <Kicker>Company move</Kicker>
          <span className="chip chip-ok">none today</span>
          <span className="text-[12px] text-muted">
            The unit shoots {locations[0].scene_numbers.length === 1 ? "1 scene" : `${locations[0].scene_numbers.length} scenes`} at {locations[0].name} and does not relocate.
          </span>
          {boardOnBaseline && <span className="chip chip-dim">stripboard is on &ldquo;before&rdquo; — this panel is not</span>}
        </div>
        {dropped.length > 0 && (
          <div className="mt-2 text-[12px] text-muted space-y-1">
            {dropped.map((c) => (
              <div key={c.label}>
                <span className="chip chip-dim mr-1">dropped</span>
                {c.label} — {c.before === null ? "no departure was on the call sheet" : <>departed <span className="mono">{c.before}</span></>}; {c.reason}
              </div>
            ))}
          </div>
        )}
      </section>
    );
  }

  return (
    <section className="card p-4">
      <div className="flex items-center gap-2 flex-wrap">
        <Kicker>Company moves</Kicker>
        <span className="chip chip-accent">{geography.move_count} move{geography.move_count === 1 ? "" : "s"}</span>
        <span className="text-[12px] text-muted">
          {geography.total_straight_line_km === null ? "distance unknown for at least one leg" : `${geography.total_straight_line_km} km straight line`}
          {" · "}
          {geography.total_travel_minutes === null ? "at least one leg has no travel time on file" : `${geography.total_travel_minutes} min in transit`}
          {" · "}
          {locations.length} locations
        </span>
        {movedCount > 0 && (
          <span className="chip chip-warn">{movedCount} move{movedCount === 1 ? "" : "s"} re-timed by the approved recovery</span>
        )}
        {addedCount > 0 && (
          <span className="chip chip-warn">{addedCount} new move{addedCount === 1 ? "" : "s"} from the approved recovery</span>
        )}
        {boardOnBaseline && <span className="chip chip-dim">stripboard is on &ldquo;before&rdquo; — this panel is not</span>}
      </div>

      <div className="mt-3 grid gap-4 grid-cols-[minmax(0,1fr)] lg:grid-cols-[340px_minmax(0,1fr)]">
        <div className="rounded-lg border border-line bg-elev/60 p-1">
          <MoveMap locations={locations} moves={moves} />
          <div className="px-2 pb-1 text-[10px] text-dim">A dashed leg is a straight line between two locality centres, not a road route.</div>
          <ol className="px-2 pb-1.5 pt-1 border-t border-line space-y-1">
            {locations.map((l) => (
              <li key={l.id} className="text-[11px] leading-tight">
                <span className="mono text-dim mr-1.5">{l.order}</span>
                <span className="text-fg">{l.name}</span>
                <div className="text-dim pl-4">
                  {l.locality || (l.latitude !== null && l.longitude !== null ? `${l.latitude.toFixed(4)}, ${l.longitude.toFixed(4)}` : "no coordinates on file")}
                  {" · "}
                  <span className="mono">{l.first_start}–{l.last_end}</span> · sc {l.scene_numbers.join(", ")}
                </div>
              </li>
            ))}
          </ol>
        </div>

        <div className="min-w-0 space-y-2">
          {rows.map(({ m, change }, i) => {
            const gap = gapMinutes(m);
            const slack = m.travel_minutes === null ? null : gap - m.travel_minutes;
            return (
              <div key={`${m.from_location_id}-${m.to_location_id}-${i}`} className="rounded-lg border border-line p-2.5">
                <div className="flex items-baseline gap-2 flex-wrap">
                  {/* the pin numbers from the map, so a leg can be found on the drawing */}
                  <span className="mono text-[11px] text-dim">{orderOf.get(m.from_location_id)}→{orderOf.get(m.to_location_id)}</span>
                  <span className="text-[13px] font-medium">{setName(m.from_name)}</span>
                  <span className="text-accent">→</span>
                  <span className="text-[13px] font-medium">{setName(m.to_name)}</span>
                  <span className="ml-auto mono text-[12px] text-muted">
                    {m.straight_line_km === null ? "distance unknown" : `${m.straight_line_km} km straight line`}
                  </span>
                </div>

                <div className="mt-1 text-[12px] text-muted">
                  Camera wraps Sc {m.after_scene} at <span className="mono text-fg">{m.wrap_at}</span>, first shot on Sc {m.before_scene} at{" "}
                  <span className="mono text-fg">{m.next_shot_at}</span> — <span className="mono text-fg">{gap} min</span> off camera.
                </div>

                <div className="mt-1 flex items-center gap-2 flex-wrap text-[12px]">
                  <span className="text-muted">
                    {m.travel_minutes === null ? (
                      <span className="text-dim">no travel time on file for this pair</span>
                    ) : (
                      <>
                        travel <span className="mono text-fg">{m.travel_minutes} min</span> <span className="text-dim">(production travel times)</span>
                      </>
                    )}
                  </span>
                  {slack !== null && (
                    <span className={`chip ${slack < 0 ? "chip-bad" : slack === 0 ? "chip-warn" : "chip-ok"}`}>
                      {slack < 0 ? `${-slack} min short` : slack === 0 ? "no slack" : `${slack} min slack`}
                    </span>
                  )}
                </div>

                {(m.vehicle_name || m.departure) && (
                  <div className="mt-1 flex items-center gap-2 flex-wrap text-[12px]">
                    <span className="chip chip-dim">{m.vehicle_name || m.vehicle_id}</span>
                    <span className="text-muted">
                      departs <span className="mono text-fg">{m.departure}</span>
                    </span>
                    {change && (
                      <span className="text-[11px] text-warn">
                        {change.before === null ? (
                          <>no such move before the recovery</>
                        ) : (
                          <>was <span className="mono">{change.before}</span> → <span className="mono">{change.after}</span></>
                        )}
                        {" · "}
                        {change.reason}
                      </span>
                    )}
                  </div>
                )}
                {!m.transport_leg_id && (
                  <div className="mt-1 text-[11px] text-dim">no transport leg booked for this move</div>
                )}
              </div>
            );
          })}

          {dropped.length > 0 && (
            <div className="rounded-lg border border-line border-dashed p-2.5 text-[12px] text-muted space-y-1">
              {dropped.map((c) => (
                <div key={c.label}>
                  <span className="chip chip-dim mr-1">dropped</span>
                  {c.label} — {c.before === null ? "no departure was on the call sheet" : <>departed <span className="mono">{c.before}</span></>}; {c.reason}
                </div>
              ))}
            </div>
          )}

          {geography.locations_missing_coordinates.length > 0 && (
            <div className="text-[11px] text-warn">
              Not on the map (no coordinates on file): {geography.locations_missing_coordinates.map((l) => l.name).join(", ")}
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 pt-2 border-t border-line text-[10px] text-dim space-y-0.5">
        <div>
          {boardOnBaseline
            ? "Read from the day's stored schedule and transport, which is the applied geometry. The stripboard above is on “before”; this panel does not follow that toggle, so the two are showing different schedules."
            : "Read from the day’s stored schedule and transport — a recovery option is not drawn here until it is approved and applied."}
        </div>
        <div>{geography.coordinates_basis} Positions are drawn to scale, north up; legs are bowed only so overlapping ones stay tellable apart.</div>
        <div>{geography.distance_basis}</div>
        <div>{geography.travel_minutes_basis}</div>
        {extraMoveCostInr !== undefined && (
          <div>A company move beyond the baseline count costs this day ₹{extraMoveCostInr.toLocaleString("en-IN")} — the figure the validator charges an option for an extra move.</div>
        )}
      </div>
    </section>
  );
}
