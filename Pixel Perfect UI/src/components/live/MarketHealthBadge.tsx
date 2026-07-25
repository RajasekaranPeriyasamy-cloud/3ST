import { Activity, AlertTriangle, Wifi, WifiOff } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { MarketHealth } from "@/hooks/useLtpFeed";

const LABEL: Record<MarketHealth["status"], string> = {
  healthy: "Feed live",
  connected_no_data: "Feed idle",
  disconnected: "Feed down",
  rest_only: "REST only",
};

function tone(health: MarketHealth | null, wsConnected: boolean) {
  if (!wsConnected || !health) return "border-muted-foreground/40 text-muted-foreground";
  switch (health.status) {
    case "healthy":
      return "border-bull/50 text-bull";
    case "rest_only":
      return "border-sky-500/50 text-sky-400";
    case "connected_no_data":
      return "border-amber-500/50 text-amber-400";
    default:
      return "border-bear/50 text-bear";
  }
}

export function MarketHealthBadge({
  health,
  wsConnected,
}: {
  health: MarketHealth | null;
  wsConnected: boolean;
}) {
  const status = health?.status;
  const safe = health?.trade_management_safe;
  const Icon =
    !wsConnected
      ? WifiOff
      : status === "healthy"
        ? Wifi
        : status === "rest_only"
          ? Activity
          : AlertTriangle;

  const label = !wsConnected
    ? "Stream off"
    : status
      ? LABEL[status]
      : "…";

  return (
    <TooltipProvider delayDuration={100}>
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          className={cn("gap-1 px-3 py-1 text-sm", tone(health, wsConnected))}
        >
          <Icon className="h-3.5 w-3.5" />
          {label}
          {health?.last_tick_age_sec != null && wsConnected && (
            <span className="opacity-70 tabular-nums">
              · {health.last_tick_age_sec.toFixed(0)}s
            </span>
          )}
          {safe === false && (
            <span className="ml-1 rounded bg-bear/20 px-1 text-[10px] uppercase text-bear">
              gated
            </span>
          )}
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-xs">
        {!wsConnected ? (
          <p>Real-time stream disconnected — retrying…</p>
        ) : health ? (
          <div className="space-y-0.5">
            <p className="font-medium">{LABEL[health.status]}</p>
            <p>WS feed: {health.feed_connected ? "connected" : "down"}</p>
            <p>
              Last tick:{" "}
              {health.last_tick_age_sec != null
                ? `${health.last_tick_age_sec.toFixed(1)}s ago`
                : "—"}
            </p>
            <p>Ticks: {health.total_updates.toLocaleString()}</p>
            <p>Reconnects: {health.reconnects}</p>
            {health.uptime_sec != null && (
              <p>Uptime: {Math.round(health.uptime_sec / 60)}m</p>
            )}
            <p>Cached symbols: {health.cache_size}</p>
            <p className="pt-1">
              Trade mgmt:{" "}
              {health.trade_management_safe ? "safe" : "gated"}
              {health.trade_management_reason
                ? ` — ${health.trade_management_reason}`
                : ""}
            </p>
          </div>
        ) : (
          <p>Waiting for health data…</p>
        )}
      </TooltipContent>
    </Tooltip>
    </TooltipProvider>
  );
}
