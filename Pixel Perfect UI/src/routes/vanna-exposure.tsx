import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import { pickNearestExpiry, useOptionExpiries } from "@/hooks/useOptionExpiries";
import type {
  OiUnderlying,
  VannaConfig,
  VannaRecommendation,
  VannaSnapshot,
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

export const Route = createFileRoute("/vanna-exposure")({
  component: VannaExposurePage,
});

const DEFAULT_UNDERLYINGS: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];
const POS = "#22c55e";
const NEG = "#ef4444";

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return Number(v).toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function numTick(v: unknown, digits = 1): string {
  if (v == null || typeof v !== "number" || Number.isNaN(v)) return "";
  return v.toFixed(digits);
}

function densTick(v: unknown): string {
  if (v == null || typeof v !== "number" || Number.isNaN(v)) return "";
  return Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v.toFixed(0);
}

function TradeIdeas({ ideas }: { ideas: VannaRecommendation[] }) {
  if (!ideas.length) return null;
  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-base">Trade ideas</CardTitle>
        <p className="text-xs text-muted-foreground">
          Dealer VEX / vol-up flow tilts with reasoning. Read-only — size premiums on Pricing
          Engine.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {ideas.map((idea) => (
          <div key={idea.id} className="rounded-md border px-3 py-3 space-y-2">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="font-medium text-sm">{idea.title}</div>
              <div className="flex flex-wrap gap-1">
                {idea.bias ? <Badge variant="secondary">{idea.bias}</Badge> : null}
                <Badge variant="outline">{idea.structure}</Badge>
              </div>
            </div>
            {idea.strikes_focus?.length ? (
              <div className="flex flex-wrap gap-2 text-[11px] font-mono">
                {idea.strikes_focus.map((s) => (
                  <span key={s} className="rounded bg-muted px-2 py-0.5">
                    focus {fmt(s, 1)}
                  </span>
                ))}
                {idea.vex_context?.regime ? (
                  <span className="rounded bg-muted px-2 py-0.5">
                    {idea.vex_context.regime} VEX
                  </span>
                ) : null}
              </div>
            ) : null}
            <p className="text-sm text-muted-foreground leading-snug">{idea.reasoning}</p>
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              {idea.pricing_hint ? (
                <span className="text-muted-foreground">{idea.pricing_hint}</span>
              ) : null}
              <Button variant="link" size="sm" className="h-auto p-0 text-[11px]" asChild>
                <Link to="/pricing-engine">Pricing Engine →</Link>
              </Button>
            </div>
            {idea.disclaimer ? (
              <p className="text-[10px] text-muted-foreground/80">{idea.disclaimer}</p>
            ) : null}
          </div>
        ))}
      </CardContent>
    </Card>
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

function VannaChart({ snap }: { snap: VannaSnapshot }) {
  const rows = snap.strikes ?? [];
  if (!rows.length) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">No strike data for chart.</p>
    );
  }
  const data = rows.map((r) => ({
    strike: r.strike,
    net_vex_cr: Number(r.net_vex_inr ?? 0) / 1e7,
    total_density: Number(r.total_density ?? 0),
  }));
  return (
    <ResponsiveContainer width="100%" height={380}>
      <ComposedChart data={data} margin={{ top: 10, right: 16, bottom: 10, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis
          dataKey="strike"
          type="number"
          domain={["dataMin", "dataMax"]}
          tick={{ fontSize: 11 }}
        />
        <YAxis
          yAxisId="vex"
          tick={{ fontSize: 11 }}
          tickFormatter={(v) => numTick(v, 1)}
          label={{
            value: "Net VEX (₹Cr / +1 vol pt)",
            angle: -90,
            position: "insideLeft",
            fontSize: 11,
          }}
        />
        <YAxis
          yAxisId="den"
          orientation="right"
          tick={{ fontSize: 11 }}
          tickFormatter={densTick}
        />
        <Tooltip
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const vexRaw = payload.find((p) => p.name === "Net VEX")?.value;
            const denRaw = payload.find((p) => p.name === "|vanna|×OI")?.value;
            const vex = typeof vexRaw === "number" ? vexRaw : undefined;
            const den = typeof denRaw === "number" ? denRaw : undefined;
            return (
              <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
                <div className="mb-1 border-b pb-1 font-semibold">Strike {label}</div>
                {vex != null ? (
                  <div className={vex >= 0 ? "text-emerald-600" : "text-red-600"}>
                    Net VEX: {numTick(vex, 2)} ₹Cr / +1 vol
                  </div>
                ) : null}
                {den != null ? <div className="text-indigo-500">|vanna|×OI: {fmt(den, 0)}</div> : null}
              </div>
            );
          }}
        />
        <ReferenceLine yAxisId="vex" y={0} stroke="currentColor" className="text-muted-foreground" />
        {typeof snap.spot === "number" && Number.isFinite(snap.spot) ? (
          <ReferenceLine
            yAxisId="vex"
            x={snap.spot}
            stroke="#3b82f6"
            strokeWidth={2}
            label={{ value: "Spot", fontSize: 10, fill: "#3b82f6", position: "top" }}
          />
        ) : null}
        {snap.vanna_line != null && Number.isFinite(snap.vanna_line) ? (
          <ReferenceLine
            yAxisId="vex"
            x={snap.vanna_line}
            stroke="#a855f7"
            strokeDasharray="4 4"
            label={{ value: "Vanna Line", fontSize: 10, fill: "#a855f7", position: "top" }}
          />
        ) : null}
        <Bar yAxisId="vex" dataKey="net_vex_cr" name="Net VEX" radius={[2, 2, 0, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.net_vex_cr >= 0 ? POS : NEG} />
          ))}
        </Bar>
        <Line
          yAxisId="den"
          type="monotone"
          dataKey="total_density"
          name="|vanna|×OI"
          stroke="#6366f1"
          strokeWidth={2}
          dot={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function VannaExposurePage() {
  const [config, setConfig] = useState<VannaConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState("");
  const { expiries } = useOptionExpiries(underlying);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [snapshot, setSnapshot] = useState<VannaSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<VannaConfig>("/vanna-exposure/config", { silent: true })
      .then(setConfig)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!expiries.length) return;
    setExpiry((current) => {
      if (current && expiries.includes(current)) return current;
      return pickNearestExpiry(expiries) ?? "";
    });
  }, [expiries, underlying]);

  const underlyings = config?.underlyings?.length
    ? (config.underlyings as OiUnderlying[])
    : DEFAULT_UNDERLYINGS;

  const fetchSnapshot = useCallback(async () => {
    setLoading(true);
    setAuthError(false);
    setLoadError(null);
    try {
      const q = new URLSearchParams({ underlying });
      if (expiry) q.set("expiry", expiry);
      const data = await api.get<VannaSnapshot>(`/vanna-exposure/snapshot?${q}`);
      setSnapshot(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("401") || msg.toLowerCase().includes("session")) setAuthError(true);
      else setLoadError(msg);
      setSnapshot(null);
    } finally {
      setLoading(false);
    }
  }, [underlying, expiry]);

  useEffect(() => {
    if (!expiry) return;
    void fetchSnapshot();
  }, [underlying, expiry, fetchSnapshot]);

  useEffect(() => {
    if (!autoRefresh || !expiry || authError || !config) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSnapshot();
    }, (config.refresh_seconds ?? 60) * 1000);
    return () => clearInterval(id);
  }, [autoRefresh, expiry, authError, config, fetchSnapshot]);

  const metaLine = useMemo(() => {
    if (!snapshot) return null;
    return (
      <>
        Spot{" "}
        <span className="font-mono font-semibold text-foreground">
          {fmt(snapshot.spot, 2)}
        </span>
        {" · "}ATM {snapshot.atm_strike ?? "—"}
        {" · "}ATM IV {snapshot.atm_iv != null ? `${snapshot.atm_iv}%` : "—"}
        {" · "}
        {snapshot.chain_legs_quoted ?? 0}/{snapshot.chain_legs_total ?? 0} legs
        {" · "}Updated{" "}
        {snapshot.updated_at ? new Date(snapshot.updated_at).toLocaleTimeString() : "—"}
      </>
    );
  }, [snapshot]);

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 pb-10 p-4 md:p-6">
      <header>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Vanna Exposure</h1>
            <p className="text-sm text-muted-foreground max-w-2xl">
              Dealer VEX (raw + ₹), Vanna Line, and IV-shock delta scenarios. Sign convention matches
              GEX (CE +, PE −). Read-only — does not drive 3ST execution.
            </p>
            {metaLine ? <p className="mt-1 text-xs text-muted-foreground">{metaLine}</p> : null}
          </div>
          <Badge variant="outline">Complementary</Badge>
        </div>
      </header>

      {authError ? (
        <Card className="border-destructive/50">
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

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <div className="space-y-1">
            <Label>Underlying</Label>
            <Select
              value={underlying}
              onValueChange={(v) => {
                setUnderlying(v as OiUnderlying);
                setExpiry("");
              }}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {underlyings.map((u) => (
                  <SelectItem key={u} value={u}>
                    {u}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
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
        </CardContent>
      </Card>

      {snapshot ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Net VEX (₹Cr)"
              value={fmt(snapshot.total_vex_cr, 2)}
              hint="Per +1 vol point"
              tone={(snapshot.total_vex_inr ?? 0) >= 0 ? "pos" : "neg"}
            />
            <StatCard
              label="Net VEX (raw)"
              value={fmt(snapshot.total_vex_raw, 0)}
              hint="vanna × OI × lot"
              tone={(snapshot.total_vex_raw ?? 0) >= 0 ? "pos" : "neg"}
            />
            <StatCard
              label="Vanna Line"
              value={snapshot.vanna_line != null ? fmt(snapshot.vanna_line, 1) : "—"}
              hint={
                snapshot.vanna_line != null && typeof snapshot.spot === "number"
                  ? snapshot.spot >= snapshot.vanna_line
                    ? "Spot at/above line"
                    : "Spot below line"
                  : "No zero crossing"
              }
            />
            <StatCard
              label="Regime"
              value={snapshot.vanna_regime ?? "—"}
              hint={`CE wall ${fmt(snapshot.call_wall, 0)} · PE wall ${fmt(snapshot.put_wall, 0)}`}
              tone={snapshot.vanna_regime === "positive" ? "pos" : "neg"}
            />
          </div>

          {snapshot.recommendations?.length ? (
            <TradeIdeas ideas={snapshot.recommendations} />
          ) : null}

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-base">IV shock scenarios</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2">
              {(snapshot.iv_shocks ?? []).map((s) => (
                <div
                  key={s.vol_points}
                  className="rounded-md border px-3 py-2 text-sm"
                >
                  <div className="font-medium">IV +{s.vol_points} vol pts</div>
                  <div className="font-mono text-xs text-muted-foreground mt-1">
                    Δδ ≈ {fmt(s.delta_shares, 0)} shares · {fmt(s.notional_cr, 2)} ₹Cr
                  </div>
                  <div
                    className={
                      s.direction === "dealers_buy_delta"
                        ? "text-emerald-600 text-xs mt-1"
                        : s.direction === "dealers_sell_delta"
                          ? "text-red-600 text-xs mt-1"
                          : "text-muted-foreground text-xs mt-1"
                    }
                  >
                    {s.direction === "dealers_buy_delta"
                      ? "Dealers buy delta (supportive on vol up)"
                      : s.direction === "dealers_sell_delta"
                        ? "Dealers sell delta (pressure on vol up)"
                        : "Flat"}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-base">VEX by strike</CardTitle>
            </CardHeader>
            <CardContent>
              <VannaChart snap={snapshot} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-base">Strike table</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Strike</TableHead>
                    <TableHead className="text-right">CE IV</TableHead>
                    <TableHead className="text-right">PE IV</TableHead>
                    <TableHead className="text-right">Net raw</TableHead>
                    <TableHead className="text-right">Net ₹</TableHead>
                    <TableHead className="text-right">₹Cr</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(snapshot.strikes ?? []).map((r) => {
                    const atm = r.strike === snapshot.atm_strike;
                    return (
                      <TableRow key={r.strike} className={atm ? "bg-muted/40" : undefined}>
                        <TableCell className="font-mono">
                          {fmt(r.strike, 0)}
                          {atm ? (
                            <Badge variant="secondary" className="ml-2 text-[10px]">
                              ATM
                            </Badge>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-right font-mono">{fmt(r.ce_iv)}</TableCell>
                        <TableCell className="text-right font-mono">{fmt(r.pe_iv)}</TableCell>
                        <TableCell className="text-right font-mono">{fmt(r.net_vex_raw, 0)}</TableCell>
                        <TableCell className="text-right font-mono">{fmt(r.net_vex_inr, 0)}</TableCell>
                        <TableCell className="text-right font-mono">
                          {fmt(
                            r.net_vex_inr != null ? r.net_vex_inr / 1e7 : null,
                            2,
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}
