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
import type { Selection } from "@/lib/types";

export const defaultSelection: Selection = {
  instrument_token: null,
  exchange: null,
  tradingsymbol: null,
  name: null,
  segment: "equity",
  lot_size: 0,
  timeframe: "15min",
  product: "underlying",
  spread: null,
  st_method: "heikin_ashi",
  system_mode: "Intraday",
  session_start: "09:15",
  session_end: "15:30",
  force_exit: "15:20",
  atr1: 21,
  factor1: 1.0,
  atr2: 14,
  factor2: 2.0,
  atr3: 7,
  factor3: 3.0,
  st1_enabled: true,
  st2_enabled: true,
  st3_enabled: true,
  adx_enabled: true,
  adx_period: 14,
  adx_threshold: 20,
  sl_mode: "Off",
  sl_value: 1.0,
  tgt_mode: "Off",
  tgt_value: 1.0,
  tsl_mode: "Off",
  tsl_value: 1.5,
};

interface Ctx {
  selection: Selection;
  loading: boolean;
  refresh: () => Promise<void>;
  update: (patch: Partial<Selection>) => void;
  save: (sel?: Selection) => Promise<Selection>;
  clear: () => Promise<void>;
}

const SelectionCtx = createContext<Ctx | null>(null);

export function SelectionProvider({ children }: { children: ReactNode }) {
  const [selection, setSelection] = useState<Selection>(defaultSelection);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const s = await api.get<Selection>("/selection", { silent: true });
      if (s) setSelection({ ...defaultSelection, ...s });
    } catch {
      // ignore; may be 401
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const update = useCallback((patch: Partial<Selection>) => {
    setSelection((prev) => ({ ...prev, ...patch }));
  }, []);

  const save = useCallback(
    async (sel?: Selection) => {
      const raw = sel ?? selection;
      const payload: Selection = {
        ...raw,
        spread: raw.product === "underlying" ? null : raw.spread,
      };
      const res = await api.post<{ ok?: boolean; selection?: Selection } | Selection>(
        "/selection",
        payload,
      );
      const saved =
        res && typeof res === "object" && "selection" in res && res.selection
          ? res.selection
          : (res as Selection);
      setSelection({ ...defaultSelection, ...saved });
      return saved;
    },
    [selection],
  );

  const clear = useCallback(async () => {
    await api.del("/selection");
    setSelection(defaultSelection);
  }, []);

  const value = useMemo<Ctx>(
    () => ({ selection, loading, refresh, update, save, clear }),
    [selection, loading, refresh, update, save, clear],
  );

  return <SelectionCtx.Provider value={value}>{children}</SelectionCtx.Provider>;
}

export function useSelection() {
  const ctx = useContext(SelectionCtx);
  if (!ctx) throw new Error("useSelection must be used inside SelectionProvider");
  return ctx;
}
