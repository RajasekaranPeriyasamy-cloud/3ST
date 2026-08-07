import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";

import { api } from "@/lib/api";
import type { LatencyRow, LatencyStats } from "@/lib/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export const Route = createFileRoute("/latency")({
  component: LatencyPage,
});

function ms(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(1)} ms`;
}

function slaTone(pct: number): string {
  if (pct >= 90) return "text-emerald-600 dark:text-emerald-400";
  if (pct >= 70) return "text-amber-600 dark:text-amber-400";
  return "text-red-600 dark:text-red-400";
}

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <Card>
      <CardContent className="py-3">
        <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className={`font-mono text-lg font-semibold ${tone ?? "text-foreground"}`}>{value}</div>
        {hint ? <div className="text-[10px] text-muted-foreground">{hint}</div> : null}
      </CardContent>
    </Card>
  );
}

function LatencyPage() {
  const [stats, setStats] = useState<LatencyStats | null>(null);
  const [rows, setRows] = useState<LatencyRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [s, r] = await Promise.all([
        api.get<LatencyStats>("/latency/stats", { silent: true }),
        api.get<{ items: LatencyRow[] }>("/latency/recent?limit=200", { silent: true }),
      ]);
      setStats(s);
      setRows(r.items ?? []);
    } catch {
      /* endpoints are local; ignore transient errors */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchAll();
    }, 15000);
    return () => clearInterval(id);
  }, [autoRefresh, fetchAll]);

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 pb-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">3ST Algo Desk — Execution Health</h1>
        <p className="text-sm text-muted-foreground">
          Order-placement latency: 3ST overhead vs broker round-trip, SLA and percentiles.
        </p>
        {stats ? (
          <p className="mt-1 text-xs text-muted-foreground">
            {stats.total_orders} orders logged · percentiles over last {stats.percentile_window_days}d
            ({stats.percentile_sample} samples) · Updated{" "}
            {new Date(stats.updated_at).toLocaleTimeString()}
          </p>
        ) : null}
      </header>

      <div className="flex gap-2">
        <Button onClick={() => void fetchAll()} disabled={loading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {loading ? "Loading…" : "Refresh now"}
        </Button>
        <Button variant="outline" onClick={() => setAutoRefresh((v) => !v)}>
          {autoRefresh ? <Pause className="mr-2 h-4 w-4" /> : <Play className="mr-2 h-4 w-4" />}
          {autoRefresh ? "Pause auto-refresh" : "Resume auto-refresh"}
        </Button>
        <label className="ml-2 flex items-center gap-2 text-sm">
          <Checkbox checked={autoRefresh} onCheckedChange={(v) => setAutoRefresh(Boolean(v))} />
          Auto (15s)
        </label>
      </div>

      {stats && stats.total_orders > 0 ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Total Orders"
              value={String(stats.total_orders)}
              hint={`${stats.failed_orders} failed`}
            />
            <Stat
              label="Success Rate"
              value={`${stats.success_rate}%`}
              tone={slaTone(stats.success_rate)}
            />
            <Stat label="Avg Total" value={ms(stats.avg_total)} hint={`RTT ${ms(stats.avg_rtt)}`} />
            <Stat
              label="Avg Overhead"
              value={ms(stats.avg_overhead)}
              hint={`validation ${ms(stats.avg_validation)}`}
            />
            <Stat label="p50" value={ms(stats.p50_total)} />
            <Stat label="p90" value={ms(stats.p90_total)} />
            <Stat label="p95" value={ms(stats.p95_total)} />
            <Stat label="p99" value={ms(stats.p99_total)} />
            <Stat label="SLA < 100ms" value={`${stats.sla_100ms}%`} tone={slaTone(stats.sla_100ms)} />
            <Stat label="SLA < 150ms" value={`${stats.sla_150ms}%`} tone={slaTone(stats.sla_150ms)} />
            <Stat label="SLA < 200ms" value={`${stats.sla_200ms}%`} tone={slaTone(stats.sla_200ms)} />
          </div>

          {Object.keys(stats.broker_stats).length > 0 ? (
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">By broker</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow className="text-xs">
                      <TableHead>Broker</TableHead>
                      <TableHead className="text-right">Orders</TableHead>
                      <TableHead className="text-right">Failed</TableHead>
                      <TableHead className="text-right">Avg</TableHead>
                      <TableHead className="text-right">p50</TableHead>
                      <TableHead className="text-right">p99</TableHead>
                      <TableHead className="text-right">SLA &lt;150ms</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries(stats.broker_stats).map(([b, s]) => (
                      <TableRow key={b} className="text-xs font-mono">
                        <TableCell className="uppercase">{b}</TableCell>
                        <TableCell className="text-right">{s.total_orders}</TableCell>
                        <TableCell className="text-right">{s.failed_orders}</TableCell>
                        <TableCell className="text-right">{ms(s.avg_total)}</TableCell>
                        <TableCell className="text-right">{ms(s.p50_total)}</TableCell>
                        <TableCell className="text-right">{ms(s.p99_total)}</TableCell>
                        <TableCell className={`text-right ${slaTone(s.sla_150ms)}`}>{s.sla_150ms}%</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : null}

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm">Recent orders</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="max-h-[480px] overflow-y-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="text-xs">
                      <TableHead>Time</TableHead>
                      <TableHead>Symbol</TableHead>
                      <TableHead>Side</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Validation</TableHead>
                      <TableHead className="text-right">RTT</TableHead>
                      <TableHead className="text-right">Total</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.map((r, i) => (
                      <TableRow key={`${r.order_id}-${i}`} className="text-xs font-mono">
                        <TableCell>{new Date(r.ts).toLocaleTimeString()}</TableCell>
                        <TableCell>{r.symbol}</TableCell>
                        <TableCell>{r.transaction_type ?? "—"}</TableCell>
                        <TableCell>
                          <Badge
                            variant={r.status === "SUCCESS" ? "outline" : "destructive"}
                            className="text-[10px]"
                          >
                            {r.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">{ms(r.validation_ms)}</TableCell>
                        <TableCell className="text-right">{ms(r.rtt_ms)}</TableCell>
                        <TableCell
                          className={`text-right ${r.total_ms < 150 ? "text-emerald-600 dark:text-emerald-400" : r.total_ms < 300 ? "text-amber-600 dark:text-amber-400" : "text-red-600 dark:text-red-400"}`}
                        >
                          {ms(r.total_ms)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </>
      ) : (
        !loading && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No orders logged yet. Latency is recorded automatically when 3ST places orders (live or
              paper) via the strategy / watchlist / rolling-straddle paths.
            </CardContent>
          </Card>
        )
      )}

      <p className="text-[10px] text-muted-foreground">
        validation = 3ST pre-trade work (risk gate + position reads) · RTT = broker place_order
        round-trip · total = end-to-end. Logged to data/latency_log.jsonl.
      </p>
    </div>
  );
}
