import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, RefreshCw, Settings2 } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import { pickNearestExpiry, useOptionExpiries } from "@/hooks/useOptionExpiries";
import type {
  GammaConfig,
  GammaMomentum,
  GammaSnapshot,
  OiUnderlying,
} from "@/lib/types";
import { ReportPageDownload } from "@/components/ReportPageDownload";
import { CasChip } from "@/components/CasChip";
import { ConcentrationBoard } from "@/components/gamma/ConcentrationBoard";
import { GexSessionPlotly } from "@/components/gamma/GexSessionPlotly";
import {
  CALL_OI,
  GEX_NEG,
  GEX_POS,
  GexStrikePlotly,
  IV_CURVE,
  PUT_OI,
} from "@/components/gamma/GexStrikePlotly";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export const Route = createFileRoute("/gamma-density")({
  component: GammaDensityPage,
});

const DEFAULT_UNDERLYINGS: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];
/** Page-local — do not share with widget-desk AnalyticsDeskContext. */
const GAMMA_UNDERLYING_KEY = "3st.gamma-density.underlying";

const SPOT_LINE = "#0891b2"; // cyan
const FLIP_LINE = "#db2777"; // pink

function loadPersistedUnderlying(): OiUnderlying {
  try {
    const raw = localStorage.getItem(GAMMA_UNDERLYING_KEY);
    if (!raw) return "NIFTY";
    const u = raw.trim().toUpperCase() as OiUnderlying;
    return u || "NIFTY";
  } catch {
    return "NIFTY";
  }
}

function persistUnderlying(u: OiUnderlying) {
  try {
    localStorage.setItem(GAMMA_UNDERLYING_KEY, u);
  } catch {
    /* ignore quota / private mode */
  }
}

type SignMode = "naive" | "customer" | "oi_delta";
type OiBaselineMode = "session_open" | "prev_close";
type ReversalTf = "1m" | "5m" | "15m";
type ReversalGexMode = "live" | "research";

const STRIKE_WINDOW_OPTIONS = [10, 15, 20, 25, 30, 40, 50] as const;

const JOINT_LABELS: Record<string, string> = {
  pin_fade: "Pin / fade extremes (GEX>0, far from flip)",
  trend_breakout: "Trend / breakout risk (GEX<0, near flip)",
  vol_amp: "Vol amplification (GEX<0 + VEX<0)",
  mean_revert: "Mean-revert bias (positive γ)",
  mixed: "Mixed regime — watch flip distance",
};

function fmt(v: number | null | undefined, digits = 0): string {
  if (v == null) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function gexCrore(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v / 1e7).toFixed(2);
}

const MOMENTUM_COMPONENT_LABELS: { key: keyof GammaMomentum["components"]; label: string }[] = [
  { key: "gex", label: "GEX" },
  { key: "squeeze", label: "Squeeze" },
  { key: "oi_flow", label: "OI flow" },
  { key: "iv", label: "IV" },
  { key: "structure", label: "Structure" },
];

