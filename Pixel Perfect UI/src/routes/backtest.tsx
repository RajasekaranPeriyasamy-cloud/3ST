import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Download, Play } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import { useSelection } from "@/context/SelectionContext";
import type { BacktestLimits, BacktestResult, Timeframe } from "@/lib/types";

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

export const Route = createFileRoute("/backtest")({
  component: BacktestPage,
});

const TIMEFRAMES: Timeframe[] = ["1min", "3min", "5min", "15min", "30min", "60min"];

function BacktestPage() {
  const { selection } = useSelection();
  const [useSaved, setUseSaved] = useState(true);
  const [manualToken, setManualToken] = useState("");
  const [manualKey, setManualKey] = useState<"NIFTY50" | "SENSEX" | "">("");
  const [timeframe, setTimeframe] = useState<Timeframe>(selection.timeframe ?? "15min");
  const [source, setSource] = useState<"yahoo" | "kite">("kite");
  const [useMax, setUseMax] = useState(true);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [tradeMode, setTradeMode] = useState<"long" | "short" | "both">("both");
  const [limits, setLimits] = useState<BacktestLimits | null>(null);

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);

  const activeTf = useSaved ? selection.timeframe : timeframe;

  useEffect(() => {
    api
      .get<BacktestLimits>(
        `/backtest/limits?source=${source}&timeframe=${encodeURIComponent(activeTf)}`,
        { silent: true },
      )
      .then((r) => {
        setLimits(r);
        if (useMax) {
          setStart(r.default_start);
          setEnd(r.default_end);
        }
      })
      .catch(() => setLimits(null));
  }, [source, activeTf, useMax]);

  const lotLabel =
    selection.segment === "future" || selection.segment === "option"
      ? ` · lot ${selection.lot_size || "—"}`
      : "";

  const equityDomain = useMemo(() => {
    if (!result?.equity?.length) return undefined;
    const vals = result.equity.map((p) => p.v);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const pad = Math.max(5, (max - min) * 0.1 || Math.abs(max) * 0.05 || 1);
    return [min - pad, max + pad] as [number, number];
  }, [result?.equity]);

  async function run() {
    setRunning(true);
    setResult(null);
    try {
      const tradeModeMap = { long: "LongOnly", short: "ShortOnly", both: "Both" } as const;
      const body: Record<string, unknown> = {
        use_selection: useSaved,
        trade_mode: tradeModeMap[tradeMode],
        source,
        use_max: useMax,
      };
      if (!useSaved) {
        body.timeframe = timeframe;
        if (manualToken) body.instrument_token = Number(manualToken);
        if (manualKey) body.instrument = manualKey;
      }
      if (!useMax) {
        if (!start || !end) {
          toast.error("Pick start and end dates, or enable Max history");
          setRunning(false);
          return;
        }
        body.start = start;
        body.end = end;
      } else if (start && end) {
        body.start = start;
        body.end = end;
      }

      const r = await api.post<BacktestResult>("/backtest/run", body);
      setResult(r);
      toast.success(`Backtest complete · ${r.meta?.bars ?? "?"} bars`);
    } catch {
      /* handled */
    } finally {
      setRunning(false);
    }
  }

  function downloadTradesCsv() {
    if (!result?.trades?.length) return;
    const cols = Object.keys(result.trades[0]);
    const csv = [
      cols.join(","),
      ...result.trades.map((row) =>
        cols
          .map((c) => {
            const v = row[c];
            if (v == null) return "";
            const s = String(v).replace(/"/g, '""');
            return /[",\n]/.test(s) ? `"${s}"` : s;
          })
          .join(","),
      ),
    ].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "trades.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Backtest</h1>
        <p className="text-sm text-muted-foreground">
          Uses 3ST rules from Stock Selection when saved selection is enabled. PnL is cumulative
          points{lotLabel ? ` (× lot size for F&O)` : ""}.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Instrument & data</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={useSaved}
              onCheckedChange={(v) => setUseSaved(Boolean(v))}
            />
            Use saved selection ({selection.tradingsymbol ?? "none"} · {selection.timeframe} ·{" "}
            {selection.st_method} · {selection.system_mode})
          </label>
          {useSaved && (
            <p className="text-xs text-muted-foreground">
              Strategy, session, force exit, SL/TGT/TSL come from Stock Selection. For long history
              use <strong>Kite</strong> source (requires login).
            </p>
          )}

          {!useSaved && (
            <div className="grid gap-3 md:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label>instrument_token</Label>
                <Input
                  value={manualToken}
                  onChange={(e) => setManualToken(e.target.value)}
                  className="font-mono"
                  placeholder="e.g. 256265"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>Yahoo key (yahoo source only)</Label>
                <Select
                  value={manualKey || undefined}
                  onValueChange={(v) => setManualKey(v as "NIFTY50" | "SENSEX")}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="NIFTY50 or SENSEX" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="NIFTY50">NIFTY50</SelectItem>
                    <SelectItem value="SENSEX">SENSEX</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}

          <div className="grid gap-3 md:grid-cols-4">
            {!useSaved && (
              <div className="flex flex-col gap-1.5">
                <Label>Timeframe</Label>
                <Select value={timeframe} onValueChange={(v) => setTimeframe(v as Timeframe)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TIMEFRAMES.map((t) => (
                      <SelectItem key={t} value={t}>{t}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="flex flex-col gap-1.5">
              <Label>Data source</Label>
              <Select value={source} onValueChange={(v) => setSource(v as "yahoo" | "kite")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="kite">Kite (up to ~400 days intraday)</SelectItem>
                  <SelectItem value="yahoo">Yahoo (~60 days, NIFTY/SENSEX)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center gap-2 pt-6 md:col-span-2">
              <Checkbox
                checked={useMax}
                onCheckedChange={(v) => {
                  const on = Boolean(v);
                  setUseMax(on);
                  if (on && limits) {
                    setStart(limits.default_start);
                    setEnd(limits.default_end);
                  }
                }}
              />
              <span className="text-sm">Max available history</span>
            </div>
          </div>

          {limits && (
            <p className="text-xs text-muted-foreground">{limits.note}</p>
          )}

          <div className="grid gap-3 md:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label>Start date</Label>
              <Input
                type="date"
                value={start}
                disabled={useMax}
                onChange={(e) => setStart(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>End date</Label>
              <Input
                type="date"
                value={end}
                disabled={useMax}
                onChange={(e) => setEnd(e.target.value)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Trade direction</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="flex flex-col gap-1.5">
            <Label>Trade mode</Label>
            <Select value={tradeMode} onValueChange={(v) => setTradeMode(v as typeof tradeMode)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="long">Long only</SelectItem>
                <SelectItem value="short">Short only</SelectItem>
                <SelectItem value="both">Both</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <div>
        <Button size="lg" onClick={run} disabled={running}>
          <Play className="mr-2 h-4 w-4" />
          {running ? "Running…" : "Run Backtest"}
        </Button>
      </div>

      {result && (
        <>
          {result.meta && (
            <p className="text-sm text-muted-foreground">
              {result.meta.source?.toUpperCase()} · {result.meta.start} → {result.meta.end} ·{" "}
              {result.meta.bars?.toLocaleString()} bars · {result.meta.instrument} ·{" "}
              {result.meta.timeframe}
              {result.metrics.start_open != null && result.metrics.end_close != null ? (
                <> · open {result.metrics.start_open.toFixed(2)} → close {result.metrics.end_close.toFixed(2)}</>
              ) : null}
            </p>
          )}

          <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
            <MetricCard
              label="Net points"
              value={result.metrics.net_points ?? result.metrics.net_pnl}
              tone={(result.metrics.net_points ?? result.metrics.net_pnl) >= 0 ? "bull" : "bear"}
            />
            {(tradeMode === "both" || tradeMode === "long") && (
              <MetricCard
                label="Long points"
                value={result.metrics.long_points ?? 0}
                tone={(result.metrics.long_points ?? 0) >= 0 ? "bull" : "bear"}
                sub={result.metrics.long_trades != null ? `${result.metrics.long_trades} trades` : undefined}
              />
            )}
            {(tradeMode === "both" || tradeMode === "short") && (
              <MetricCard
                label="Short points"
                value={result.metrics.short_points ?? 0}
                tone={(result.metrics.short_points ?? 0) >= 0 ? "bull" : "bear"}
                sub={result.metrics.short_trades != null ? `${result.metrics.short_trades} trades` : undefined}
              />
            )}
            {result.metrics.start_open != null && (
              <MetricCard label="Start open" value={result.metrics.start_open} format="price" />
            )}
            {result.metrics.end_close != null && (
              <MetricCard label="End close" value={result.metrics.end_close} format="price" />
            )}
            <MetricCard label="Trades" value={result.metrics.trades} />
            <MetricCard label="Win rate" value={result.metrics.win_rate} format="pct" />
            <MetricCard label="Profit factor" value={result.metrics.profit_factor} />
            <MetricCard
              label="Max DD (pts)"
              value={result.metrics.max_drawdown ?? result.metrics.max_drawdown_pct}
              tone="bear"
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Cumulative P&L (points)</CardTitle>
            </CardHeader>
            <CardContent className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={result.equity}>
                  <defs>
                    <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--color-bull)" stopOpacity={0.5} />
                      <stop offset="100%" stopColor="var(--color-bull)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                  <XAxis dataKey="t" stroke="var(--color-muted-foreground)" fontSize={11} tickFormatter={(t) => t.slice(0, 10)} />
                  <YAxis
                    stroke="var(--color-muted-foreground)"
                    fontSize={11}
                    domain={equityDomain}
                    tickFormatter={(v) => Number(v).toFixed(0)}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "var(--color-popover)",
                      border: "1px solid var(--color-border)",
                      fontSize: 12,
                    }}
                    formatter={(v: number) => [v.toFixed(2), "PnL"]}
                  />
                  <Area
                    type="monotone"
                    dataKey="v"
                    stroke="var(--color-bull)"
                    fill="url(#eq)"
                    strokeWidth={2}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          {result.trades?.length ? (
            <Card>
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle className="text-base">Trades ({result.trades.length})</CardTitle>
                <Button size="sm" variant="outline" onClick={downloadTradesCsv}>
                  <Download className="mr-2 h-4 w-4" /> CSV
                </Button>
              </CardHeader>
              <CardContent className="max-h-96 overflow-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {Object.keys(result.trades[0]).map((k) => (
                        <TableHead key={k}>{k}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {result.trades.slice(0, 200).map((t, i) => (
                      <TableRow key={i}>
                        {Object.keys(result.trades[0]).map((k) => (
                          <TableCell key={k} className="font-mono text-xs">
                            {String(t[k] ?? "")}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : null}
        </>
      )}
    </div>
  );
}

function MetricCard({
  label,
  value,
  tone,
  format,
  sub,
}: {
  label: string;
  value: number;
  tone?: "bull" | "bear";
  format?: "pct" | "price";
  sub?: string;
}) {
  const cls =
    tone === "bull" ? "text-bull" : tone === "bear" ? "text-bear" : "text-foreground";
  const display =
    value == null || Number.isNaN(value)
      ? "—"
      : format === "pct"
        ? `${Number(value).toFixed(2)}%`
        : format === "price"
          ? Number(value).toFixed(2)
          : Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`font-mono text-lg ${cls}`}>{display}</div>
        {sub ? <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p> : null}
      </CardContent>
    </Card>
  );
}
