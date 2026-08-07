import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { pickNearestExpiry, useOptionExpiries } from "@/hooks/useOptionExpiries";
import type { OiUnderlying } from "@/lib/types";

export type WidgetId =
  | "gamma-density"
  | "vol-surface"
  | "oi-profile"
  | "oi-tracker"
  | "oi-var"
  | "vanna-exposure"
  | "iv-smile"
  | "trade-suggestions";

const STORAGE_KEY = "3st.widget-desk.v1";
const DEFAULT_ENABLED: WidgetId[] = [
  "oi-tracker",
  "gamma-density",
  "oi-profile",
  "vol-surface",
];
const DESK_UNDERLYINGS: OiUnderlying[] = [
  "NIFTY",
  "BANKNIFTY",
  "SENSEX",
  "CRUDEOIL",
  "CRUDEOILM",
  "NATURALGAS",
];

interface PersistedDesk {
  enabled?: WidgetId[];
  underlying?: OiUnderlying;
  expiry?: string;
  refreshSec?: number;
  autoRefresh?: boolean;
}

function loadPersisted(): PersistedDesk {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    return JSON.parse(raw) as PersistedDesk;
  } catch {
    return {};
  }
}

function savePersisted(state: PersistedDesk) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* ignore quota */
  }
}

interface AnalyticsDeskValue {
  underlying: OiUnderlying;
  setUnderlying: (u: OiUnderlying) => void;
  underlyings: OiUnderlying[];
  expiry: string;
  setExpiry: (e: string) => void;
  expiries: string[];
  expiriesLoading: boolean;
  refreshSec: number;
  setRefreshSec: (n: number) => void;
  autoRefresh: boolean;
  setAutoRefresh: (v: boolean) => void;
  enabled: WidgetId[];
  toggleWidget: (id: WidgetId) => void;
  setEnabled: (ids: WidgetId[]) => void;
  refreshNonce: number;
  bumpRefresh: () => void;
}

const AnalyticsDeskContext = createContext<AnalyticsDeskValue | null>(null);

export function AnalyticsDeskProvider({ children }: { children: ReactNode }) {
  const persisted = useMemo(() => loadPersisted(), []);
  const [underlying, setUnderlyingState] = useState<OiUnderlying>(
    persisted.underlying && DESK_UNDERLYINGS.includes(persisted.underlying)
      ? persisted.underlying
      : "NIFTY",
  );
  const [expiry, setExpiryState] = useState(persisted.expiry ?? "");
  const [refreshSec, setRefreshSec] = useState(persisted.refreshSec ?? 60);
  const [autoRefresh, setAutoRefresh] = useState(persisted.autoRefresh ?? true);
  const [enabled, setEnabledState] = useState<WidgetId[]>(
    persisted.enabled?.length ? persisted.enabled : DEFAULT_ENABLED,
  );
  const [refreshNonce, setRefreshNonce] = useState(0);

  const { expiries, loading: expiriesLoading } = useOptionExpiries(underlying);

  useEffect(() => {
    if (!expiries.length) return;
    setExpiryState((current) => {
      if (current && expiries.includes(current)) return current;
      return pickNearestExpiry(expiries, underlying) ?? expiries[0] ?? "";
    });
  }, [expiries, underlying]);

  useEffect(() => {
    savePersisted({
      enabled,
      underlying,
      expiry,
      refreshSec,
      autoRefresh,
    });
  }, [enabled, underlying, expiry, refreshSec, autoRefresh]);

  const setUnderlying = useCallback((u: OiUnderlying) => {
    setUnderlyingState(u);
    setExpiryState("");
  }, []);

  const setExpiry = useCallback((e: string) => setExpiryState(e), []);

  const toggleWidget = useCallback((id: WidgetId) => {
    setEnabledState((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }, []);

  const setEnabled = useCallback((ids: WidgetId[]) => setEnabledState(ids), []);

  const bumpRefresh = useCallback(() => setRefreshNonce((n) => n + 1), []);

  const value = useMemo<AnalyticsDeskValue>(
    () => ({
      underlying,
      setUnderlying,
      underlyings: DESK_UNDERLYINGS,
      expiry,
      setExpiry,
      expiries,
      expiriesLoading,
      refreshSec,
      setRefreshSec,
      autoRefresh,
      setAutoRefresh,
      enabled,
      toggleWidget,
      setEnabled,
      refreshNonce,
      bumpRefresh,
    }),
    [
      underlying,
      setUnderlying,
      expiry,
      setExpiry,
      expiries,
      expiriesLoading,
      refreshSec,
      autoRefresh,
      enabled,
      toggleWidget,
      setEnabled,
      refreshNonce,
      bumpRefresh,
    ],
  );

  return (
    <AnalyticsDeskContext.Provider value={value}>{children}</AnalyticsDeskContext.Provider>
  );
}

export function useAnalyticsDesk() {
  const ctx = useContext(AnalyticsDeskContext);
  if (!ctx) throw new Error("useAnalyticsDesk must be used within AnalyticsDeskProvider");
  return ctx;
}
