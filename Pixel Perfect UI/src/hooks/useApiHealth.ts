import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

export type ApiHealthState = {
  reachable: boolean;
  checking: boolean;
  lastOkAt: number | null;
  instrumentsCache: boolean | null;
  kiteAuthenticated: boolean | null;
  kiteConfigured: boolean | null;
  userId: string | null;
  userName: string | null;
  loginTime: string | null;
};

type HealthPayload = {
  ok?: boolean;
  instruments_cache?: boolean;
  kite_authenticated?: boolean;
  kite_configured?: boolean;
};

type SessionPayload = {
  authenticated?: boolean;
  kite_configured?: boolean;
  user_id?: string;
  user_name?: string;
  login_time?: string;
};

/** Poll /health and /auth/me so every page knows API + Kite session state. */
export function useApiHealth(pollMs = 5000) {
  const [state, setState] = useState<ApiHealthState>({
    reachable: true,
    checking: true,
    lastOkAt: null,
    instrumentsCache: null,
    kiteAuthenticated: null,
    kiteConfigured: null,
    userId: null,
    userName: null,
    loginTime: null,
  });

  const probe = useCallback(async () => {
    try {
      const [h, me] = await Promise.all([
        api.get<HealthPayload>("/health", { silent: true }),
        api.get<SessionPayload>("/auth/me", { silent: true }).catch(() => null),
      ]);
      const authenticated = me?.authenticated ?? h.kite_authenticated ?? false;
      setState({
        reachable: true,
        checking: false,
        lastOkAt: Date.now(),
        instrumentsCache: h.instruments_cache ?? null,
        kiteAuthenticated: authenticated,
        kiteConfigured: me?.kite_configured ?? h.kite_configured ?? null,
        userId: me?.user_id ?? null,
        userName: me?.user_name ?? null,
        loginTime: me?.login_time ?? null,
      });
    } catch {
      setState((prev) => ({
        ...prev,
        reachable: false,
        checking: false,
      }));
    }
  }, []);

  useEffect(() => {
    void probe();
    const id = window.setInterval(() => void probe(), pollMs);
    return () => window.clearInterval(id);
  }, [probe, pollMs]);

  return { ...state, retry: probe };
}
