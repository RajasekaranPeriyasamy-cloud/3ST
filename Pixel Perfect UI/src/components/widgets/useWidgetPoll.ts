import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useAnalyticsDesk } from "@/context/AnalyticsDeskContext";

/** Shared poll/fetch for desk widgets. Pass null url to skip. Unmount stops poll. */
export function useWidgetPoll<T>(url: string | null) {
  const { autoRefresh, refreshSec, refreshNonce } = useAnalyticsDesk();
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authError, setAuthError] = useState(false);

  const fetchData = useCallback(async () => {
    if (!url) return;
    setLoading(true);
    setError(null);
    setAuthError(false);
    try {
      const snap = await api.get<T>(url, { silent: true });
      setData(snap);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      const status = (e as { status?: number })?.status;
      if (status === 401 || msg.includes("401") || msg.toLowerCase().includes("session")) {
        setAuthError(true);
      }
      setError(msg);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [url]);

  useEffect(() => {
    void fetchData();
  }, [fetchData, refreshNonce]);

  useEffect(() => {
    if (!autoRefresh || authError || !url) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void fetchData();
    }, Math.max(15, refreshSec) * 1000);
    return () => window.clearInterval(id);
  }, [autoRefresh, refreshSec, authError, fetchData, url]);

  return { data, loading, error, authError, reload: fetchData };
}
