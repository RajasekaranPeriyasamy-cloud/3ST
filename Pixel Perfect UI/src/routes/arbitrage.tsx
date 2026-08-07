import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";

import { api } from "@/lib/api";
import type { ArbitrageConfig, ArbitrageSnapshot } from "@/lib/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export const Route = createFileRoute("/arbitrage")({
  component: ArbitragePage,
});

function directionLabel(dir: string | null) {
  if (dir === "SHORT_SPREAD") return "Sell far / Buy near";
  if (dir === "LONG_SPREAD") return "Buy far / Sell near";
  return "—";
}

function ArbitragePage() {
  const [config, setConfig] = useState<ArbitrageConfig | null>(null);
  const [snap, setSnap] = useState<ArbitrageSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [auto, setAuto] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exchangeFilter, setExchangeFilter] = useState("ALL");
  const [typeFilter, setTypeFilter] = useState("ALL");
  const [minSpread, setMinSpread] = useState("");
  const [search, setSearch] = useState("");
  const [onlyLiquid, setOnlyLiquid] = useState(false);
  const [exchanges, setExchanges] = useState("NFO,MCX");

  useEffect(() => {
    api.get<ArbitrageConfig>("/arbitrage/config", { silent: true }).then(setConfig).catch(() => {});
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<ArbitrageSnapshot>(
        `/arbitrage/snapshot?exchanges=${encodeURIComponent(exchanges)}`,
        { silent: true },
      );
      setSnap(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load arbitrage data");
    } finally {
      setLoading(false);
    }
  }, [exchanges]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!auto || !config) return;
    const sec = config.quote_refresh_seconds ?? 8;
    const id = window.setInterval(() => void refresh(), sec * 1000);
    return () => window.clearInterval(id);
  }, [auto, config, refresh]);

  const rows = useMemo(() => {
    const list = snap?.rows ?? [];
    const min = minSpread.trim() === "" ? null : Number.parseFloat(minSpread);
    const q = search.trim().toUpperCase();
    return list.filter((r) => {
      if (exchangeFilter !== "ALL" && r.exchange !== exchangeFilter) return false;
      if (typeFilter !== "ALL" && r.type !== typeFilter) return false;
      if (onlyLiquid && !r.liquid) return false;
      if (q && !r.underlying.toUpperCase().includes(q)) return false;
      if (min != null && (r.spread_pct == null || r.spread_pct < min)) return false;
      return true;
    });
  }, [snap, exchangeFilter, typeFilter, minSpread, search, onlyLiquid]);

  const exchangeOptions = useMemo(() => {
    const set = new Set((snap?.rows ?? []).map((r) => r.exchange));
    return Array.from(set).sort();
  }, [snap]);

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Calendar Arbitrage</h1>
        <p className="text-sm text-muted-foreground">
          Futures calendar spreads — REST quote poll (near vs far month)
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-4 pt-6">
          <div className="space-y-1">
            <Label>Exchanges</Label>
            <Input className="w-[140px]" value={exchanges} onChange={(e) => setExchanges(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>Exchange filter</Label>
            <Select value={exchangeFilter} onValueChange={setExchangeFilter}>
              <SelectTrigger className="w-[120px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All</SelectItem>
                {exchangeOptions.map((ex) => (
                  <SelectItem key={ex} value={ex}>{ex}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Pair type</Label>
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="w-[140px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All</SelectItem>
                <SelectItem value="near-next">near-next</SelectItem>
                <SelectItem value="near-third">near-third</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Min spread %</Label>
            <Input className="w-[100px]" value={minSpread} onChange={(e) => setMinSpread(e.target.value)} placeholder="0" />
          </div>
          <div className="space-y-1">
            <Label>Search</Label>
            <Input className="w-[120px]" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="NIFTY" />
          </div>
          <div className="flex items-center gap-2 pb-2">
            <Checkbox id="liquid" checked={onlyLiquid} onCheckedChange={(c) => setOnlyLiquid(Boolean(c))} />
            <Label htmlFor="liquid" className="text-xs">Liquid only</Label>
          </div>
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={`mr-1 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button variant={auto ? "default" : "outline"} size="sm" onClick={() => setAuto((a) => !a)}>
            {auto ? <Pause className="mr-1 h-4 w-4" /> : <Play className="mr-1 h-4 w-4" />}
            {auto ? "Live poll" : "Paused"}
          </Button>
        </CardContent>
      </Card>

      {error && (
        <Card className="border-destructive/50">
          <CardContent className="pt-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {snap && (
        <p className="text-xs text-muted-foreground">
          {snap.counts.pairs} pairs · {snap.counts.symbols} symbols · updated {snap.updated_at}
        </p>
      )}

      <Card>
        <CardHeader><CardTitle className="text-base">Ranked spreads</CardTitle></CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Underlying</TableHead>
                <TableHead>Exch</TableHead>
                <TableHead>Type</TableHead>
                <TableHead className="text-right">Near mid</TableHead>
                <TableHead className="text-right">Far mid</TableHead>
                <TableHead className="text-right">Spread %</TableHead>
                <TableHead>Direction</TableHead>
                <TableHead>Liquid</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-medium">{r.underlying}</TableCell>
                  <TableCell>{r.exchange}</TableCell>
                  <TableCell className="text-xs">{r.type}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{r.near_mid?.toFixed(2) ?? "—"}</TableCell>
                  <TableCell className="text-right font-mono text-xs">{r.far_mid?.toFixed(2) ?? "—"}</TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    {r.spread_pct != null ? `${r.spread_pct.toFixed(3)}%` : "—"}
                  </TableCell>
                  <TableCell className="text-xs">{directionLabel(r.direction)}</TableCell>
                  <TableCell>{r.liquid ? "Yes" : "—"}</TableCell>
                </TableRow>
              ))}
              {!rows.length && (
                <TableRow>
                  <TableCell colSpan={8} className="py-10 text-center text-muted-foreground">
                    {loading ? "Loading…" : "No pairs match filters"}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
