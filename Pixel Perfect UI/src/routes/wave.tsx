import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, Play, Power, RefreshCw, Save, ShieldOff, Square } from "lucide-react";

import { api } from "@/lib/api";
import type { WaveConfig, WaveLogEntry, WaveStatus } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

export const Route = createFileRoute("/wave")({
  component: WavePage,
});

function lotForSymbol(symbol: string): number {
  const s = symbol.toUpperCase();
  if (s.startsWith("BANKNIFTY")) return 30;
  if (s.startsWith("SENSEX")) return 20;
  if (s.startsWith("NIFTY")) return 65;
  return 1;
}

const DEFAULT: WaveConfig = {
  symbol_name: "NIFTY26JULFUT",
  exchange: "NFO",
  buy_gap: 25,
  sell_gap: 25,
  buy_quantity: 65,
  sell_quantity: 65,
  lot_size: 65,
  cool_off_time: 10,
  product_type: "NRML",
  order_type: "LIMIT",
  tag: "WaveScraper",
  check_interval_sec: 60,
  auto_start_on_boot: false,
};

function WavePage() {
  const [config, setConfig] = useState<WaveConfig>(DEFAULT);
  const [status, setStatus] = useState<WaveStatus | null>(null);
  const [logs, setLogs] = useState<WaveLogEntry[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [cfg, st, logRes] = await Promise.all([
        api.get<WaveConfig>("/live/wave/config", { silent: true }),
        api.get<WaveStatus>("/live/wave/status", { silent: true }),
        api.get<{ items?: WaveLogEntry[] }>("/live/wave/log?limit=40", { silent: true }),
      ]);
      setConfig({ ...DEFAULT, ...cfg });
      setStatus(st);
      setLogs(logRes.items ?? []);
    } catch {
      /* silent */
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  async function saveConfig() {
    await api.post("/live/wave/config", config);
    toast.success("Wave config saved");
    refresh();
  }

  async function switchMode(mode: "paper" | "live") {
    await api.post("/live/mode", { mode });
    toast.success(`Mode: ${mode.toUpperCase()}`);
    refresh();
  }

  async function armNow() {
    await api.post("/live/arm", { confirm: true });
    toast.success("ARMED — live orders enabled");
    refresh();
  }

  async function disarmNow() {
    await api.post("/live/disarm");
    toast.success("DISARMED");
    refresh();
  }

  const running = status?.state?.runner === "running";
  const lot = config.lot_size || lotForSymbol(config.symbol_name);
  const qtyInvalid =
    config.buy_quantity <= 0 ||
    config.sell_quantity <= 0 ||
    config.buy_quantity % lot !== 0 ||
    config.sell_quantity % lot !== 0;
  const liveReady =
    Boolean(status?.kite_authenticated) &&
    status?.arm?.mode === "live" &&
    Boolean(status?.arm?.armed) &&
    !qtyInvalid;

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 pb-10">
      <header className="flex items-center gap-3">
        <Button asChild variant="ghost" size="icon">
          <Link to="/execution"><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <div>
          <h1 className="text-2xl font-semibold">Wave Strategy</h1>
          <p className="text-sm text-muted-foreground">
            Limit buy/sell pair wave extractor (ported from trading-algo)
          </p>
        </div>
      </header>

      <Card>
        <CardHeader><CardTitle className="text-base">Status</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Badge variant={running ? "default" : "secondary"}>{running ? "RUNNING" : "STOPPED"}</Badge>
          <Badge variant="outline">Spot {status?.state?.last_spot?.toFixed(2) ?? "—"}</Badge>
          <Badge variant="outline">Active orders {status?.state?.active_orders ?? 0}</Badge>
          {status?.state?.last_error ? (
            <Badge variant="destructive">{status.state.last_error}</Badge>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Live readiness</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2 text-sm">
            <Badge variant={status?.kite_authenticated ? "default" : "destructive"}>
              Kite {status?.kite_authenticated ? "logged in" : "login required"}
            </Badge>
            <Badge variant={status?.arm?.mode === "live" ? "default" : "secondary"}>
              Mode {status?.arm?.mode ?? "—"}
            </Badge>
            <Badge variant={status?.arm?.armed ? "default" : "destructive"}>
              {status?.arm?.armed ? "ARMED" : "DISARMED"}
            </Badge>
            <Badge variant={qtyInvalid ? "destructive" : "outline"}>
              Qty {qtyInvalid ? `invalid (lot ${lot})` : `OK (lot ${lot})`}
            </Badge>
            <Badge variant={liveReady ? "default" : "secondary"}>
              {liveReady ? "Ready for live orders" : "Not ready for live orders"}
            </Badge>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant={status?.arm?.mode === "paper" ? "default" : "outline"}
              onClick={() => switchMode("paper").catch(() => {})}
            >
              Paper
            </Button>
            <Button
              size="sm"
              variant={status?.arm?.mode === "live" ? "default" : "outline"}
              onClick={() => switchMode("live").catch(() => {})}
            >
              Live
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  size="sm"
                  disabled={status?.arm?.mode !== "live" || status?.arm?.armed}
                >
                  <Power className="mr-2 h-4 w-4" /> ARM
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Arm live trading?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Wave will place real LIMIT buy/sell pairs on Kite when the runner is active.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={() => armNow().catch(() => {})}>Arm now</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <Button size="sm" variant="destructive" onClick={() => disarmNow().catch(() => {})}>
              <ShieldOff className="mr-2 h-4 w-4" /> DISARM
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Use <strong>Live</strong> + <strong>ARM</strong> before Start. Spot updates every {config.check_interval_sec}s while running.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Config</CardTitle></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="md:col-span-2">
            <Label>Symbol</Label>
            <Input
              value={config.symbol_name}
              onChange={(e) => {
                const symbol_name = e.target.value;
                setConfig((c) => ({
                  ...c,
                  symbol_name,
                  lot_size: lotForSymbol(symbol_name),
                }));
              }}
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Current month NIFTY fut — roll before expiry (e.g. NIFTY26JULFUT)
            </p>
          </div>
          <div><Label>Exchange</Label><Input value={config.exchange} onChange={(e) => setConfig((c) => ({ ...c, exchange: e.target.value }))} /></div>
          <div><Label>Tag</Label><Input value={config.tag} onChange={(e) => setConfig((c) => ({ ...c, tag: e.target.value }))} /></div>
          <div><Label>Buy gap</Label><Input type="number" value={config.buy_gap} onChange={(e) => setConfig((c) => ({ ...c, buy_gap: Number(e.target.value) }))} /></div>
          <div><Label>Sell gap</Label><Input type="number" value={config.sell_gap} onChange={(e) => setConfig((c) => ({ ...c, sell_gap: Number(e.target.value) }))} /></div>
          <div>
            <Label>Buy qty</Label>
            <Input type="number" value={config.buy_quantity} onChange={(e) => setConfig((c) => ({ ...c, buy_quantity: Number(e.target.value) }))} />
            <p className="mt-1 text-xs text-muted-foreground">Lot {lot} — use {lot}, {lot * 2}, …</p>
          </div>
          <div>
            <Label>Sell qty</Label>
            <Input type="number" value={config.sell_quantity} onChange={(e) => setConfig((c) => ({ ...c, sell_quantity: Number(e.target.value) }))} />
            <p className="mt-1 text-xs text-muted-foreground">Lot {lot} — use {lot}, {lot * 2}, …</p>
          </div>
          <div><Label>Cool-off (s)</Label><Input type="number" value={config.cool_off_time} onChange={(e) => setConfig((c) => ({ ...c, cool_off_time: Number(e.target.value) }))} /></div>
          <div><Label>Check interval (s)</Label><Input type="number" value={config.check_interval_sec} onChange={(e) => setConfig((c) => ({ ...c, check_interval_sec: Number(e.target.value) }))} /></div>
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-2">
        <Button onClick={saveConfig}><Save className="mr-2 h-4 w-4" />Save</Button>
        <Button variant="outline" onClick={() => api.post("/live/wave/tick").then(refresh)}><RefreshCw className="mr-2 h-4 w-4" />Tick now</Button>
        {!running ? (
          <Button onClick={() => api.post("/live/wave/start").then(refresh)}><Play className="mr-2 h-4 w-4" />Start</Button>
        ) : (
          <Button variant="destructive" onClick={() => api.post("/live/wave/stop").then(refresh)}><Square className="mr-2 h-4 w-4" />Stop</Button>
        )}
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">Log</CardTitle></CardHeader>
        <CardContent className="max-h-64 overflow-y-auto font-mono text-xs">
          {logs.map((row, i) => (
            <div key={i} className="border-b border-border/40 py-1">{row.at} · {row.event} · {row.detail}</div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
