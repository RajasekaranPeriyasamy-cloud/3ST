import { createFileRoute, Link } from "@tanstack/react-router";
import {
  Component,
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ErrorInfo,
  type ReactNode,
} from "react";
import { Pause, Play, RefreshCw } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import type { OiUnderlying, VolSurfaceConfig, VolSurfaceSnapshot } from "@/lib/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const VolSurface3D = lazy(() => import("@/components/VolSurface3D"));

export const Route = createFileRoute("/vol-surface")({
  component: VolSurfacePage,
});

const UNDERLYINGS: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];
const SERIES_COLORS = [
  "#6366f1",
  "#22c55e",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#a855f7",
  "#ec4899",
  "#84cc16",
];

class Plot3DBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(_e: Error, _info: ErrorInfo) {}
  render() {
    if (this.state.failed) {
      return (
        <div className="py-10 text-center text-sm text-muted-foreground">
          3D view unavailable. Install its dependencies:
          <pre className="mt-2 inline-block rounded bg-muted px-2 py-1 text-xs">
            npm install plotly.js react-plotly.js
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

function ivColor(iv: number | null, lo: number, hi: number): string {
  if (iv == null) return "transparent";
  const t = hi === lo ? 0.5 : Math.max(0, Math.min(1, (iv - lo) / (hi - lo)));
  // Blue (low IV) → red (high IV)
  const hue = 220 * (1 - t);
  return `hsl(${hue}, 72%, 46%)`;
}

function Heatmap({ snap }: { snap: VolSurfaceSnapshot }) {
  const flat = snap.z.flat().filter((v): v is number => v != null);
  const lo = flat.length ? Math.min(...flat) : 0;
  const hi = flat.length ? Math.max(...flat) : 1;

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="border-separate border-spacing-0 text-[10px]">
          <thead>
            <tr>
              <th className="sticky left-0 z-10 bg-background px-2 py-1 text-left font-medium text-muted-foreground">
                DTE ＼ Strike
              </th>
              {snap.strikes.map((s) => (
                <th key={s} className="px-1.5 py-1 font-mono font-normal text-muted-foreground">
                  {s}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {snap.expiries.map((exp, i) => (
              <tr key={exp.expiry}>
                <td className="sticky left-0 z-10 bg-background px-2 py-1 font-mono text-muted-foreground">
                  {exp.dte}d
                </td>
                {snap.z[i].map((iv, j) => (
                  <td
                    key={j}
                    className="px-1.5 py-1 text-center font-mono text-white"
                    style={{ backgroundColor: ivColor(iv, lo, hi) }}
                    title={`${exp.expiry} · ${snap.strikes[j]} · IV ${iv ?? "—"}%`}
                  >
                    {iv != null ? iv.toFixed(1) : ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
        <span>{lo.toFixed(1)}%</span>
        <div
          className="h-2 w-40 rounded"
          style={{ background: "linear-gradient(90deg, hsl(220,72%,46%), hsl(0,72%,46%))" }}
        />
        <span>{hi.toFixed(1)}%</span>
      </div>
    </div>
  );
}

function Smiles({ snap }: { snap: VolSurfaceSnapshot }) {
  const data = snap.strikes.map((s, j) => {
    const row: Record<string, number | null> = { strike: s };
    snap.expiries.forEach((e, i) => {
      row[e.expiry] = snap.z[i][j];
    });
    return row;
  });
  return (
    <ResponsiveContainer width="100%" height={400}>
      <LineChart data={data} margin={{ top: 10, right: 20, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis dataKey="strike" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${v}%`} width={44} />
        <Tooltip
          formatter={(v: number, n: string) => [`${v?.toFixed?.(2)}%`, `${n} DTE`]}
          labelFormatter={(l) => `Strike ${l}`}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {snap.expiries.map((e, i) => (
          <Line
            key={e.expiry}
            type="monotone"
            dataKey={e.expiry}
            name={`${e.dte}d`}
            stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
            strokeWidth={2}
            dot={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function TermStructure({ snap }: { snap: VolSurfaceSnapshot }) {
  const data = snap.term_structure.map((t) => ({ dte: t.dte, atm_iv: t.atm_iv }));
  return (
    <ResponsiveContainer width="100%" height={360}>
      <LineChart data={data} margin={{ top: 10, right: 20, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis
          dataKey="dte"
          type="number"
          domain={["dataMin", "dataMax"]}
          tick={{ fontSize: 11 }}
          label={{ value: "Days to expiry", position: "insideBottom", offset: -2, fontSize: 11 }}
        />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${v}%`} width={44} />
        <Tooltip formatter={(v: number) => [`${v?.toFixed?.(2)}%`, "ATM IV"]} labelFormatter={(l) => `${l} DTE`} />
        <Line type="monotone" dataKey="atm_iv" name="ATM IV" stroke="#6366f1" strokeWidth={2} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}

function VolSurfacePage() {
  const [config, setConfig] = useState<VolSurfaceConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [strikeCount, setStrikeCount] = useState(15);
  const [maxExpiries, setMaxExpiries] = useState(6);
  const [refreshSec, setRefreshSec] = useState(120);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [snapshot, setSnapshot] = useState<VolSurfaceSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState(false);

  useEffect(() => {
    api
      .get<VolSurfaceConfig>("/vol-surface/config", { silent: true })
      .then((c) => {
        setConfig(c);
        setStrikeCount(c.strike_count);
        setMaxExpiries(c.max_expiries);
        setRefreshSec(c.refresh_seconds);
      })
      .catch(() => {});
  }, []);

  const fetchSnapshot = useCallback(async () => {
    setLoading(true);
    setAuthError(false);
    try {
      const q = new URLSearchParams({
        underlying,
        strike_count: String(strikeCount),
        max_expiries: String(maxExpiries),
      });
      const data = await api.get<VolSurfaceSnapshot>(`/vol-surface/snapshot?${q}`);
      setSnapshot(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("401") || msg.toLowerCase().includes("session")) setAuthError(true);
    } finally {
      setLoading(false);
    }
  }, [underlying, strikeCount, maxExpiries]);

  useEffect(() => {
    void fetchSnapshot();
  }, [underlying, strikeCount, maxExpiries]);

  useEffect(() => {
    if (!autoRefresh || authError) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSnapshot();
    }, refreshSec * 1000);
    return () => clearInterval(id);
  }, [autoRefresh, refreshSec, authError, fetchSnapshot]);

  const metaLine = useMemo(() => {
    if (!snapshot) return null;
    return (
      <>
        Spot <span className="font-mono font-semibold text-foreground">{snapshot.spot.toFixed(2)}</span>
        {" · "}ATM {snapshot.atm_strike}
        {" · "}
        {snapshot.expiries.length} expiries × {snapshot.strikes.length} strikes
        {" · "}
        {snapshot.legs_resolved} legs solved
        {" · "}Updated {new Date(snapshot.updated_at).toLocaleTimeString()}
      </>
    );
  }, [snapshot]);

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 pb-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">3ST Algo Desk — Volatility Surface</h1>
        <p className="text-sm text-muted-foreground">
          OTM implied-vol across strikes × expiries — heatmap, skew smiles, term structure and 3D
          surface. Requires Kite login.
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
            <Select value={underlying} onValueChange={(v) => setUnderlying(v as OiUnderlying)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {UNDERLYINGS.map((u) => (
                  <SelectItem key={u} value={u}>
                    {u}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Strikes (± ATM)</Label>
            <Select value={String(strikeCount)} onValueChange={(v) => setStrikeCount(Number(v))}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[9, 11, 15, 21, 31, 41].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Expiries</Label>
            <Select value={String(maxExpiries)} onValueChange={(v) => setMaxExpiries(Number(v))}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[2, 4, 6, 8].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
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
                {[60, 120, 180, 300].map((n) => (
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

      {snapshot ? (
        <Tabs defaultValue="heatmap">
          <TabsList>
            <TabsTrigger value="heatmap">Heatmap</TabsTrigger>
            <TabsTrigger value="smiles">Skew smiles</TabsTrigger>
            <TabsTrigger value="term">Term structure</TabsTrigger>
            <TabsTrigger value="surface">3D surface</TabsTrigger>
          </TabsList>
          <TabsContent value="heatmap">
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">IV heatmap (expiry × strike)</CardTitle>
              </CardHeader>
              <CardContent>
                <Heatmap snap={snapshot} />
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value="smiles">
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">Volatility skew by expiry</CardTitle>
              </CardHeader>
              <CardContent>
                <Smiles snap={snapshot} />
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value="term">
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">ATM term structure</CardTitle>
              </CardHeader>
              <CardContent>
                <TermStructure snap={snapshot} />
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value="surface">
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">3D implied-vol surface</CardTitle>
              </CardHeader>
              <CardContent>
                <Plot3DBoundary>
                  <Suspense
                    fallback={<div className="py-10 text-center text-sm text-muted-foreground">Loading 3D…</div>}
                  >
                    <VolSurface3D snap={snapshot} />
                  </Suspense>
                </Plot3DBoundary>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      ) : (
        !loading && <p className="text-sm text-muted-foreground">Refresh to load the surface.</p>
      )}

      <p className="text-[10px] text-muted-foreground">
        IV solved per strike from the OTM option's LTP (puts below spot, calls at/above) via
        Black-Scholes. Strikes indexed off ATM = round(spot / step). Blank cells = no solvable IV
        (illiquid/zero-bid strikes).
      </p>
    </div>
  );
}
