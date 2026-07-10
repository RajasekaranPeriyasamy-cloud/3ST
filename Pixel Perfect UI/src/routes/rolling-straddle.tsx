import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { ArrowLeft, Play, Power, RefreshCw, Save, ShieldOff, Square } from "lucide-react";

import { api } from "@/lib/api";
import type {
  HealthResponse,
  RiskMode,
  RollingLogEntry,
  RollingStraddleConfig,
  RollingStraddleStatus,
  RollingUnderlying,
  StMethod,
  SystemMode,
  Timeframe,
  TradeMode,
} from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Separator } from "@/components/ui/separator";

export const Route = createFileRoute("/rolling-straddle")({
  component: RollingStraddlePage,
});

const TIMEFRAMES: Timeframe[] = ["5min", "15min", "30min", "60min"];
const UNDERLYINGS: RollingUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];

const DEFAULT_CONFIG: RollingStraddleConfig = {
  underlying: "NIFTY",
  expiry: "",
  timeframe: "5min",
  entry_start: "09:20",
  session_start: "09:15",
  session_end: "15:30",
  force_exit: "15:20",
  system_mode: "Intraday",
  order_type: "MARKET",
  product: "MIS",
  tick_interval_sec: 60,
  trade_mode: "Both",
  max_reentries_ce: 1,
  max_reentries_pe: 1,
  reentry_style: "zone_active",
  allow_dual_open: true,
  auto_start_on_boot: false,
  st_method: "heikin_ashi",
  atr1: 21,
  factor1: 1,
  atr2: 14,
  factor2: 2,
  atr3: 7,
  factor3: 3,
  st1_enabled: true,
  st2_enabled: true,
  st3_enabled: true,
  adx_enabled: true,
  adx_period: 14,
  adx_threshold: 20,
  sl_mode: "Off",
  sl_value: 1,
  tgt_mode: "Off",
  tgt_value: 1,
  tsl_mode: "Off",
  tsl_value: 1.5,
};

