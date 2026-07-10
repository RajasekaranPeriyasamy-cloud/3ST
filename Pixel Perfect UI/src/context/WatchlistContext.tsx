import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/lib/api";
import type { Selection, WatchlistItem, WatchlistStatus } from "@/lib/types";

interface Ctx {
  items: WatchlistItem[];
  loading: boolean;
  refresh: () => Promise<void>;
  add: (sel: Selection) => Promise<WatchlistItem>;
  remove: (id: string) => Promise<void>;
  activate: (id: string) => Promise<WatchlistItem>;
  close: (id: string) => Promise<void>;
  scan: (requireArmed?: boolean) => Promise<{ triggered: WatchlistItem[] }>;
}

const WatchlistCtx = createContext<Ctx | null>(null);

export function WatchlistProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get<{ items?: WatchlistItem[] }>("/watchlist", { silent: true });
      setItems(r.items ?? []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const add = useCallback(async (sel: Selection) => {
    const payload = {
      ...sel,
      spread: sel.product === "underlying" ? null : sel.spread,
    };
    const r = await api.post<{ item: WatchlistItem }>("/watchlist", payload);
    await refresh();
    return r.item;
  }, [refresh]);

  const remove = useCallback(
    async (id: string) => {
      await api.del(`/watchlist/${id}`);
      await refresh();
    },
    [refresh],
  );

  const activate = useCallback(
    async (id: string) => {
      const r = await api.post<{ item: WatchlistItem }>(`/watchlist/${id}/activate`);
      await refresh();
      return r.item;
    },
    [refresh],
  );

  const close = useCallback(
    async (id: string) => {
      await api.post(`/watchlist/${id}/close`);
      await refresh();
    },
    [refresh],
  );

  const scan = useCallback(
    async (requireArmed = false) => {
      const r = await api.post<{ triggered?: WatchlistItem[] }>(
        `/watchlist/scan?require_armed=${requireArmed ? "true" : "false"}`,
      );
      await refresh();
      return { triggered: r.triggered ?? [] };
    },
    [refresh],
  );

  const value = useMemo<Ctx>(
    () => ({ items, loading, refresh, add, remove, activate, close, scan }),
    [items, loading, refresh, add, remove, activate, close, scan],
  );

  return <WatchlistCtx.Provider value={value}>{children}</WatchlistCtx.Provider>;
}

export function useWatchlist() {
  const ctx = useContext(WatchlistCtx);
  if (!ctx) throw new Error("useWatchlist must be used inside WatchlistProvider");
  return ctx;
}

export function useWatchlistByStatus(status: WatchlistStatus | string) {
  const { items, loading, refresh, remove, activate, close, scan } = useWatchlist();
  const filtered = useMemo(
    () =>
      items.filter((i) =>
        status.includes(",")
          ? status.split(",").includes(i.status)
          : i.status === status,
      ),
    [items, status],
  );
  return { items: filtered, allItems: items, loading, refresh, remove, activate, close, scan };
}
