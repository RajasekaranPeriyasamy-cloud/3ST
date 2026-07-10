import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, Play, RefreshCw, Save, Square } from "lucide-react";

import { api } from "@/lib/api";
import type { WaveConfig, WaveLogEntry, WaveStatus } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const Route = createFileRoute("/wave")({
  component: WavePage,
});

const DEFAULT: WaveConfig = {
  symbol_name: "NIFTY25SEPFUT",
  exchange: "NFO",
  buy_gap: 25,
  sell_gap: 25,
  buy_quantity: 75,
  sell_quantity: 75,
  lot_size: 75,
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

  const running = status?.state?.runner === "running";

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
        <CardHeader><CardTitle className="text-base">Config</CardTitle></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div><Label>Symbol</Label><Input value={config.symbol_name} onChange={(e) => setConfig((c) => ({ ...c, symbol_name: e.target.value }))} /></div>
          <div><Label>Exchange</Label><Input value={config.exchange} onChange={(e) => setConfig((c) => ({ ...c, exchange: e.target.value }))} /></div>
          <div><Label>Tag</Label><Input value={config.tag} onChange={(e) => setConfig((c) => ({ ...c, tag: e.target.value }))} /></div>
          <div><Label>Buy gap</Label><Input type="number" value={config.buy_gap} onChange={(e) => setConfig((c) => ({ ...c, buy_gap: Number(e.target.value) }))} /></div>
          <div><Label>Sell gap</Label><Input type="number" value={config.sell_gap} onChange={(e) => setConfig((c) => ({ ...c, sell_gap: Number(e.target.value) }))} /></div>
          <div><Label>Buy qty</Label><Input type="number" value={config.buy_quantity} onChange={(e) => setConfig((c) => ({ ...c, buy_quantity: Number(e.target.value) }))} /></div>
          <div><Label>Sell qty</Label><Input type="number" value={config.sell_quantity} onChange={(e) => setConfig((c) => ({ ...c, sell_quantity: Number(e.target.value) }))} /></div>
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
