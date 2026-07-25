import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
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
  GreeksStrikeRow,
  OiUnderlying,
  TradeSuggestion,
  TradeSuggestionsConfig,
  TradeSuggestionsSnapshot,
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

export const Route = createFileRoute("/trade-suggestions")({
  component: TradeSuggestionsPage,
});

const DEFAULT_UNDERLYINGS: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];

/** Bright desk chart palette */
const POS = "#059669";
const NEG = "#e11d48";
const CALL = "#d97706";
const PUT = "#0891b2";
const NET = "#7c3aed";
const SPOT_LINE = "#ea580c";
const FLIP_LINE = "#64748b";
const GRID = "#cbd5e1";
const TIP_BG = "#ffffff";
const TIP_BORDER = "#99f6e4";

type DeskTab = "overview" | "gamma" | "charm" | "vanna" | "ideas";

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return Number(v).toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function fmtCr(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const cr = Number(v) / 1e7;
  const sign = cr >= 0 ? "+" : "";
  return `${sign}${cr.toFixed(digits)} Cr`;
}

function pctFromSpot(level: number | null | undefined, spot: number | undefined): string {
  if (level == null || spot == null || !spot) return "";
  const pct = ((level - spot) / spot) * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}% from spot`;
}

function RegimeBar({ snap }: { snap: TradeSuggestionsSnapshot }) {
  const gammaPos = snap.regimes?.gamma === "positive";
  const gex = snap.portfolio_greeks?.total_gex;
  const pin = snap.levels?.pin_level ?? snap.atm_strike;
  const flip = snap.levels?.dynamic_flip_level ?? snap.levels?.flip_level;
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-primary/20 bg-gradient-to-r from-primary/10 via-card to-accent/20 px-4 py-2.5 text-xs font-medium tracking-wide shadow-sm">
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">REGIME</span>
        <span className={gammaPos ? "font-semibold text-emerald-600" : "font-semibold text-rose-600"}>
          {gammaPos ? "Positive Gamma" : "Negative Gamma"}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">NET GEX</span>
        <span className={(gex ?? 0) >= 0 ? "font-semibold text-emerald-600" : "font-semibold text-rose-600"}>
          {fmtCr(gex)}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">PIN</span>
        <span className="font-semibold text-foreground">{fmt(pin, 0)}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">FLIP</span>
        <span className="font-semibold text-amber-600">{flip != null ? fmt(flip, 0) : "—"}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">VANNA LINE</span>
        <span className="font-semibold text-cyan-700">
          {snap.levels?.vanna_line != null ? fmt(snap.levels.vanna_line, 0) : "—"}
        </span>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <span className="text-muted-foreground">SPOT</span>
        <span className="font-mono font-semibold text-orange-600">{fmt(snap.spot, 2)}</span>
      </div>
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
  tone?: "pos" | "neg" | "muted" | "warn";
}) {
  const color =
    tone === "pos"
      ? "text-emerald-600"
      : tone === "neg"
        ? "text-rose-600"
        : tone === "warn"
          ? "text-amber-600"
          : "text-foreground";
  return (
    <div className="rounded-md border border-primary/15 bg-card/90 px-3 py-3 shadow-sm shadow-primary/5">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={`mt-0.5 font-mono text-lg font-semibold ${color}`}>{value}</div>
      {hint ? <div className="mt-0.5 text-[10px] text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
        active
          ? "bg-primary/15 text-primary ring-1 ring-primary/40"
          : "text-muted-foreground hover:bg-secondary hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

function GexChart({
  rows,
  spot,
  callWall,
  putWall,
  flip,
}: {
  rows: GreeksStrikeRow[];
  spot: number;
  callWall?: number | null;
  putWall?: number | null;
  flip?: number | null;
}) {
  const data = rows.map((r) => ({
    strike: r.strike,
    call: Number(r.ce_gex || 0) / 1e7,
    put: Number(r.pe_gex || 0) / 1e7,
    net: Number(r.net_gex || 0) / 1e7,
  }));
  if (!data.length) {
    return <p className="py-10 text-center text-sm text-muted-foreground">No GEX strike data.</p>;
  }
  return (
    <ResponsiveContainer width="100%" height={360}>
      <ComposedChart data={data} margin={{ top: 12, right: 12, bottom: 8, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
        <XAxis dataKey="strike" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 10 }} />
        <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => `${Number(v).toFixed(1)}`} />
        <Tooltip
          contentStyle={{ background: TIP_BG, border: `1px solid ${TIP_BORDER}`, fontSize: 11, borderRadius: 8 }}
          formatter={(v: number, name: string) => [`${v.toFixed(2)} Cr`, name]}
        />
        <Bar dataKey="call" name="Call GEX" fill={CALL} opacity={0.85} />
        <Bar dataKey="put" name="Put GEX" fill={PUT} opacity={0.85} />
        <Line type="monotone" dataKey="net" name="Net GEX" stroke={NET} strokeWidth={2} dot={false} />
        <ReferenceLine x={spot} stroke={SPOT_LINE} strokeWidth={1.5} label={{ value: "Spot", fill: SPOT_LINE, fontSize: 10 }} />
        {flip != null ? (
          <ReferenceLine x={flip} stroke={FLIP_LINE} strokeDasharray="4 4" label={{ value: "Flip", fill: FLIP_LINE, fontSize: 10 }} />
        ) : null}
        {callWall != null ? (
          <ReferenceLine x={callWall} stroke={CALL} strokeDasharray="3 3" />
        ) : null}
        {putWall != null ? (
          <ReferenceLine x={putWall} stroke={PUT} strokeDasharray="3 3" />
        ) : null}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function CharmCharts({ rows, spot }: { rows: GreeksStrikeRow[]; spot: number }) {
  const data = rows.map((r) => ({
    strike: r.strike,
    net: Number(r.net_charm || 0),
    call: Number(r.ce_charm || 0),
    put: Number(r.pe_charm || 0),
  }));
  if (!data.length) {
    return <p className="py-10 text-center text-sm text-muted-foreground">No charm data.</p>;
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-md border border-primary/15 bg-card p-2 shadow-sm">
        <div className="mb-1 px-2 text-xs text-muted-foreground">Net Charm Exposure</div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
            <XAxis dataKey="strike" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} />
            <Tooltip contentStyle={{ background: TIP_BG, border: `1px solid ${TIP_BORDER}`, fontSize: 11, borderRadius: 8 }} />
            <Line type="monotone" dataKey="net" stroke={PUT} strokeWidth={2} dot={false} name="Net Charm" />
            <ReferenceLine x={spot} stroke={SPOT_LINE} strokeWidth={1.5} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="rounded-md border border-primary/15 bg-card p-2 shadow-sm">
        <div className="mb-1 px-2 text-xs text-muted-foreground">Call vs Put Charm</div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
            <XAxis dataKey="strike" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} />
            <Tooltip contentStyle={{ background: TIP_BG, border: `1px solid ${TIP_BORDER}`, fontSize: 11, borderRadius: 8 }} />
            <Line type="monotone" dataKey="call" stroke={POS} strokeWidth={2} dot={false} name="Call Charm" />
            <Line type="monotone" dataKey="put" stroke={NEG} strokeWidth={2} dot={false} name="Put Charm" />
            <ReferenceLine x={spot} stroke={SPOT_LINE} strokeWidth={1.5} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function VannaCharts({
  rows,
  spot,
  vannaLine,
}: {
  rows: GreeksStrikeRow[];
  spot: number;
  vannaLine?: number | null;
}) {
  const data = rows.map((r) => ({
    strike: r.strike,
    net: Number(r.net_vex_inr || 0) / 1e7,
    call: Number(r.ce_vanna || 0) / 1e7,
    put: Number(r.pe_vanna || 0) / 1e7,
  }));
  if (!data.length) {
    return <p className="py-10 text-center text-sm text-muted-foreground">No vanna data.</p>;
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-md border border-primary/15 bg-card p-2 shadow-sm">
        <div className="mb-1 px-2 text-xs text-muted-foreground">Net Vanna Exposure (₹Cr / +1 vol pt)</div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
            <XAxis dataKey="strike" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} />
            <Tooltip contentStyle={{ background: TIP_BG, border: `1px solid ${TIP_BORDER}`, fontSize: 11, borderRadius: 8 }} />
            <Line type="monotone" dataKey="net" stroke={PUT} strokeWidth={2} dot={false} name="Net Vanna" />
            <ReferenceLine x={spot} stroke={SPOT_LINE} strokeWidth={1.5} />
            {vannaLine != null ? (
              <ReferenceLine
                x={vannaLine}
                stroke={FLIP_LINE}
                strokeDasharray="4 4"
                label={{ value: "Vanna flip", fill: FLIP_LINE, fontSize: 10 }}
              />
            ) : null}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="rounded-md border border-primary/15 bg-card p-2 shadow-sm">
        <div className="mb-1 px-2 text-xs text-muted-foreground">Call vs Put Vanna</div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
            <XAxis dataKey="strike" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} />
            <Tooltip contentStyle={{ background: TIP_BG, border: `1px solid ${TIP_BORDER}`, fontSize: 11, borderRadius: 8 }} />
            <Line type="monotone" dataKey="call" stroke={NEG} strokeWidth={2} dot={false} name="Call Vanna" />
            <Line type="monotone" dataKey="put" stroke={POS} strokeWidth={2} dot={false} name="Put Vanna" />
            <ReferenceLine x={spot} stroke={SPOT_LINE} strokeWidth={1.5} />
            {vannaLine != null ? (
              <ReferenceLine x={vannaLine} stroke={FLIP_LINE} strokeDasharray="4 4" />
            ) : null}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function IdeaCard({ idea }: { idea: TradeSuggestion }) {
  const risk = idea.risk_profile || {};
  return (
    <div className="rounded-md border border-primary/20 bg-card px-4 py-3 space-y-2 shadow-sm shadow-primary/5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-foreground">{idea.title}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {idea.category ? <Badge variant="secondary">{idea.category}</Badge> : null}
            {idea.bias ? <Badge variant="outline">{idea.bias}</Badge> : null}
            <Badge variant="outline">{idea.structure}</Badge>
            {idea.score != null ? (
              <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100">
                score {fmt(idea.score, 1)}
              </Badge>
            ) : null}
          </div>
        </div>
        <Button variant="link" size="sm" className="h-auto p-0 text-[11px]" asChild>
          <Link to="/pricing-engine">Pricing Engine →</Link>
        </Button>
      </div>
      <p className="text-sm text-muted-foreground leading-snug">{idea.reasoning}</p>
      {idea.legs?.length ? (
        <div className="flex flex-wrap gap-1.5 font-mono text-[11px]">
          {idea.legs.map((leg, i) => (
            <span
              key={`${leg.side}-${leg.option_type}-${leg.strike}-${i}`}
              className={`rounded px-2 py-0.5 ${
                leg.side === "buy" ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"
              }`}
            >
              {leg.side.toUpperCase()} {leg.option_type} {fmt(leg.strike, 0)}
              {leg.tenor ? ` (${leg.tenor})` : ""}
            </span>
          ))}
        </div>
      ) : null}
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 text-[11px]">
        <div>
          <span className="text-muted-foreground">Max risk </span>
          <span className="font-mono">{risk.max_risk != null ? fmt(risk.max_risk) : "—"}</span>
        </div>
        <div>
          <span className="text-muted-foreground">Max return </span>
          <span className="font-mono">{risk.max_return != null ? fmt(risk.max_return) : "—"}</span>
        </div>
        <div>
          <span className="text-muted-foreground">POP </span>
          <span className="font-mono">
            {risk.pop != null ? `${(risk.pop * 100).toFixed(1)}%` : "—"}
          </span>
        </div>
        <div>
          <span className="text-muted-foreground">Δ/Γ/ν/θ </span>
          <span className="font-mono">
            {fmt(risk.net_delta, 2)}/{fmt(risk.net_gamma, 3)}/{fmt(risk.net_vega, 1)}/
            {fmt(risk.net_theta, 1)}
          </span>
        </div>
      </div>
      {idea.adjustment_rules?.length ? (
        <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">
          {idea.adjustment_rules.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function StrikeTable({ rows, spot, atm }: { rows: GreeksStrikeRow[]; spot: number; atm: number }) {
  const ranked = useMemo(
    () =>
      [...rows]
        .sort((a, b) => Math.abs(b.net_gex) - Math.abs(a.net_gex))
        .slice(0, 12)
        .map((r, i) => ({
          ...r,
          rank: i + 1,
          dist: r.strike - spot,
          side: r.strike >= spot ? "ABOVE" : "BELOW",
        })),
    [rows, spot],
  );
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>#</TableHead>
          <TableHead>Strike</TableHead>
          <TableHead className="text-right">Net GEX</TableHead>
          <TableHead className="text-right">Net VEX</TableHead>
          <TableHead className="text-right">Charm</TableHead>
          <TableHead className="text-right">Spot Dist</TableHead>
          <TableHead>Type</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ranked.map((r) => (
          <TableRow key={r.strike} className={r.strike === atm ? "bg-muted/30" : undefined}>
            <TableCell className="font-mono text-muted-foreground">{r.rank}</TableCell>
            <TableCell className="font-mono">
              {fmt(r.strike, 0)}
              {r.strike === atm ? (
                <Badge variant="secondary" className="ml-2 text-[10px]">
                  ATM
                </Badge>
              ) : null}
            </TableCell>
            <TableCell
              className={`text-right font-mono ${(r.net_gex || 0) >= 0 ? "text-emerald-600" : "text-rose-600"}`}
            >
              {fmtCr(r.net_gex)}
            </TableCell>
            <TableCell className="text-right font-mono">{fmtCr(r.net_vex_inr)}</TableCell>
            <TableCell className="text-right font-mono">{fmt(r.net_charm, 2)}</TableCell>
            <TableCell className="text-right font-mono">{fmt(r.dist, 0)}</TableCell>
            <TableCell>
              <Badge
                variant="outline"
                className={
                  r.side === "ABOVE"
                    ? "border-emerald-500/40 text-emerald-700"
                    : "border-rose-500/40 text-rose-700"
                }
              >
                {r.side}
              </Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function TradeSuggestionsPage() {
  const [config, setConfig] = useState<TradeSuggestionsConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<TradeSuggestionsSnapshot | null>(null);
  const [tab, setTab] = useState<DeskTab>("ideas");

  const underlyings = useMemo(() => {
    const fromCfg = (config?.underlyings?.length ? config.underlyings : DEFAULT_UNDERLYINGS) as OiUnderlying[];
    // Indices only — never show commodity underlyings on this desk
    const allowed = new Set(DEFAULT_UNDERLYINGS);
    const filtered = fromCfg.filter((u) => allowed.has(u as OiUnderlying));
    return filtered.length ? filtered : DEFAULT_UNDERLYINGS;
  }, [config?.underlyings]);
  const { expiries } = useOptionExpiries(underlying);

  useEffect(() => {
    void api
      .get<TradeSuggestionsConfig>("/trade-suggestions/config")
      .then(setConfig)
      .catch(() => setConfig(null));
  }, []);

  // Always re-pick nearest expiry for the *current* underlying (never reuse NIFTY's date on SENSEX).
  useEffect(() => {
    if (!expiries.length) {
      setExpiry("");
      return;
    }
    setExpiry(pickNearestExpiry(expiries) || expiries[0] || "");
  }, [underlying, expiries]);

  const fetchSnapshot = useCallback(async () => {
    // Guard: do not call API with another index's leftover expiry
    if (!expiry || !expiries.length || !expiries.includes(expiry)) return;
    setLoading(true);
    setLoadError(null);
    setAuthError(false);
    try {
      const snap = await api.get<TradeSuggestionsSnapshot>(
        `/trade-suggestions/snapshot?underlying=${underlying}&expiry=${expiry}`,
      );
      setSnapshot(snap);
    } catch (e: unknown) {
      const status = (e as { status?: number })?.status;
      if (status === 401) setAuthError(true);
      setLoadError(e instanceof Error ? e.message : "Failed to load trade suggestions");
      setSnapshot(null);
    } finally {
      setLoading(false);
    }
  }, [underlying, expiry, expiries]);

  useEffect(() => {
    void fetchSnapshot();
  }, [fetchSnapshot]);

  useEffect(() => {
    if (!autoRefresh) return;
    const sec = config?.refresh_seconds ?? 60;
    const id = window.setInterval(() => void fetchSnapshot(), sec * 1000);
    return () => window.clearInterval(id);
  }, [autoRefresh, config?.refresh_seconds, fetchSnapshot]);

  const strikes = snapshot?.greeks_snapshot?.strikes ?? [];
  const gammaPos = snapshot?.regimes?.gamma === "positive";

  return (
    <div className="flex flex-col gap-3 p-4 md:p-6">
      {/* Hedgewall-style index bar — top, not a dropdown */}
      <div className="flex flex-wrap items-center justify-center gap-2 rounded-md border border-primary/20 bg-card/90 px-3 py-2 shadow-sm">
        {underlyings.map((u) => (
          <button
            key={u}
            type="button"
            onClick={() => {
              if (u === underlying) return;
              setSnapshot(null);
              setLoadError(null);
              setExpiry("");
              setUnderlying(u);
            }}
            className={`min-w-[6.5rem] rounded-md px-4 py-2 text-sm font-semibold tracking-wide transition-colors ${
              underlying === u
                ? "bg-primary text-primary-foreground shadow-sm shadow-primary/30"
                : "bg-secondary/70 text-secondary-foreground hover:bg-accent hover:text-accent-foreground"
            }`}
          >
            {u}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Trade Suggestions</h1>
          <p className="text-xs text-muted-foreground">
            Higher-order Greeks · GEX/VEX · Hedgewall-style flows desk
          </p>
        </div>
        <div className="font-mono text-xs text-muted-foreground">
          {underlying}
          {expiry ? ` · ${expiry}` : ""}
        </div>
      </div>

      {authError ? (
        <Card className="border-destructive/40">
          <CardContent className="py-4 text-sm">
            Kite session required.{" "}
            <Link to="/login" className="text-primary underline">
              Log in
            </Link>
          </CardContent>
        </Card>
      ) : null}

      {loadError && !authError ? (
        <Card className="border-destructive/50">
          <CardContent className="py-4 text-sm text-destructive">{loadError}</CardContent>
        </Card>
      ) : null}

      <Card className="border-border/50 bg-background/40">
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <div className="space-y-1">
            <Label>Expiry</Label>
            <Select value={expiry} onValueChange={setExpiry}>
              <SelectTrigger className="w-[150px]">
                <SelectValue placeholder="Expiry" />
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
          <label className="flex items-center gap-2 pb-2 text-sm">
            <Checkbox checked={autoRefresh} onCheckedChange={(c) => setAutoRefresh(c === true)} />
            Auto refresh
          </label>
          <Button variant="outline" size="sm" onClick={() => void fetchSnapshot()} disabled={loading}>
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setAutoRefresh((a) => !a)}>
            {autoRefresh ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          </Button>
          <Button variant="link" size="sm" asChild>
            <Link to="/gamma-density">Gamma Density →</Link>
          </Button>
          <Button variant="link" size="sm" asChild>
            <Link to="/vanna-exposure">Vanna Exposure →</Link>
          </Button>
        </CardContent>
      </Card>

      {snapshot ? (
        <>
          <RegimeBar snap={snapshot} />

          <div className="flex flex-wrap gap-1.5">
            <TabBtn active={tab === "overview"} onClick={() => setTab("overview")}>
              Overview
            </TabBtn>
            <TabBtn active={tab === "gamma"} onClick={() => setTab("gamma")}>
              Gamma
            </TabBtn>
            <TabBtn active={tab === "charm"} onClick={() => setTab("charm")}>
              Charm
            </TabBtn>
            <TabBtn active={tab === "vanna"} onClick={() => setTab("vanna")}>
              Vanna
            </TabBtn>
            <TabBtn active={tab === "ideas"} onClick={() => setTab("ideas")}>
              Trade Ideas
            </TabBtn>
          </div>

          {tab === "overview" ? (
            <div className="space-y-3">
              <div>
                <h2 className="text-lg font-semibold">
                  {snapshot.signals?.gamma ?? (gammaPos ? "Positive Gamma" : "Negative Gamma")}
                </h2>
                <p className="text-sm text-muted-foreground">
                  Vol regime: {snapshot.regimes?.vol ?? "—"} · IV flow:{" "}
                  {snapshot.regimes?.iv_flow ?? "—"}
                  {snapshot.weekend_bleed_window ? " · Weekend theta window active" : ""}
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label="Net GEX"
                  value={fmtCr(snapshot.portfolio_greeks?.total_gex)}
                  tone={(snapshot.portfolio_greeks?.total_gex ?? 0) >= 0 ? "pos" : "neg"}
                  hint={snapshot.regimes?.vol === "dampening" ? "Dampening" : "Amplifying"}
                />
                <StatCard
                  label="Net VEX"
                  value={`${fmt(snapshot.portfolio_greeks?.total_vex_cr, 2)} Cr`}
                  tone={(snapshot.portfolio_greeks?.total_vex_cr ?? 0) >= 0 ? "pos" : "neg"}
                  hint="Per +1 vol point"
                />
                <StatCard
                  label="Dynamic Flip"
                  value={
                    snapshot.levels?.dynamic_flip_level != null
                      ? fmt(snapshot.levels.dynamic_flip_level, 0)
                      : "—"
                  }
                  tone="warn"
                  hint={pctFromSpot(snapshot.levels?.dynamic_flip_level, snapshot.spot)}
                />
                <StatCard
                  label="Dealer θ / ν"
                  value={`${fmt(snapshot.portfolio_greeks?.net_theta, 1)} / ${fmt(snapshot.portfolio_greeks?.net_vega, 1)}`}
                  hint={`Speed ${fmt(snapshot.portfolio_greeks?.total_speed, 4)} · Vomma ${fmt(snapshot.portfolio_greeks?.total_vomma, 2)}`}
                />
              </div>
              <div className="grid gap-3 lg:grid-cols-3">
                <div className="rounded-md border border-primary/15 bg-card p-3 shadow-sm lg:col-span-2">
                  <div className="mb-2 text-sm font-medium">GEX by strike</div>
                  <GexChart
                    rows={strikes}
                    spot={snapshot.spot}
                    callWall={snapshot.levels?.call_wall}
                    putWall={snapshot.levels?.put_wall}
                    flip={snapshot.levels?.dynamic_flip_level ?? snapshot.levels?.flip_level}
                  />
                </div>
                <div className="space-y-3">
                  <div className="rounded-md border border-primary/15 bg-card p-3 shadow-sm">
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      Closest levels
                    </div>
                    <div className="mt-2 space-y-2 text-sm">
                      <div>
                        <span className="font-medium text-amber-600">Call Wall </span>
                        <span className="font-mono">{fmt(snapshot.levels?.call_wall, 0)}</span>
                        <div className="text-[10px] text-muted-foreground">
                          {pctFromSpot(snapshot.levels?.call_wall, snapshot.spot)}
                        </div>
                      </div>
                      <div>
                        <span className="font-medium text-cyan-700">Put Wall </span>
                        <span className="font-mono">{fmt(snapshot.levels?.put_wall, 0)}</span>
                        <div className="text-[10px] text-muted-foreground">
                          {pctFromSpot(snapshot.levels?.put_wall, snapshot.spot)}
                        </div>
                      </div>
                      <div>
                        <span className="font-medium text-emerald-600">Pin </span>
                        <span className="font-mono">{fmt(snapshot.levels?.pin_level, 0)}</span>
                      </div>
                    </div>
                  </div>
                  <div className="rounded-md border border-amber-400/40 bg-amber-50 p-3">
                    <div className="text-[10px] uppercase tracking-wider text-amber-700">Cliff alert</div>
                    <div className="mt-1 text-sm font-medium">
                      Flip @ {fmt(snapshot.levels?.dynamic_flip_level ?? snapshot.levels?.flip_level, 0)}
                    </div>
                    <div className="text-[11px] text-muted-foreground">
                      Speed-refined gamma flip — hedging regime may accelerate through this level.
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {tab === "gamma" ? (
            <div className="space-y-3">
              <h2 className="text-lg font-semibold">{snapshot.signals?.gamma}</h2>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label="Net GEX"
                  value={fmtCr(snapshot.portfolio_greeks?.total_gex)}
                  tone={gammaPos ? "pos" : "neg"}
                />
                <StatCard
                  label="Speed (dΓ/dS)"
                  value={fmt(snapshot.portfolio_greeks?.total_speed, 5)}
                  hint="Dealer gamma acceleration"
                />
                <StatCard
                  label="Classic Flip"
                  value={fmt(snapshot.levels?.flip_level, 0)}
                />
                <StatCard
                  label="Dynamic Flip"
                  value={fmt(snapshot.levels?.dynamic_flip_level, 0)}
                  tone="warn"
                  hint="GEX + Speed refinement"
                />
              </div>
              <Card className="border-primary/15 bg-card/95 shadow-sm">
                <CardHeader className="py-3">
                  <CardTitle className="text-base">Gamma Exposure by Strike</CardTitle>
                </CardHeader>
                <CardContent>
                  <GexChart
                    rows={strikes}
                    spot={snapshot.spot}
                    callWall={snapshot.levels?.call_wall}
                    putWall={snapshot.levels?.put_wall}
                    flip={snapshot.levels?.dynamic_flip_level ?? snapshot.levels?.flip_level}
                  />
                </CardContent>
              </Card>
              <Card className="border-primary/15 bg-card/95 shadow-sm">
                <CardHeader className="py-3">
                  <CardTitle className="text-base">Cliff Detail</CardTitle>
                </CardHeader>
                <CardContent className="overflow-x-auto">
                  <StrikeTable rows={strikes} spot={snapshot.spot} atm={snapshot.atm_strike} />
                </CardContent>
              </Card>
            </div>
          ) : null}

          {tab === "charm" ? (
            <div className="space-y-3">
              <div>
                <h2 className="text-lg font-semibold">{snapshot.signals?.charm}</h2>
                <p className="text-sm text-muted-foreground">{snapshot.signals?.charm_detail}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label="Live Charm"
                  value={fmt(snapshot.charm_sides?.net, 2)}
                  tone={(snapshot.charm_sides?.net ?? 0) >= 0 ? "pos" : "neg"}
                  hint={(snapshot.charm_sides?.net ?? 0) >= 0 ? "buy pressure" : "sell pressure"}
                />
                <StatCard
                  label="Call Side"
                  value={fmt(snapshot.charm_sides?.call, 2)}
                  tone="pos"
                  hint="growing / decaying vs puts"
                />
                <StatCard
                  label="Put Side"
                  value={fmt(snapshot.charm_sides?.put, 2)}
                  tone="neg"
                />
                <StatCard
                  label="Peak Strike"
                  value={fmt(snapshot.charm_sides?.peak_strike, 0)}
                  hint={pctFromSpot(snapshot.charm_sides?.peak_strike, snapshot.spot)}
                />
              </div>
              <CharmCharts rows={strikes} spot={snapshot.spot} />
            </div>
          ) : null}

          {tab === "vanna" ? (
            <div className="space-y-3">
              <div>
                <h2 className="text-lg font-semibold">{snapshot.signals?.vanna}</h2>
                <p className="text-sm text-muted-foreground">{snapshot.signals?.vanna_detail}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label="Net Vanna"
                  value={`${fmt(snapshot.vanna_sides?.net_cr, 2)} Cr`}
                  tone={(snapshot.vanna_sides?.net_cr ?? 0) >= 0 ? "pos" : "neg"}
                  hint={(snapshot.vanna_sides?.net_cr ?? 0) >= 0 ? "Long" : "Short"}
                />
                <StatCard
                  label="Top Vol-Sensitive"
                  value={fmt(snapshot.vanna_sides?.peak_strike, 0)}
                  hint={pctFromSpot(snapshot.vanna_sides?.peak_strike, snapshot.spot)}
                />
                <StatCard
                  label="Δ from +1pt IV"
                  value={`${fmt(snapshot.vanna_sides?.delta_from_1pt_iv_cr, 2)} Cr`}
                  hint="Dealer delta rehedge notional"
                />
                <StatCard
                  label="IV Flow"
                  value={snapshot.regimes?.iv_flow ?? "—"}
                  hint={`Vomma ${fmt(snapshot.portfolio_greeks?.total_vomma, 2)}`}
                />
              </div>
              <VannaCharts
                rows={strikes}
                spot={snapshot.spot}
                vannaLine={snapshot.levels?.vanna_line}
              />
            </div>
          ) : null}

          {tab === "ideas" ? (
            <div className="space-y-3">
              <div>
                <h2 className="text-lg font-semibold">Actionable structures</h2>
                <p className="text-sm text-muted-foreground">
                  Ranked from GEX + VEX + Vanna/Gamma/Charm/Speed. Read-only — size on Pricing Engine.
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label="Ideas"
                  value={String(snapshot.suggestions?.length ?? 0)}
                  hint={snapshot.weekend_bleed_window ? "Weekend bleed window" : "Intraday"}
                />
                <StatCard
                  label="Hot Zone"
                  value={
                    snapshot.hot_zones?.[0]
                      ? fmt(snapshot.hot_zones[0].strike, 0)
                      : "—"
                  }
                  hint="Peak |Vanna×Gamma|"
                  tone="warn"
                />
                <StatCard
                  label="Call Wall"
                  value={fmt(snapshot.levels?.call_wall, 0)}
                />
                <StatCard
                  label="Put Wall"
                  value={fmt(snapshot.levels?.put_wall, 0)}
                />
              </div>
              <div className="flex flex-col gap-3">
                {(snapshot.suggestions ?? []).map((idea) => (
                  <IdeaCard key={idea.id} idea={idea} />
                ))}
                {!snapshot.suggestions?.length ? (
                  <p className="py-8 text-center text-sm text-muted-foreground">
                    No structures scored for this expiry — check session / chain quotes.
                  </p>
                ) : null}
              </div>
              {snapshot.disclaimer ? (
                <p className="text-[10px] text-muted-foreground/80">{snapshot.disclaimer}</p>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
