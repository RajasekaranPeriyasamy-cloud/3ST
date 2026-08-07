import { useEffect, useRef, useState } from "react";

import { getWsUrl } from "@/lib/api";

export interface LtpTick {
  price: number;
  age_sec: number;
  source: "ws" | "rest";
  fresh: boolean;
}

export interface MarketHealth {
  status: "healthy" | "connected_no_data" | "disconnected" | "rest_only";
  ws_enabled: boolean;
  feed_connected: boolean;
  rest_fallback: boolean;
  data_flow_healthy: boolean;
  last_tick_age_sec: number | null;
  total_updates: number;
  reconnects: number;
  uptime_sec: number | null;
  cache_size: number;
  subscribed_tokens: number;
  ttl_sec: number;
  trade_management_safe?: boolean;
  trade_management_reason?: string;
}

export interface LtpFeedState {
  prices: Record<string, LtpTick>;
  health: MarketHealth | null;
  /** True while the browser WebSocket to /ws/ltp is open. */
  connected: boolean;
}

/**
 * Live LTP + feed-health stream from the FastAPI /ws/ltp endpoint.
 * Auto-reconnects with backoff when the socket drops.
 */
export function useLtpFeed(enabled = true): LtpFeedState {
  const [prices, setPrices] = useState<Record<string, LtpTick>>({});
  const [health, setHealth] = useState<MarketHealth | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<number | null>(null);
  const closedRef = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    closedRef.current = false;
    let attempts = 0;

    const connect = () => {
      if (closedRef.current) return;
      let ws: WebSocket;
      try {
        ws = new WebSocket(getWsUrl("/ws/ltp"));
      } catch {
        scheduleRetry();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        attempts = 0;
        setConnected(true);
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg?.type === "ltp") {
            if (msg.prices) setPrices(msg.prices as Record<string, LtpTick>);
            if (msg.health) setHealth(msg.health as MarketHealth);
          }
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        scheduleRetry();
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      };
    };

    const scheduleRetry = () => {
      if (closedRef.current) return;
      attempts += 1;
      const delay = Math.min(1000 * 2 ** Math.min(attempts, 4), 15000);
      retryRef.current = window.setTimeout(connect, delay);
    };

    connect();

    return () => {
      closedRef.current = true;
      if (retryRef.current) window.clearTimeout(retryRef.current);
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          /* ignore */
        }
      }
    };
  }, [enabled]);

  return { prices, health, connected };
}
