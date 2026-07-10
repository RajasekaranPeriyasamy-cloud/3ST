import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ArrowRight, Power, ShieldOff } from "lucide-react";

import { api } from "@/lib/api";
import { useWatchlistByStatus } from "@/context/WatchlistContext";
import type { WatchlistItem } from "@/lib/types";
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

export const Route = createFileRoute("/live")({
  component: LivePage,
});

interface ArmStatus {
  armed?: boolean;
  mode?: "paper" | "live";
}
interface RiskLimits {
  max_loss_day?: number;
  max_trades_day?: number;
  max_qty?: number;
}

function LivePage() {
  const {
    items: liveQueue,
    refresh: refreshWatchlist,
    activate,
    close,
    scan,
  } = useWatchlistByStatus("triggered,active");
  const triggered = useMemo(() => liveQueue.filter((i) => i.status === "triggered"), [liveQueue]);
  const active = useMemo(() => liveQueue.filter((i) => i.status === "active"), [liveQueue]);
  const [arm, setArm] = useState<ArmStatus | null>(null);
  const [mode, setMode] = useState<"paper" | "live">("paper");
  const [positions, setPositions] = useState<Record<string, unknown>[]>([]);
  const [orders, setOrders] = useState<Record<string, unknown>[]>([]);
  const [risk, setRisk] = useState<RiskLimits | null>(null);

  async function refreshDesk() {
    try {
      const a = await api.get<ArmStatus>("/live/arm", { silent: true });
      setArm(a);
      if (a?.mode) setMode(a.mode);
    } catch { /* */ }
    await refreshWatchlist();
    api.get<{ positions?: Record<string, unknown>[] }>("/live/positions", { silent: true })
      .then((r) => setPositions(r.positions ?? []))
      .catch(() => {});
    api.get<{ orders?: Record<string, unknown>[] }>("/live/orders", { silent: true })
      .then((r) => setOrders(r.orders ?? []))
      .catch(() => {});
    api.get<RiskLimits>("/risk/limits", { silent: true }).then(setRisk).catch(() => {});
  }

  useEffect(() => {
    refreshDesk();
    const t = setInterval(refreshDesk, 5000);
    return () => clearInterval(t);
  }, []);

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
    } catch { /* */ }
  }

  async function armNow() {
    try {
      await api.post("/live/arm", { confirm: true });
      toast.success("ARMED");
      refreshDesk();
    } catch { /* */ }
  }

  async function disarmNow() {
    try {
      await api.post("/live/disarm");
      toast.success("DISARMED");
      refreshDesk();
    } catch { /* */ }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Live Desk</h1>
          <p className="text-sm text-muted-foreground">
            Triggered 3ST signals land here for execution. {triggered.length} pending · {active.length} active
          </p>
        </div>
        <Badge className={arm?.armed ? "bg-bull text-base px-3 py-1" : "bg-bear text-base px-3 py-1"}>
          {arm?.armed ? "ARMED" : "DISARMED"}
        </Badge>
      </header>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Signal inbox</CardTitle>
          <Button size="sm" variant="outline" onClick={() => scan(false).catch(() => {})}>
            Scan now
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {triggered.length === 0 && active.length === 0 ? (
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
                    await activate(id);
                    toast.success("Moved to active — order placement when ARMED");
                    refreshDesk();
                  }}
                  onDismiss={async (id) => {
                    await close(id);
                    toast.message("Trade closed");
                    refreshDesk();
                  }}
                />
              )}
              {active.length > 0 && (
                <LiveQueueTable
                  title="Active"
                  items={active}
                  onActivate={async () => {}}
                  onDismiss={async (id) => {
                    await close(id);
                    toast.message("Trade closed");
                    refreshDesk();
                  }}
                  activeOnly
                />
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Controls</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Mode:</span>
            <Button
              size="sm"
              variant={mode === "paper" ? "default" : "outline"}
              onClick={() => switchMode("paper")}
            >
              Paper
            </Button>
            <Button
              size="sm"
              variant={mode === "live" ? "default" : "outline"}
              onClick={() => switchMode("live")}
            >
              Live
            </Button>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button disabled={mode !== "live" || arm?.armed}>
                  <Power className="mr-2 h-4 w-4" /> ARM
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Arm live trading?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Real orders will be placed for active queue items when execution is enabled.
                    {active.length === 0 && " Activate at least one triggered signal first."}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={armNow}>Arm now</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>

            <Button variant="destructive" onClick={disarmNow}>
              <ShieldOff className="mr-2 h-4 w-4" /> DISARM (Kill Switch)
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Positions</CardTitle>
          </CardHeader>
          <CardContent className="max-h-72 overflow-auto">
            <RowsTable rows={positions} empty="No open positions" />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Orders</CardTitle>
          </CardHeader>
          <CardContent className="max-h-72 overflow-auto">
            <RowsTable rows={orders} empty="No orders" />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Risk Limits</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3 text-sm">
          <RiskItem label="Max loss / day" value={risk?.max_loss_day} />
          <RiskItem label="Max trades / day" value={risk?.max_trades_day} />
          <RiskItem label="Max quantity" value={risk?.max_qty} />
        </CardContent>
      </Card>
    </div>
  );
}

function LiveQueueTable({
  title,
  items,
  onActivate,
  onDismiss,
  activeOnly = false,
}: {
  title: string;
  items: WatchlistItem[];
  onActivate: (id: string) => Promise<void>;
  onDismiss: (id: string) => Promise<void>;
  activeOnly?: boolean;
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
                      {item.spread.underlying} · {item.signal === "long" ? item.spread.long_template : item.spread.short_template}
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
                  {!activeOnly && (
                    <Button size="sm" onClick={() => onActivate(item.id)}>
                      Activate <ArrowRight className="ml-1 h-3 w-3" />
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => onDismiss(item.id)}>
                    Close
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

function RiskItem({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <div className="text-xs uppercase text-muted-foreground">{label}</div>
      <div className="font-mono text-lg">{value ?? "—"}</div>
    </div>
  );
}

function RowsTable({ rows, empty }: { rows: Record<string, unknown>[]; empty: string }) {
  if (!rows.length)
    return (
      <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
        {empty}
      </div>
    );
  const cols = Object.keys(rows[0]);
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
