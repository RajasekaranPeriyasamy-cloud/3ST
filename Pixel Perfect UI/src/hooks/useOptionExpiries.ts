import { useEffect, useState } from "react";

import { api } from "@/lib/api";

export function pickNearestExpiry(
  expiries: string[],
  underlying?: string,
): string | undefined {
  if (!expiries.length) return undefined;
  // Use IST calendar date. Cash indices close ~15:30; MCX commodities trade until ~23:30.
  const ist = new Date(Date.now() + 5.5 * 60 * 60 * 1000);
  const today = ist.toISOString().slice(0, 10);
  const minutes = ist.getUTCHours() * 60 + ist.getUTCMinutes();
  const u = (underlying ?? "").toUpperCase();
  const isMcx = u === "CRUDEOIL" || u === "CRUDEOILM" || u === "NATURALGAS";
  const closeMinutes = isMcx ? 23 * 60 + 30 : 15 * 60 + 30;
  const afterClose = minutes >= closeMinutes;
  const firstOk = expiries.find((e) => (afterClose ? e > today : e >= today));
  return firstOk ?? expiries[expiries.length - 1];
}

const expiryCache = new Map<string, string[]>();
const inflight = new Map<string, Promise<string[]>>();

function fetchExpiries(underlying: string): Promise<string[]> {
  const key = underlying.toUpperCase();
  const cached = expiryCache.get(key);
  if (cached?.length) return Promise.resolve(cached);

  const pending = inflight.get(key);
  if (pending) return pending;

  const promise = api
    .get<{ expiries?: string[] }>(
      `/options/expiries?underlying=${encodeURIComponent(key)}`,
      { silent: true },
    )
    .then((r) => {
      const list = r.expiries ?? [];
      if (list.length) expiryCache.set(key, list);
      return list;
    })
    .finally(() => {
      inflight.delete(key);
    });

  inflight.set(key, promise);
  return promise;
}

/** Warm expiry lists for all underlyings (parallel, silent). */
export function prefetchOptionExpiries(underlyings: string[]) {
  for (const u of underlyings) {
    void fetchExpiries(u).catch(() => {});
  }
}

/** Load option expiries for an underlying; shows cached list instantly when available. */
export function useOptionExpiries(underlying: string) {
  const [expiries, setExpiries] = useState<string[]>(() => {
    const key = underlying?.toUpperCase() ?? "";
    return key ? (expiryCache.get(key) ?? []) : [];
  });
  const [loading, setLoading] = useState(() => {
    const key = underlying?.toUpperCase() ?? "";
    return Boolean(key && !expiryCache.has(key));
  });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!underlying) {
      setExpiries([]);
      setError(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    const key = underlying.toUpperCase();
    const cached = expiryCache.get(key);
    if (cached?.length) {
      setExpiries(cached);
      setLoading(false);
      setError(null);
    } else {
      setLoading(true);
      setExpiries([]);
      setError(null);
    }

    fetchExpiries(key)
      .then((list) => {
        if (cancelled) return;
        setExpiries(list);
        if (list.length === 0) {
          setError("No expiries returned — restart API or refresh Kite instruments.");
        } else {
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (!cached?.length) setExpiries([]);
        const msg = e instanceof Error ? e.message : "Could not load expiries";
        setError(msg.includes("Network") ? "API unreachable — restart API." : msg);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [underlying]);

  useEffect(() => {
    if (!underlying || !error) return;
    let cancelled = false;
    const retry = () => {
      fetchExpiries(underlying.toUpperCase())
        .then((list) => {
          if (cancelled) return;
          if (list.length > 0) {
            setExpiries(list);
            setError(null);
          }
        })
        .catch(() => {});
    };
    const id = window.setInterval(retry, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [underlying, error]);

  return { expiries, loading, error };
}
