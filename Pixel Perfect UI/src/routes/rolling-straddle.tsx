import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { ArrowLeft, Play, Power, RefreshCw, Save, ShieldOff, Square } from "lucide-react";

import { api } from "@/lib/api";
import { pickNearestExpiry, prefetchOptionExpiries, useOptionExpiries } from "@/hooks/useOptionExpiries";
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

const TIMEFRAMES: Timeframe[] = ["1min", "3min", "5min", "15min", "30min", "60min"];
const UNDERLYING_OPTIONS: { value: RollingUnderlying; label: string }[] = [
  { value: "NIFTY", label: "NIFTY" },
  { value: "BANKNIFTY", label: "BANKNIFTY" },
  { value: "SENSEX", label: "SENSEX" },
  { value: "CRUDEOIL", label: "Crude Oil" },
  { value: "CRUDEOILM", label: "Crude Oil Mini" },
  { value: "NATURALGAS", label: "Natural Gas" },
];
const LOT_SIZES: Record<RollingUnderlying, number> = {
  NIFTY: 65,
  BANKNIFTY: 30,
  SENSEX: 20,
  CRUDEOIL: 1,
  CRUDEOILM: 1,
  NATURALGAS: 1,
};

const MCX_UNDERLYINGS = new Set<RollingUnderlying>(["CRUDEOIL", "CRUDEOILM", "NATURALGAS"]);
const MCX_SESSION_DEFAULTS = {
  session_start: "09:00",
  session_end: "23:30",
  force_exit: "23:20",
  entry_start: "09:20",
  product: "NRML" as const,
};

function symMatchesUnderlying(tradingsymbol: string, underlying: string): boolean {
  const sym = tradingsymbol.toUpperCase();
  const u = underlying.toUpperCase();
  return new RegExp(`^${u}\\d`).test(sym);
}

function underlyingDefaults(u: RollingUnderlying): Partial<RollingStraddleConfig> {
  if (MCX_UNDERLYINGS.has(u)) {
    return { underlying: u, expiry: "", ...MCX_SESSION_DEFAULTS };
  }
  return {
    underlying: u,
    expiry: "",
    session_start: "09:15",
    session_end: "15:40",
    force_exit: "15:20",
    entry_start: "09:20",
    product: "MIS",
  };
}

const DEFAULT_CONFIG: RollingStraddleConfig = {
  underlying: "NIFTY",
  expiry: "",
  timeframe: "5min",
  entry_start: "09:20",
  session_start: "09:15",
  session_end: "15:40",
  force_exit: "15:20",
  system_mode: "Intraday",
  order_type: "MARKET",
  product: "MIS",
  tick_interval_sec: 60,
  trade_mode: "Both",
  execution_mode: "auto",
  exit_on_bar_close_only: true,
  max_reentries_ce: 1,
  max_reentries_pe: 1,
  reentry_style: "zone_active",
  allow_dual_open: true,
  auto_start_on_boot: false,
  size_mode: "lots",
  size_value: 1,
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
  entry_exit_enabled: true,
};

