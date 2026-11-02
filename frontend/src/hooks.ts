import { useCallback, useEffect, useRef, useState } from "react";

/* ------------------------------------------------------------------ hash router */

export interface Route {
  path: string[];
  query: URLSearchParams;
}

function parse(): Route {
  const raw = window.location.hash.replace(/^#/, "") || "/";
  const [p, q = ""] = raw.split("?");
  return { path: p.split("/").filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(q) };
}

export function navigate(to: string): void {
  if (window.location.hash === `#${to}`) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else window.location.hash = to;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

/** Change the query string of the current page without adding history entries for every keystroke. */
export function replaceQuery(path: string, params: Record<string, string | undefined>): void {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const s = q.toString();
  window.history.replaceState(null, "", `#${path}${s ? `?${s}` : ""}`);
}

/* ------------------------------------------------------------------ data loading */

export interface Loaded<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => void;
}

/**
 * Load data when `deps` change and optionally keep it fresh.
 * `every` returns the delay in ms before the next refresh, or null to stop. Refreshing pauses while the tab is hidden.
 */
export function useLoad<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[], every?: (data: T | undefined) => number | null): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const everyRef = useRef(every);
  everyRef.current = every;

  useEffect(() => {
    const ctl = new AbortController();
    let timer: number | undefined;
    let dead = false;
    const run = async (first: boolean) => {
      if (first) setLoading(true);
      let latest: T | undefined;
      try {
        latest = await fetcherRef.current(ctl.signal);
        if (dead) return;
        setData(latest);
        setError(undefined);
      } catch (e) {
        if (dead || (e as Error).name === "AbortError") return;
        setError(e as Error);
      } finally {
        if (!dead) setLoading(false);
      }
      if (dead) return;
      const delay = everyRef.current?.(latest);
      if (delay) {
        timer = window.setTimeout(function again() {
          if (document.hidden) timer = window.setTimeout(again, delay);
          else void run(false);
        }, delay);
      }
    };
    void run(true);
    return () => {
      dead = true;
      ctl.abort();
      if (timer) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Remember a value for the length of the browser session (for example the review queue order). */
export function remember<T>(key: string, value?: T): T | undefined {
  try {
    if (value !== undefined) sessionStorage.setItem(key, JSON.stringify(value));
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : undefined;
  } catch {
    return undefined;
  }
}
