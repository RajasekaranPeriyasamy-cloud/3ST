import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Calculator, Pause, Play, RefreshCw } from "lucide-react";

import { api } from "@/lib/api";
import { pickNearestExpiry, useOptionExpiries } from "@/hooks/useOptionExpiries";
import type {
  OiUnderlying,
  PricingCalcResult,
  PricingDeskSnapshot,
  PricingEngineConfig,
  PricingRecommendation,
} from "@/lib/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
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

export const Route = createFileRoute("/pricing-engine")({
  component: PricingEnginePage,
});

const DEFAULT_UNDERLYINGS: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function edgeTone(v: number | null | undefined): string {
  if (v == null) return "";
  if (v > 0.5) return "text-amber-600 dark:text-amber-400";
  if (v < -0.5) return "text-emerald-600 dark:text-emerald-400";
  return "text-muted-foreground";
}

function TradeIdeas({ ideas }: { ideas: PricingRecommendation[] }) {
  if (!ideas.length) return null;
  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-base">Trade ideas</CardTitle>
        <p className="text-xs text-muted-foreground">
          Ranked from BS edge (LTP − fair @ ATM IV). Defined-risk spreads only. Read-only —
          does not place orders.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {ideas.map((idea) => (
          <div key={idea.id} className="rounded-md border px-3 py-3 space-y-2">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="font-medium text-sm">{idea.title}</div>
              <div className="flex flex-wrap gap-1">
                <Badge variant="outline">{idea.action}</Badge>
                {idea.bias ? <Badge variant="secondary">{idea.bias}</Badge> : null}
              </div>
            </div>
            <div className="flex flex-wrap gap-2 text-[11px] font-mono">
              <span className="rounded bg-muted px-2 py-0.5">
                {idea.action === "credit" ? "Credit" : "Debit"} {fmt(idea.net_premium)}
              </span>
              <span className="rounded bg-muted px-2 py-0.5">
                Max profit {fmt(idea.max_profit)}
              </span>
              <span className="rounded bg-muted px-2 py-0.5">
                Max loss {fmt(idea.max_loss)}
              </span>
              <span className="rounded bg-muted px-2 py-0.5">
                BE {fmt(idea.breakeven, 1)}
              </span>
              <span className="rounded bg-muted px-2 py-0.5">
                ₹{fmt(idea.net_premium_inr, 0)}/lot
              </span>
              {idea.edge_vs_fair != null ? (
                <span className={`rounded bg-muted px-2 py-0.5 ${edgeTone(idea.edge_vs_fair)}`}>
                  vs fair {fmt(idea.edge_vs_fair)}
                </span>
              ) : null}
            </div>
            <p className="text-sm text-muted-foreground leading-snug">{idea.reasoning}</p>
            {idea.disclaimer ? (
              <p className="text-[10px] text-muted-foreground/80">{idea.disclaimer}</p>
            ) : null}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function PricingEnginePage() {
  const [config, setConfig] = useState<PricingEngineConfig | null>(null);
  const [tab, setTab] = useState("desk");

  // Live desk
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState("");
  const [snap, setSnap] = useState<PricingDeskSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [auto, setAuto] = useState(true);
  const [includeHeston, setIncludeHeston] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Calculator
  const [spot, setSpot] = useState("24000");
  const [strike, setStrike] = useState("24000");
  const [optType, setOptType] = useState<"CE" | "PE">("CE");
  const [mktPx, setMktPx] = useState("150");
  const [ivPct, setIvPct] = useState("14");
  const [tteDays, setTteDays] = useState("7");
  const [calcHeston, setCalcHeston] = useState(true);
  const [calcResult, setCalcResult] = useState<PricingCalcResult | null>(null);
  const [calcError, setCalcError] = useState<string | null>(null);
  const [calcLoading, setCalcLoading] = useState(false);

  const { expiries } = useOptionExpiries(underlying);
  const underlyings = config?.underlyings?.length ? config.underlyings : DEFAULT_UNDERLYINGS;

  useEffect(() => {
    api
      .get<PricingEngineConfig>("/pricing/config", { silent: true })
      .then(setConfig)
      .catch(() => {});
  }, []);

  useEffect(() => {
    const next = pickNearestExpiry(expiries);
    if (next) setExpiry(next);
  }, [expiries]);

  const refreshDesk = useCallback(async () => {
    if (!expiry) return;
    setLoading(true);
    setError(null);
    try {
      const q = new URLSearchParams({
        underlying,
        expiry,
        include_heston: includeHeston ? "true" : "false",
      });
      if (config?.strike_count) q.set("strike_count", String(config.strike_count));
      const data = await api.get<PricingDeskSnapshot>(`/pricing/desk?${q}`, { silent: true });
      setSnap(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load pricing desk");
    } finally {
      setLoading(false);
    }
  }, [underlying, expiry, includeHeston, config?.strike_count]);

  useEffect(() => {
    if (tab !== "desk") return;
    void refreshDesk();
  }, [refreshDesk, tab]);

  useEffect(() => {
    if (!auto || !config || tab !== "desk") return;
    const id = window.setInterval(
      () => void refreshDesk(),
      (config.refresh_seconds ?? 30) * 1000,
    );
    return () => window.clearInterval(id);
  }, [auto, config, refreshDesk, tab]);

  const runCalc = async () => {
    setCalcLoading(true);
    setCalcError(null);
    try {
      const body = {
        spot: Number(spot),
        strike: Number(strike),
        option_type: optType,
        market_price: mktPx ? Number(mktPx) : null,
        iv: ivPct ? Number(ivPct) : null,
        tte_years: Number(tteDays) / 365,
        risk_free_rate: config?.risk_free_rate ?? 0.065,
        include_heston: calcHeston,
      };
      const data = await api.post<PricingCalcResult>("/pricing/calculate", body);
      setCalcResult(data);
    } catch (e) {
      setCalcError(e instanceof Error ? e.message : "Calculate failed");
    } finally {
      setCalcLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Pricing Engine</h1>
          <p className="text-sm text-muted-foreground max-w-2xl">
            Complementary BS IV / Greeks and optional Heston-COS fair value. Read-only
            analytics — does not arm, order, or change the 3ST / Rolling Straddle engine.
          </p>
        </div>
        <Badge variant="outline" className="gap-1">
          <Calculator className="h-3 w-3" />
          Complementary
        </Badge>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="desk">Live desk</TabsTrigger>
          <TabsTrigger value="calc">Calculator</TabsTrigger>
        </TabsList>

        <TabsContent value="desk" className="flex flex-col gap-4">
          <Card>
            <CardContent className="flex flex-wrap items-end gap-3 pt-4">
              <div className="space-y-1">
                <Label>Underlying</Label>
                <Select
                  value={underlying}
                  onValueChange={(v) => setUnderlying(v as OiUnderlying)}
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
                <Checkbox
                  checked={includeHeston}
                  onCheckedChange={(c) => setIncludeHeston(c === true)}
                />
                Heston-COS column
              </label>
              <label className="flex items-center gap-2 pb-2 text-sm">
                <Checkbox checked={auto} onCheckedChange={(c) => setAuto(c === true)} />
                Auto refresh
              </label>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void refreshDesk()}
                disabled={loading || !expiry}
              >
                <RefreshCw className={`mr-1 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                Refresh
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setAuto((a) => !a)}>
                {auto ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
              </Button>
            </CardContent>
          </Card>

          {error ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm">
              {error}{" "}
              <Link to="/login" className="underline">
                Login
              </Link>{" "}
              if session expired.
            </div>
          ) : null}

          {snap ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardContent className="py-3">
                  <div className="text-[11px] uppercase text-muted-foreground">Spot</div>
                  <div className="font-mono text-lg font-semibold">{fmt(snap.spot, 2)}</div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="py-3">
                  <div className="text-[11px] uppercase text-muted-foreground">ATM</div>
                  <div className="font-mono text-lg font-semibold">{fmt(snap.atm_strike, 0)}</div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="py-3">
                  <div className="text-[11px] uppercase text-muted-foreground">ATM IV</div>
                  <div className="font-mono text-lg font-semibold">
                    {snap.atm_iv != null ? `${fmt(snap.atm_iv, 2)}%` : "—"}
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="py-3">
                  <div className="text-[11px] uppercase text-muted-foreground">Updated</div>
                  <div className="font-mono text-sm">
                    {new Date(snap.updated_at).toLocaleTimeString()}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    BS fair @ flat ATM IV · edge = LTP − fair
                  </div>
                </CardContent>
              </Card>
            </div>
          ) : null}

          {snap?.recommendations?.length ? (
            <TradeIdeas ideas={snap.recommendations} />
          ) : null}

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-base">Strike matrix</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Strike</TableHead>
                    <TableHead className="text-right">CE LTP</TableHead>
                    <TableHead className="text-right">CE IV%</TableHead>
                    <TableHead className="text-right">CE BS</TableHead>
                    <TableHead className="text-right">CE edge</TableHead>
                    {includeHeston ? (
                      <TableHead className="text-right">CE Heston</TableHead>
                    ) : null}
                    <TableHead className="text-right">PE LTP</TableHead>
                    <TableHead className="text-right">PE IV%</TableHead>
                    <TableHead className="text-right">PE BS</TableHead>
                    <TableHead className="text-right">PE edge</TableHead>
                    {includeHeston ? (
                      <TableHead className="text-right">PE Heston</TableHead>
                    ) : null}
                    <TableHead className="text-right">Straddle edge</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(snap?.rows ?? []).map((row) => {
                    const isAtm = snap && row.strike === snap.atm_strike;
                    return (
                      <TableRow key={row.strike} className={isAtm ? "bg-muted/40" : undefined}>
                        <TableCell className="font-mono font-medium">
                          {fmt(row.strike, 0)}
                          {isAtm ? (
                            <Badge variant="secondary" className="ml-2 text-[10px]">
                              ATM
                            </Badge>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-right font-mono">{fmt(row.ce.ltp)}</TableCell>
                        <TableCell className="text-right font-mono">{fmt(row.ce.iv)}</TableCell>
                        <TableCell className="text-right font-mono">{fmt(row.ce.bs_fair)}</TableCell>
                        <TableCell className={`text-right font-mono ${edgeTone(row.ce.edge)}`}>
                          {fmt(row.ce.edge)}
                        </TableCell>
                        {includeHeston ? (
                          <TableCell className="text-right font-mono">
                            {fmt(row.ce.heston_fair)}
                          </TableCell>
                        ) : null}
                        <TableCell className="text-right font-mono">{fmt(row.pe.ltp)}</TableCell>
                        <TableCell className="text-right font-mono">{fmt(row.pe.iv)}</TableCell>
                        <TableCell className="text-right font-mono">{fmt(row.pe.bs_fair)}</TableCell>
                        <TableCell className={`text-right font-mono ${edgeTone(row.pe.edge)}`}>
                          {fmt(row.pe.edge)}
                        </TableCell>
                        {includeHeston ? (
                          <TableCell className="text-right font-mono">
                            {fmt(row.pe.heston_fair)}
                          </TableCell>
                        ) : null}
                        <TableCell
                          className={`text-right font-mono ${edgeTone(row.straddle_edge)}`}
                        >
                          {fmt(row.straddle_edge)}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              {!snap && !loading ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  Connect Kite and refresh to load the live desk.
                </p>
              ) : null}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="calc" className="flex flex-col gap-4">
          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-base">Manual BS / Heston calculator</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1">
                <Label>Spot</Label>
                <Input value={spot} onChange={(e) => setSpot(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>Strike</Label>
                <Input value={strike} onChange={(e) => setStrike(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>Type</Label>
                <Select value={optType} onValueChange={(v) => setOptType(v as "CE" | "PE")}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="CE">CE</SelectItem>
                    <SelectItem value="PE">PE</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Market price (LTP)</Label>
                <Input value={mktPx} onChange={(e) => setMktPx(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>Model IV %</Label>
                <Input value={ivPct} onChange={(e) => setIvPct(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>Days to expiry</Label>
                <Input value={tteDays} onChange={(e) => setTteDays(e.target.value)} />
              </div>
              <label className="flex items-center gap-2 self-end pb-2 text-sm">
                <Checkbox
                  checked={calcHeston}
                  onCheckedChange={(c) => setCalcHeston(c === true)}
                />
                Include Heston-COS
              </label>
              <div className="self-end">
                <Button onClick={() => void runCalc()} disabled={calcLoading}>
                  {calcLoading ? "Calculating…" : "Calculate"}
                </Button>
              </div>
            </CardContent>
          </Card>

          {calcError ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm">
              {calcError}
            </div>
          ) : null}

          {calcResult ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Card>
                <CardHeader className="py-2">
                  <CardTitle className="text-sm">Black–Scholes</CardTitle>
                </CardHeader>
                <CardContent className="space-y-1 font-mono text-sm">
                  <div>IV (from LTP): {fmt(calcResult.bs.iv)}%</div>
                  <div>Fair @ model IV: {fmt(calcResult.bs.bs_fair_value)}</div>
                  <div className={edgeTone(calcResult.bs.edge)}>
                    Edge: {fmt(calcResult.bs.edge)} ({calcResult.bs.rich_cheap ?? "—"})
                  </div>
                  <div>
                    Δ {fmt(calcResult.bs.delta, 4)} · Γ {fmt(calcResult.bs.gamma, 6)}
                  </div>
                  <div>
                    Θ {fmt(calcResult.bs.theta, 4)} · ν {fmt(calcResult.bs.vega, 4)}
                  </div>
                </CardContent>
              </Card>
              {calcResult.heston ? (
                <Card>
                  <CardHeader className="py-2">
                    <CardTitle className="text-sm">Heston-COS</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-1 font-mono text-sm">
                    <div>Model price: {fmt(calcResult.heston.price)}</div>
                    <div className="text-xs text-muted-foreground">
                      Stochastic-vol complementary fair value (not used by 3ST).
                    </div>
                  </CardContent>
                </Card>
              ) : null}
            </div>
          ) : null}
        </TabsContent>
      </Tabs>
    </div>
  );
}