function RollingStraddlePage() {
  const [config, setConfig] = useState<RollingStraddleConfig>(DEFAULT_CONFIG);
  const [status, setStatus] = useState<RollingStraddleStatus | null>(null);
  const { expiries, loading: expiriesLoading, error: expiriesError } = useOptionExpiries(config.underlying);
  const [logs, setLogs] = useState<RollingLogEntry[]>([]);
  const [logError, setLogError] = useState<string | null>(null);
  const [positions, setPositions] = useState<Record<string, unknown>[]>([]);
  const [orders, setOrders] = useState<Record<string, unknown>[]>([]);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [armState, setArmState] = useState<{ armed?: boolean; mode?: string } | null>(null);
  const [kiteSession, setKiteSession] = useState<{ authenticated?: boolean } | null>(null);
  const [saving, setSaving] = useState(false);
  const [armDialogOpen, setArmDialogOpen] = useState(false);
  const [arming, setArming] = useState(false);
  const autoSavedUnderlyingRef = useRef<string | null>(null);

  const patch = useCallback((p: Partial<RollingStraddleConfig>) => {
    setConfig((c) => ({ ...c, ...p }));
  }, []);

  const loadConfig = useCallback(async () => {
    try {
      const cfg = await api.get<RollingStraddleConfig>("/live/rolling-straddle/config", { silent: true });
      setConfig({ ...DEFAULT_CONFIG, ...cfg });
    } catch {
      /* silent */
    }
  }, []);

  const refreshLogs = useCallback(async () => {
    try {
      const logRes = await api.get<{ items?: RollingLogEntry[] }>(
        "/live/rolling-straddle/log?limit=50",
        { silent: true },
      );
      setLogs(logRes.items ?? []);
      setLogError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Could not load log";
      setLogError(msg);
    }
  }, []);

  const refreshStatus = useCallback(async (light = true) => {
    try {
      const q = light ? "?light=1" : "";
      const st = await api.get<RollingStraddleStatus>(
        `/live/rolling-straddle/status${q}`,
        { silent: true },
      );
      setStatus(st);
    } catch {
      /* silent */
    }
  }, []);

  const refreshLight = useCallback(async () => {
    void refreshLogs();
    try {
      const [st, h, arm, session] = await Promise.all([
        api.get<RollingStraddleStatus>("/live/rolling-straddle/status?light=1", { silent: true }).catch(() => null),
        api.get<HealthResponse>("/health", { silent: true }).catch(() => null),
        api.get<{ armed?: boolean; mode?: string }>("/live/arm", { silent: true }).catch(() => null),
        api.get<{ authenticated?: boolean }>("/auth/me", { silent: true }).catch(() => null),
      ]);
      if (st) setStatus(st);
      setArmState(arm ?? null);
      setKiteSession(session ?? null);
      if (h) setHealth(h);
    } catch {
      /* silent poll */
    }
  }, [refreshLogs]);

  const refresh = useCallback(async () => {
    void refreshLogs();
    try {
      const [st, pos, ord, h, arm, session] = await Promise.all([
        api.get<RollingStraddleStatus>("/live/rolling-straddle/status", { silent: true }).catch(() => null),
        api.get<{ positions?: Record<string, unknown>[] }>("/live/positions", { silent: true }).catch(() => ({ positions: [] })),
        api.get<{ orders?: Record<string, unknown>[] }>("/live/orders", { silent: true }).catch(() => ({ orders: [] })),
        api.get<HealthResponse>("/health", { silent: true }).catch(() => null),
        api.get<{ armed?: boolean; mode?: string }>("/live/arm", { silent: true }).catch(() => null),
        api.get<{ authenticated?: boolean }>("/auth/me", { silent: true }).catch(() => null),
      ]);
      if (st) setStatus(st);
      setArmState(arm ?? null);
      setKiteSession(session ?? null);
      setPositions(pos.positions ?? []);
      setOrders(ord.orders ?? []);
      if (h) setHealth(h);
    } catch {
      /* silent poll */
    }
  }, [refreshLogs]);

  useEffect(() => {
    prefetchOptionExpiries(UNDERLYING_OPTIONS.map((u) => u.value));
    void loadConfig();
    void refresh();
    const fast = setInterval(refreshLight, 5000);
    const slow = setInterval(refresh, 30000);
    return () => {
      clearInterval(fast);
      clearInterval(slow);
    };
  }, [loadConfig, refresh, refreshLight]);

  useEffect(() => {
    if (!expiries.length) return;
    setConfig((c) => {
      if (c.expiry && expiries.includes(c.expiry)) return c;
      const nearest = pickNearestExpiry(expiries);
      return nearest ? { ...c, expiry: nearest } : c;
    });
  }, [expiries, config.underlying]);

  const savedUnderlying = status?.config?.underlying;
  const underlyingDirty =
    savedUnderlying != null && config.underlying !== savedUnderlying;

  useEffect(() => {
    if (!underlyingDirty) {
      autoSavedUnderlyingRef.current = null;
      return;
    }
    if (expiriesLoading || !config.expiry || !expiries.includes(config.expiry)) {
      return;
    }
    const token = `${config.underlying}:${config.expiry}`;
    if (autoSavedUnderlyingRef.current === token) {
      return;
    }
    autoSavedUnderlyingRef.current = token;

    void (async () => {
      setSaving(true);
      try {
        const res = await api.post<{ config?: RollingStraddleConfig }>(
          "/live/rolling-straddle/config",
          config,
          { silent: true },
        );
        if (res.config) {
          setConfig({ ...DEFAULT_CONFIG, ...res.config });
        }
        toast.success(`${config.underlying} saved — spot will refresh`);
        void refreshLight();
      } catch {
        autoSavedUnderlyingRef.current = null;
      } finally {
        setSaving(false);
      }
    })();
  }, [
    config,
    expiries,
    expiriesLoading,
    underlyingDirty,
    refreshLight,
  ]);

  async function saveConfig() {
    setSaving(true);
    try {
      const res = await api.post<{ config?: RollingStraddleConfig }>(
        "/live/rolling-straddle/config",
        config,
      );
      if (res.config) {
        setConfig({ ...DEFAULT_CONFIG, ...res.config });
      }
      toast.success("Config saved");
      void refreshStatus();
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
      await api.post("/live/rolling-straddle/config", config);
      await api.post("/live/rolling-straddle/start");
      toast.success("Rolling straddle started");
      void refresh();
      void refreshLogs();
    } catch {
      /* toast from api */
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
    setArming(true);
    try {
      const next = await api.post<{ armed?: boolean; mode?: string; server_ms?: number }>(
        "/live/arm",
        { confirm: true, mode: "live" },
      );
      setArmState({ armed: next.armed, mode: next.mode ?? "live" });
      toast.success(
        next.server_ms != null
          ? `ARMED — live orders enabled (${next.server_ms}ms)`
          : "ARMED — live orders enabled",
      );
      setArmDialogOpen(false);
      void refresh();
    } catch {
      /* toast from api */
    } finally {
      setArming(false);
    }
  }

  async function setLiveMode() {
    try {
      const next = await api.post<{ mode?: string }>("/live/mode", { mode: "live" });
      setArmState((prev) => ({ ...prev, mode: next.mode ?? "live", armed: false }));
      toast.success("Live mode — click ARM to allow exchange orders");
      void refresh();
    } catch {
      /* toast from api */
    }
  }

  async function disarm() {
    try {
      const next = await api.post<{ armed?: boolean; mode?: string; server_ms?: number }>("/live/disarm");
      setArmState({ armed: next.armed ?? false, mode: next.mode ?? arm?.mode });
      toast.success(next.server_ms != null ? `DISARMED (${next.server_ms}ms)` : "DISARMED");
      void refresh();
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

  async function unlinkLeg(leg: "ce" | "pe") {
    try {
      await api.post("/live/rolling-straddle/unlink-leg", { leg });
      toast.message(`${leg.toUpperCase()} unlinked — Kite position kept`);
      refresh();
    } catch {
      /* */
    }
  }

  async function adoptLeg(leg: "ce" | "pe") {
    try {
      await api.post("/live/rolling-straddle/adopt-leg", { leg });
      toast.success(`${leg.toUpperCase()} linked for 3ST exit monitoring`);
      refresh();
    } catch {
      /* */
    }
  }

  const st = status?.state;
  const liveUnderlying = status?.config?.underlying ?? config.underlying;
  const spotReady = !underlyingDirty && !st?.spot_stale;
  const arm = armState ?? status?.arm;
  const kiteAuthenticated =
    kiteSession?.authenticated ?? status?.kite_authenticated ?? health?.kite_authenticated ?? false;
  const liveMode = arm?.mode === "live";
  const startBlocked = liveMode && !kiteAuthenticated;
  const entryStartLabel = config.entry_start || "09:20";
  const running = st?.runner === "running";
  const lotSize = LOT_SIZES[config.underlying];
  const configuredQty =
    status?.order_quantity ??
    (config.size_mode === "qty" ? config.size_value : config.size_value * lotSize);
  const underlyingPositions = positions.filter((p) =>
    symMatchesUnderlying(String((p as { tradingsymbol?: string }).tradingsymbol ?? ""), config.underlying),
  );

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
            Auto ATM CE/PE on 3ST signals — rolls strike with spot from {entryStartLabel}, dual-leg independent
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={arm?.armed ? "destructive" : "secondary"}>
            {arm?.armed ? "ARMED" : "DISARMED"}
          </Badge>
          <Badge variant="outline">{arm?.mode?.toUpperCase() ?? "PAPER"}</Badge>
          {arm?.mode === "live" && !arm?.armed ? (
            <Badge variant="outline" className="border-amber-500/60 text-amber-600 dark:text-amber-400">
              Live orders blocked until ARM
            </Badge>
          ) : null}
          <Badge variant={running ? "default" : "secondary"}>
            {running ? "RUNNING" : "STOPPED"}
          </Badge>
          {st?.morning_bar_seen ? (
            <Badge variant="outline" className="border-emerald-600/60 text-emerald-600 dark:text-emerald-400">
              Entry OK ({entryStartLabel})
            </Badge>
          ) : (
            <Badge variant="outline">Waiting {entryStartLabel}</Badge>
          )}
        </div>
      </header>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-2 pt-6">
          <Button
            variant="outline"
            size="sm"
            onClick={setPaper}
            className={arm?.mode === "paper" ? "border-primary" : undefined}
          >
            Paper
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={setLiveMode}
            className={arm?.mode === "live" ? "border-primary" : undefined}
          >
            Live mode
          </Button>
          <AlertDialog open={armDialogOpen} onOpenChange={setArmDialogOpen}>
            <AlertDialogTrigger asChild>
              <Button
                size="sm"
                variant={arm?.armed ? "secondary" : "destructive"}
                disabled={arm?.armed || arm?.mode !== "live"}
                title={
                  arm?.armed
                    ? "Already ARMED"
                    : arm?.mode !== "live"
                      ? "Switch to Live mode first"
                      : "Arm live trading"
                }
              >
                <Power className="mr-1 h-4 w-4" /> {arm?.armed ? "ARMED" : "ARM"}
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
                <AlertDialogCancel disabled={arming}>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  disabled={arming}
                  onClick={(e) => {
                    e.preventDefault();
                    void armLive();
                  }}
                >
                  {arming ? "Arming…" : "ARM"}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <Button
            size="sm"
            variant={arm?.armed ? "destructive" : "secondary"}
            onClick={disarm}
            disabled={!arm?.armed}
          >
            <ShieldOff className="mr-1 h-4 w-4" /> DISARM
          </Button>
          <Separator orientation="vertical" className="mx-1 h-8" />
          <Button
            size="sm"
            onClick={startAlgo}
            disabled={startBlocked}
            title={
              startBlocked
                ? "Log in to Kite first (Live mode)"
                : running
                  ? "Scheduler already running"
                  : "Start rolling straddle scheduler"
            }
          >
            <Play className="mr-1 h-4 w-4" /> Start
          </Button>
          <Button size="sm" variant="outline" onClick={stopAlgo}>
            <Square className="mr-1 h-4 w-4" /> Stop
          </Button>
          {startBlocked ? (
            <p className="w-full text-xs text-amber-400">
              Start blocked in Live mode —{" "}
              <Link to="/login" className="underline">
                log in to Kite
              </Link>{" "}
              or switch to Paper to test.
            </p>
          ) : null}
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
                <Save className="mr-1 h-4 w-4" /> {saving ? "Saving…" : "Save"}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field label="Underlying">
              <Select
                value={config.underlying}
                onValueChange={(v) => patch(underlyingDefaults(v as RollingUnderlying))}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {UNDERLYING_OPTIONS.map((u) => (
                    <SelectItem key={u.value} value={u.value}>{u.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Expiry">
              <Select
                value={
                  expiries.length && config.expiry && expiries.includes(config.expiry)
                    ? config.expiry
                    : undefined
                }
                onValueChange={(v) => patch({ expiry: v })}
              >
                <SelectTrigger>
                  <SelectValue
                    placeholder={
                      expiriesLoading && !expiries.length
                        ? "Loading expiries…"
                        : expiries.length
                          ? "Select expiry"
                          : "No expiries"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {expiries.map((e) => (
                    <SelectItem key={e} value={e}>{e}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {expiriesError ? (
                <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">{expiriesError}</p>
              ) : null}
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
            <Field label="Session start">
              <Input
                type="time"
                value={config.session_start}
                onChange={(e) => patch({ session_start: e.target.value })}
                disabled={MCX_UNDERLYINGS.has(config.underlying)}
              />
            </Field>
            <Field label="Session end">
              <Input
                type="time"
                value={config.session_end}
                onChange={(e) => patch({ session_end: e.target.value })}
                disabled={MCX_UNDERLYINGS.has(config.underlying)}
              />
            </Field>
            <Field label="Entry start">
              <Input type="time" value={config.entry_start} onChange={(e) => patch({ entry_start: e.target.value })} />
            </Field>
            <Field label="Force exit">
              <Input type="time" value={config.force_exit} onChange={(e) => patch({ force_exit: e.target.value })} />
            </Field>
            <p className="text-xs text-muted-foreground sm:col-span-2">
              NSE/BSE typically 09:15–15:40 (CAS / F&O close). MCX market hours are fixed 09:00–23:30 — only Entry start and Force exit are editable.
            </p>
            <Field label="Tick interval (sec)">
              <Input type="number" value={config.tick_interval_sec} onChange={(e) => patch({ tick_interval_sec: Number(e.target.value) })} />
            </Field>
            <Field label="Trade mode">
              <Select value={config.trade_mode} onValueChange={(v) => patch({ trade_mode: v as TradeMode })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="Both">Buy / Short (CE & PE)</SelectItem>
                  <SelectItem value="LongOnly">CE leg only</SelectItem>
                  <SelectItem value="ShortOnly">PE leg only</SelectItem>
                  <SelectItem value="ShortSignalsOnly">Short only (CE/PE — no buy)</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">
                Buy = long option; Short = sell option. Each leg follows its own chart signals.
              </p>
            </Field>
            <Field label="Execution mode">
              <Select
                value={config.execution_mode ?? "auto"}
                onValueChange={(v) => patch({ execution_mode: v as "auto" | "confirm" })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Auto — ship on signal</SelectItem>
                  <SelectItem value="confirm">Confirm — queue in taskbar</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="Zone exit timing">
              <Select
                value={config.exit_on_bar_close_only === false ? "ltp" : "bar"}
                onValueChange={(v) => patch({ exit_on_bar_close_only: v === "bar" })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="bar">Bar close only (recommended)</SelectItem>
                  <SelectItem value="ltp">Intrabar LTP cross</SelectItem>
                </SelectContent>
              </Select>
              <p className="mt-1 text-xs text-muted-foreground">
                Bar close waits for the {config.timeframe} candle; LTP can whipsaw with 60s ticks.
              </p>
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
            <Field label="Order size mode">
              <Select value={config.size_mode} onValueChange={(v) => patch({ size_mode: v as "lots" | "qty" })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="lots">Lots</SelectItem>
                  <SelectItem value="qty">Quantity</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label={config.size_mode === "lots" ? "Lots per leg" : "Qty per leg"}>
              <Input
                type="number"
                min={1}
                value={config.size_value}
                onChange={(e) => patch({ size_value: Number(e.target.value) })}
              />
              <p className="mt-1 text-xs text-muted-foreground">
                {config.underlying} lot = {lotSize} · order size = {configuredQty} per entry
              </p>
            </Field>
            <div className="flex items-center gap-2 sm:col-span-2">
              <Checkbox checked={config.allow_dual_open} onCheckedChange={(v) => patch({ allow_dual_open: Boolean(v) })} />
              <span className="text-sm">Allow CE + PE open simultaneously</span>
            </div>
            <div className="flex items-center gap-2 sm:col-span-2">
              <Checkbox checked={config.adx_enabled} onCheckedChange={(v) => patch({ adx_enabled: Boolean(v) })} />
              <span className="text-sm">ADX filter</span>
            </div>
            <div className="space-y-2 rounded-md border border-border/60 bg-muted/20 p-3 sm:col-span-2">
              <p className="text-xs font-semibold text-foreground">Exit ladder (first hit closes)</p>
              <ol className="list-decimal space-y-1 pl-4 text-xs text-muted-foreground">
                <li>
                  <span className="text-foreground">Entry</span> — Short: {config.timeframe} close above entry · Long: close below entry
                </li>
                <li>
                  <span className="text-foreground">ATR</span> — live {config.timeframe}/LTP ± (ATR1 × mult), trails dynamically (not from entry)
                </li>
                <li>
                  <span className="text-foreground">ST1</span> — live dynamic band · Short above / Long below
                </li>
              </ol>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-1">
                <div className="flex items-center gap-2">
                  <Checkbox
                    checked={config.entry_exit_enabled !== false}
                    onCheckedChange={(v) => patch({ entry_exit_enabled: Boolean(v) })}
                  />
                  <span className="text-sm">1. Entry exit</span>
                </div>
                <div className="flex items-center gap-2">
                  <Checkbox
                    checked={config.tsl_mode === "ATR"}
                    onCheckedChange={(v) =>
                      patch(
                        v
                          ? {
                              tsl_mode: "ATR",
                              tsl_value: config.tsl_mode === "ATR" ? config.tsl_value : config.tsl_value || 1.2,
                            }
                          : { tsl_mode: "Off" },
                      )
                    }
                  />
                  <span className="text-sm">2. ATR TSL</span>
                </div>
                <div className="flex items-center gap-2">
                  <Label className="text-xs text-muted-foreground whitespace-nowrap">× mult</Label>
                  <Input
                    type="number"
                    step={0.1}
                    min={0.1}
                    value={config.tsl_value}
                    disabled={config.tsl_mode !== "ATR"}
                    onChange={(e) => patch({ tsl_value: Number(e.target.value), tsl_mode: "ATR" })}
                    className="h-8 w-20 font-mono"
                  />
                </div>
                <span className="text-sm text-muted-foreground">3. ST1 always on</span>
              </div>
            </div>
            <Field label="ST method">
              <Select
                value={config.st_method}
                onValueChange={(v) => patch({ st_method: v as StMethod })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="heikin_ashi">Heikin Ashi ST (matches PRS TV)</SelectItem>
                  <SelectItem value="regular">Regular ST</SelectItem>
                  <SelectItem value="hybrid">Hybrid</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="ADX period / threshold">
              <div className="grid grid-cols-2 gap-2">
                <Input
                  type="number"
                  min={1}
                  value={config.adx_period}
                  disabled={!config.adx_enabled}
                  onChange={(e) => patch({ adx_period: Number(e.target.value) })}
                  className="font-mono"
                />
                <Input
                  type="number"
                  step={0.1}
                  min={0}
                  value={config.adx_threshold}
                  disabled={!config.adx_enabled}
                  onChange={(e) => patch({ adx_threshold: Number(e.target.value) })}
                  className="font-mono"
                />
              </div>
            </Field>
            <Field label="ATR 1 / Factor 1 (ST1)">
              <div className="grid grid-cols-2 gap-2">
                <Input
                  type="number"
                  min={1}
                  value={config.atr1}
                  onChange={(e) => patch({ atr1: Number(e.target.value) })}
                  className="font-mono"
                />
                <Input
                  type="number"
                  step={0.1}
                  min={0.1}
                  value={config.factor1}
                  onChange={(e) => patch({ factor1: Number(e.target.value) })}
                  className="font-mono"
                />
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                ST1 exit uses this ATR/factor. ATR TSL trails from live TF/LTP × multiplier (not entry).
              </p>
            </Field>
            <Field label="ATR 2 / Factor 2 (ST2)">
              <div className="grid grid-cols-2 gap-2">
                <Input
                  type="number"
                  min={1}
                  value={config.atr2}
                  onChange={(e) => patch({ atr2: Number(e.target.value) })}
                  className="font-mono"
                />
                <Input
                  type="number"
                  step={0.1}
                  min={0.1}
                  value={config.factor2}
                  onChange={(e) => patch({ factor2: Number(e.target.value) })}
                  className="font-mono"
                />
              </div>
            </Field>
            <Field label="ATR 3 / Factor 3 (ST3)">
              <div className="grid grid-cols-2 gap-2">
                <Input
                  type="number"
                  min={1}
                  value={config.atr3}
                  onChange={(e) => patch({ atr3: Number(e.target.value) })}
                  className="font-mono"
                />
                <Input
                  type="number"
                  step={0.1}
                  min={0.1}
                  value={config.factor3}
                  onChange={(e) => patch({ factor3: Number(e.target.value) })}
                  className="font-mono"
                />
              </div>
            </Field>
            <RiskField
              label="Stop loss"
              mode={config.sl_mode}
              value={config.sl_value}
              onMode={(sl_mode) => patch({ sl_mode })}
              onValue={(sl_value) => patch({ sl_value })}
            />
            <RiskField
              label="Target"
              mode={config.tgt_mode}
              value={config.tgt_value}
              onMode={(tgt_mode) => patch({ tgt_mode })}
              onValue={(tgt_value) => patch({ tgt_value })}
            />
            {config.tsl_mode !== "Off" && config.tsl_mode !== "ATR" ? (
              <RiskField
                label="Trailing SL"
                mode={config.tsl_mode}
                value={config.tsl_value}
                onMode={(tsl_mode) => patch({ tsl_mode })}
                onValue={(tsl_value) => patch({ tsl_value })}
              />
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Live status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <StatusRow label="Order size" value={`${configuredQty} (${config.size_mode === "lots" ? `${config.size_value} lot` : "qty"})`} />
            <StatusRow
              label="ST / ATR TSL"
              value={`${config.st_method === "heikin_ashi" ? "HA" : config.st_method === "regular" ? "Regular" : "Hybrid"} · ATR${config.atr1}×${config.factor1}${
                config.tsl_mode === "Off"
                  ? " · TSL off"
                  : config.tsl_mode === "ATR"
                    ? ` · TSL ATR×${config.tsl_value}`
                    : ` · TSL ${config.tsl_mode} ${config.tsl_value}`
              }`}
            />
            <StatusRow
              label="Underlying"
              value={
                underlyingDirty
                  ? `${config.underlying} (saving…)`
                  : liveUnderlying
              }
            />
            <StatusRow
              label="Spot"
              value={
                spotReady && st?.last_spot != null
                  ? st.last_spot.toFixed(2)
                  : underlyingDirty
                    ? "—"
                    : "—"
              }
            />
            {underlyingDirty ? (
              <p className="text-xs text-amber-400">
                Switching to {config.underlying} — clearing stale spot from {savedUnderlying}…
              </p>
            ) : st?.spot_stale ? (
              <p className="text-xs text-amber-400">
                Refreshing spot for {liveUnderlying}…
              </p>
            ) : null}
            <StatusRow
              label="ATM strike"
              value={spotReady ? (st?.current_atm?.toString() ?? "—") : "—"}
            />
            <StatusRow label="Roll" value={st?.last_roll_direction ?? "—"} />
            <StatusRow label="Last signal" value={st?.last_signal ?? "none"} />
            <StatusRow label="Last tick" value={st?.last_tick_at ?? "—"} />
            <StatusRow label="Scheduler" value={status?.scheduler?.scheduler_alive ? "alive" : "off"} />
            <StatusRow
              label="Kite"
              value={kiteAuthenticated ? "logged in" : "not logged in"}
            />
            {!kiteAuthenticated ? (
              <Button asChild size="sm" variant="link" className="h-auto p-0">
                <Link to="/login">Log in to Kite</Link>
              </Button>
            ) : null}
            {arm?.mode === "paper" ? (
              <p className="text-xs text-muted-foreground">
                Paper mode — switch to <strong>Live mode</strong>, then click <strong>ARM</strong> for real orders.
              </p>
            ) : !arm?.armed ? (
              <p className="text-xs text-muted-foreground">
                Live mode — click <strong>ARM</strong> to allow Kite orders.
              </p>
            ) : null}
          </CardContent>
        </Card>
      </div>

      {(status?.broker_mismatches?.length ?? 0) > 0 ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-200">
          Broker sync: {status?.broker_mismatches?.join(" · ")}
        </div>
      ) : null}

      {(status?.orphans?.length ?? 0) > 0 ? (
        <div className="rounded-md border border-amber-500/50 bg-amber-500/10 px-4 py-3 text-sm">
          <p className="font-medium text-amber-800 dark:text-amber-200">Unlinked Kite positions</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Open on Kite but not managed by Rolling Straddle — exits will not run until you adopt or close on Kite.
          </p>
          <ul className="mt-3 space-y-2">
            {status!.orphans!.map((o) => (
              <li
                key={`${o.leg_key}-${o.tradingsymbol}`}
                className="flex flex-wrap items-center justify-between gap-2 rounded border border-amber-500/30 px-3 py-2"
              >
                <div className="font-mono text-xs">
                  <span className="uppercase text-amber-700 dark:text-amber-300">{o.leg_key}</span> · {o.tradingsymbol} · qty{" "}
                  {o.quantity}
                  {o.average_price != null ? ` @ ${Number(o.average_price).toFixed(2)}` : ""}
                  {o.has_3st_order ? (
                    <Badge variant="outline" className="ml-2 text-[10px]">
                      3ST order
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="ml-2 text-[10px]">
                      manual
                    </Badge>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => adoptLeg(o.leg_key)}>
                    Adopt &amp; manage exits
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => closeLeg(o.leg_key)}>
                    Close on Kite
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <LegCard
          title="CE"
          legKind="ce"
          leg={st?.ce}
          armed={arm?.armed ?? false}
          liveMode={arm?.mode === "live"}
          onClose={() => closeLeg("ce")}
          onUnlink={() => unlinkLeg("ce")}
          accent="green"
        />
        <LegCard
          title="PE"
          legKind="pe"
          leg={st?.pe}
          armed={arm?.armed ?? false}
          liveMode={arm?.mode === "live"}
          onClose={() => closeLeg("pe")}
          onUnlink={() => unlinkLeg("pe")}
          accent="red"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">Positions & orders</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-2 text-xs text-muted-foreground">
              {config.underlying} positions ({underlyingPositions.length})
            </p>
            <MiniTable
              rows={underlyingPositions.slice(0, 5)}
              cols={["tradingsymbol", "quantity", "average_price", "last_price", "pnl"]}
            />
            <p className="mb-2 mt-4 text-xs text-muted-foreground">All open positions ({positions.length})</p>
            <MiniTable rows={positions.slice(0, 5)} cols={["tradingsymbol", "quantity", "average_price"]} />
            <p className="mb-2 mt-4 text-xs text-muted-foreground">Recent orders ({orders.length})</p>
            <MiniTable rows={orders.slice(-5).reverse()} cols={["tradingsymbol", "transaction_type", "status", "tag"]} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Activity log</CardTitle></CardHeader>
          <CardContent className="max-h-64 overflow-y-auto">
            {logError ? (
              <p className="text-sm text-destructive">
                Error loading log — {logError}
                <Button size="sm" variant="link" className="ml-1 h-auto p-0" onClick={() => void refreshLogs()}>
                  Retry
                </Button>
              </p>
            ) : logs.length === 0 ? (
              <p className="text-sm text-muted-foreground">No events yet</p>
            ) : (
              <ul className="space-y-2 text-xs font-mono">
                {logs.map((row, i) => (
                  <li key={i} className="border-b border-border/50 pb-1">
                    <span className="text-muted-foreground">{row.at}</span>{" "}
                    <span className={`font-semibold ${String(row.event).includes("failed") ? "text-destructive" : ""}`}>
                      {row.event}
                    </span>{" "}
                    {row.detail}
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

function RiskField({
  label,
  mode,
  value,
  onMode,
  onValue,
  allowAtr = false,
}: {
  label: string;
  mode: RiskMode;
  value: number;
  onMode: (m: RiskMode) => void;
  onValue: (v: number) => void;
  allowAtr?: boolean;
}) {
  return (
    <Field label={label}>
      <div className="grid grid-cols-2 gap-2">
        <Select value={mode} onValueChange={(v) => onMode(v as RiskMode)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="Off">Off</SelectItem>
            <SelectItem value="%">%</SelectItem>
            <SelectItem value="Pts">Points</SelectItem>
            {allowAtr ? <SelectItem value="ATR">ATR × multiplier</SelectItem> : null}
          </SelectContent>
        </Select>
        <Input
          type="number"
          step={0.1}
          value={value}
          disabled={mode === "Off"}
          onChange={(e) => onValue(Number(e.target.value))}
          className="font-mono"
        />
      </div>
    </Field>
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

function formatLastAction(action: string | null | undefined, legKind: "ce" | "pe", side?: string | null): string {
  if (!action) return "—";
  const sym = legKind === "ce" ? "CE" : "PE";
  const isShort = side === "short" || action.toLowerCase().includes("short");
  const entryTxn = isShort ? `SELL ${sym}` : `BUY ${sym}`;
  const map: Record<string, string> = {
    long_entry: `Long entry · ${entryTxn}`,
    long_ready: `Long ready · ${entryTxn}`,
    long_reentry: `Long re-entry · ${entryTxn}`,
    short_entry: `Short entry · ${entryTxn}`,
    short_ready: `Short ready · ${entryTxn}`,
    short_reentry: `Short re-entry · ${entryTxn}`,
    long_zone_exit: `Zone break exit · close ${sym}`,
    short_zone_exit: `Zone break exit · close ${sym}`,
    long_zone_exit_ltp: `ST LTP exit · close ${sym}`,
    short_zone_exit_ltp: `ST LTP exit · close ${sym}`,
    sl: `SL hit · close ${sym}`,
    target: `Target hit · close ${sym}`,
    atr_exit: `ATR exit · close ${sym}`,
    entry_exit: `Entry exit · ${sym} TF close vs entry`,
    manual_close: `Manual close · close ${sym}`,
    force_exit: `Force exit · close ${sym}`,
    adopted: `Adopted · manage ${sym} exits`,
    unlinked: `Unlinked · ${sym} not monitored`,
    reconcile_restored: `Restored from Kite · ${sym}`,
  };
  return map[action] ?? action;
}

function ExitTriggersBlock({
  exit,
  ltp,
}: {
  exit: NonNullable<RollingStraddleStatus["state"]["ce"]["exit_params"]>;
  ltp?: number | null;
}) {
  const tf = exit.timeframe ?? "TF";
  return (
    <div className="mt-2 space-y-2 rounded-md border border-border/60 bg-muted/30 p-2.5 text-xs">
      <p className="font-semibold text-foreground">
        Exit prices · {exit.trade_side_label ?? "—"} · {tf} (first hit closes)
      </p>
      {(exit.exit_levels ?? []).length === 0 ? (
        <p className="text-muted-foreground">No exit levels configured</p>
      ) : (
        <div className="space-y-2">
          {exit.exit_levels?.map((row) => (
            <div
              key={`${row.order ?? ""}-${row.category}-${row.price ?? "x"}`}
              className={`space-y-0.5 ${row.enabled === false || row.missing ? "opacity-60" : ""}`}
            >
              <div className="flex justify-between gap-2 font-mono">
                <span>
                  <span className="text-muted-foreground">
                    {row.order != null ? `${row.order}. ` : ""}
                    {row.category}
                  </span>
                  {row.price != null ? ` @ ${row.price.toFixed(2)}` : " —"}
                </span>
                <span className="text-right">
                  {row.triggered ? (
                    <Badge className="bg-amber-500/20 text-amber-700 dark:text-amber-400">Triggered</Badge>
                  ) : row.missing ? (
                    <span className="text-muted-foreground">n/a</span>
                  ) : row.distance != null && ltp != null ? (
                    <span className="text-muted-foreground">{row.distance.toFixed(2)} away</span>
                  ) : null}
                </span>
              </div>
              {row.rule ? <p className="text-[10px] text-muted-foreground leading-snug">{row.rule}</p> : null}
            </div>
          ))}
        </div>
      )}
      {exit.next_exit && !exit.next_exit.triggered && exit.next_exit.price != null ? (
        <p className="text-[10px] text-muted-foreground">
          Nearest: {exit.next_exit.category} @ {exit.next_exit.price.toFixed(2)}
          {exit.next_exit.distance != null ? ` (${exit.next_exit.distance.toFixed(2)} from LTP)` : ""}
        </p>
      ) : null}
      {exit.signal_close != null ? (
        <div className="font-mono text-muted-foreground">
          Last {tf} bar close {exit.signal_close.toFixed(2)}
          {exit.st1 != null ? ` · ST1 ${exit.st1.toFixed(2)}` : ""}
        </div>
      ) : null}
      {exit.force_exit ? (
        <div className="font-mono text-muted-foreground">
          Session force @ {exit.force_exit}
          {exit.force_exit_due ? <Badge className="ml-1 bg-amber-500/20 text-amber-700 dark:text-amber-400">Due</Badge> : null}
        </div>
      ) : null}
    </div>
  );
}

function legExitTriggered(
  leg?: RollingStraddleStatus["state"]["ce"],
): boolean {
  const exit = leg?.exit_params;
  if (!exit) return false;
  if (exit.zone_exit_triggered || exit.zone_exit_at_ltp) return true;
  return (exit.exit_levels ?? []).some((row) => row.triggered);
}

function LegCard({
  title,
  legKind,
  leg,
  armed,
  liveMode,
  onClose,
  onUnlink,
  accent,
}: {
  title: string;
  legKind: "ce" | "pe";
  leg?: RollingStraddleStatus["state"]["ce"];
  armed: boolean;
  liveMode: boolean;
  onClose: () => void;
  onUnlink: () => void;
  accent: "green" | "red";
}) {
  const open = leg?.status === "open";
  const algoManaged = leg?.managed_by === "algo" || (leg?.last_action != null && !["paper_sync", "reconcile_adopted"].includes(String(leg.last_action)));
  const brokerQty = leg?.broker_qty ?? null;
  const hasBrokerPosition = brokerQty != null && brokerQty !== 0;
  const effectivelyOpen = open && algoManaged;
  const brokerDetached = !open && hasBrokerPosition && algoManaged;
  const externalOpen = open && !algoManaged;
  const posSide = leg?.position_side ?? leg?.exit_params?.position_side ?? (brokerQty != null && brokerQty < 0 ? "short" : "long");
  const border = accent === "green" ? "border-green-600/40" : "border-red-600/40";
  const exitTriggered = legExitTriggered(leg);
  const exitBlockedDisarm = effectivelyOpen && liveMode && !armed && exitTriggered;
  return (
    <Card className={effectivelyOpen ? border : ""}>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
        <Badge variant={effectivelyOpen ? "default" : brokerDetached ? "default" : externalOpen ? "outline" : "secondary"}>
          {effectivelyOpen ? leg?.status ?? "flat" : brokerDetached ? "live (restoring)" : externalOpen ? "external" : leg?.status ?? "flat"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <StatusRow label="Symbol" value={leg?.tradingsymbol ?? "—"} />
        <StatusRow label="Strike" value={leg?.strike?.toString() ?? "—"} />
        <StatusRow label="Entry" value={leg?.entry_at ?? "—"} />
        <StatusRow
          label="Entry price"
          value={
            (leg?.entry_price ?? leg?.broker_average_price)?.toFixed(2) ?? "—"
          }
        />
        {hasBrokerPosition && algoManaged ? (
          <StatusRow
            label="Broker qty"
            value={`${brokerQty} (${posSide === "short" ? "SHORT" : "LONG"})`}
          />
        ) : null}
        {brokerDetached ? (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            Live Kite position detected (qty {brokerQty}) — refreshing leg state on next status poll.
          </p>
        ) : null}
        {externalOpen ? (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            External / manual position — not managed by Rolling Straddle. Close on Kite manually.
          </p>
        ) : null}
        {exitBlockedDisarm ? (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-800 dark:text-amber-200">
            Exit triggered but blocked — <strong>DISARMED</strong>. Click <strong>ARM</strong> above to send the Kite close order.
          </p>
        ) : null}
        {effectivelyOpen || externalOpen ? (
          <StatusRow
            label="Trade"
            value={`${posSide === "short" ? "Short" : "Long"} · ${leg?.entry_side === "SELL" ? "SELL" : "BUY"} ${title}`}
          />
        ) : null}
        <StatusRow label="LTP" value={leg?.ltp != null ? leg.ltp.toFixed(2) : "—"} />
        {leg?.signal_strike != null && leg?.strike != null && leg.signal_strike !== leg.strike ? (
          <StatusRow label="Signal @ strike" value={String(leg.signal_strike)} />
        ) : null}
        {!effectivelyOpen && (leg?.long_entry || leg?.long_ready) ? (
          <StatusRow label="Pending entry" value="Long · above ST" />
        ) : null}
        {!effectivelyOpen && (leg?.short_entry || leg?.short_ready) ? (
          <StatusRow label="Pending entry" value="Short · below ST" />
        ) : null}
        {effectivelyOpen && leg?.exit_params ? (
          <ExitTriggersBlock exit={leg.exit_params} ltp={leg.ltp} />
        ) : null}
        <StatusRow label="Reentries used" value={String(leg?.reentries_used ?? 0)} />
        <StatusRow label="Entries today" value={String(leg?.entries_today ?? 0)} />
        <StatusRow label="Last action" value={formatLastAction(leg?.last_action, legKind, leg?.position_side)} />
        {effectivelyOpen ? (
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={onClose}>
              Close leg
            </Button>
            <Button size="sm" variant="ghost" onClick={onUnlink}>
              Stop monitoring
            </Button>
          </div>
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