function MomentumStrip({ momentum }: { momentum: GammaMomentum }) {
  const tone =
    momentum.label === "bullish"
      ? "pos"
      : momentum.label === "bearish"
        ? "neg"
        : "muted";
  const scoreTone =
    tone === "pos"
      ? "text-emerald-700 dark:text-emerald-400"
      : tone === "neg"
        ? "text-red-700 dark:text-red-400"
        : "text-foreground";
  const borderTone =
    tone === "pos"
      ? "border-emerald-500/35 bg-emerald-50/40 dark:bg-emerald-950/20"
      : tone === "neg"
        ? "border-red-500/35 bg-red-50/40 dark:bg-red-950/20"
        : "border-border/70 bg-muted/20";

  return (
    <div className={`rounded-md border px-3 py-2.5 ${borderTone}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Squeeze / momentum
        </p>
        <p className={`font-mono text-lg font-semibold tabular-nums ${scoreTone}`}>
          {momentum.score.toFixed(0)}
        </p>
        <Badge
          variant="outline"
          className={
            tone === "pos"
              ? "border-emerald-500/50 capitalize"
              : tone === "neg"
                ? "border-red-500/50 capitalize"
                : "capitalize"
          }
        >
          {momentum.label}
        </Badge>
        <span className="text-[10px] text-muted-foreground">
          &gt;60 up-bias · 40–60 neutral · &lt;40 fade
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {MOMENTUM_COMPONENT_LABELS.map(({ key, label }) => {
          const v = momentum.components[key];
          return (
            <Badge key={key} variant="outline" className="font-mono text-[10px] tabular-nums">
              {label} {Number.isFinite(v) ? v.toFixed(0) : "—"}
            </Badge>
          );
        })}
      </div>
      {(momentum.drivers?.length ?? 0) > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
          {momentum.drivers.slice(0, 5).map((d) => (
            <span key={d} className="rounded bg-background/60 px-1.5 py-0.5">
              {d}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "pos" | "neg" | "muted";
}) {
  const color =
    tone === "pos"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "neg"
        ? "text-red-600 dark:text-red-400"
        : "text-foreground";
  return (
    <Card>
      <CardContent className="py-3">
        <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className={`font-mono text-lg font-semibold ${color}`}>{value}</div>
        {hint ? <div className="text-[10px] text-muted-foreground">{hint}</div> : null}
      </CardContent>
    </Card>
  );
}

function GexProfileChart({ snap, height = 280 }: { snap: GammaSnapshot; height?: number }) {
  const data = snap.gex_profile ?? [];
  if (!data.length) {
    return <p className="py-8 text-center text-sm text-muted-foreground">No GEX(S) profile</p>;
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 10, right: 16, bottom: 10, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis dataKey="spot" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 11 }} />
        <YAxis
          tick={{ fontSize: 11 }}
          tickFormatter={(v) => (v / 1e7).toFixed(1)}
          label={{ value: "GEX(S) ₹Cr", angle: -90, position: "insideLeft", fontSize: 11 }}
        />
        <Tooltip
          formatter={(v: number) => [`${(v / 1e7).toFixed(2)} Cr`, "GEX"]}
          labelFormatter={(l) => `Spot ${l}`}
        />
        <ReferenceLine y={0} stroke="currentColor" className="text-muted-foreground" />
        <ReferenceLine
          x={snap.spot}
          stroke={SPOT_LINE}
          strokeWidth={2}
          label={{ value: "Spot", fontSize: 10, fill: SPOT_LINE, position: "top" }}
        />
        {snap.flip_level != null ? (
          <ReferenceLine
            x={snap.flip_level}
            stroke={FLIP_LINE}
            strokeDasharray="4 4"
            label={{ value: "Flip SS", fontSize: 10, fill: FLIP_LINE, position: "top" }}
          />
        ) : null}
        {snap.flip_sticky_delta != null ? (
          <ReferenceLine
            x={snap.flip_sticky_delta}
            stroke="#ec4899"
            strokeDasharray="2 3"
            label={{ value: "Flip SD", fontSize: 10, fill: "#ec4899", position: "insideTopRight" }}
          />
        ) : null}
        <Line type="monotone" dataKey="gex" stroke="#0ea5e9" strokeWidth={2} dot={false} name="GEX(S)" />
      </LineChart>
    </ResponsiveContainer>
  );
}

function GammaOptionsPopover({
  signMode,
  onSignModeChange,
  multiExpiry,
  onMultiExpiryChange,
  strikeWindow,
  onStrikeWindowChange,
  oiBaseline,
  onOiBaselineChange,
  reversalTf,
  onReversalTfChange,
  reversalGexGate,
  onReversalGexGateChange,
  reversalGexMode,
  onReversalGexModeChange,
  reversalOiGate,
  onReversalOiGateChange,
}: {
  signMode: SignMode;
  onSignModeChange: (v: SignMode) => void;
  multiExpiry: boolean;
  onMultiExpiryChange: (v: boolean) => void;
  strikeWindow: number;
  onStrikeWindowChange: (v: number) => void;
  oiBaseline: OiBaselineMode;
  onOiBaselineChange: (v: OiBaselineMode) => void;
  reversalTf: ReversalTf;
  onReversalTfChange: (v: ReversalTf) => void;
  reversalGexGate: boolean;
  onReversalGexGateChange: (v: boolean) => void;
  reversalGexMode: ReversalGexMode;
  onReversalGexModeChange: (v: ReversalGexMode) => void;
  reversalOiGate: boolean;
  onReversalOiGateChange: (v: boolean) => void;
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="report-controls gap-1.5" title="GEX options">
          <Settings2 className="h-4 w-4" />
          Options
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 space-y-3 p-4">
        <div>
          <p className="text-sm font-medium">GEX parameters</p>
          <p className="text-[11px] text-muted-foreground">
            Snapshot knobs from <span className="font-mono">gamma_density</span>. Changing dealer
            sign or strike window mid-session mixes the GEX Cr recorder series.
          </p>
        </div>
        <div className="grid gap-3">
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Dealer sign</Label>
            <Select value={signMode} onValueChange={(v) => onSignModeChange(v as SignMode)}>
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="naive">Naive (CE+ / PE−)</SelectItem>
                <SelectItem value="customer">Customer (inverted)</SelectItem>
                <SelectItem value="oi_delta">OI Δ (EOD baseline)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Strike window (±ATM)</Label>
            <Select
              value={String(strikeWindow)}
              onValueChange={(v) => onStrikeWindowChange(Number(v))}
            >
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STRIKE_WINDOW_OPTIONS.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">OI Δ baseline</Label>
            <Select
              value={oiBaseline}
              onValueChange={(v) => onOiBaselineChange(v as OiBaselineMode)}
            >
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="session_open">Session open</SelectItem>
                <SelectItem value="prev_close">Prev day close</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Reversal TF</Label>
            <Select value={reversalTf} onValueChange={(v) => onReversalTfChange(v as ReversalTf)}>
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="1m">1m (fastest)</SelectItem>
                <SelectItem value="5m">5m (bar-close)</SelectItem>
                <SelectItem value="15m">15m (bar-close)</SelectItem>
              </SelectContent>
            </Select>
            {reversalTf !== "1m" ? (
              <p className="max-w-[14rem] text-[10px] leading-snug text-muted-foreground">
                {reversalTf} confirms on TF bar close — switch to 1m for real-time pivots.
                Live still shows provisional chips as soon as the swing clears.
              </p>
            ) : null}
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={multiExpiry}
              onCheckedChange={(v) => onMultiExpiryChange(v === true)}
            />
            Multi-expiry stack
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={reversalGexGate}
              onCheckedChange={(v) => onReversalGexGateChange(v === true)}
            />
            Require GEX on reversals
          </label>
          {reversalGexGate ? (
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">GEX mode</Label>
              <Select
                value={reversalGexMode}
                onValueChange={(v) => onReversalGexModeChange(v as ReversalGexMode)}
              >
                <SelectTrigger className="h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="live">Live</SelectItem>
                  <SelectItem value="research">Research</SelectItem>
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={reversalOiGate}
              onCheckedChange={(v) => onReversalOiGateChange(v === true)}
            />
            Require OI on reversals
          </label>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function GammaDensityPage() {
  const [config, setConfig] = useState<GammaConfig | null>(null);
  // Persist so HMR / remount / refresh does not silently snap back to NIFTY.
  const [underlying, setUnderlyingState] = useState<OiUnderlying>(() => loadPersistedUnderlying());
  const [expiry, setExpiry] = useState<string>("");
  const { expiries, loading: expiriesLoading } = useOptionExpiries(underlying);
  const [refreshSec, setRefreshSec] = useState(60);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [signMode, setSignMode] = useState<SignMode>("naive");
  const [multiExpiry, setMultiExpiry] = useState(true);
  const [strikeWindow, setStrikeWindow] = useState(20);
  const [snapshot, setSnapshot] = useState<GammaSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState(false);
  const [showDayLevels, setShowDayLevels] = useState(true);
  const [showWeekLevels, setShowWeekLevels] = useState(true);
  const [showIvCurve, setShowIvCurve] = useState(false);
  const [showCePeOi, setShowCePeOi] = useState(true);
  const [showDoi, setShowDoi] = useState(true);
  const [oiBaseline, setOiBaseline] = useState<OiBaselineMode>("session_open");
  const [reversalTf, setReversalTf] = useState<ReversalTf>("5m");
  const [reversalGexGate, setReversalGexGate] = useState(true);
  const [reversalGexMode, setReversalGexMode] = useState<ReversalGexMode>("live");
  const [reversalOiGate, setReversalOiGate] = useState(false);
  const [summaryRefreshToken, setSummaryRefreshToken] = useState(0);
  /** Ignore stale snapshot responses after underlying / options change. */
  const snapshotReqId = useRef(0);

  const selectUnderlying = useCallback((u: OiUnderlying) => {
    const next = String(u || "").toUpperCase() as OiUnderlying;
    if (!next) return;
    setUnderlyingState((prev) => {
      if (prev === next) return prev;
      return next;
    });
  }, []);

  // Keep last pick across remount/HMR/refresh; config polls must not write this key.
  useEffect(() => {
    persistUnderlying(underlying);
  }, [underlying]);

  const prevUnderlyingRef = useRef(underlying);
  useEffect(() => {
    if (prevUnderlyingRef.current === underlying) return;
    prevUnderlyingRef.current = underlying;
    setExpiry("");
  }, [underlying]);

  useEffect(() => {
    api
      .get<GammaConfig>("/gamma-density/config", { silent: true })
      .then((c) => {
        setConfig(c);
        setRefreshSec(c.refresh_seconds);
        if (c.sign_mode) setSignMode(c.sign_mode);
        if (c.strike_window) setStrikeWindow(c.strike_window);
        // Config / poll must never overwrite the user's underlying (e.g. CRUDEOIL → NIFTY).
      })
      .catch(() => {});
  }, []);

  // Expiry only — never mutates underlying (pickNearestExpiry is expiry-scoped).
  useEffect(() => {
    if (!expiries.length) return;
    setExpiry((current) => {
      if (current && expiries.includes(current)) return current;
      return pickNearestExpiry(expiries, underlying) ?? "";
    });
  }, [expiries, underlying]);

  const underlyingOptions = useMemo(() => {
    const base = config?.underlyings?.length
      ? (config.underlyings as OiUnderlying[])
      : DEFAULT_UNDERLYINGS;
    // Keep Select controlled-valid while config is loading or MCX is selected.
    if (underlying && !base.includes(underlying)) {
      return [...base, underlying];
    }
    return base;
  }, [config?.underlyings, underlying]);

  const fetchSnapshot = useCallback(async () => {
    const reqId = ++snapshotReqId.current;
    setLoading(true);
    setAuthError(false);
    try {
      const q = new URLSearchParams({ underlying, sign_mode: signMode });
      if (expiry) q.set("expiry", expiry);
      q.set("strike_window", String(strikeWindow));
      q.set("multi_expiry", multiExpiry ? "true" : "false");
      q.set("oi_baseline", oiBaseline);
      q.set("reversal_tf", reversalTf);
      q.set("reversal_gex_gate", reversalGexGate ? "true" : "false");
      q.set("reversal_gex_mode", reversalGexMode);
      q.set("reversal_oi_gate", reversalOiGate ? "true" : "false");
      const data = await api.get<GammaSnapshot>(`/gamma-density/snapshot?${q}`);
      // Drop late responses so a slow Crude poll cannot overwrite Nifty (or vice versa).
      if (reqId !== snapshotReqId.current) return;
      setSnapshot(data);
    } catch (e: unknown) {
      if (reqId !== snapshotReqId.current) return;
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("401") || msg.toLowerCase().includes("session")) setAuthError(true);
    } finally {
      if (reqId === snapshotReqId.current) setLoading(false);
    }
  }, [
    underlying,
    expiry,
    signMode,
    strikeWindow,
    multiExpiry,
    oiBaseline,
    reversalTf,
    reversalGexGate,
    reversalGexMode,
    reversalOiGate,
  ]);

  useEffect(() => {
    if (!expiry) return;
    void fetchSnapshot();
  }, [expiry, fetchSnapshot]);

  useEffect(() => {
    if (!autoRefresh || !expiry || authError) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSnapshot();
    }, refreshSec * 1000);
    return () => clearInterval(id);
  }, [autoRefresh, refreshSec, expiry, authError, fetchSnapshot]);

  const metaLine = useMemo(() => {
    if (!snapshot) return null;
    const mid = snapshot.price_source_stats?.mid ?? 0;
    const ltp = snapshot.price_source_stats?.ltp ?? 0;
    return (
      <>
        Spot <span className="font-mono font-semibold text-foreground">{snapshot.spot.toFixed(2)}</span>
        <CasChip cas={snapshot.cas} spot={snapshot.spot} />
        {snapshot.session_poc?.poc != null ? (
          <>
            {" · "}
            Fut POC{" "}
            <span className="font-mono font-semibold text-violet-700 dark:text-violet-400">
              {snapshot.session_poc.poc.toFixed(2)}
            </span>
          </>
        ) : null}
        {" · "}ATM {snapshot.atm_strike}
        {" · "}ATM IV {snapshot.atm_iv != null ? `${snapshot.atm_iv}%` : "—"}
        {" · "}q {(snapshot.dividend_yield ?? 0) * 100}%
        {" · "}mid {mid} / ltp {ltp}
        {" · "}
        {snapshot.chain_legs_quoted}/{snapshot.chain_legs_total} legs
        {" · "}Updated {new Date(snapshot.updated_at).toLocaleTimeString()}
      </>
    );
  }, [snapshot]);

  const jointHint = snapshot?.vanna_strip?.joint_read
    ? JOINT_LABELS[snapshot.vanna_strip.joint_read] ?? snapshot.vanna_strip.joint_read
    : null;

  return (
    <div className="report-page mx-auto flex max-w-[1400px] flex-col gap-6 pb-10">
      <header className="report-controls flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">3ST Algo Desk — Gamma Density</h1>
          <p className="text-sm text-muted-foreground">
            Dealer-hedging map: Net GEX plus HHI shape, conviction, pin, flip, hedge flow, Vanna strip.
          </p>
          {metaLine ? <p className="mt-1 text-xs text-muted-foreground">{metaLine}</p> : null}
        </div>
        <div className="flex items-center gap-2">
          <GammaOptionsPopover
            signMode={signMode}
            onSignModeChange={setSignMode}
            multiExpiry={multiExpiry}
            onMultiExpiryChange={setMultiExpiry}
            strikeWindow={strikeWindow}
            onStrikeWindowChange={setStrikeWindow}
            oiBaseline={oiBaseline}
            onOiBaselineChange={setOiBaseline}
            reversalTf={reversalTf}
            onReversalTfChange={setReversalTf}
            reversalGexGate={reversalGexGate}
            onReversalGexGateChange={setReversalGexGate}
            reversalGexMode={reversalGexMode}
            onReversalGexModeChange={setReversalGexMode}
            reversalOiGate={reversalOiGate}
            onReversalOiGateChange={setReversalOiGate}
          />
          <ReportPageDownload title="Gamma_Density" />
        </div>
      </header>

      {authError && (
        <Card className="report-controls border-destructive/50">
          <CardContent className="py-4 text-sm">
            Kite session required.{" "}
            <Link to="/login" className="text-primary underline">
              Log in
            </Link>
          </CardContent>
        </Card>
      )}

      <Card className="report-controls">
        <CardHeader>
          <CardTitle className="text-base">Settings</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-4">
          <div className="flex flex-col gap-1.5">
            <Label>Underlying</Label>
            <Select
              value={underlying}
              onValueChange={(v) => selectUnderlying(v as OiUnderlying)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {underlyingOptions.map((u) => (
                  <SelectItem key={u} value={u}>
                    {u === "CRUDEOIL" ||
                    u === "CRUDEOILM" ||
                    u === "NATURALGAS" ||
                    u === "GOLD" ||
                    u === "SILVER"
                      ? `${u} (MCX)`
                      : u}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Expiry</Label>
            <Select
              value={expiries.length && expiry && expiries.includes(expiry) ? expiry : undefined}
              onValueChange={setExpiry}
            >
              <SelectTrigger>
                <SelectValue
                  placeholder={
                    expiriesLoading ? "Loading expiries…" : expiries.length ? "Select expiry" : "No expiries"
                  }
                />
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
          <div className="flex flex-col gap-1.5">
            <Label>Dealer sign</Label>
            <Select value={signMode} onValueChange={(v) => setSignMode(v as SignMode)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="naive">Naive (CE+ / PE−)</SelectItem>
                <SelectItem value="customer">Customer (inverted)</SelectItem>
                <SelectItem value="oi_delta">OI Δ (EOD baseline)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Refresh (sec)</Label>
            <Select value={String(refreshSec)} onValueChange={(v) => setRefreshSec(Number(v))}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[30, 60, 90, 120].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}s
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={autoRefresh} onCheckedChange={(v) => setAutoRefresh(Boolean(v))} />
            Auto-refresh
          </label>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <Checkbox checked={multiExpiry} onCheckedChange={(v) => setMultiExpiry(Boolean(v))} />
            Multi-expiry stack (slower — extra quote pass)
          </label>
        </CardContent>
      </Card>

      <div className="report-controls flex gap-2">
        <Button
          onClick={() => {
            setSummaryRefreshToken((n) => n + 1);
            void fetchSnapshot();
          }}
          disabled={loading || !expiry}
        >
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {loading ? "Loading…" : "Refresh now"}
        </Button>
        <Button variant="outline" onClick={() => setAutoRefresh((v) => !v)}>
          {autoRefresh ? <Pause className="mr-2 h-4 w-4" /> : <Play className="mr-2 h-4 w-4" />}
          {autoRefresh ? "Pause auto-refresh" : "Resume auto-refresh"}
        </Button>
      </div>

      {snapshot ? (
        <Tabs defaultValue="profile" className="w-full">
          <TabsList className="report-controls">
            <TabsTrigger value="profile">Profile</TabsTrigger>
            <TabsTrigger value="concentration">Concentration</TabsTrigger>
          </TabsList>

          <TabsContent value="profile" className="mt-4 flex flex-col gap-6">
          {jointHint ? (
            <Card className="border-primary/30 bg-primary/5">
              <CardContent className="flex flex-wrap items-center gap-3 py-3 text-sm">
                <Badge variant="outline">GEX × Vanna</Badge>
                <span className="font-medium">{jointHint}</span>
                {snapshot.vanna_strip ? (
                  <span className="text-xs text-muted-foreground">
                    VEX {snapshot.vanna_strip.total_vex_cr.toFixed(2)} Cr · {snapshot.vanna_strip.vanna_regime} vanna
                    {" · "}
                    <Link to="/vanna-exposure" className="text-primary underline">
                      Open Vanna
                    </Link>
                  </span>
                ) : null}
              </CardContent>
            </Card>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Gamma Regime"
              value={snapshot.gamma_regime === "positive" ? "Positive γ" : "Negative γ"}
              hint={
                snapshot.gamma_regime === "positive"
                  ? "Dealers dampen moves (mean-revert)"
                  : "Dealers amplify moves (trend)"
              }
              tone={snapshot.gamma_regime === "positive" ? "pos" : "neg"}
            />
            <StatCard
              label="Total Net GEX"
              value={`${gexCrore(snapshot.total_gex)} ₹Cr`}
              hint={`per 1% · mode ${snapshot.sign_mode ?? signMode}`}
              tone={snapshot.total_gex >= 0 ? "pos" : "neg"}
            />
            <StatCard
              label="Distance to Flip"
              value={
                snapshot.distance_to_flip != null
                  ? `${snapshot.distance_to_flip > 0 ? "+" : ""}${fmt(snapshot.distance_to_flip, 0)}`
                  : "—"
              }
              hint={
                snapshot.flip_level != null
                  ? `SS flip ${fmt(snapshot.flip_level)} · SD ${fmt(snapshot.flip_sticky_delta)}`
                  : "no zero crossing"
              }
              tone="muted"
            />
            <StatCard
              label="Expected Move"
              value={snapshot.expected_move ? `±${fmt(snapshot.expected_move.sigma1_pts, 0)}` : "—"}
              hint={
                snapshot.expected_move
                  ? `${snapshot.expected_move.source ?? "atm_iv"} · ${fmt(snapshot.expected_move.sigma1_dn, 0)}–${fmt(snapshot.expected_move.sigma1_up, 0)}`
                  : undefined
              }
              tone="muted"
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="HHI Concentration"
              value={
                snapshot.concentration?.hhi != null
                  ? snapshot.concentration.hhi.toFixed(2)
                  : "—"
              }
              hint={
                snapshot.concentration?.band
                  ? `${
                      snapshot.concentration.band === "mixed"
                        ? "balanced"
                        : snapshot.concentration.band === "diffuse"
                          ? "dispersed"
                          : "concentrated"
                    } · top1 ${
                      snapshot.concentration.top1_share != null
                        ? `${(snapshot.concentration.top1_share * 100).toFixed(0)}%`
                        : "—"
                    }`
                  : "same GEX, different shape"
              }
              tone={
                snapshot.concentration?.band === "concentrated"
                  ? "pos"
                  : snapshot.concentration?.band === "diffuse"
                    ? "neg"
                    : "muted"
              }
            />
            <StatCard
              label="Conviction"
              value={
                snapshot.conviction?.score != null ? String(snapshot.conviction.score) : "—"
              }
              hint={
                snapshot.conviction
                  ? `${snapshot.conviction.direction}${
                      snapshot.conviction.delta != null
                        ? ` · Δ ${snapshot.conviction.delta > 0 ? "+" : ""}${snapshot.conviction.delta}`
                        : ""
                    }`
                  : undefined
              }
              tone={
                snapshot.conviction?.direction === "rising"
                  ? "pos"
                  : snapshot.conviction?.direction === "falling"
                    ? "neg"
                    : "muted"
              }
            />
            <StatCard
              label="Pin Candidate"
              value={fmt(snapshot.concentration?.pin_strike)}
              hint={
                snapshot.concentration?.pin_stable === true
                  ? `stable · share ${
                      snapshot.concentration.pin_share != null
                        ? `${(snapshot.concentration.pin_share * 100).toFixed(0)}%`
                        : "—"
                    }`
                  : snapshot.concentration?.pin_stable === false
                    ? `moving · ${snapshot.concentration.pin_stability_pct ?? "—"}% stable`
                    : snapshot.concentration?.pin_share != null
                      ? `share ${(snapshot.concentration.pin_share * 100).toFixed(0)}%`
                      : "structural pin — not a guarantee"
              }
              tone="muted"
            />
            <StatCard
              label="Dominant Strike"
              value={fmt(snapshot.concentration?.dominant_strike)}
              hint={
                snapshot.concentration?.dominant_share != null
                  ? `${(snapshot.concentration.dominant_share * 100).toFixed(0)}% of |GEX| · eff ${
                      snapshot.concentration.effective_strikes ?? "—"
                    }`
                  : undefined
              }
              tone="muted"
            />
          </div>

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm">Reference levels · Prev Day / Prev Week</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <div className="rounded-md border border-border/70 bg-muted/20 px-3 py-2 text-xs">
                  <p className="mb-1.5 font-semibold uppercase tracking-wide text-muted-foreground">
                    Previous day
                  </p>
                  <div className="grid grid-cols-3 gap-2 font-mono tabular-nums">
                    <div>
                      <p className="text-[10px] text-muted-foreground">High</p>
                      <p>{fmt(snapshot.reference_levels?.prev_day_high, 2)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-muted-foreground">Low</p>
                      <p>{fmt(snapshot.reference_levels?.prev_day_low, 2)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-muted-foreground">Close</p>
                      <p>{fmt(snapshot.reference_levels?.prev_day_close, 2)}</p>
                    </div>
                  </div>
                </div>
                <div className="rounded-md border border-border/70 bg-muted/20 px-3 py-2 text-xs">
                  <p className="mb-1.5 font-semibold uppercase tracking-wide text-muted-foreground">
                    Previous week
                  </p>
                  <div className="grid grid-cols-3 gap-2 font-mono tabular-nums">
                    <div>
                      <p className="text-[10px] text-muted-foreground">High</p>
                      <p>{fmt(snapshot.reference_levels?.prev_week_high, 2)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-muted-foreground">Low</p>
                      <p>{fmt(snapshot.reference_levels?.prev_week_low, 2)}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-muted-foreground">Close</p>
                      <p>{fmt(snapshot.reference_levels?.prev_week_close, 2)}</p>
                    </div>
                  </div>
                </div>
                <div className="rounded-md border border-border/70 bg-muted/20 px-3 py-2 text-xs">
                  <p className="mb-1.5 font-semibold uppercase tracking-wide text-muted-foreground">
                    Key levels
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    <Badge variant="outline">Flip {fmt(snapshot.flip_level)}</Badge>
                    <Badge variant="outline" className="border-emerald-500/50">
                      Call wall {fmt(snapshot.call_wall)}
                    </Badge>
                    <Badge variant="outline" className="border-red-500/50">
                      Put wall {fmt(snapshot.put_wall)}
                    </Badge>
                    <Badge variant="outline">
                      Dom {fmt(snapshot.concentration?.dominant_strike)}
                    </Badge>
                    <Badge variant="outline">
                      Pin {fmt(snapshot.concentration?.pin_strike)}
                    </Badge>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {snapshot.market_read ? (
            <Card className="border-amber-500/30 bg-amber-50/40 dark:bg-amber-950/20 dark:border-amber-500/25">
              <CardContent className="space-y-1.5 py-3 text-sm">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-amber-800 dark:text-amber-300">
                  Market read · shape over size
                </p>
                <p className="font-medium text-foreground">{snapshot.market_read.regime_line}</p>
                <p className="text-muted-foreground">{snapshot.market_read.vol_line}</p>
                <p className="text-muted-foreground">{snapshot.market_read.shape_line}</p>
                <p className="text-muted-foreground">{snapshot.market_read.change_line}</p>
                <p className="text-muted-foreground">{snapshot.market_read.levels_line}</p>
              </CardContent>
            </Card>
          ) : null}

          {snapshot.momentum ? <MomentumStrip momentum={snapshot.momentum} /> : null}

          <div className="flex flex-wrap gap-2 text-xs">
            <Badge variant="outline" className="border-emerald-500/50">
              Call magnet wall: <span className="ml-1 font-mono">{fmt(snapshot.call_wall)}</span>
              {snapshot.call_wall_magnet != null ? (
                <span className="ml-1 text-muted-foreground">({snapshot.call_wall_magnet.toFixed(1)})</span>
              ) : null}
            </Badge>
            <Badge variant="outline" className="border-red-500/50">
              Put magnet wall: <span className="ml-1 font-mono">{fmt(snapshot.put_wall)}</span>
              {snapshot.put_wall_magnet != null ? (
                <span className="ml-1 text-muted-foreground">({snapshot.put_wall_magnet.toFixed(1)})</span>
              ) : null}
            </Badge>
            {(snapshot.flip_crossings?.length ?? 0) > 1 ? (
              <Badge variant="outline">
                {snapshot.flip_crossings!.length} zero crossings
              </Badge>
            ) : null}
            {snapshot.flip_slope != null ? (
              <Badge variant="outline">Flip slope {snapshot.flip_slope.toFixed(2)}</Badge>
            ) : null}
          </div>

          <GexSessionPlotly
            snap={snapshot}
            reversalTf={reversalTf}
            onReversalTfChange={setReversalTf}
            reversalGexGate={reversalGexGate}
            onReversalGexGateChange={setReversalGexGate}
            reversalGexMode={reversalGexMode}
            onReversalGexModeChange={setReversalGexMode}
            reversalOiGate={reversalOiGate}
            onReversalOiGateChange={setReversalOiGate}
            optionsSlot={
              <GammaOptionsPopover
                signMode={signMode}
                onSignModeChange={setSignMode}
                multiExpiry={multiExpiry}
                onMultiExpiryChange={setMultiExpiry}
                strikeWindow={strikeWindow}
                onStrikeWindowChange={setStrikeWindow}
                oiBaseline={oiBaseline}
                onOiBaselineChange={setOiBaseline}
                reversalTf={reversalTf}
                onReversalTfChange={setReversalTf}
                reversalGexGate={reversalGexGate}
                onReversalGexGateChange={setReversalGexGate}
                reversalGexMode={reversalGexMode}
                onReversalGexModeChange={setReversalGexMode}
                reversalOiGate={reversalOiGate}
                onReversalOiGateChange={setReversalOiGate}
              />
            }
          />

          <div className="flex flex-col gap-4">
            <Card>
              <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 py-3">
                <CardTitle className="text-sm">Net GEX by strike · Γ×OI density</CardTitle>
                <div className="report-no-print flex flex-wrap items-center gap-3 text-xs">
                  <label className="flex items-center gap-1.5">
                    <Checkbox
                      checked={showDayLevels}
                      onCheckedChange={(v) => setShowDayLevels(Boolean(v))}
                    />
                    Day H/L/C
                  </label>
                  <label className="flex items-center gap-1.5">
                    <Checkbox
                      checked={showWeekLevels}
                      onCheckedChange={(v) => setShowWeekLevels(Boolean(v))}
                    />
                    Week H/L/C
                  </label>
                  <label className="flex items-center gap-1.5">
                    <Checkbox
                      checked={showIvCurve}
                      onCheckedChange={(v) => setShowIvCurve(Boolean(v))}
                    />
                    IV Curve
                  </label>
                  <label className="flex items-center gap-1.5" title="Solid CE/PE OI base at each strike (Put left, Call right)">
                    <Checkbox
                      checked={showCePeOi}
                      onCheckedChange={(v) => setShowCePeOi(Boolean(v))}
                    />
                    CE/PE OI
                  </label>
                  <label className="flex items-center gap-1.5" title="Striped ΔOI ↑ / hollow ΔOI ↓ stacked on OI base">
                    <Checkbox checked={showDoi} onCheckedChange={(v) => setShowDoi(Boolean(v))} />
                    ΔOI
                  </label>
                  <label className="flex items-center gap-1.5">
                    <span className="text-muted-foreground">Baseline</span>
                    <Select
                      value={oiBaseline}
                      onValueChange={(v) => setOiBaseline(v as OiBaselineMode)}
                    >
                      <SelectTrigger className="h-7 w-[7.5rem] text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="session_open">Session open</SelectItem>
                        <SelectItem value="prev_close">Prev day</SelectItem>
                      </SelectContent>
                    </Select>
                  </label>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className="text-[11px] text-muted-foreground font-mono">
                  {showDayLevels ? (
                    <>
                      PDH {fmt(snapshot.reference_levels?.prev_day_high, 2)} · PDL{" "}
                      {fmt(snapshot.reference_levels?.prev_day_low, 2)} · PDC{" "}
                      {fmt(snapshot.reference_levels?.prev_day_close, 2)}
                    </>
                  ) : null}
                  {showDayLevels && showWeekLevels ? " · " : null}
                  {showWeekLevels ? (
                    <>
                      PWH {fmt(snapshot.reference_levels?.prev_week_high, 2)} · PWL{" "}
                      {fmt(snapshot.reference_levels?.prev_week_low, 2)} · PWC{" "}
                      {fmt(snapshot.reference_levels?.prev_week_close, 2)}
                    </>
                  ) : null}
                  {!snapshot.reference_levels?.prev_day_close &&
                  !snapshot.reference_levels?.prev_week_close ? (
                    <span className="text-amber-700 dark:text-amber-300">
                      {" "}
                      Day/Week levels unavailable — restart API / refresh after Kite login
                    </span>
                  ) : null}
                </p>
                {(showIvCurve || showCePeOi || showDoi) && (
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                    <span className="inline-flex items-center gap-1" style={{ color: GEX_POS }}>
                      <span className="inline-block h-2 w-2 rounded-sm" style={{ background: GEX_POS }} />
                      GEX+
                    </span>
                    <span className="inline-flex items-center gap-1" style={{ color: GEX_NEG }}>
                      <span className="inline-block h-2 w-2 rounded-sm" style={{ background: GEX_NEG }} />
                      GEX−
                    </span>
                    {showIvCurve ? (
                      <span className="inline-flex items-center gap-1">
                        <span className="inline-block h-0.5 w-3" style={{ background: IV_CURVE }} />
                        IV Curve
                      </span>
                    ) : null}
                    {showCePeOi || showDoi ? (
                      <>
                        <span className="inline-flex items-center gap-1" style={{ color: PUT_OI }}>
                          <span className="inline-block h-2 w-2 rounded-sm" style={{ background: PUT_OI }} />
                          Put (left)
                        </span>
                        <span className="inline-flex items-center gap-1" style={{ color: CALL_OI }}>
                          <span className="inline-block h-2 w-2 rounded-sm" style={{ background: CALL_OI }} />
                          Call (right)
                        </span>
                        <span className="text-muted-foreground/80">OI ↑ from GEX 0</span>
                      </>
                    ) : null}
                    {showCePeOi ? (
                      <span className="inline-flex items-center gap-1">
                        <span className="inline-block h-2 w-2 rounded-sm" style={{ background: CALL_OI }} />
                        solid = OI base
                      </span>
                    ) : null}
                    {showDoi ? (
                      <>
                        <span className="inline-flex items-center gap-1">
                          <span
                            className="inline-block h-2 w-3 border"
                            style={{
                              borderColor: CALL_OI,
                              background:
                                "repeating-linear-gradient(45deg, transparent, transparent 1px, #ef4444 1px, #ef4444 2px)",
                            }}
                          />
                          stripes = ΔOI ↑
                        </span>
                        <span className="inline-flex items-center gap-1">
                          <span
                            className="inline-block h-2 w-3 bg-transparent"
                            style={{ border: `1.5px solid ${PUT_OI}` }}
                          />
                          hollow = ΔOI ↓
                        </span>
                      </>
                    ) : null}
                    {snapshot.oi_baseline_note ? (
                      <span className="font-mono text-muted-foreground/80">· {snapshot.oi_baseline_note}</span>
                    ) : null}
                  </div>
                )}
                <GexStrikePlotly
                  key="net-gex-by-strike-v2"
                  snap={snapshot}
                  showDayLevels={showDayLevels}
                  showWeekLevels={showWeekLevels}
                  showIvCurve={showIvCurve}
                  showCePeOi={showCePeOi}
                  showDoi={showDoi}
                  height={400}
                />
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">
                  GEX(S) profile · sticky-strike / sticky-delta flips
                </CardTitle>
              </CardHeader>
              <CardContent>
                <GexProfileChart snap={snapshot} height={300} />
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">Hedge flow (est. dealer futures)</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow className="text-xs">
                      <TableHead>Move</TableHead>
                      <TableHead>Direction</TableHead>
                      <TableHead className="text-right">Fut lots</TableHead>
                      <TableHead className="text-right">Notional Cr</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(snapshot.hedge_flow ?? []).map((h) => (
                      <TableRow key={h.move_pts} className="text-xs font-mono">
                        <TableCell>
                          {h.move_pts > 0 ? "+" : ""}
                          {h.move_pts} pts
                        </TableCell>
                        <TableCell
                          className={
                            h.direction === "dealers_buy"
                              ? "text-emerald-600 dark:text-emerald-400"
                              : h.direction === "dealers_sell"
                                ? "text-red-600 dark:text-red-400"
                                : ""
                          }
                        >
                          {h.direction.replace("dealers_", "")}
                        </TableCell>
                        <TableCell className="text-right">{h.futures_lots.toFixed(1)}</TableCell>
                        <TableCell className="text-right">{h.notional_cr.toFixed(2)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">
                  Multi-expiry stack
                  {snapshot.multi_expiry_gex != null
                    ? ` · weighted GEX ${gexCrore(snapshot.multi_expiry_gex)} Cr`
                    : ""}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow className="text-xs">
                      <TableHead>Expiry</TableHead>
                      <TableHead className="text-right">Weight</TableHead>
                      <TableHead className="text-right">GEX Cr</TableHead>
                      <TableHead className="text-right">Flip</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    <TableRow className="text-xs font-mono font-semibold">
                      <TableCell>{snapshot.expiry} (front)</TableCell>
                      <TableCell className="text-right">{snapshot.primary_weight?.toFixed(2) ?? "—"}</TableCell>
                      <TableCell className="text-right">{gexCrore(snapshot.total_gex)}</TableCell>
                      <TableCell className="text-right">{fmt(snapshot.flip_level)}</TableCell>
                    </TableRow>
                    {(snapshot.multi_expiry ?? []).map((m) => (
                      <TableRow key={m.expiry} className="text-xs font-mono">
                        <TableCell>{m.expiry}</TableCell>
                        <TableCell className="text-right">{m.weight?.toFixed(2) ?? "—"}</TableCell>
                        <TableCell className="text-right">{gexCrore(m.total_gex)}</TableCell>
                        <TableCell className="text-right">{fmt(m.flip_level)}</TableCell>
                      </TableRow>
                    ))}
                    {!snapshot.multi_expiry?.length ? (
                      <TableRow>
                        <TableCell colSpan={4} className="py-4 text-center text-xs text-muted-foreground">
                          Enable multi-expiry or only one listed expiry available.
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">Top convexity zones</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow className="text-xs">
                      <TableHead>Strike</TableHead>
                      <TableHead className="text-right">Γ×OI</TableHead>
                      <TableHead className="text-right">Magnet</TableHead>
                      <TableHead className="text-right">Net GEX</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {snapshot.convexity_zones.map((z) => (
                      <TableRow key={z.strike} className="text-xs font-mono">
                        <TableCell>{z.strike}</TableCell>
                        <TableCell className="text-right">{(z.total_density / 1e6).toFixed(2)}M</TableCell>
                        <TableCell className="text-right">{z.magnet != null ? z.magnet.toFixed(1) : "—"}</TableCell>
                        <TableCell
                          className={`text-right ${z.net_gex >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}
                        >
                          {gexCrore(z.net_gex)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">Per-strike detail</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="max-h-[360px] overflow-y-auto">
                  <Table>
                    <TableHeader>
                      <TableRow className="text-xs">
                        <TableHead>Strike</TableHead>
                        <TableHead className="text-right">CE OI</TableHead>
                        <TableHead className="text-right">PE OI</TableHead>
                        <TableHead className="text-right">Net GEX</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {snapshot.strikes.map((r) => (
                        <TableRow key={r.strike} className="text-xs font-mono">
                          <TableCell className={r.strike === snapshot.atm_strike ? "font-bold text-primary" : ""}>
                            {r.strike}
                          </TableCell>
                          <TableCell className="text-right">{fmt(r.ce_oi)}</TableCell>
                          <TableCell className="text-right">{fmt(r.pe_oi)}</TableCell>
                          <TableCell
                            className={`text-right ${r.net_gex >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}
                          >
                            {gexCrore(r.net_gex)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          </div>

          <p className="text-[10px] text-muted-foreground">
            Mid IV when spread ≤12%; else LTP. Gamma uses BSM with dividend yield q. Flip from GEX(S) scan
            (sticky-strike + sticky-delta). Magnet walls = density / |K−S|. EM prefers ATM straddle.
            Hedge flow = Γ_units × ΔS / lot. Multi-expiry weights ∝ 1/√TTE. Spot path = day minute
            candles; GEX ticks only while desk polls in-session (no post-close append). Reversals =
            spot swing extremes with ≥~0.15% reclaim, optional GEX regime confirm.
          </p>
          </TabsContent>

          <TabsContent value="concentration" className="mt-4">
            <ConcentrationBoard
              snap={snapshot}
              selectedUnderlying={underlying}
              onSelectUnderlying={selectUnderlying}
              summaryRefreshToken={summaryRefreshToken}
            />
          </TabsContent>
        </Tabs>
      ) : (
        !loading && (
          <p className="text-sm text-muted-foreground">Select an expiry and refresh to load gamma density.</p>
        )
      )}
    </div>
  );
}
