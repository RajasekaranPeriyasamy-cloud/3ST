import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowLeft, Play, Power, RefreshCw, Save, ShieldOff, Square } from "lucide-react";

import { api } from "@/lib/api";
import { pickNearestExpiry, useOptionExpiries } from "@/hooks/useOptionExpiries";
import type {
  PremiumBookConfig,
  PremiumBookLogEntry,
  PremiumBookPreview,
  PremiumBookStatus,
  PremiumBookStructure,
  PremiumBookTradeBias,
  PremiumBookUnderlying,
  StMethod,
  SystemMode,
  Timeframe,
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

export const Route = createFileRoute("/premium-book")({
  component: PremiumBookPage,
});

const TIMEFRAMES: Timeframe[] = ["1min", "3min", "5min", "15min", "30min", "60min"];
const UNDERLYING_OPTIONS: { value: PremiumBookUnderlying; label: string }[] = [
  { value: "NIFTY", label: "NIFTY" },
  { value: "BANKNIFTY", label: "BANKNIFTY" },
  { value: "SENSEX", label: "SENSEX" },
  { value: "CRUDEOIL", label: "Crude Oil (MCX)" },
  { value: "CRUDEOILM", label: "Crude Oil Mini (MCX)" },
];
const MCX_UNDERLYINGS = new Set<PremiumBookUnderlying>(["CRUDEOIL", "CRUDEOILM"]);
const MCX_SESSION_DEFAULTS = {
  session_start: "09:00",
  session_end: "23:30",
  force_exit: "23:20",
  entry_start: "09:20",
  product: "NRML" as const,
};
const NSE_SESSION_DEFAULTS = {
  session_start: "09:15",
  session_end: "15:40",
  force_exit: "15:20",
  entry_start: "09:20",
  product: "MIS" as const,
};
const SELL_STRUCTURES: { value: PremiumBookStructure; label: string }[] = [
  { value: "bull_put", label: "Bull put (credit)" },
  { value: "bear_call", label: "Bear call (credit)" },
];
const BUY_STRUCTURES: { value: PremiumBookStructure; label: string }[] = [
  { value: "bull_call", label: "Bull call (debit vertical)" },
  { value: "bear_put", label: "Bear put (debit vertical)" },
  { value: "long_call", label: "Long call (ATM±OTM)" },
  { value: "long_put", label: "Long put (ATM±OTM)" },
  { value: "long_strangle", label: "Long strangle" },
];

const DEFAULT: PremiumBookConfig = {
  underlying: "NIFTY",
  expiry: "",
  trade_bias: "sell_premium",
  structure: "bull_put",
  otm_offset: 1,
  width_steps: 1,
  timeframe: "5min",
  entry_start: "09:20",
  session_start: "09:15",
  session_end: "15:40",
  force_exit: "15:20",
  system_mode: "Intraday",
  order_type: "MARKET",
  product: "MIS",
  tick_interval_sec: 60,
  convert_sl_to_spread: true,
  auto_structure: true,
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
  entry_require_st1_st2: true,
  adx_enabled: true,
  adx_period: 14,
  adx_threshold: 20,
  sl_mode: "Off",
  sl_value: 1,
  tgt_mode: "Off",
  tgt_value: 1,
  tsl_mode: "ATR",
  tsl_value: 1.2,
  entry_exit_enabled: false,
  exit_on_bar_close_only: true,
};

function PremiumBookPage() {
  const [config, setConfig] = useState<PremiumBookConfig>(DEFAULT);
  const [status, setStatus] = useState<PremiumBookStatus | null>(null);
  const [preview, setPreview] = useState<PremiumBookPreview | null>(null);
  const [logs, setLogs] = useState<PremiumBookLogEntry[]>([]);
  const { expiries } = useOptionExpiries(config.underlying);
  const [armDialogOpen, setArmDialogOpen] = useState(false);
  const [arming, setArming] = useState(false);

  const patch = useCallback((p: Partial<PremiumBookConfig>) => {
    setConfig((c) => ({ ...c, ...p }));
  }, []);

  // Config is loaded on mount / Save / explicit Refresh only.
  // Status poll must NOT overwrite local form state.
  const loadConfig = useCallback(async () => {
    try {
      const cfg = await api.get<PremiumBookConfig>("/live/premium-book/config", { silent: true });
      setConfig({ ...DEFAULT, ...cfg });
    } catch {
      /* silent */
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const [st, logRes] = await Promise.all([
        api.get<PremiumBookStatus>("/live/premium-book/status", { silent: true }),
        api.get<{ items?: PremiumBookLogEntry[] }>("/live/premium-book/log?limit=40", { silent: true }),
      ]);
      setStatus(st);
      setPreview(st.preview ?? null);
      setLogs(logRes.items ?? []);
    } catch {
      /* silent */
    }
  }, []);

  const refresh = useCallback(async () => {
    await Promise.all([loadConfig(), refreshStatus()]);
  }, [loadConfig, refreshStatus]);

  useEffect(() => {
    void loadConfig();
    void refreshStatus();
    const t = setInterval(refreshStatus, 5000);
    return () => clearInterval(t);
  }, [loadConfig, refreshStatus]);

  useEffect(() => {
    if (!expiries.length) return;
    setConfig((c) => {
      if (c.expiry && expiries.includes(c.expiry)) return c;
      const nearest = pickNearestExpiry(expiries);
      return nearest ? { ...c, expiry: nearest } : c;
    });
  }, [expiries, config.underlying]);

  async function saveConfig() {
    await api.post("/live/premium-book/config", config);
    toast.success("Premium Book config saved");
    await loadConfig();
    void refreshStatus();
  }

  async function runPreview() {
    try {
      await api.post("/live/premium-book/config", config);
      const p = await api.post<PremiumBookPreview>("/live/premium-book/preview", {});
      setPreview(p);
      toast.success("Strike preview refreshed");
    } catch {
      /* api toast */
    }
  }

  async function switchMode(mode: "paper" | "live") {
    await api.post("/live/mode", { mode });
    toast.success(`Mode: ${mode.toUpperCase()}`);
    void refreshStatus();
  }

  async function armNow() {
    setArming(true);
    try {
      await api.post("/live/arm", { confirm: true });
      toast.success("ARMED — live orders enabled");
      setArmDialogOpen(false);
      void refreshStatus();
    } catch {
      /* api toast */
    } finally {
      setArming(false);
    }
  }

  async function disarmNow() {
    await api.post("/live/disarm");
    toast.success("DISARMED");
    void refreshStatus();
  }

  const running = status?.state?.runner === "running";
  const arm = status?.arm;
  const tradeBias: PremiumBookTradeBias =
    config.trade_bias ??
    (BUY_STRUCTURES.some((s) => s.value === config.structure) ? "buy_hold" : "sell_premium");
  const structureOptions = tradeBias === "buy_hold" ? BUY_STRUCTURES : SELL_STRUCTURES;
  const isBuyBook = tradeBias === "buy_hold";

  function setTradeBias(bias: PremiumBookTradeBias) {
    if (bias === tradeBias) return;
    const nextStructure =
      bias === "buy_hold"
        ? BUY_STRUCTURES.some((s) => s.value === config.structure)
          ? config.structure
          : "bull_call"
        : SELL_STRUCTURES.some((s) => s.value === config.structure)
          ? config.structure
          : "bull_put";
    patch({
      trade_bias: bias,
      structure: nextStructure,
      convert_sl_to_spread: bias === "sell_premium" ? Boolean(config.convert_sl_to_spread) : false,
      entry_exit_enabled: false,
    });
  }

  async function revokeBuyHold() {
    try {
      await api.post("/live/premium-book/revoke-buy-hold", {});
      toast.success("Buy & Hold revoked — back to sell premium");
      await loadConfig();
      void refreshStatus();
    } catch {
      /* api toast */
    }
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 pb-10">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button asChild variant="ghost" size="icon">
            <Link to="/execution">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <div>
            <h1 className="text-2xl font-semibold">Premium Book</h1>
            <p className="text-sm text-muted-foreground">
              Sell premium or Buy &amp; hold — ST1+ST2 entry, Force → ATR → ST1 exit
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant={arm?.armed ? "destructive" : "secondary"}>
            {arm?.armed ? "ARMED" : "DISARMED"}
          </Badge>
          <Badge variant="outline">{(arm?.mode ?? "paper").toUpperCase()}</Badge>
          <Badge variant={running ? "default" : "secondary"}>
            {running ? "RUNNING" : "STOPPED"}
          </Badge>
        </div>
      </header>

      <Card className="border-dashed">
        <CardContent className="pt-4 text-sm text-muted-foreground">
          {status?.narrative ??
            "ST1+ST2 entry · ST1 exit · ATR risk · Force time · Entry-exit off · SL convert → vertical"}
          {" · "}
          <Link to="/rolling-straddle" className="text-primary underline-offset-4 hover:underline">
            Open Rolling Straddle
          </Link>
          {" for ATM legacy"}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Controls</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant={arm?.mode === "paper" ? "default" : "outline"}
            onClick={() => switchMode("paper")}
          >
            Paper
          </Button>
          <Button
            size="sm"
            variant={arm?.mode === "live" ? "default" : "outline"}
            onClick={() => switchMode("live")}
          >
            Live
          </Button>
          {arm?.mode === "live" && !arm?.armed ? (
            <AlertDialog open={armDialogOpen} onOpenChange={setArmDialogOpen}>
              <AlertDialogTrigger asChild>
                <Button size="sm" variant="destructive">
                  <Power className="mr-1 h-4 w-4" /> ARM
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>ARM live orders?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Confirms live Kite order placement for Premium Book and other desks.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={armNow} disabled={arming}>
                    Confirm ARM
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          ) : null}
          {arm?.armed ? (
            <Button size="sm" variant="outline" onClick={disarmNow}>
              <ShieldOff className="mr-1 h-4 w-4" /> DISARM
            </Button>
          ) : null}
          {!running ? (
            <Button
              size="sm"
              onClick={async () => {
                await api.post("/live/premium-book/start", {});
                toast.success("Premium Book started");
                void refreshStatus();
              }}
            >
              <Play className="mr-1 h-4 w-4" /> Start
            </Button>
          ) : (
            <Button
              size="sm"
              variant="secondary"
              onClick={async () => {
                await api.post("/live/premium-book/stop", {});
                toast.success("Stopped");
                void refreshStatus();
              }}
            >
              <Square className="mr-1 h-4 w-4" /> Stop
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={async () => {
              await api.post("/live/premium-book/close", {});
              toast.success("Force closed");
              void refreshStatus();
            }}
          >
            Force close
          </Button>
          {isBuyBook ? (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button size="sm" variant="destructive">
                  Revoke Buy &amp; Hold
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Revoke Buy &amp; Hold?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Turns Buy &amp; Hold off (back to sell premium), sets structure to bull put, and
                    flattens any open buy-hold package. Short-premium legs are not touched.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={() => void revokeBuyHold()}>
                    Revoke &amp; flatten
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          ) : null}
          <Button size="sm" variant="ghost" onClick={refresh}>
            <RefreshCw className="mr-1 h-4 w-4" /> Refresh
          </Button>
          <Button size="sm" variant="outline" onClick={saveConfig}>
            <Save className="mr-1 h-4 w-4" /> Save
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Structure & strikes</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label>Underlying</Label>
            <Select
              value={config.underlying}
              onValueChange={(v) => {
                const next = v as PremiumBookUnderlying;
                const wasMcx = MCX_UNDERLYINGS.has(config.underlying);
                const isMcx = MCX_UNDERLYINGS.has(next);
                const sessionPatch =
                  isMcx && !wasMcx
                    ? MCX_SESSION_DEFAULTS
                    : !isMcx && wasMcx
                      ? NSE_SESSION_DEFAULTS
                      : isMcx
                        ? { product: "NRML" as const }
                        : {};
                patch({ underlying: next, expiry: "", ...sessionPatch });
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {UNDERLYING_OPTIONS.map((u) => (
                  <SelectItem key={u.value} value={u.value}>
                    {u.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              MCX crude uses front-month future spot, strike step 50, lot 1, product NRML.
              Market session fixed 09:00–23:30 — only Entry start and Force exit are editable.
            </p>
          </div>
          <div className="space-y-2">
            <Label>Expiry</Label>
            <Select value={config.expiry || undefined} onValueChange={(v) => patch({ expiry: v })}>
              <SelectTrigger>
                <SelectValue placeholder="Select expiry" />
              </SelectTrigger>
              <SelectContent>
                {expiries.map((e) => (
                  <SelectItem key={e} value={e}>
                    {e}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label>Trade bias</Label>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant={tradeBias === "sell_premium" ? "default" : "outline"}
                onClick={() => setTradeBias("sell_premium")}
              >
                Sell premium
              </Button>
              <Button
                type="button"
                size="sm"
                variant={tradeBias === "buy_hold" ? "default" : "outline"}
                onClick={() => setTradeBias("buy_hold")}
              >
                Buy &amp; hold
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {isBuyBook
                ? "Buy & hold (default OFF): ST1+ST2 entry · exits = force + ATR + ST1 reverse. Prefer bull_call/bear_put. Use Revoke Buy & Hold to turn off and flatten."
                : "Sell premium: credit verticals only — above→bull put · below→bear call · flat/whipsaw→sit out · ST1 exit. Use Rolling Straddle for ATM straddles."}
            </p>
          </div>
          <div className="space-y-2 sm:col-span-2">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={config.auto_structure !== false}
                onCheckedChange={(c) => patch({ auto_structure: Boolean(c) })}
              />
              Auto structure from ST1+ST2 direction
            </label>
            <p className="text-xs text-muted-foreground">
              {config.auto_structure !== false
                ? isBuyBook
                  ? "Live pick: above ST1+ST2 → Bull call · below → Bear put · flat → Long strangle. Dropdown is fallback/manual override only."
                  : "Live pick: above ST1+ST2 → Bull put · below → Bear call · no signal/whipsaw → sit out (no entry). Dropdown is fallback/manual override only."
                : "Manual structure — runner uses the dropdown below."}
            </p>
            {status?.auto_structure_reason ||
            status?.state?.auto_structure_reason ||
            status?.active_structure ||
            status?.state?.active_structure ? (
              <p className="text-sm font-mono">
                Live pick:{" "}
                <span className="font-semibold">
                  {String(
                    status.active_structure ??
                      status.state?.active_structure ??
                      "sit out (no entry)",
                  )}
                </span>
                {status.auto_structure_reason || status.state?.auto_structure_reason
                  ? ` · ${String(status.auto_structure_reason ?? status.state?.auto_structure_reason)}`
                  : null}
              </p>
            ) : null}
            <Label>Structure {config.auto_structure !== false ? "(disabled while auto on)" : ""}</Label>
            <Select
              value={config.structure}
              onValueChange={(v) =>
                patch({
                  structure: v as PremiumBookStructure,
                  trade_bias: tradeBias,
                  auto_structure: false,
                })
              }
              disabled={config.auto_structure !== false}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {structureOptions.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>OTM offset (1–2)</Label>
            <Select
              value={String(config.otm_offset)}
              onValueChange={(v) => patch({ otm_offset: Number(v) })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="0">0</SelectItem>
                <SelectItem value="1">1</SelectItem>
                <SelectItem value="2">2</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Width steps (credit vertical)</Label>
            <Select
              value={String(config.width_steps)}
              onValueChange={(v) => patch({ width_steps: Number(v) })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="1">1</SelectItem>
                <SelectItem value="2">2</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-wrap items-center gap-4 sm:col-span-2">
            <Button size="sm" variant="secondary" onClick={runPreview}>
              Preview strikes
            </Button>
          </div>
          {preview && !preview.error ? (
            <div className="sm:col-span-2 rounded-md border p-3 text-sm font-mono space-y-1">
              <div>
                Spot {preview.spot ?? "—"} · ATM {preview.atm ?? "—"} ·{" "}
                {preview.is_debit || isBuyBook
                  ? `Debit ${preview.net_debit?.toFixed?.(2) ?? preview.net_debit ?? "—"}`
                  : `Credit ${preview.net_credit?.toFixed?.(2) ?? preview.net_credit ?? "—"}`}
                {preview.max_loss_estimate != null
                  ? ` · Max loss ~ ${Number(preview.max_loss_estimate).toFixed(2)}`
                  : ""}
              </div>
              {(preview.legs ?? []).map((leg, i) => (
                <div key={i}>
                  {leg.side} {leg.option_type} {leg.strike} {leg.tradingsymbol} @ {leg.ltp ?? "—"}
                </div>
              ))}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">3ST + risk</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <div className="space-y-2">
            <Label>ST method</Label>
            <Select
              value={config.st_method}
              onValueChange={(v) => patch({ st_method: v as StMethod })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="heikin_ashi">Heikin Ashi</SelectItem>
                <SelectItem value="regular">Regular</SelectItem>
                <SelectItem value="hybrid">Hybrid</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Timeframe</Label>
            <Select
              value={config.timeframe}
              onValueChange={(v) => patch({ timeframe: v as Timeframe })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TIMEFRAMES.map((tf) => (
                  <SelectItem key={tf} value={tf}>
                    {tf}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>System mode</Label>
            <Select
              value={config.system_mode}
              onValueChange={(v) => patch({ system_mode: v as SystemMode })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Intraday">Intraday</SelectItem>
                <SelectItem value="Positional">Positional</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>ATR mult (TSL)</Label>
            <Input
              type="number"
              step="0.1"
              value={config.tsl_value}
              onChange={(e) => patch({ tsl_value: Number(e.target.value) || 1.2 })}
            />
          </div>
          <div className="space-y-2">
            <Label>ADX threshold</Label>
            <Input
              type="number"
              value={config.adx_threshold}
              onChange={(e) => patch({ adx_threshold: Number(e.target.value) || 20 })}
            />
          </div>
          <div className="space-y-2">
            <Label>Session start</Label>
            <Input
              type="time"
              value={config.session_start}
              onChange={(e) => patch({ session_start: e.target.value })}
              disabled={MCX_UNDERLYINGS.has(config.underlying)}
            />
            {MCX_UNDERLYINGS.has(config.underlying) ? (
              <p className="text-xs text-muted-foreground">MCX market hours fixed 09:00.</p>
            ) : null}
          </div>
          <div className="space-y-2">
            <Label>Session end</Label>
            <Input
              type="time"
              value={config.session_end}
              onChange={(e) => patch({ session_end: e.target.value })}
              disabled={MCX_UNDERLYINGS.has(config.underlying)}
            />
            {MCX_UNDERLYINGS.has(config.underlying) ? (
              <p className="text-xs text-muted-foreground">MCX market hours fixed 23:30.</p>
            ) : null}
          </div>
          <div className="space-y-2">
            <Label>Force exit</Label>
            <Input
              type="time"
              value={config.force_exit}
              onChange={(e) => patch({ force_exit: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label>Entry start</Label>
            <Input
              type="time"
              value={config.entry_start}
              onChange={(e) => patch({ entry_start: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label>Product</Label>
            <Select
              value={config.product}
              onValueChange={(v) => patch({ product: v as "MIS" | "NRML" })}
              disabled={MCX_UNDERLYINGS.has(config.underlying)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="MIS">MIS</SelectItem>
                <SelectItem value="NRML">NRML</SelectItem>
              </SelectContent>
            </Select>
            {MCX_UNDERLYINGS.has(config.underlying) ? (
              <p className="text-xs text-muted-foreground">MCX options are NRML-only on Kite.</p>
            ) : null}
          </div>
          <div className="space-y-2">
            <Label>Lots</Label>
            <Input
              type="number"
              value={config.size_value}
              onChange={(e) => patch({ size_value: Math.max(1, Number(e.target.value) || 1) })}
            />
          </div>
          <div className="flex flex-col justify-end gap-2 sm:col-span-3">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={config.adx_enabled}
                onCheckedChange={(c) => patch({ adx_enabled: Boolean(c) })}
              />
              ADX filter (entry)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={config.entry_exit_enabled}
                onCheckedChange={(c) => patch({ entry_exit_enabled: Boolean(c) })}
              />
              Entry-exit (default OFF for shorts)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={config.exit_on_bar_close_only}
                onCheckedChange={(c) => patch({ exit_on_bar_close_only: Boolean(c) })}
              />
              Exit on bar close only
            </label>
            <p className="text-xs text-muted-foreground">
              Entry: ST1 zone (+ADX) and ST1+ST2 same direction. Exit: ST1 only (+ ATR / force). ST3 does not gate entry.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Status</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm font-mono text-muted-foreground">
          <div>
            Spot {status?.state?.last_spot ?? "—"} · ATM {status?.state?.current_atm ?? "—"} · Signal{" "}
            {status?.state?.last_signal === "long"
              ? "long (bull zone)"
              : status?.state?.last_signal === "short"
                ? "short (bear zone)"
                : "—"}
            {status?.auto_structure_reason || status?.state?.auto_structure_reason
              ? ` · ${String(status.auto_structure_reason ?? status.state?.auto_structure_reason)}`
              : null}
          </div>
          <div>
            Package: {status?.state?.package?.status ?? "flat"}
            {status?.state?.package?.structure
              ? ` (${status.state.package.structure})`
              : ""}
            {status?.state?.package?.net_debit != null
              ? ` · debit ${status.state.package.net_debit}`
              : status?.state?.package?.net_credit != null
                ? ` · credit ${status.state.package.net_credit}`
                : ""}
            {status?.state?.package?.max_loss != null
              ? ` · max loss ${status.state.package.max_loss}`
              : ""}
          </div>
          <div>
            CE: {(status?.state?.ce as { status?: string } | undefined)?.status ?? "flat"}
            {" · "}
            PE: {(status?.state?.pe as { status?: string } | undefined)?.status ?? "flat"}
          </div>
          {status?.state?.last_error ? (
            <div className="text-destructive">{status.state.last_error}</div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Activity log</CardTitle>
        </CardHeader>
        <CardContent className="max-h-72 space-y-2 overflow-y-auto text-xs font-mono">
          {logs.length === 0 ? (
            <p className="text-muted-foreground">No events yet</p>
          ) : (
            logs.map((row, i) => (
              <div key={`${row.at}-${i}`}>
                <span className="text-muted-foreground">{row.at}</span>{" "}
                <span className="text-foreground">{row.event}</span> {row.detail}
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
