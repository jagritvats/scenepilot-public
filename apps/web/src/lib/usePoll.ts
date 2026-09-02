"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Poll a loader while `active(data)` is true; always fetch once on mount and on `reload()`. */
export function usePoll<T>(loader: () => Promise<T>, active: (data: T | null) => boolean, intervalMs = 1500) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loaderRef = useRef(loader);
  const activeRef = useRef(active);
  const dataRef = useRef<T | null>(null);

  useEffect(() => {
    loaderRef.current = loader;
    activeRef.current = active;
  }, [loader, active]);

  const reload = useCallback(async () => {
    try {
      const d = await loaderRef.current();
      dataRef.current = d;
      setData(d);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      await reload();
      if (!alive) return;
      const wait = activeRef.current(dataRef.current) ? intervalMs : intervalMs * 6;
      timer = setTimeout(loop, wait);
    };
    loop();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [reload, intervalMs]);

  return { data, error, loading, reload };
}
