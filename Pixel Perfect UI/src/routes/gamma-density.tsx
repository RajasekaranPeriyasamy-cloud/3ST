import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceDot,
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
  GammaSnapshot,
  GammaStrikeRow,
  OiUnderlying,
} from "@/lib/types";

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

export const Route = createFileRoute("/gamma-density")({
  component: GammaDensityPage,
});

const DEFAULT_UNDERLYINGS: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];
const POS = "#22c55e";
const NEG = "#ef4444";

type SignMode = "naive" | "customer" | "oi_delta";

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

interface TooltipEntry {
  name?: string;
  value?: number;
}

function GammaTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: number | string;
}) {
  if (!active || !payload?.length) return null;
  const gex = payload.find((p) => p.name === "Net GEX")?.value;
  const den = payload.find((p) => p.name === "Γ×OI density")?.value;
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="mb-1 border-b pb-1 font-semibold text-foreground">
        Strike: {typeof label === "number" ? label.toLocaleString() : label}
      </div>
      {gex != null ? (
        <div className={gex >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}>
          Net GEX: {gex.toFixed(2)} ₹Cr
        </div>
      ) : null}
      {den != null ? (
        <div className="text-indigo-500 dark:text-indigo-400">
          Γ×OI density: {(den / 1e6).toFixed(2)}M
        </div>
      ) : null}
    </div>
  );
}