function RollingStraddlePage() {
  const [config, setConfig] = useState<RollingStraddleConfig>(DEFAULT_CONFIG);
  const [status, setStatus] = useState<RollingStraddleStatus | null>(null);
  const [expiries, setExpiries] = useState<string[]>([]);
  const [logs, setLogs] = useState<RollingLogEntry[]>([]);
  const [positions, setPositions] = useState<Record<string, unknown>[]>([]);
  const [orders, setOrders] = useState<Record<string, unknown>[]>([]);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [saving, setSaving] = useState(false);

  const patch = useCallback((p: Partial<RollingStraddleConfig>) => {
    setConfig((c) => ({ ...c, ...p }));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [cfg, st, logRes, pos, ord, h] = await Promise.all([
        api.get<RollingStraddleConfig>("/live/rolling-straddle/config", { silent: true }),
        api.get<RollingStraddleStatus>("/live/rolling-straddle/status", { silent: true }),
        api.get<{ items?: RollingLogEntry[] }>("/live/rolling-straddle/log?limit=50", { silent: true }),
        api.get<{ positions?: Record<string, unknown>[] }>("/live/positions", { silent: true }),
        api.get<{ orders?: Record<string, unknown>[] }>("/live/orders", { silent: true }),
        api.get<HealthResponse>("/health", { silent: true }),
      ]);
      setConfig({ ...DEFAULT_CONFIG, ...cfg });
      setStatus(st);
      setLogs(logRes.items ?? []);
      setPositions(pos.positions ?? []);
      setOrders(ord.orders ?? []);
      setHealth(h);
    } catch {
      /* silent poll */
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    api
      .get<{ expiries?: string[] }>(
        `/options/expiries?underlying=${config.underlying}`,
        { silent: true },
      )
      .then((r) => setExpiries(r.expiries ?? []))
      .catch(() => setExpiries([]));
  }, [config.underlying]);

  async function saveConfig() {
    setSaving(true);
    try {
      await api.post("/live/rolling-straddle/config", config);
      toast.success("Config saved");
      refresh();
    } catch {
      /* toast from api */
    } finally {
      setSaving(false);
    }
  }

  async function loadFromSelection() {
    try {
      const sel = await api.get<Partial<RollingStraddleConfig>>("/selection", { silent: true });
      patch({
        st_method: (sel.st_method as StMethod) ?? config.st_method,
        system_mode: (sel.system_mode as SystemMode) ?? config.system_mode,
        session_start: sel.session_start ?? config.session_start,
        session_end: sel.session_end ?? config.session_end,
        force_exit: sel.force_exit ?? config.force_exit,
        atr1: sel.atr1 ?? config.atr1,
        factor1: sel.factor1 ?? config.factor1,
        atr2: sel.atr2 ?? config.atr2,
        factor2: sel.factor2 ?? config.factor2,
        atr3: sel.atr3 ?? config.atr3,
        factor3: sel.factor3 ?? config.factor3,
        st1_enabled: sel.st1_enabled ?? config.st1_enabled,
        st2_enabled: sel.st2_enabled ?? config.st2_enabled,
        st3_enabled: sel.st3_enabled ?? config.st3_enabled,
        adx_enabled: sel.adx_enabled ?? config.adx_enabled,
        adx_period: sel.adx_period ?? config.adx_period,
        adx_threshold: sel.adx_threshold ?? config.adx_threshold,
        sl_mode: (sel.sl_mode as RiskMode) ?? config.sl_mode,
        sl_value: sel.sl_value ?? config.sl_value,
        tgt_mode: (sel.tgt_mode as RiskMode) ?? config.tgt_mode,
        tgt_value: sel.tgt_value ?? config.tgt_value,
        tsl_mode: (sel.tsl_mode as RiskMode) ?? config.tsl_mode,
        tsl_value: sel.tsl_value ?? config.tsl_value,
        timeframe: (sel.timeframe as Timeframe) ?? config.timeframe,
      });
      toast.success("Loaded 3ST params from Stock Selection");
    } catch {
      toast.error("Could not load selection");
    }
  }

  async function startAlgo() {
    try {
      await saveConfig();
      await api.post("/live/rolling-straddle/start");
      toast.success("Rolling straddle started");
      refresh();
    } catch {
      /* */
    }
  }

  async function stopAlgo() {
    try {
      await api.post("/live/rolling-straddle/stop");
      toast.success("Rolling straddle stopped");
      refresh();
    } catch {
      /* */
    }
  }

  async function armLive() {
    try {
      await api.post("/live/mode", { mode: "live" });
      await api.post("/live/arm", { confirm: true });
      toast.success("ARMED");
      refresh();
    } catch {
      /* */
    }
  }

  async function disarm() {
    try {
      await api.post("/live/disarm");
      toast.success("DISARMED");
      refresh();
    } catch {
      /* */
    }
  }

  async function setPaper() {
    try {
      await api.post("/live/mode", { mode: "paper" });
      toast.success("Paper mode");
      refresh();
    } catch {
      /* */
    }
  }

  async function closeAll() {
    try {
      await api.post("/live/rolling-straddle/close-all");
      toast.success("Close-all sent");
      refresh();
    } catch {
      /* */
    }
  }

  async function closeLeg(leg: "ce" | "pe") {
    try {
      await api.post("/live/rolling-straddle/close-leg", { leg });
      toast.success(`Closed ${leg.toUpperCase()}`);
      refresh();
    } catch {
      /* */
    }
  }

  const st = status?.state;
  const arm = status?.arm;
  const running = st?.runner === "running";

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 pb-10">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Button asChild variant="ghost" size="sm" className="mb-2 -ml-2 h-8 px-2">
            <Link to="/execution">
              <ArrowLeft className="mr-1 h-4 w-4" /> Algo Execution
            </Link>
          </Button>
          <h1 className="text-2xl font-semibold tracking-tight">Rolling Straddle</h1>
          <p className="text-sm text-muted-foreground">
            Auto ATM CE/PE on 3ST signals — rolls strike with spot from 9:20, dual-leg independent
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={arm?.armed ? "destructive" : "secondary"}>
            {arm?.armed ? "ARMED" : "DISARMED"}
          </Badge>
          <Badge variant="outline">{arm?.mode?.toUpperCase() ?? "PAPER"}</Badge>
          <Badge variant={running ? "default" : "secondary"}>
            {running ? "RUNNING" : "STOPPED"}
          </Badge>
          {st?.morning_bar_seen ? (
            <Badge variant="outline" className="border-green-600 text-green-600">
              9:20 OK
            </Badge>
          ) : (
            <Badge variant="outline">Waiting 9:20</Badge>
          )}
        </div>
      </header>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-2 pt-6">
          <Button variant="outline" size="sm" onClick={setPaper}>
            Paper
          </Button>
          <Button variant="outline" size="sm" onClick={() => api.post("/live/mode", { mode: "live" }).then(refresh)}>
            Live mode
          </Button>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button size="sm" variant="destructive">
                <Power className="mr-1 h-4 w-4" /> ARM
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>ARM live trading?</AlertDialogTitle>
                <AlertDialogDescription>
                  Real Kite orders will be placed when this algo is running and ARMED.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={armLive}>ARM</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <Button size="sm" variant="secondary" onClick={disarm}>
            <ShieldOff className="mr-1 h-4 w-4" /> DISARM
          </Button>
          <Separator orientation="vertical" className="mx-1 h-8" />
          <Button size="sm" onClick={startAlgo} disabled={!health?.kite_authenticated && arm?.mode === "live"}>
            <Play className="mr-1 h-4 w-4" /> Start
          </Button>
          <Button size="sm" variant="outline" onClick={stopAlgo}>
            <Square className="mr-1 h-4 w-4" /> Stop
          </Button>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button size="sm" variant="destructive">
                Force close all
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Close all legs?</AlertDialogTitle>
                <AlertDialogDescription>Sells open CE and PE positions.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={closeAll}>Close all</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <Button size="sm" variant="ghost" onClick={refresh}>
            <RefreshCw className="h-4 w-4" />
          </Button>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">Configuration</CardTitle>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={loadFromSelection}>
                Load from Selection
              </Button>
              <Button size="sm" onClick={saveConfig} disabled={saving}>
                <Save className="mr-1 h-4 w-4" /> Save
              </Button>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field label="Underlying">
              <Select value={config.underlying} onValueChange={(v) => patch({ underlying: v as RollingUnderlying })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {UNDERLYINGS.map((u) => (
                    <SelectItem key={u} value={u}>{u}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Expiry">
              <Select value={config.expiry || undefined} onValueChange={(v) => patch({ expiry: v })}>
                <SelectTrigger><SelectValue placeholder="Select expiry" /></SelectTrigger>
                <SelectContent>
                  {expiries.map((e) => (
                    <SelectItem key={e} value={e}>{e}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Timeframe">
              <Select value={config.timeframe} onValueChange={(v) => patch({ timeframe: v as Timeframe })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {TIMEFRAMES.map((t) => (
                    <SelectItem key={t} value={t}>{t}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Entry start">
              <Input type="time" value={config.entry_start} onChange={(e) => patch({ entry_start: e.target.value })} />
            </Field>
            <Field label="Force exit">
              <Input type="time" value={config.force_exit} onChange={(e) => patch({ force_exit: e.target.value })} />
            </Field>
            <Field label="Tick interval (sec)">
              <Input type="number" value={config.tick_interval_sec} onChange={(e) => patch({ tick_interval_sec: Number(e.target.value) })} />
            </Field>
            <Field label="Trade mode">
              <Select value={config.trade_mode} onValueChange={(v) => patch({ trade_mode: v as TradeMode })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="Both">Both</SelectItem>
                  <SelectItem value="LongOnly">Long only (CE)</SelectItem>
                  <SelectItem value="ShortOnly">Short only (PE)</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="Reentry style">
              <Select value={config.reentry_style} onValueChange={(v) => patch({ reentry_style: v as "zone_active" | "edge_only" })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="zone_active">Zone active (Pine)</SelectItem>
                  <SelectItem value="edge_only">Edge only</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="Max reentries CE">
              <Input type="number" min={0} max={3} value={config.max_reentries_ce} onChange={(e) => patch({ max_reentries_ce: Number(e.target.value) })} />
            </Field>
            <Field label="Max reentries PE">
              <Input type="number" min={0} max={3} value={config.max_reentries_pe} onChange={(e) => patch({ max_reentries_pe: Number(e.target.value) })} />
            </Field>
            <div className="flex items-center gap-2 sm:col-span-2">
              <Checkbox checked={config.allow_dual_open} onCheckedChange={(v) => patch({ allow_dual_open: Boolean(v) })} />
              <span className="text-sm">Allow CE + PE open simultaneously</span>
            </div>
            <div className="flex items-center gap-2 sm:col-span-2">
              <Checkbox checked={config.adx_enabled} onCheckedChange={(v) => patch({ adx_enabled: Boolean(v) })} />
              <span className="text-sm">ADX filter</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Live status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <StatusRow label="Spot" value={st?.last_spot?.toFixed(2) ?? "—"} />
            <StatusRow label="ATM strike" value={st?.current_atm?.toString() ?? "—"} />
            <StatusRow label="Roll" value={st?.last_roll_direction ?? "—"} />
            <StatusRow label="Last signal" value={st?.last_signal ?? "none"} />
            <StatusRow label="Last tick" value={st?.last_tick_at ?? "—"} />
            <StatusRow label="Scheduler" value={status?.scheduler?.scheduler_alive ? "alive" : "off"} />
            <StatusRow
              label="Kite"
              value={
                status?.kite_authenticated
                  ? "logged in"
                  : "not logged in"
              }
            />
            {!status?.kite_authenticated ? (
              <Button asChild size="sm" variant="link" className="h-auto p-0">
                <Link to="/login">Log in to Kite</Link>
              </Button>
            ) : null}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <LegCard title="CE leg (Long)" leg={st?.ce} onClose={() => closeLeg("ce")} accent="green" />
        <LegCard title="PE leg (Short)" leg={st?.pe} onClose={() => closeLeg("pe")} accent="red" />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">Positions & orders</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-2 text-xs text-muted-foreground">Open positions ({positions.length})</p>
            <MiniTable rows={positions.slice(0, 5)} cols={["tradingsymbol", "quantity", "average_price"]} />
            <p className="mb-2 mt-4 text-xs text-muted-foreground">Recent orders ({orders.length})</p>
            <MiniTable rows={orders.slice(-5).reverse()} cols={["tradingsymbol", "transaction_type", "status", "tag"]} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Activity log</CardTitle></CardHeader>
          <CardContent className="max-h-64 overflow-y-auto">
            {logs.length === 0 ? (
              <p className="text-sm text-muted-foreground">No events yet</p>
            ) : (
              <ul className="space-y-2 text-xs font-mono">
                {logs.map((row, i) => (
                  <li key={i} className="border-b border-border/50 pb-1">
                    <span className="text-muted-foreground">{row.at}</span>{" "}
                    <span className="font-semibold">{row.event}</span> {row.detail}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function StatusRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono">{value}</span>
    </div>
  );
}

function LegCard({
  title,
  leg,
  onClose,
  accent,
}: {
  title: string;
  leg?: RollingStraddleStatus["state"]["ce"];
  onClose: () => void;
  accent: "green" | "red";
}) {
  const open = leg?.status === "open";
  const border = accent === "green" ? "border-green-600/40" : "border-red-600/40";
  return (
    <Card className={open ? border : ""}>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
        <Badge variant={open ? "default" : "secondary"}>{leg?.status ?? "flat"}</Badge>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <StatusRow label="Symbol" value={leg?.tradingsymbol ?? "—"} />
        <StatusRow label="Strike" value={leg?.strike?.toString() ?? "—"} />
        <StatusRow label="Entry" value={leg?.entry_at ?? "—"} />
        <StatusRow label="Entry price" value={leg?.entry_price?.toFixed(2) ?? "—"} />
        <StatusRow label="LTP" value={leg?.ltp != null ? leg.ltp.toFixed(2) : "—"} />
        <StatusRow
          label={`Zone exit (${leg?.zone_exit_label ?? "ST1"})`}
          value={leg?.zone_exit_level != null ? leg.zone_exit_level.toFixed(2) : "—"}
        />
        <StatusRow
          label="Zone exit status"
          value={
            leg?.zone_exit_triggered
              ? "TRIGGERED — exit on next tick"
              : leg?.ltp != null && leg?.zone_exit_level != null
                ? accent === "red"
                  ? leg.ltp > leg.zone_exit_level
                    ? "LTP above ST1"
                    : "In short zone"
                  : leg.ltp < leg.zone_exit_level
                    ? "LTP below ST1"
                    : "In long zone"
                : "—"
          }
        />
        <StatusRow label="Signal bar close" value={leg?.signal_close?.toFixed(2) ?? "—"} />
        {accent === "green" && (leg?.short_ready || leg?.short_entry) ? (
          <>
            <StatusRow
              label="Short signal (CE chart)"
              value={leg?.short_entry ? "SHORT ENTRY" : "Short zone active"}
            />
            <StatusRow label="PE leg trigger" value="CE short → buy PE (PE leg)" />
          </>
        ) : null}
        {leg?.signal_strike != null && leg?.strike != null && leg.signal_strike !== leg.strike ? (
          <StatusRow label="Signal strike" value={String(leg.signal_strike)} />
        ) : null}
        <StatusRow label="Reentries used" value={String(leg?.reentries_used ?? 0)} />
        <StatusRow label="Entries today" value={String(leg?.entries_today ?? 0)} />
        <StatusRow label="Last action" value={leg?.last_action ?? "—"} />
        {open ? (
          <Button size="sm" variant="outline" className="mt-2" onClick={onClose}>
            Close leg
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

function MiniTable({
  rows,
  cols,
}: {
  rows: Record<string, unknown>[];
  cols: string[];
}) {
  if (!rows.length) return <p className="text-xs text-muted-foreground">None</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {cols.map((c) => (
            <TableHead key={c} className="text-xs">{c}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, i) => (
          <TableRow key={i}>
            {cols.map((c) => (
              <TableCell key={c} className="font-mono text-xs">
                {String(row[c] ?? "—")}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
