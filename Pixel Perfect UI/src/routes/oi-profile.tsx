import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Customized,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import type {
  OiProfileConfig,
  OiProfileInterval,
  OiProfileSnapshot,
  OiProfileUnderlying,
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
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/oi-profile")({
  component: OiProfilePage,
});

const POS = "#22c55e";
const NEG = "#ef4444";

const INTERP_TONE: Record<string, string> = {
  "Long buildup": "border-emerald-500/50 text-emerald-600 dark:text-emerald-400",
  "Short covering": "border-emerald-500/40 text-emerald-500",
  "Short buildup": "border-red-500/50 text-red-600 dark:text-red-400",
  "Long unwinding": "border-red-500/40 text-red-500",
  Neutral: "border-muted-foreground/40 text-muted-foreground",
};

function fmt(v: number | null | undefined, digits = 0): string {
  if (v == null) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtSigned(v: number | null | undefined, digits = 0): string {
  if (v == null) return "—";
  const s = v > 0 ? "+" : "";
  return s + v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function hhmm(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
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

interface CandleDatum {
  idx: number;
  t: string;
  open: number;
  high: number;
  low: number;
  close: number;
  oi: number;
}

/**
 * Custom candlestick layer drawn inside a recharts ComposedChart via <Customized>.
 * Recharts injects xAxisMap / yAxisMap (with `.scale`) into this element.
 */
function CandleLayer(props: {
  data?: CandleDatum[];
  xAxisMap?: Record<string, { scale: (v: number) => number }>;
  yAxisMap?: Record<string, { scale: (v: number) => number }>;
}) {
  const { data, xAxisMap, yAxisMap } = props;
  if (!data?.length || !xAxisMap || !yAxisMap) return null;
  const xAxis = xAxisMap[Object.keys(xAxisMap)[0]];
  const yAxis = yAxisMap["price"];
  if (!xAxis?.scale || !yAxis?.scale) return null;
  const xScale = xAxis.scale;
  const yScale = yAxis.scale;

  const band =
    data.length > 1 ? Math.abs(xScale(data[1].idx) - xScale(data[0].idx)) : 10;
  const w = Math.max(1, band * 0.6);

  return (
    <g>
      {data.map((d) => {
        const cx = xScale(d.idx);
        const up = d.close >= d.open;
        const color = up ? POS : NEG;
        const yHigh = yScale(d.high);
        const yLow = yScale(d.low);
        const yOpen = yScale(d.open);
        const yClose = yScale(d.close);
        const bodyTop = Math.min(yOpen, yClose);
        const bodyH = Math.max(1, Math.abs(yOpen - yClose));
        return (
          <g key={d.idx}>
            <line x1={cx} x2={cx} y1={yHigh} y2={yLow} stroke={color} strokeWidth={1} />
            <rect
              x={cx - w / 2}
              y={bodyTop}
              width={w}
              height={bodyH}
              fill={color}
              opacity={0.9}
            />
          </g>
        );
      })}
    </g>
  );
}

function CandleTooltip({
  active,
  payload,
  candles,
}: {
  active?: boolean;
  payload?: { payload?: CandleDatum }[];
  candles: CandleDatum[];
}) {
  if (!active || !payload?.length) return null;
  const idx = payload[0]?.payload?.idx;
  if (idx == null) return null;
  const d = candles[idx];
  if (!d) return null;
  const up = d.close >= d.open;
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="mb-1 border-b pb-1 font-semibold">{hhmm(d.t)}</div>
      <div className={up ? "text-emerald-500" : "text-red-500"}>
        O {d.open} · H {d.high} · L {d.low} · C {d.close}
      </div>
      <div className="text-indigo-500 dark:text-indigo-400">OI {d.oi.toLocaleString()}</div>
    </div>
  );
}

function CandleOiChart({ snap }: { snap: OiProfileSnapshot }) {
  const data: CandleDatum[] = snap.candles.map((c, i) => ({
    idx: i,
    t: c.t,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    oi: c.oi,
  }));
  const lows = data.map((d) => d.low);
  const highs = data.map((d) => d.high);
  const pad = (Math.max(...highs) - Math.min(...lows)) * 0.05 || 1;
  const priceDomain: [number, number] = [Math.min(...lows) - pad, Math.max(...highs) + pad];

  return (
    <ResponsiveContainer width="100%" height={420}>
      <ComposedChart data={data} margin={{ top: 10, right: 8, bottom: 10, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis
          dataKey="idx"
          type="number"
          domain={[0, data.length - 1]}
          tick={{ fontSize: 10 }}
          tickFormatter={(v: number) => (data[v] ? hhmm(data[v].t) : "")}
          minTickGap={40}
        />
        <YAxis
          yAxisId="price"
          domain={priceDomain}
          tick={{ fontSize: 11 }}
          tickFormatter={(v: number) => v.toFixed(0)}
          width={56}
          allowDataOverflow
        />
        <YAxis
          yAxisId="oi"
          orientation="right"
          tick={{ fontSize: 11 }}
          tickFormatter={(v: number) => (v / 1e5).toFixed(1) + "L"}
          width={52}
        />
        <Tooltip content={<CandleTooltip candles={data} />} />
        {snap.poc_price != null ? (
          <ReferenceLine
            yAxisId="price"
            y={snap.poc_price}
            stroke="#a855f7"
            strokeDasharray="4 4"
            label={{ value: "POC", fontSize: 10, fill: "#a855f7", position: "left" }}
          />
        ) : null}
        <Line
          yAxisId="oi"
          type="monotone"
          dataKey="oi"
          name="OI"
          stroke="#6366f1"
          strokeWidth={1.5}
          dot={false}
        />
        <Customized component={<CandleLayer data={data} />} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function ButterflyChart({ snap }: { snap: OiProfileSnapshot }) {
  const data = snap.profile.map((b) => ({
    price: b.price_mid,
    buildup: b.buildup,
    unwind: -b.unwind,
  }));
  return (
    <ResponsiveContainer width="100%" height={Math.max(280, data.length * 22)}>
      <BarChart data={data} layout="vertical" stackOffset="sign" margin={{ top: 8, right: 12, bottom: 8, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis
          type="number"
          tick={{ fontSize: 10 }}
          tickFormatter={(v: number) => (Math.abs(v) / 1e5).toFixed(1) + "L"}
        />
        <YAxis
          type="category"
          dataKey="price"
          tick={{ fontSize: 10 }}
          width={64}
          tickFormatter={(v: number) => v.toFixed(0)}
          label={{ value: "Strike", angle: -90, position: "insideLeft", fontSize: 10 }}
        />
        <Tooltip
          formatter={(value: number, name: string) => [
            Math.abs(value).toLocaleString(),
            name === "buildup" ? "Buildup" : "Unwind",
          ]}
          labelFormatter={(v) => `Strike ${v}`}
        />
        <ReferenceLine x={0} stroke="currentColor" className="text-muted-foreground" />
        {snap.poc_price != null ? (
          <ReferenceLine y={snap.poc_price} stroke="#a855f7" strokeDasharray="4 4" />
        ) : null}
        <Bar dataKey="unwind" name="unwind" fill={NEG} stackId="oi" radius={[0, 2, 2, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={NEG} />
          ))}
        </Bar>
        <Bar dataKey="buildup" name="buildup" fill={POS} stackId="oi" radius={[0, 2, 2, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={POS} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function OiProfilePage() {
  const [config, setConfig] = useState<OiProfileConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiProfileUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const [expiries, setExpiries] = useState<string[]>([]);
  const [interval, setIntervalState] = useState<OiProfileInterval>("5min");
  const [days, setDays] = useState(5);
  const [refreshSec, setRefreshSec] = useState(60);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [snapshot, setSnapshot] = useState<OiProfileSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState(false);

  useEffect(() => {
    api
      .get<OiProfileConfig>("/oi-profile/config", { silent: true })
      .then((c) => {
        setConfig(c);
        setRefreshSec(c.refresh_seconds);
        setIntervalState(c.default_interval);
        setDays(c.default_days);
      })
      .catch(() => {});
  }, []);

  const fetchSnapshot = useCallback(async () => {
    setLoading(true);
    setAuthError(false);
    try {
      const q = new URLSearchParams({ underlying, interval, days: String(days) });
      if (expiry) q.set("expiry", expiry);
      const data = await api.get<OiProfileSnapshot>(`/oi-profile/snapshot?${q}`);
      setSnapshot(data);
      if (data.meta?.available_expiries?.length) {
        setExpiries([...new Set(data.meta.available_expiries)]);
        if (!expiry && data.meta.expiry) setExpiry(data.meta.expiry);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("401") || msg.toLowerCase().includes("session")) setAuthError(true);
    } finally {
      setLoading(false);
    }
  }, [underlying, interval, days, expiry]);

  useEffect(() => {
    void fetchSnapshot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [underlying, interval, days, expiry]);

  useEffect(() => {
    if (!autoRefresh || authError) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSnapshot();
    }, refreshSec * 1000);
    return () => clearInterval(id);
  }, [autoRefresh, refreshSec, authError, fetchSnapshot]);

  const metaLine = useMemo(() => {
    if (!snapshot?.meta) return null;
    const m = snapshot.meta;
    return (
      <>
        {m.fut_symbol ? (
          <span className="font-mono font-semibold text-foreground">{m.fut_symbol}</span>
        ) : null}
        {" · "}
        {m.exchange} · lot {m.lot_size ?? "—"} · {m.interval} · {m.days}d
        {snapshot.stats?.last_bar ? ` · updated ${hhmm(snapshot.stats.last_bar)}` : ""}
      </>
    );
  }, [snapshot]);

  const onUnderlyingChange = (v: string) => {
    setUnderlying(v as OiProfileUnderlying);
    setExpiry("");
    setExpiries([]);
  };

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 pb-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">3ST Algo Desk — OI Profile</h1>
        <p className="text-sm text-muted-foreground">
          Index-futures candles with open interest, an OI-by-price butterfly (buildup vs unwinding),
          and day-over-day OI change. Requires Kite login.
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
            <Select value={underlying} onValueChange={onUnderlyingChange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(config?.underlyings ?? ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]).map((u) => (
                  <SelectItem key={u} value={u}>
                    {u}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Expiry (future)</Label>
            <Select value={expiry || undefined} onValueChange={setExpiry}>
              <SelectTrigger>
                <SelectValue placeholder="Front-month" />
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
            <Label>Interval</Label>
            <Select value={interval} onValueChange={(v) => setIntervalState(v as OiProfileInterval)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(config?.intervals ?? ["1min", "5min", "15min"]).map((iv) => (
                  <SelectItem key={iv} value={iv}>
                    {iv}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Days</Label>
            <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[1, 2, 3, 5, 10, 15, 20, 30].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}d
                  </SelectItem>
                ))}
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
          <label className="flex items-center gap-2 text-sm md:col-span-4">
            <Checkbox checked={autoRefresh} onCheckedChange={(v) => setAutoRefresh(Boolean(v))} />
            Auto-refresh
          </label>
        </CardContent>
      </Card>

      <div className="flex gap-2">
        <Button onClick={() => void fetchSnapshot()} disabled={loading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {loading ? "Loading…" : "Refresh now"}
        </Button>
        <Button variant="outline" onClick={() => setAutoRefresh((v) => !v)}>
          {autoRefresh ? <Pause className="mr-2 h-4 w-4" /> : <Play className="mr-2 h-4 w-4" />}
          {autoRefresh ? "Pause auto-refresh" : "Resume auto-refresh"}
        </Button>
      </div>

      {snapshot && !snapshot.empty ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <StatCard label="Price" value={fmt(snapshot.stats.current_price, 2)} tone="muted" />
            <StatCard label="Current OI" value={fmt(snapshot.stats.current_oi)} hint="contracts" tone="muted" />
            <StatCard
              label="Session ΔOI"
              value={fmtSigned(snapshot.stats.session_oi_change)}
              tone={(snapshot.stats.session_oi_change ?? 0) >= 0 ? "pos" : "neg"}
            />
            <StatCard label="POC (strike)" value={fmt(snapshot.poc_price, 0)} hint="max OI activity" tone="muted" />
            <StatCard
              label="Day read"
              value={snapshot.stats.day_interpretation ?? "—"}
              tone={
                (snapshot.stats.day_interpretation ?? "").includes("Long buildup") ||
                (snapshot.stats.day_interpretation ?? "").includes("Short covering")
                  ? "pos"
                  : (snapshot.stats.day_interpretation ?? "").includes("Short buildup") ||
                      (snapshot.stats.day_interpretation ?? "").includes("Long unwinding")
                    ? "neg"
                    : "muted"
              }
            />
          </div>

          {snapshot.stats.oi_walls?.length ? (
            <div className="flex flex-wrap gap-2 text-xs">
              {snapshot.stats.oi_walls.map((w) => (
                <Badge key={w} variant="outline" className="border-emerald-500/50">
                  OI wall: <span className="ml-1 font-mono">{fmt(w)}</span>
                </Badge>
              ))}
            </div>
          ) : null}

          <div className="grid gap-6 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader className="py-3">
                <CardTitle className="text-sm">Futures candles · OI overlay (line)</CardTitle>
              </CardHeader>
              <CardContent>
                <CandleOiChart snap={snapshot} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">
                  OI-by-strike butterfly · <span className="text-emerald-500">buildup</span> /{" "}
                  <span className="text-red-500">unwinding</span>
                  {snapshot.meta.strike_step ? (
                    <span className="ml-1 font-normal text-muted-foreground">
                      (per {snapshot.meta.strike_step}-pt strike)
                    </span>
                  ) : null}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ButterflyChart snap={snapshot} />
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm">Daily OI change</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow className="text-xs">
                    <TableHead>Date</TableHead>
                    <TableHead className="text-right">Close</TableHead>
                    <TableHead className="text-right">Price Δ%</TableHead>
                    <TableHead className="text-right">OI</TableHead>
                    <TableHead className="text-right">ΔOI</TableHead>
                    <TableHead className="text-right">ΔOI %</TableHead>
                    <TableHead>Interpretation</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[...snapshot.daily].reverse().map((r) => (
                    <TableRow key={r.date} className="text-xs font-mono">
                      <TableCell>{r.date}</TableCell>
                      <TableCell className="text-right">{r.close.toFixed(2)}</TableCell>
                      <TableCell
                        className={cn(
                          "text-right",
                          r.price_chg_pct >= 0 ? "text-emerald-500" : "text-red-500",
                        )}
                      >
                        {fmtSigned(r.price_chg_pct, 2)}%
                      </TableCell>
                      <TableCell className="text-right">{fmt(r.oi)}</TableCell>
                      <TableCell
                        className={cn(
                          "text-right",
                          r.oi_chg >= 0 ? "text-emerald-500" : "text-red-500",
                        )}
                      >
                        {fmtSigned(r.oi_chg)}
                      </TableCell>
                      <TableCell className="text-right">{fmtSigned(r.oi_chg_pct, 2)}%</TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={cn("text-[10px]", INTERP_TONE[r.interpretation] ?? "")}
                        >
                          {r.interpretation}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <p className="text-[10px] text-muted-foreground">
            OI from Kite futures history (oi=1). Butterfly snaps each bar to its nearest strike level
            and splits bar-over-bar OI increases (buildup) from decreases (unwinding); POC = strike
            with the most OI activity. Daily read: price↑&amp;OI↑ Long buildup · price↓&amp;OI↑ Short buildup ·
            price↑&amp;OI↓ Short covering · price↓&amp;OI↓ Long unwinding.
          </p>
        </>
      ) : snapshot?.empty ? (
        <p className="text-sm text-muted-foreground">
          {snapshot.message ?? "No futures data for this contract/interval."}
        </p>
      ) : (
        !loading && <p className="text-sm text-muted-foreground">Loading OI profile…</p>
      )}
    </div>
  );
}