function StrikeGexChart({ snap }: { snap: GammaSnapshot }) {
  const data = snap.strikes.map((r) => ({
    strike: r.strike,
    net_gex_cr: r.net_gex / 1e7,
    total_density: r.total_density,
  }));
  return (
    <ResponsiveContainer width="100%" height={340}>
      <ComposedChart data={data} margin={{ top: 10, right: 16, bottom: 10, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis
          dataKey="strike"
          type="number"
          domain={["dataMin", "dataMax"]}
          tick={{ fontSize: 11 }}
        />
        <YAxis
          yAxisId="gex"
          tick={{ fontSize: 11 }}
          tickFormatter={(v) => v.toFixed(1)}
          label={{ value: "Net GEX (₹Cr / 1%)", angle: -90, position: "insideLeft", fontSize: 11 }}
        />
        <YAxis
          yAxisId="den"
          orientation="right"
          tick={{ fontSize: 11 }}
          tickFormatter={(v) => `${(v / 1e6).toFixed(1)}M`}
        />
        <Tooltip content={<GammaTooltip />} />
        <ReferenceLine yAxisId="gex" y={0} stroke="currentColor" className="text-muted-foreground" />
        <ReferenceLine
          yAxisId="gex"
          x={snap.spot}
          stroke="#3b82f6"
          strokeWidth={2}
          label={{ value: "Spot", fontSize: 10, fill: "#3b82f6", position: "top" }}
        />
        {snap.flip_level != null ? (
          <ReferenceLine
            yAxisId="gex"
            x={snap.flip_level}
            stroke="#a855f7"
            strokeDasharray="4 4"
            label={{ value: "Flip", fontSize: 10, fill: "#a855f7", position: "top" }}
          />
        ) : null}
        {snap.concentration?.dominant_strike != null ? (
          <ReferenceLine
            yAxisId="gex"
            x={snap.concentration.dominant_strike}
            stroke="#0d9488"
            strokeDasharray="3 3"
            label={{ value: "Dom", fontSize: 10, fill: "#0d9488", position: "insideTopLeft" }}
          />
        ) : null}
        {snap.expected_move ? (
          <>
            <ReferenceLine
              yAxisId="gex"
              x={snap.expected_move.sigma1_up}
              stroke="#f59e0b"
              strokeDasharray="2 4"
              label={{ value: "+1σ", fontSize: 9, fill: "#f59e0b", position: "top" }}
            />
            <ReferenceLine
              yAxisId="gex"
              x={snap.expected_move.sigma1_dn}
              stroke="#f59e0b"
              strokeDasharray="2 4"
              label={{ value: "−1σ", fontSize: 9, fill: "#f59e0b", position: "top" }}
            />
          </>
        ) : null}
        <Bar yAxisId="gex" dataKey="net_gex_cr" name="Net GEX" radius={[2, 2, 0, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.net_gex_cr >= 0 ? POS : NEG} />
          ))}
        </Bar>
        <Line
          yAxisId="den"
          type="monotone"
          dataKey="total_density"
          name="Γ×OI density"
          stroke="#6366f1"
          strokeWidth={2}
          dot={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function GexProfileChart({ snap }: { snap: GammaSnapshot }) {
  const data = snap.gex_profile ?? [];
  if (!data.length) {
    return <p className="py-8 text-center text-sm text-muted-foreground">No GEX(S) profile</p>;
  }
  return (
    <ResponsiveContainer width="100%" height={280}>
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
          stroke="#3b82f6"
          strokeWidth={2}
          label={{ value: "Spot", fontSize: 10, fill: "#3b82f6", position: "top" }}
        />
        {snap.flip_level != null ? (
          <ReferenceLine
            x={snap.flip_level}
            stroke="#a855f7"
            strokeDasharray="4 4"
            label={{ value: "Flip SS", fontSize: 10, fill: "#a855f7", position: "top" }}
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

function HistoryChart({ snap }: { snap: GammaSnapshot }) {
  const raw = (snap.chart_series?.length ? snap.chart_series : snap.history) ?? [];
  const data = useMemo(
    () =>
      raw.map((h) => {
        const ms =
          h.ts_ms ??
          (h.t ? new Date(h.t).getTime() : NaN);
        return {
          ts: Number.isFinite(ms) ? ms : 0,
          label: h.t
            ? new Date(h.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
            : "",
          gex_cr: h.total_gex != null ? h.total_gex / 1e7 : null,
          flip: h.flip_level,
          spot: h.spot,
        };
      }),
    [raw],
  );

  const levelDomain = useMemo((): [number, number] | ["auto", "auto"] => {
    const levels: number[] = [];
    for (const row of data) {
      if (row.spot != null && Number.isFinite(row.spot)) levels.push(Number(row.spot));
      if (row.flip != null && Number.isFinite(row.flip)) levels.push(Number(row.flip));
    }
    if (levels.length < 2) return ["auto", "auto"];
    const lo = Math.min(...levels);
    const hi = Math.max(...levels);
    const span = hi - lo;
    const pad = Math.max(span * 0.35, Math.max(lo * 0.002, 25));
    return [Math.floor(lo - pad), Math.ceil(hi + pad)];
  }, [data]);

  const reversals = snap.reversals ?? [];

  if (data.length < 2) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        Day spot path loads from minute candles; GEX ticks appear while the desk refreshes in-session.
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {reversals.length > 0 ? (
        <div className="flex flex-wrap gap-2 text-[11px]">
          {reversals.map((r) => (
            <span
              key={`${r.t}-${r.side}`}
              className={
                r.side === "bullish"
                  ? "rounded border border-emerald-500/40 bg-emerald-50 px-2 py-0.5 text-emerald-800"
                  : "rounded border border-rose-500/40 bg-rose-50 px-2 py-0.5 text-rose-800"
              }
            >
              {r.t
                ? new Date(r.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                : "—"}{" "}
              · {r.label}
            </span>
          ))}
        </div>
      ) : null}
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
          <XAxis
            dataKey="ts"
            type="number"
            domain={["dataMin", "dataMax"]}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) =>
              new Date(Number(v)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
            }
          />
          <YAxis yAxisId="gex" tick={{ fontSize: 10 }} tickFormatter={(v) => v.toFixed(1)} />
          <YAxis
            yAxisId="lvl"
            orientation="right"
            domain={levelDomain}
            allowDataOverflow
            tick={{ fontSize: 10 }}
            tickFormatter={(v) =>
              Math.abs(v) >= 1000
                ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })
                : String(v)
            }
            width={56}
          />
          <Tooltip
            labelFormatter={(v) =>
              new Date(Number(v)).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })
            }
            formatter={(value: number, name: string) => {
              if (value == null || Number.isNaN(value)) return ["—", name];
              if (name === "GEX Cr") return [value.toFixed(2), name];
              return [Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 }), name];
            }}
          />
          <ReferenceLine yAxisId="gex" y={0} stroke="#94a3b8" />
          <Line
            yAxisId="gex"
            type="monotone"
            dataKey="gex_cr"
            stroke="#22c55e"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            name="GEX Cr"
          />
          <Line
            yAxisId="lvl"
            type="monotone"
            dataKey="flip"
            stroke="#a855f7"
            strokeWidth={1.5}
            dot={false}
            connectNulls={false}
            name="Flip"
          />
          <Line
            yAxisId="lvl"
            type="monotone"
            dataKey="spot"
            stroke="#3b82f6"
            strokeWidth={1.5}
            dot={false}
            connectNulls
            name="Spot"
          />
          {reversals.map((r) =>
            r.ts_ms != null && r.spot != null ? (
              <ReferenceDot
                key={`rev-${r.ts_ms}`}
                yAxisId="lvl"
                x={r.ts_ms}
                y={r.spot}
                r={5}
                fill={r.side === "bullish" ? "#16a34a" : "#e11d48"}
                stroke="#fff"
                strokeWidth={1}
              />
            ) : null,
          )}
        </ComposedChart>
      </ResponsiveContainer>
      <p className="text-[10px] text-muted-foreground">
        Blue = day spot (minute candles). Green/purple GEX &amp; flip only where the desk sampled —
        gaps stay empty (no post-close inventing). Dots = detected spot reversals.
      </p>
    </div>
  );
}

function GammaDensityPage() {
  const [config, setConfig] = useState<GammaConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const { expiries, loading: expiriesLoading } = useOptionExpiries(underlying);
  const [refreshSec, setRefreshSec] = useState(60);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [signMode, setSignMode] = useState<SignMode>("naive");
  const [multiExpiry, setMultiExpiry] = useState(true);
  const [snapshot, setSnapshot] = useState<GammaSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState(false);

  useEffect(() => {
    api
      .get<GammaConfig>("/gamma-density/config", { silent: true })
      .then((c) => {
        setConfig(c);
        setRefreshSec(c.refresh_seconds);
        if (c.sign_mode) setSignMode(c.sign_mode);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!expiries.length) return;
    setExpiry((current) => {
      if (current && expiries.includes(current)) return current;
      return pickNearestExpiry(expiries) ?? "";
    });
  }, [expiries, underlying]);

  const underlyingOptions = config?.underlyings?.length
    ? (config.underlyings as OiUnderlying[])
    : DEFAULT_UNDERLYINGS;

  const fetchSnapshot = useCallback(async () => {
    setLoading(true);
    setAuthError(false);
    try {
      const q = new URLSearchParams({ underlying, sign_mode: signMode });
      if (expiry) q.set("expiry", expiry);
      q.set("multi_expiry", multiExpiry ? "true" : "false");
      const data = await api.get<GammaSnapshot>(`/gamma-density/snapshot?${q}`);
      setSnapshot(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("401") || msg.toLowerCase().includes("session")) setAuthError(true);
    } finally {
      setLoading(false);
    }
  }, [underlying, expiry, signMode, multiExpiry]);

  useEffect(() => {
    if (!expiry) return;
    void fetchSnapshot();
  }, [underlying, expiry, signMode, multiExpiry]);

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
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 pb-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">3ST Algo Desk — Gamma Density</h1>
        <p className="text-sm text-muted-foreground">
          Dealer-hedging map: Net GEX plus HHI shape, conviction, pin, flip, hedge flow, Vanna strip.
        </p>
        {metaLine ? <p className="mt-1 text-xs text-muted-foreground">{metaLine}</p> : null}
      </header>

      {authError && (
        <Card className="border-destructive/50">
          <CardContent className="py-4 text-sm">
            Kite session required.{" "}
            <Link to="/login" className="text-primary underline">
              Log in
            </Link>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Settings</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-4">
          <div className="flex flex-col gap-1.5">
            <Label>Underlying</Label>
            <Select
              value={underlying}
              onValueChange={(v) => {
                setUnderlying(v as OiUnderlying);
                setExpiry("");
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {underlyingOptions.map((u) => (
                  <SelectItem key={u} value={u}>
                    {u}
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

      <div className="flex gap-2">
        <Button onClick={() => void fetchSnapshot()} disabled={loading || !expiry}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {loading ? "Loading…" : "Refresh now"}
        </Button>
        <Button variant="outline" onClick={() => setAutoRefresh((v) => !v)}>
          {autoRefresh ? <Pause className="mr-2 h-4 w-4" /> : <Play className="mr-2 h-4 w-4" />}
          {autoRefresh ? "Pause auto-refresh" : "Resume auto-refresh"}
        </Button>
      </div>

      {snapshot ? (
        <>
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
                  ? `${snapshot.concentration.band} · top1 ${
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

          {snapshot.market_read ? (
            <Card className="border-amber-500/30 bg-amber-50/40 dark:bg-amber-950/20">
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

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">Net GEX by strike · Γ×OI density</CardTitle>
              </CardHeader>
              <CardContent>
                <StrikeGexChart snap={snapshot} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">GEX(S) profile · sticky-strike / sticky-delta flips</CardTitle>
              </CardHeader>
              <CardContent>
                <GexProfileChart snap={snapshot} />
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

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm">Intraday GEX / flip history</CardTitle>
            </CardHeader>
            <CardContent>
              <HistoryChart snap={snapshot} />
            </CardContent>
          </Card>

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
                      {snapshot.strikes.map((r: GammaStrikeRow) => (
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
        </>
      ) : (
        !loading && (
          <p className="text-sm text-muted-foreground">Select an expiry and refresh to load gamma density.</p>
        )
      )}
    </div>
  );
}
