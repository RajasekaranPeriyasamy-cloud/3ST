import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, Play, Power, RefreshCw, Save, ShieldOff, Square } from "lucide-react";

import { api } from "@/lib/api";
import type { SurvivorConfig, SurvivorLogEntry, SurvivorStatus } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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

export const Route = createFileRoute("/survivor")({
  component: SurvivorPage,
});

const LOT_SIZES: Record<SurvivorConfig["underlying"], number> = {
  NIFTY: 65,
  BANKNIFTY: 30,
  SENSEX: 20,
};

const DEFAULT: SurvivorConfig = {
  underlying: "NIFTY",
  expiry: "",
  symbol_initials: "",
  pe_gap: 20,
  ce_gap: 20,
  pe_quantity: 65,
  ce_quantity: 65,
  pe_symbol_gap: 200,
  ce_symbol_gap: 200,
  min_price_to_sell: 15,
  sell_multiplier_threshold: 5,
  tick_interval_sec: 15,
  product_type: "NRML",
  tag: "Survivor",
  auto_start_on_boot: false,
};

function SurvivorPage() {
  const [config, setConfig] = useState<SurvivorConfig>(DEFAULT);
  const [status, setStatus] = useState<SurvivorStatus | null>(null);
  const [logs, setLogs] = useState<SurvivorLogEntry[]>([]);
  const [expiries, setExpiries] = useState<string[]>([]);
  const [armDialogOpen, setArmDialogOpen] = useState(false);
  const [arming, setArming] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [cfg, st, logRes] = await Promise.all([
        api.get<SurvivorConfig>("/live/survivor/config", { silent: true }),
        api.get<SurvivorStatus>("/live/survivor/status", { silent: true }),
        api.get<{ items?: SurvivorLogEntry[] }>("/live/survivor/log?limit=40", { silent: true }),
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

  useEffect(() => {
    api
      .get<{ expiries?: string[] }>(`/options/expiries?underlying=${config.underlying}`, { silent: true })
      .then((r) => setExpiries(r.expiries ?? []))
      .catch(() => setExpiries([]));
  }, [config.underlying]);

  async function saveConfig() {
    await api.post("/live/survivor/config", config);
    toast.success("Survivor config saved");
    refresh();
  }

  async function switchMode(mode: "paper" | "live") {
    await api.post("/live/mode", { mode });
    toast.success(`Mode: ${mode.toUpperCase()}`);
    refresh();
  }

  async function armNow() {
    setArming(true);
    try {
      await api.post("/live/arm", { confirm: true });
      toast.success("ARMED — live orders enabled");
      setArmDialogOpen(false);
      refresh();
    } catch {
      /* api toast */
    } finally {
      setArming(false);
    }
  }

  async function disarmNow() {
    await api.post("/live/disarm");
    toast.success("DISARMED");
    refresh();
  }

  const running = status?.state?.runner === "running";
  const lot = LOT_SIZES[config.underlying] ?? 1;
  const qtyInvalid =
    config.pe_quantity <= 0 ||
    config.ce_quantity <= 0 ||
    config.pe_quantity % lot !== 0 ||
    config.ce_quantity % lot !== 0;
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
          <h1 className="text-2xl font-semibold">Survivor Strategy</h1>
          <p className="text-sm text-muted-foreground">
            Gap-based NIFTY option premium selling (ported from trading-algo)
          </p>
        </div>
      </header>

      <Card>
        <CardHeader><CardTitle className="text-base">Status</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Badge variant={running ? "default" : "secondary"}>{running ? "RUNNING" : "STOPPED"}</Badge>
          <Badge variant="outline">Spot {status?.state?.last_spot?.toFixed(2) ?? "—"}</Badge>
          <Badge variant="outline">PE ref {status?.state?.nifty_pe_last_value?.toFixed(0) ?? "—"}</Badge>
          <Badge variant="outline">CE ref {status?.state?.nifty_ce_last_value?.toFixed(0) ?? "—"}</Badge>
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
            <AlertDialog open={armDialogOpen} onOpenChange={setArmDialogOpen}>
              <AlertDialogTrigger asChild>
                <Button
                  size="sm"
                  disabled={status?.arm?.mode !== "live" || status?.arm?.armed}
                  title={
                    status?.arm?.armed
                      ? "Already ARMED"
                      : status?.arm?.mode !== "live"
                        ? "Switch to Live first"
                        : undefined
                  }
                >
                  <Power className="mr-2 h-4 w-4" /> ARM
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Arm live trading?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Survivor will place real MARKET SELL orders on Kite when gap triggers fire.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel disabled={arming}>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    disabled={arming}
                    onClick={(e) => {
                      e.preventDefault();
                      void armNow();
                    }}
                  >
                    {arming ? "Arming…" : "Arm now"}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => disarmNow().catch(() => {})}
              disabled={!status?.arm?.armed}
            >
              <ShieldOff className="mr-2 h-4 w-4" /> DISARM
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            The red DISARMED badge is status only — use the <strong>ARM</strong> button above (or Live Desk) to enable orders.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Config</CardTitle></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div>
            <Label>Underlying</Label>
            <Select value={config.underlying} onValueChange={(v) => setConfig((c) => ({ ...c, underlying: v as SurvivorConfig["underlying"] }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {["NIFTY", "BANKNIFTY", "SENSEX"].map((u) => (
                  <SelectItem key={u} value={u}>{u}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Expiry</Label>
            <Select value={config.expiry || undefined} onValueChange={(v) => setConfig((c) => ({ ...c, expiry: v }))}>
              <SelectTrigger><SelectValue placeholder="Nearest" /></SelectTrigger>
              <SelectContent>
                {expiries.map((e) => <SelectItem key={e} value={e}>{e}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Symbol initials (optional)</Label>
            <Input value={config.symbol_initials ?? ""} onChange={(e) => setConfig((c) => ({ ...c, symbol_initials: e.target.value }))} placeholder="Auto from expiry" />
          </div>
          <div><Label>PE gap</Label><Input type="number" value={config.pe_gap} onChange={(e) => setConfig((c) => ({ ...c, pe_gap: Number(e.target.value) }))} /></div>
          <div><Label>CE gap</Label><Input type="number" value={config.ce_gap} onChange={(e) => setConfig((c) => ({ ...c, ce_gap: Number(e.target.value) }))} /></div>
          <div>
            <Label>PE qty</Label>
            <Input type="number" value={config.pe_quantity} onChange={(e) => setConfig((c) => ({ ...c, pe_quantity: Number(e.target.value) }))} />
            <p className="mt-1 text-xs text-muted-foreground">Lot {lot} — use {lot}, {lot * 2}, …</p>
          </div>
          <div>
            <Label>CE qty</Label>
            <Input type="number" value={config.ce_quantity} onChange={(e) => setConfig((c) => ({ ...c, ce_quantity: Number(e.target.value) }))} />
            <p className="mt-1 text-xs text-muted-foreground">Lot {lot} — use {lot}, {lot * 2}, …</p>
          </div>
          <div><Label>Min premium</Label><Input type="number" value={config.min_price_to_sell} onChange={(e) => setConfig((c) => ({ ...c, min_price_to_sell: Number(e.target.value) }))} /></div>
          <div><Label>Tick interval (s)</Label><Input type="number" value={config.tick_interval_sec} onChange={(e) => setConfig((c) => ({ ...c, tick_interval_sec: Number(e.target.value) }))} /></div>
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-2">
        <Button onClick={saveConfig}><Save className="mr-2 h-4 w-4" />Save</Button>
        <Button variant="outline" onClick={() => api.post("/live/survivor/tick").then(refresh)}><RefreshCw className="mr-2 h-4 w-4" />Tick now</Button>
        {!running ? (
          <Button onClick={() => api.post("/live/survivor/start").then(refresh)}><Play className="mr-2 h-4 w-4" />Start</Button>
        ) : (
          <Button variant="destructive" onClick={() => api.post("/live/survivor/stop").then(refresh)}><Square className="mr-2 h-4 w-4" />Stop</Button>
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
