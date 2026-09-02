import Link from "next/link";

export default function NotFound() {
  return (
    <div className="card max-w-xl mx-auto mt-10 p-10 text-center">
      <div className="kicker">Not on the schedule</div>
      <h1 className="display text-4xl font-bold mt-2">404 — no such page</h1>
      <p className="text-muted text-sm mt-2 max-w-md mx-auto">
        This URL does not belong to any production ScenePilot is tracking. Scene and shoot-day pages are addressed by id, so a stale or mistyped link lands here.
      </p>
      <div className="mt-6 flex items-center justify-center gap-3 flex-wrap">
        <Link href="/" className="btn btn-primary">Control room</Link>
        <Link href="/projects/proj_nightfall/days/day_4" className="btn btn-ghost">Shoot day 4</Link>
      </div>
    </div>
  );
}
