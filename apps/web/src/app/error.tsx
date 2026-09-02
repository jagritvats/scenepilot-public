"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="card max-w-xl mx-auto mt-10 p-10 text-center">
      <div className="kicker">Unscheduled stop</div>
      <h1 className="display text-4xl font-bold mt-2">This view failed to render</h1>
      <p className="text-muted text-sm mt-2 max-w-md mx-auto">
        Retrying re-fetches the production state from the agent; it does not re-send an approval or re-run a search.
      </p>
      {error.digest && <p className="mono text-[11px] text-dim mt-3">digest {error.digest}</p>}
      <div className="mt-6 flex items-center justify-center gap-3 flex-wrap">
        <button onClick={reset} className="btn btn-primary">Try again</button>
        <Link href="/" className="btn btn-ghost">Control room</Link>
      </div>
    </div>
  );
}
