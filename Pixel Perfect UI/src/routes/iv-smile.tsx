import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
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
import { pickNearestExpiry, useOptionExpiries } from "@/hooks/useOptionExpiries";
import type { IvSmileConfig, IvSmileSnapshot, OiUnderlying } from "@/lib/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const Route = createFileRoute("/iv-smile")({
  component: IvSmilePage,
});

const UNDERLYINGS: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];

function IvSmilePage() {
  const [config, setConfig] = useState<IvSmileConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const [snap, setSnap] = useState<IvSmileSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [auto, setAuto] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { expiries, loading: expLoading } = useOptionExpiries(underlying);

  useEffect(() => {
    api.get<IvSmileConfig>("/iv-smile/config", { silent: true }).then(setConfig).catch(() => {});
  }, []);

  useEffect(() => {
    const next = pickNearestExpiry(expiries);
    if (next) setExpiry(next);
  }, [expiries]);

  const refresh = useCallback(async () => {
    if (!expiry) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<IvSmileSnapshot>(
        `/iv-smile/snapshot?underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(expiry)}`,
        { silent: true },
      );
      setSnap(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load IV smile");
    } finally {
      setLoading(false);
    }
  }, [underlying, expiry]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!auto || !config) return;
    const id = window.setInterval(() => void refresh(), (config.refresh_seconds ?? 60) * 1000);
    return () => window.clearInterval(id);
  }, [auto, config, refresh]);

  const chartData = useMemo(
    () =>
      (snap?.chain ?? []).map((p) => ({
        strike: p.strike,
        ce_iv: p.ce_iv,
        pe_iv: p.pe_iv,
      })),
    [snap],
  );

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">IV Smile</h1>
          <p className="text-sm text-muted-foreground">
            CE/PE implied vol by strike — single expiry skew desk
          </p>
        </div>
        <Link to="/vol-surface" className="text-sm text-primary hover:underline">
          Vol Surface →
        </Link>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Controls</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-4">
          <div className="space-y-1">
            <Label>Underlying</Label>
            <Select value={underlying} onValueChange={(v) => setUnderlying(v as OiUnderlying)}>
              <SelectTrigger className="w-[140px]">
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
          <div className="space-y-1">
            <Label>Expiry</Label>
            <Select value={expiry} onValueChange={setExpiry} disabled={expLoading || !expiries.length}>
              <SelectTrigger className="w-[160px]">
                <SelectValue placeholder={expLoading ? "Loading…" : "Select expiry"} />
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
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading || !expiry}>
              <RefreshCw className={`mr-1 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <Button variant={auto ? "default" : "outline"} size="sm" onClick={() => setAuto((a) => !a)}>
              {auto ? <Pause className="mr-1 h-4 w-4" /> : <Play className="mr-1 h-4 w-4" />}
              {auto ? "Auto" : "Paused"}
            </Button>
            <div className="flex items-center gap-2 pl-2">
              <Checkbox id="auto-smile" checked={auto} onCheckedChange={(c) => setAuto(Boolean(c))} />
              <Label htmlFor="auto-smile" className="text-xs text-muted-foreground">
                Auto refresh
              </Label>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Card className="border-destructive/50">
          <CardContent className="pt-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {snap && (
        <div className="grid gap-4 md:grid-cols-4">
          {[
            ["Spot", snap.spot.toLocaleString()],
            ["ATM strike", snap.atm_strike.toLocaleString()],
            ["ATM IV", snap.atm_iv != null ? `${snap.atm_iv}%` : "—"],
            ["Skew (5% OTM)", snap.skew != null ? `${snap.skew}%` : "—"],
          ].map(([label, val]) => (
            <Card key={label}>
              <CardContent className="pt-4">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="text-lg font-semibold">{val}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">IV by strike</CardTitle>
        </CardHeader>
        <CardContent className="h-[420px]">
          {chartData.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis dataKey="strike" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} unit="%" />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="ce_iv" name="CE IV" stroke="#6366f1" dot={false} connectNulls />
                <Line type="monotone" dataKey="pe_iv" name="PE IV" stroke="#ef4444" dot={false} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-16 text-center text-sm text-muted-foreground">
              {loading ? "Loading smile…" : "Select expiry and refresh"}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
