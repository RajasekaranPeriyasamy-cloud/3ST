import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ArrowRight, Power, RefreshCw, ShieldOff, TrendingDown, TrendingUp } from "lucide-react";

import { api } from "@/lib/api";
import { useWatchlistByStatus } from "@/context/WatchlistContext";
import type { ActiveTradesView, ActiveTradeRow, PositionsView, WatchlistItem } from "@/lib/types";
import { KitePositionsTable, formatDeskPnl } from "@/components/live/KitePositionsTable";
import { LiveWorkflowPanel, type WorkflowStatus } from "@/components/live/LiveWorkflowPanel";
import { MarketHealthBadge } from "@/components/live/MarketHealthBadge";
import { useLtpFeed, type LtpTick } from "@/hooks/useLtpFeed";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/live")({
  component: LivePage,
});

interface ArmStatus {
  armed?: boolean;
  mode?: "paper" | "live";
}
interface RiskLimits {
  max_qty?: number;
  max_open_positions?: number;
  max_daily_loss?: number;
  open_positions?: number;
}

function LivePage() {
  const {
    items: liveQueue,
    refresh: refreshWatchlist,
    activate,
    close,
    scan,
    manualEnter,
    triggerSide,
    executeLive,
    scanExits,
  } = useWatchlistByStatus("triggered,active");
  const { items: waitingManual } = useWatchlistByStatus("waiting");
  const manualWaiting = useMemo(
    () => waitingManual.filter((i) => (i.entry_mode ?? "manual") === "manual"),
    [waitingManual],
  );
  const triggered = useMemo(() => liveQueue.filter((i) => i.status === "triggered"), [liveQueue]);
  const active = useMemo(() => liveQueue.filter((i) => i.status === "active"), [liveQueue]);
  const [arm, setArm] = useState<ArmStatus | null>(null);
  const [mode, setMode] = useState<"paper" | "live">("paper");
  const [desk, setDesk] = useState<PositionsView | null>(null);
  const [activeTrades, setActiveTrades] = useState<ActiveTradesView | null>(null);
  const [orders, setOrders] = useState<Record<string, unknown>[]>([]);
  const [risk, setRisk] = useState<RiskLimits | null>(null);
  const [workflow, setWorkflow] = useState<WorkflowStatus | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const { prices: liveLtp, health: marketHealth, connected: streamOn } = useLtpFeed();

  async function executeManualOrder(id: string, side: "buy" | "sell") {
    try {
      if (mode === "live") {
        if (!arm?.armed) {
          toast.error("ARM the desk first — required for exchange orders");
          return;
        }
        await executeLive(id, side);
        toast.success(`${side.toUpperCase()} sent to exchange — 3ST exit monitoring started`);
      } else {
        await triggerSide(id, side);
        toast.success(`Paper ${side.toUpperCase()} filled — 3ST exit monitoring started`);
      }
      refreshDesk();
    } catch {
      /* api toast */
    }
  }

  async function refreshDesk() {
    try {
      const a = await api.get<ArmStatus>("/live/arm", { silent: true });
      setArm(a);
      if (a?.mode) setMode(a.mode);
    } catch {
      /* silent */
    }
    await refreshWatchlist();
    api
      .get<PositionsView>("/live/positions", { silent: true })
      .then(setDesk)
      .catch(() => setDesk(null));
    api
      .get<ActiveTradesView>("/live/active-trades", { silent: true })
      .then(setActiveTrades)
      .catch(() => setActiveTrades(null));
    api
      .get<{ orders?: Record<string, unknown>[] }>("/live/orders", { silent: true })
      .then((r) => setOrders(r.orders ?? []))
      .catch(() => setOrders([]));
    api.get<RiskLimits>("/risk/limits", { silent: true }).then(setRisk).catch(() => {});
    api.get<WorkflowStatus>("/live/workflow", { silent: true }).then(setWorkflow).catch(() => {});
  }

  async function manualRefresh() {
    setRefreshing(true);
    try {
      await refreshDesk();
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    refreshDesk();
    const t = setInterval(refreshDesk, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!active.length) return;
    const pollMs = active.some((i) => i.timeframe === "1min" || i.timeframe === "3min") ? 20000 : 45000;
    const t = setInterval(() => {
      scanExits().then(() => refreshDesk()).catch(() => {});
    }, pollMs);
    return () => clearInterval(t);
  }, [active.length, active.map((i) => i.timeframe).join(",")]);

  useEffect(() => {
    if (!arm?.armed) return;
    const t = setInterval(() => {
      scan(true).catch(() => {});
    }, 30000);
    return () => clearInterval(t);
  }, [arm?.armed, scan]);

  async function switchMode(m: "paper" | "live") {
    try {
      await api.post("/live/mode", { mode: m });
      setMode(m);
      toast.success(`Mode: ${m.toUpperCase()}`);
      refreshDesk();
    } catch {
      /* api toast */
    }
  }

  async function armNow() {
    try {
      await api.post("/live/arm", { confirm: true });
      toast.success("ARMED");
      refreshDesk();
    } catch {
      /* api toast */
    }
  }

  async function disarmNow() {
    try {
      await api.post("/live/disarm");
      toast.success("DISARMED");
      refreshDesk();
    } catch {
      /* api toast */
    }
  }

  async function adoptFor3stExit() {
    try {
      const r = await api.post<{ count?: number }>("/live/adopt-positions");
      toast.success(`Linked ${r.count ?? 0} position(s) for 3ST exit monitoring`);
      await refreshWatchlist();
      refreshDesk();
    } catch {
      /* api toast */
    }
  }

  const monitoredTrades = activeTrades?.trades ?? [];
  const orphanCount = activeTrades?.orphan_count ?? 0;
  const totalPnl = desk?.total_pnl ?? 0;
  const pnlPositive = totalPnl >= 0;
  const deskMode = desk?.mode ?? mode;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Live Desk</h1>
          <p className="text-sm text-muted-foreground">
            Signals from Dashboard activate here. {triggered.length} triggered · {active.length}{" "}
            active · {desk?.count ?? 0} positions
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <MarketHealthBadge health={marketHealth} wsConnected={streamOn} />
          <Badge
            variant="outline"
            className={cn(
              "px-3 py-1 text-sm uppercase",
              deskMode === "live"
                ? "border-amber-500/50 text-amber-400"
                : "border-sky-500/50 text-sky-400",
            )}
          >
            {deskMode === "live" ? "Live Trade" : "Paper Trade"}
          </Badge>
          <Badge className={arm?.armed ? "bg-bull px-3 py-1 text-base" : "bg-bear px-3 py-1 text-base"}>
            {arm?.armed ? "ARMED" : "DISARMED"}
          </Badge>
        </div>
      </header>

      <LiveWorkflowPanel workflow={workflow} />

      {manualWaiting.length > 0 && (
        <Card className="border-sky-500/30">
          <CardHeader>
            <CardTitle className="text-base">Step 5 — Manual BUY / SELL</CardTitle>
            <p className="text-xs text-muted-foreground">
              {mode === "live"
                ? "LIVE mode: ARM first, then BUY or SELL places order on Kite."
                : "Paper mode: BUY/SELL simulates fill. Switch to Live Trade + ARM for exchange."}
            </p>
          </CardHeader>
          <CardContent>
            <ManualQueueTable
              items={manualWaiting}
              onEnter={executeManualOrder}
              onDismiss={async (id) => {
                await close(id);
                toast.message("Removed from queue");
                refreshDesk();
              }}
            />
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="md:col-span-1">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Total P&amp;L</CardTitle>
          </CardHeader>
          <CardContent className="flex items-center gap-3">
            {pnlPositive ? (
              <TrendingUp className="h-8 w-8 text-bull" />
            ) : (
              <TrendingDown className="h-8 w-8 text-bear" />
            )}
            <div
              className={cn(
                "font-mono text-3xl tabular-nums tracking-tight",
                pnlPositive ? "text-bull" : "text-bear",
              )}
            >
              {formatDeskPnl(totalPnl)}
            </div>
          </CardContent>
        </Card>
        <Card className="md:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Trading mode</CardTitle>
            <Button size="sm" variant="ghost" onClick={manualRefresh} disabled={refreshing}>
              <RefreshCw className={cn("mr-2 h-4 w-4", refreshing && "animate-spin")} />
              Refresh
            </Button>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant={mode === "paper" ? "default" : "outline"}
                onClick={() => switchMode("paper")}
              >
                Paper Trade
              </Button>
              <Button
                size="sm"
                variant={mode === "live" ? "default" : "outline"}
                onClick={() => switchMode("live")}
              >
                Live Trade
              </Button>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button disabled={mode !== "live" || arm?.armed} size="sm">
                    <Power className="mr-2 h-4 w-4" /> ARM
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Arm live trading?</AlertDialogTitle>
                    <AlertDialogDescription>
                      Real Kite orders will be placed for new activations. Existing paper positions
                      stay in the paper broker until you close them.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={armNow}>Arm now</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
              <Button variant="destructive" size="sm" onClick={disarmNow}>
                <ShieldOff className="mr-2 h-4 w-4" /> DISARM
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle className="text-base">Positions</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Kite-style view — entry at Avg., mark-to-market at LTP
            </p>
          </div>
          <Badge variant="outline">{desk?.count ?? 0} open</Badge>
        </CardHeader>
        <CardContent>
          <KitePositionsTable
            groups={desk?.groups ?? []}
            empty="No open positions. Activate a triggered signal to enter a trade."
            liveLtp={liveLtp}
          />
        </CardContent>
      </Card>

      {orphanCount > 0 ? (
        <Card className="border-amber-500/40">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 pt-6">
            <div className="text-sm">
              <p className="font-medium text-amber-400">
                {orphanCount} open position{orphanCount > 1 ? "s" : ""} not linked to 3ST exit
              </p>
              <p className="text-xs text-muted-foreground">
                Link your Kite position to show ST/TSL exit levels and auto-exit in the signal box.
              </p>
            </div>
            <Button size="sm" onClick={adoptFor3stExit}>
              Link for 3ST exit
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {monitoredTrades.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Active trades</CardTitle>
            <p className="text-xs text-muted-foreground">
              Live LTP from Kite. Use <strong>BUY</strong> or <strong>SELL</strong> to manually trigger entry
              when status is not Running. 3ST manages exit.
            </p>
          </CardHeader>
          <CardContent>
            <ActiveTradesTable
              trades={monitoredTrades}
              liveMode={mode === "live"}
              armed={!!arm?.armed}
              liveLtp={liveLtp}
              onTriggerSide={executeManualOrder}
              onClose={async (id) => {
                try {
                  await close(id);
                  toast.message("Trade closed");
                  refreshDesk();
                } catch {
                  /* api toast */
                }
              }}
            />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Signal inbox</CardTitle>
          <Button size="sm" variant="outline" onClick={() => scan(false).catch(() => {})}>
            Scan now
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {monitoredTrades.length > 0 ? (
            <div>
              <h3 className="mb-2 text-sm font-medium text-muted-foreground">
                Active — 3ST exit monitoring
              </h3>
              <div className="space-y-3">
                {monitoredTrades.map((row) => (
                  <ExitSignalCard key={row.id} row={row} />
                ))}
              </div>
            </div>
          ) : null}
          {triggered.length === 0 && active.length === 0 && manualWaiting.length === 0 && monitoredTrades.length === 0 ? (
            <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
              No triggered signals yet. Queue instruments on the{" "}
              <Link to="/dashboard" className="text-primary underline">
                Dashboard
              </Link>{" "}
              and scan for 3ST entries.
            </div>
          ) : (
            <>
              {triggered.length > 0 && (
                <LiveQueueTable
                  title="Triggered — ready to trade"
                  items={triggered}
                  onActivate={async (id) => {
                    try {
                      await activate(id);
                      toast.success("Activated — paper entry placed at market");
                      refreshDesk();
                    } catch {
                      /* api toast */
                    }
                  }}
                  onDismiss={async (id) => {
                    await close(id);
                    toast.message("Trade closed");
                    refreshDesk();
                  }}
                />
              )}
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Orders</CardTitle>
          </CardHeader>
          <CardContent className="max-h-72 overflow-auto">
            <OrdersTable rows={orders} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Risk limits</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-3 text-sm">
            <RiskItem
              label="Open positions"
              value={
                risk?.open_positions != null && risk?.max_open_positions != null
                  ? `${risk.open_positions} / ${risk.max_open_positions}`
                  : undefined
              }
              warn={
                risk?.open_positions != null &&
                risk?.max_open_positions != null &&
                risk.open_positions >= risk.max_open_positions
              }
            />
            <RiskItem label="Max loss / day" value={risk?.max_daily_loss} />
            <RiskItem label="Max quantity" value={risk?.max_qty} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function ExitSignalCard({ row }: { row: ActiveTradeRow }) {
  return (
    <div className="rounded-md border border-border bg-muted/20 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono font-medium">{row.tradingsymbol}</span>
        <Badge variant="outline">{row.timeframe ?? "—"}</Badge>
        <Badge variant="outline">{row.entry_side ?? row.signal ?? "—"}</Badge>
        {row.last_price != null ? (
          <Badge variant="outline">LTP {row.last_price.toFixed(2)}</Badge>
        ) : null}
      </div>
      <div className="mt-2 space-y-1 font-mono text-xs text-muted-foreground">
        {row.st_exit_price != null ? (
          <div className={row.st_exit_at_ltp ? "text-amber-400" : "text-foreground"}>
            {row.st_exit_label ?? "ST exit"} @ {Number(row.st_exit_price).toFixed(2)}
            {row.st_exit_ltp_distance != null ? (
              <span> ({row.st_exit_ltp_distance >= 0 ? "+" : ""}{row.st_exit_ltp_distance.toFixed(2)} vs LTP)</span>
            ) : null}
            {row.st_exit_at_ltp ? " · ZONE HIT" : ""}
          </div>
        ) : null}
        {row.st_entry_price != null ? (
          <div>
            {row.st_entry_label ?? "ST entry"} @ {Number(row.st_entry_price).toFixed(2)}
          </div>
        ) : null}
        {row.trail_stop != null && row.tsl_mode && row.tsl_mode !== "Off" ? (
          <div>
            TSL ({row.tsl_mode}) trail @ {Number(row.trail_stop).toFixed(2)}
            {row.risk_exit_triggered ? " · HIT" : ""}
          </div>
        ) : null}
        {row.force_exit ? (
          <div className={row.force_exit_due ? "text-amber-400" : undefined}>
            Force exit @ {row.force_exit}
            {row.session_end ? ` (session ${row.session_end})` : ""}
            {row.force_exit_due ? " · DUE" : ""}
          </div>
        ) : null}
        {row.exit_note ? <div>{row.exit_note}</div> : null}
      </div>
    </div>
  );
}

function ManualQueueTable({
  items,
  onEnter,
  onDismiss,
}: {
  items: WatchlistItem[];
  onEnter: (id: string, side: "buy" | "sell") => Promise<void>;
  onDismiss: (id: string) => Promise<void>;
}) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-muted-foreground">
        Manual entry — choose BUY or SELL (exit by 3ST algo on your timeframe)
      </h3>
      <div className="overflow-auto rounded-md border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Instrument</TableHead>
              <TableHead>TF</TableHead>
              <TableHead>Product</TableHead>
              <TableHead className="text-right">Manual order</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell className="font-mono text-sm">{item.tradingsymbol}</TableCell>
                <TableCell className="font-mono">{item.timeframe}</TableCell>
                <TableCell className="font-mono text-xs">{item.product}</TableCell>
                <TableCell className="text-right space-x-1">
                  <Button size="sm" className="bg-bull hover:bg-bull/90" onClick={() => onEnter(item.id, "buy")}>
                    BUY
                  </Button>
                  <Button size="sm" className="bg-bear hover:bg-bear/90" onClick={() => onEnter(item.id, "sell")}>
                    SELL
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => onDismiss(item.id)}>
                    Remove
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function orderSideLabel(row: ActiveTradeRow): string {
  if (row.entry_side) return row.entry_side;
  if (row.signal === "long") return "BUY";
  if (row.signal === "short") return "SELL";
  return "—";
}

function ActiveTradesTable({
  trades,
  liveMode,
  armed,
  liveLtp,
  onTriggerSide,
  onClose,
}: {
  trades: ActiveTradeRow[];
  liveMode: boolean;
  armed: boolean;
  liveLtp?: Record<string, LtpTick>;
  onTriggerSide: (id: string, side: "buy" | "sell") => Promise<void>;
  onClose: (id: string) => Promise<void>;
}) {
  const statusLabel: Record<ActiveTradeRow["status"], string> = {
    running: "Running",
    tracking: "Tracking",
    no_quote: "No live quote",
    no_position: "No position",
  };
  const statusClass: Record<ActiveTradeRow["status"], string> = {
    running: "border-bull/40 text-bull",
    tracking: "border-sky-500/40 text-sky-400",
    no_quote: "border-amber-500/40 text-amber-400",
    no_position: "border-bear/40 text-bear",
  };

  return (
    <div className="overflow-auto rounded-md border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Instrument</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Mode</TableHead>
            <TableHead>Side</TableHead>
            <TableHead className="text-right">Entry</TableHead>
            <TableHead className="text-right">Qty</TableHead>
            <TableHead className="text-right">LTP</TableHead>
            <TableHead className="text-right">P&amp;L</TableHead>
            <TableHead>Exit triggers</TableHead>
            <TableHead className="text-right">Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {trades.map((row) => {
            const tick =
              row.exchange && row.tradingsymbol
                ? liveLtp?.[`${row.exchange}:${row.tradingsymbol}`]
                : undefined;
            const ltp = tick ? tick.price : row.last_price;
            const pnl =
              tick && row.entry_price != null && row.quantity
                ? (tick.price - row.entry_price) *
                  row.quantity *
                  (row.signal === "short" ? -1 : 1)
                : row.pnl;
            return (
            <TableRow key={row.id}>
              <TableCell className="font-mono text-sm">
                {row.tradingsymbol || "—"}
                {row.exchange && (
                  <div className="text-xs text-muted-foreground">{row.exchange}</div>
                )}
              </TableCell>
              <TableCell>
                <Badge variant="outline" className={cn("text-xs", statusClass[row.status])}>
                  {statusLabel[row.status]}
                </Badge>
              </TableCell>
              <TableCell>
                <div className="flex flex-col gap-0.5">
                  <Badge variant="outline" className="text-xs uppercase w-fit">
                    {row.trade_mode}
                  </Badge>
                  {row.kite_product && (
                    <Badge variant="secondary" className="text-[10px] w-fit font-mono">
                      {row.kite_product}
                    </Badge>
                  )}
                  {row.system_mode === "Intraday" && (
                    <span className="text-[10px] text-muted-foreground">Intraday</span>
                  )}
                </div>
              </TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  <Badge
                    variant={orderSideLabel(row) === "BUY" ? "default" : "outline"}
                    className={cn(
                      orderSideLabel(row) === "BUY" ? "bg-bull" : "border-bull/40 text-bull/70",
                    )}
                  >
                    BUY
                  </Badge>
                  <Badge
                    variant={orderSideLabel(row) === "SELL" ? "default" : "outline"}
                    className={cn(
                      orderSideLabel(row) === "SELL" ? "bg-bear" : "border-bear/40 text-bear/70",
                    )}
                  >
                    SELL
                  </Badge>
                </div>
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">
                {row.entry_price != null ? row.entry_price.toFixed(2) : "—"}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-sky-400">
                {row.quantity || "—"}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">
                <span className="inline-flex items-center justify-end gap-1">
                  {tick?.fresh && (
                    <span
                      className="h-1.5 w-1.5 rounded-full bg-bull"
                      title={`Live · ${tick.age_sec.toFixed(1)}s`}
                    />
                  )}
                  {ltp != null ? ltp.toFixed(2) : "—"}
                </span>
              </TableCell>
              <TableCell
                className={cn(
                  "text-right font-mono tabular-nums",
                  pnl != null && (pnl >= 0 ? "text-bull" : "text-bear"),
                )}
              >
                {pnl != null ? formatDeskPnl(pnl) : "—"}
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {row.last_price != null && (
                  <div className="font-mono text-foreground">LTP {row.last_price.toFixed(2)}</div>
                )}
                {row.trail_stop != null && row.tsl_mode && row.tsl_mode !== "Off" && (
                  <div>
                    TSL trail
                    {row.tsl_mode === "ATR" ? ` (ATR×${row.tsl_value ?? "—"})` : ""} @{" "}
                    {Number(row.trail_stop).toFixed(2)}
                    {row.risk_exit_triggered && (
                      <Badge className="ml-1 bg-amber-500/20 text-amber-400">Hit</Badge>
                    )}
                  </div>
                )}
                {row.tsl_live != null && (
                  <div className="font-mono">
                    TSL @ LTP {Number(row.tsl_live).toFixed(2)}
                  </div>
                )}
                {row.st_exit_price != null && (
                  <div className="font-mono">
                    {row.st_exit_label ?? "ST exit"} @ {Number(row.st_exit_price).toFixed(2)}
                    {row.st_bands_live && (
                      <span className="text-[10px] text-sky-400"> live</span>
                    )}
                    {row.st_exit_ltp_distance != null && row.last_price != null && (
                      <span className="opacity-70">
                        {" "}
                        ({row.st_exit_ltp_distance >= 0 ? "+" : ""}
                        {row.st_exit_ltp_distance.toFixed(2)} vs LTP)
                      </span>
                    )}
                    {row.st_exit_at_ltp && (
                      <Badge className="ml-1 bg-amber-500/20 text-amber-400">Zone</Badge>
                    )}
                  </div>
                )}
                {row.st_entry_price != null && (
                  <div className="font-mono opacity-80">
                    {row.st_entry_label ?? "ST entry"} @ {Number(row.st_entry_price).toFixed(2)}
                    {row.st_bands_live && (
                      <span className="text-[10px] text-sky-400"> live</span>
                    )}
                  </div>
                )}
                {row.st_exit_price == null && (row.st1 != null || row.exit_line != null) && (
                  <div>
                    ST1 {row.st1_dir ?? ""} @ {(row.st1 ?? row.exit_line)?.toFixed(2) ?? "—"}
                  </div>
                )}
                {row.target_level != null && (
                  <div>TGT @ {Number(row.target_level).toFixed(2)}</div>
                )}
                {row.force_exit && (
                  <div>
                    Force @ {row.force_exit}
                    {row.session_end ? ` → ${row.session_end}` : ""}
                    {row.force_exit_due && (
                      <Badge className="ml-1 bg-amber-500/20 text-amber-400">Due</Badge>
                    )}
                  </div>
                )}
                {row.entry_bar_close != null && (
                  <div className="text-[10px] opacity-70">
                    Entry bar {row.entry_bar_close.toFixed(2)}
                    {row.timeframe ? ` · ${row.timeframe}` : ""}
                    {row.st_method && row.st_method !== "heikin_ashi" ? ` · ${row.st_method}` : ""}
                  </div>
                )}
                {row.timeframe && row.entry_bar_close == null && (
                  <div className="text-[10px] opacity-70">
                    {row.timeframe}
                    {row.signal_close != null ? ` · bar ${row.signal_close.toFixed(2)}` : ""}
                  </div>
                )}
                {row.price_divergence && (
                  <div className="text-[10px] text-amber-400">{row.price_divergence}</div>
                )}
                {!row.trail_stop && row.st1 == null && !row.exit_line && !row.target_level && (
                  <span>{row.exit_note ?? "Monitoring"}</span>
                )}
              </TableCell>
              <TableCell className="text-right">
                <div className="flex flex-wrap justify-end gap-1">
                  {row.status !== "running" && (
                    <>
                      <Button
                        size="sm"
                        className="bg-bull hover:bg-bull/90"
                        disabled={liveMode && !armed}
                        title={liveMode && !armed ? "ARM required for exchange" : undefined}
                        onClick={() => onTriggerSide(row.id, "buy").catch(() => {})}
                      >
                        BUY
                      </Button>
                      <Button
                        size="sm"
                        className="bg-bear hover:bg-bear/90"
                        disabled={liveMode && !armed}
                        onClick={() => onTriggerSide(row.id, "sell").catch(() => {})}
                      >
                        SELL
                      </Button>
                    </>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => onClose(row.id)}>
                    Close
                  </Button>
                </div>
              </TableCell>
            </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function LiveQueueTable({
  title,
  items,
  onActivate,
  onDismiss,
}: {
  title: string;
  items: WatchlistItem[];
  onActivate: (id: string) => Promise<void>;
  onDismiss: (id: string) => Promise<void>;
}) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-muted-foreground">{title}</h3>
      <div className="overflow-auto rounded-md border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Instrument</TableHead>
              <TableHead>Signal</TableHead>
              <TableHead>Timeframe</TableHead>
              <TableHead>Product</TableHead>
              <TableHead className="text-right">Action</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell>
                  <div className="font-mono text-sm">{item.tradingsymbol}</div>
                  {item.spread && (
                    <div className="text-xs text-muted-foreground">
                      {item.spread.underlying} ·{" "}
                      {item.signal === "long"
                        ? item.spread.long_template
                        : item.spread.short_template}
                    </div>
                  )}
                </TableCell>
                <TableCell>
                  {item.signal ? (
                    <Badge className={item.signal === "long" ? "bg-bull" : "bg-bear"}>
                      {item.signal.toUpperCase()}
                    </Badge>
                  ) : (
                    "—"
                  )}
                </TableCell>
                <TableCell className="font-mono">{item.timeframe}</TableCell>
                <TableCell className="font-mono text-xs">{item.product}</TableCell>
                <TableCell className="text-right">
                  <Button size="sm" onClick={() => onActivate(item.id)}>
                    Activate <ArrowRight className="ml-1 h-3 w-3" />
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => onDismiss(item.id)}>
                    Dismiss
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function OrdersTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) {
    return (
      <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
        No orders
      </div>
    );
  }
  const cols = ["tradingsymbol", "transaction_type", "quantity", "price", "status", "product"];
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {cols.map((c) => (
            <TableHead key={c}>{c}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r, i) => (
          <TableRow key={i}>
            {cols.map((c) => (
              <TableCell key={c} className="font-mono text-xs">
                {String(r[c] ?? "")}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function RiskItem({
  label,
  value,
  warn = false,
}: {
  label: string;
  value: number | string | undefined;
  warn?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-md border p-3",
        warn ? "border-bear/50 bg-bear/10" : "border-border bg-muted/20",
      )}
    >
      <div className="text-xs uppercase text-muted-foreground">{label}</div>
      <div className={cn("font-mono text-lg", warn && "text-bear")}>{value ?? "—"}</div>
    </div>
  );
}
