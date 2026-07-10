import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, Layers, Minus, Pause, Play, RefreshCw } from "lucide-react";

import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  OiBiasSide,
  OiLogEntry,
  OiOverallBias,
  OiTrackerConfig,
  OiTrackerRow,
  OiTrackerSignal,
  OiTrackerSnapshot,
  OiUnderlying,
} from "@/lib/types";

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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export const Route = createFileRoute("/oi-tracker")({
  component: OiTrackerPage,
});

const UNDERLYINGS: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];
const ALERT_DEBOUNCE_MS = 5 * 60 * 1000;

function OiTrackerPage() {
  const [config, setConfig] = useState<OiTrackerConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const [expiries, setExpiries] = useState<string[]>([]);
  const [optionsCount, setOptionsCount] = useState(5);
  const [refreshSec, setRefreshSec] = useState(60);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [snapshot, setSnapshot] = useState<OiTrackerSnapshot | null>(null);
  const [logs, setLogs] = useState<OiLogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState(false);

  const lastAlertRef = useRef<{ call: number; put: number }>({ call: 0, put: 0 });
  const audioCtxRef = useRef<AudioContext | null>(null);

  useEffect(() => {
    api.get<OiTrackerConfig>("/oi-tracker/config", { silent: true }).then((c) => {
      setConfig(c);
      setOptionsCount(c.options_count);
      setRefreshSec(c.refresh_seconds);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    api
      .get<{ expiries: string[] }>(`/options/expiries?underlying=${underlying}`, { silent: true })
      .then((r) => {
        const list = r.expiries ?? [];
        setExpiries(list);
        if (list.length && !expiry) {
          const today = new Date().toISOString().slice(0, 10);
          const nearest = list.find((e) => e >= today) ?? list[list.length - 1];
          setExpiry(nearest);
        }
      })
      .catch(() => setExpiries([]));
  }, [underlying]);

  const playBeep = useCallback(() => {
    if (!soundEnabled) return;
    try {
      const ctx = audioCtxRef.current ?? new AudioContext();
      audioCtxRef.current = ctx;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = 880;
      gain.gain.value = 0.08;
      osc.start();
      osc.stop(ctx.currentTime + 0.15);
    } catch {
      /* browser blocked */
    }
  }, [soundEnabled]);

  const maybeAlert = useCallback(
    (data: OiTrackerSnapshot) => {
      if (!data.alert?.triggered) return;
      const now = Date.now();
      const threshold = data.alert.breach_ratio_threshold ?? 0.5;
      const { call_breach_ratio, put_breach_ratio } = data.alert;

      if (call_breach_ratio > threshold && now - lastAlertRef.current.call > ALERT_DEBOUNCE_MS) {
        lastAlertRef.current.call = now;
        toast.warning(
          `Call OI alert: ${(call_breach_ratio * 100).toFixed(0)}% of cells breached threshold`,
        );
        playBeep();
      }
      if (put_breach_ratio > threshold && now - lastAlertRef.current.put > ALERT_DEBOUNCE_MS) {
        lastAlertRef.current.put = now;
        toast.warning(
          `Put OI alert: ${(put_breach_ratio * 100).toFixed(0)}% of cells breached threshold`,
        );
        playBeep();
      }
    },
    [playBeep],
  );

  const fetchLogs = useCallback(async () => {
    try {
      const res = await api.get<{ items?: OiLogEntry[] }>("/oi-tracker/log?limit=50", { silent: true });
      setLogs(res.items ?? []);
    } catch {
      /* silent poll */
    }
  }, []);

  const fetchSnapshot = useCallback(async () => {
    setLoading(true);
    setAuthError(false);
    try {
      const q = new URLSearchParams({ underlying });
      if (expiry) q.set("expiry", expiry);
      q.set("options_count", String(optionsCount));
      const data = await api.get<OiTrackerSnapshot>(`/oi-tracker/snapshot?${q}`);
      setSnapshot(data);
      maybeAlert(data);
      void fetchLogs();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("401") || msg.toLowerCase().includes("session")) {
        setAuthError(true);
      }
    } finally {
      setLoading(false);
    }
  }, [underlying, expiry, optionsCount, maybeAlert, fetchLogs]);

  useEffect(() => {
    void fetchLogs();
  }, [fetchLogs]);

  useEffect(() => {
    if (!expiry) return;
    void fetchSnapshot();
  }, [underlying, expiry, optionsCount]);

  useEffect(() => {
    if (!autoRefresh || !expiry || authError) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSnapshot();
    }, refreshSec * 1000);
    return () => clearInterval(id);
  }, [autoRefresh, refreshSec, expiry, authError, fetchSnapshot]);

  const intervals = snapshot?.intervals_min ?? config?.intervals_min ?? [5, 10, 15, 30];

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">OI Tracker</h1>
        <p className="text-sm text-muted-foreground">
          Live Open Interest % change vs 5/10/15/30 minutes. Requires Kite login.
        </p>
      </header>

      {snapshot?.overall_bias ? (
        <OverallBiasBar bias={snapshot.overall_bias} atmStrike={snapshot.atm_strike} />
      ) : null}

      {authError && (
        <Card className="border-bear/50">
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
            <Select value={underlying} onValueChange={(v) => { setUnderlying(v as OiUnderlying); setExpiry(""); }}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {UNDERLYINGS.map((u) => (
                  <SelectItem key={u} value={u}>{u}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Expiry</Label>
            <Select value={expiry || undefined} onValueChange={setExpiry}>
              <SelectTrigger><SelectValue placeholder="Select expiry" /></SelectTrigger>
              <SelectContent>
                {expiries.map((e) => (
                  <SelectItem key={e} value={e}>{e}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Strikes each side</Label>
            <Select value={String(optionsCount)} onValueChange={(v) => setOptionsCount(Number(v))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {[2, 3, 4, 5, 7, 10].map((n) => (
                  <SelectItem key={n} value={String(n)}>{n}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Refresh (sec)</Label>
            <Select value={String(refreshSec)} onValueChange={(v) => setRefreshSec(Number(v))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {[30, 60, 90, 120].map((n) => (
                  <SelectItem key={n} value={String(n)}>{n}s</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <Checkbox checked={autoRefresh} onCheckedChange={(v) => setAutoRefresh(Boolean(v))} />
            Auto-refresh
          </label>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <Checkbox checked={soundEnabled} onCheckedChange={(v) => setSoundEnabled(Boolean(v))} />
            Sound on high breach (optional)
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

      {snapshot && (
        <>
          <p className="text-sm text-muted-foreground">
            <Layers className="mr-1 inline h-4 w-4" />
            {snapshot.underlying} · expiry {snapshot.expiry} · spot {snapshot.spot.toFixed(2)} · ATM{" "}
            {snapshot.atm_strike} · updated {new Date(snapshot.updated_at).toLocaleTimeString()}
            {snapshot.spot_warning ? ` · ${snapshot.spot_warning}` : ""}
            {snapshot.alert.triggered ? (
              <span className="ml-2 text-bear">
                Alert: calls {(snapshot.alert.call_breach_ratio * 100).toFixed(0)}% · puts{" "}
                {(snapshot.alert.put_breach_ratio * 100).toFixed(0)}% breached
              </span>
            ) : null}
          </p>
          {snapshot.pcr ? (
            <p className="text-sm text-muted-foreground">
              Chain PCR:{" "}
              <span className="font-mono font-medium text-foreground">
                {snapshot.pcr.chain_oi != null ? snapshot.pcr.chain_oi.toFixed(2) : "N/A"}
              </span>
              {" · "}
              Call OI {snapshot.pcr.call_oi_total.toLocaleString()} · Put OI{" "}
              {snapshot.pcr.put_oi_total.toLocaleString()}
            </p>
          ) : null}

          <OiTable title="CALL Options OI" rows={snapshot.calls} intervals={intervals} side="call" />
          <OiTable title="PUT Options OI" rows={snapshot.puts} intervals={intervals} side="put" />

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Signal legend</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 text-xs text-muted-foreground md:grid-cols-2">
              <p className="md:col-span-2">
                OI %Chg cells with a{" "}
                <span className="inline-block rounded px-1.5 py-0.5 breach-cell-up font-semibold text-bull">
                  green highlight
                </span>{" "}
                or{" "}
                <span className="inline-block rounded px-1.5 py-0.5 breach-cell-down font-semibold text-bear">
                  red highlight
                </span>{" "}
                exceeded the interval threshold (5m 8% · 10m 10% · 15m 15% · 30m 25%).
              </p>
              <div>
                <p className="mb-1 font-medium text-foreground">Core — OI rising (fresh positions)</p>
                <ul className="space-y-0.5">
                  <li><span className="text-bear">↓ Long Puts</span> · PCR↑ Puts↑ IV↑</li>
                  <li><span className="text-bull">↑ Short Puts</span> · PCR↑ Puts↑ IV↓</li>
                  <li><span className="text-bull">↑ Long Calls</span> · PCR↓ Calls↑ IV↑</li>
                  <li><span className="text-bear">↓ Short Calls</span> · PCR↓ Calls↑ IV↓</li>
                </ul>
              </div>
              <div>
                <p className="mb-1 font-medium text-foreground">Sub — OI falling (positions closing)</p>
                <ul className="space-y-0.5">
                  <li><span className="text-bull">↑ Put Unwinding</span> · PCR↓ Puts↓ IV↓</li>
                  <li><span className="text-bull">↑ Short Covering (Puts)</span> · PCR↓ Puts↓ IV↑</li>
                  <li><span className="text-bear">↓ Call Unwinding</span> · PCR↑ Calls↓ IV↓</li>
                  <li><span className="text-bear">↓ Short Covering (Calls)</span> · PCR↑ Calls↓ IV↑</li>
                </ul>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Activity log</CardTitle>
        </CardHeader>
        <CardContent className="max-h-72 overflow-y-auto">
          {logs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No events yet — refresh to load OI snapshot</p>
          ) : (
            <ul className="space-y-2 text-xs font-mono">
              {logs.map((row, i) => (
                <li key={i} className="border-b border-border/50 pb-1">
                  <span className="text-muted-foreground">{row.at}</span>{" "}
                  <span
                    className={
                      row.event === "alert"
                        ? "font-semibold text-bear"
                        : row.event === "error"
                          ? "font-semibold text-destructive"
                          : "font-semibold"
                    }
                  >
                    {row.event}
                  </span>{" "}
                  {row.detail}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function BiasBadge({
  title,
  bias,
  detail,
}: {
  title: string;
  bias: OiBiasSide;
  detail?: string;
}) {
  const view = bias.view;
  const shell =
    view === "long"
      ? "border-bull/50 bg-bull/10 text-bull"
      : view === "short"
        ? "border-bear/50 bg-bear/10 text-bear"
        : "border-border bg-muted/40 text-muted-foreground";
  const Icon = view === "long" ? ArrowUp : view === "short" ? ArrowDown : Minus;

  return (
    <div className={`flex min-w-[8.5rem] flex-1 flex-col gap-1 rounded-lg border px-4 py-3 ${shell}`}>
      <span className="text-[10px] font-medium uppercase tracking-wider opacity-80">{title}</span>
      <span className="inline-flex items-center gap-1.5 text-lg font-bold">
        <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
        {bias.label}
      </span>
      {bias.signal?.label ? (
        <span className="truncate text-xs opacity-90" title={bias.signal.label}>
          {bias.signal.label}
        </span>
      ) : (
        <span className="text-xs opacity-70">{detail ?? "No ATM signal"}</span>
      )}
    </div>
  );
}

function OverallBiasBar({ bias, atmStrike }: { bias: OiOverallBias; atmStrike: number }) {
  const chainDetail =
    bias.chain.samples > 0
      ? `Bull ${bias.chain.bull_pct}% · Bear ${bias.chain.bear_pct}% (${bias.chain.samples} leg${bias.chain.samples > 1 ? "s" : ""})`
      : "No matching ATM pattern";

  return (
    <Card className="border-primary/20">
      <CardContent className="flex flex-col gap-3 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium">
            Overall bias · ATM {atmStrike} · {bias.interval_min}m · sideways if &lt;{" "}
            {bias.sideways_threshold_pct}%
          </p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row">
          <BiasBadge title="Whole chain" bias={bias.chain} detail={chainDetail} />
          <BiasBadge title="Calls (ATM CE)" bias={bias.calls} />
          <BiasBadge title="Puts (ATM PE)" bias={bias.puts} />
        </div>
      </CardContent>
    </Card>
  );
}

function strikeClass(position: number, side: "call" | "put"): string {
  if (position === 0) return "text-cyan-400 font-semibold";
  if (side === "call") {
    return position < 0 ? "text-bull" : "text-bear";
  }
  return position > 0 ? "text-bull" : "text-bear";
}

function breachCellClass(breached: boolean, value: number | null | undefined): string {
  if (!breached || value == null) return "";
  if (value > 0) return "breach-cell-up";
  if (value < 0) return "breach-cell-down";
  return "bg-warn/30 ring-2 ring-warn/70 ring-inset";
}

function ChangeFlag({
  value,
  breached,
}: {
  value: number | null | undefined;
  breached?: boolean;
}) {
  if (value == null) {
    return <span className="text-muted-foreground">N/A</span>;
  }

  const isUp = value > 0;
  const isDown = value < 0;
  const tone = isUp ? "text-bull" : isDown ? "text-bear" : "text-muted-foreground";
  const display = `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
  const Icon = isUp ? ArrowUp : ArrowDown;

  return (
    <span
      className={cn(
        "inline-flex items-center justify-end gap-0.5 rounded-md px-1.5 py-0.5",
        breached && isUp && "breach-pill-up font-extrabold",
        breached && isDown && "breach-pill-down font-extrabold",
        !breached && tone,
      )}
      title={breached ? "OI threshold breached" : isUp ? "Up" : isDown ? "Down" : "Flat"}
    >
      {(isUp || isDown) && (
        <Icon className={cn("h-3 w-3 shrink-0", breached && "h-3.5 w-3.5")} aria-hidden="true" />
      )}
      {display}
    </span>
  );
}

function SignalFlag({ signal }: { signal?: OiTrackerSignal | null }) {
  if (!signal) {
    return <span className="text-muted-foreground">—</span>;
  }

  const tone =
    signal.tone === "bull" ? "text-bull" : signal.tone === "bear" ? "text-bear" : "text-muted-foreground";
  const Icon = signal.arrow === "up" ? ArrowUp : ArrowDown;

  return (
    <span
      className={`inline-flex items-center justify-end gap-1 ${tone} ${signal.tone !== "neutral" ? "font-semibold" : ""}`}
      title={signal.label}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="max-w-[6.5rem] truncate text-left">{signal.label}</span>
    </span>
  );
}

function OiTable({
  title,
  rows,
  intervals,
  side,
}: {
  title: string;
  rows: OiTrackerRow[];
  intervals: number[];
  side: "call" | "put";
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="overflow-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="text-right">Strike</TableHead>
              <TableHead>Symbol</TableHead>
              <TableHead className="text-right">Latest OI</TableHead>
              <TableHead className="text-right">IV %</TableHead>
              <TableHead className="text-right">OI Time</TableHead>
              {intervals.map((iv) => (
                <TableHead key={`oi-${iv}`} className="text-right">
                  OI %Chg ({iv}m)
                  <span className="ml-1 text-[10px] font-normal text-warn">● breach</span>
                </TableHead>
              ))}
              {intervals.map((iv) => (
                <TableHead key={`iv-${iv}`} className="text-right">
                  IV %Chg ({iv}m)
                </TableHead>
              ))}
              {intervals.map((iv) => (
                <TableHead key={`sig-${iv}`} className="min-w-[7.5rem] text-right">
                  Signal ({iv}m)
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const rowHasBreach = intervals.some((iv) => row.breach[String(iv)]);
              return (
              <TableRow key={row.key} className={rowHasBreach ? "bg-warn/5" : undefined}>
                <TableCell
                  className={cn(
                    "text-right font-mono",
                    strikeClass(row.position, side),
                    rowHasBreach && "border-l-[3px] border-l-warn font-bold",
                  )}
                >
                  {row.strike}
                </TableCell>
                <TableCell className="font-mono text-xs">{row.symbol}</TableCell>
                <TableCell className="text-right font-mono">
                  {row.latest_oi != null ? row.latest_oi.toLocaleString() : "N/A"}
                </TableCell>
                <TableCell className="text-right font-mono text-xs">
                  {row.iv != null ? `${row.iv.toFixed(2)}%` : "N/A"}
                </TableCell>
                <TableCell className="text-right font-mono text-xs">
                  {row.oi_time ? new Date(row.oi_time).toLocaleTimeString() : "N/A"}
                </TableCell>
                {intervals.map((iv) => {
                  const key = String(iv);
                  const pct = row.pct[key];
                  const breached = row.breach[key];
                  return (
                    <TableCell
                      key={`oi-${iv}`}
                      className={cn("text-right font-mono text-xs", breachCellClass(breached, pct))}
                    >
                      <ChangeFlag value={pct} breached={breached} />
                    </TableCell>
                  );
                })}
                {intervals.map((iv) => {
                  const key = String(iv);
                  const ivPct = row.iv_pct?.[key];
                  return (
                    <TableCell key={`iv-${iv}`} className="text-right font-mono text-xs">
                      <ChangeFlag value={ivPct} />
                    </TableCell>
                  );
                })}
                {intervals.map((iv) => {
                  const key = String(iv);
                  const signal = row.signals?.[key];
                  return (
                    <TableCell key={`sig-${iv}`} className="text-right text-xs">
                      <SignalFlag signal={signal} />
                    </TableCell>
                  );
                })}
              </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
