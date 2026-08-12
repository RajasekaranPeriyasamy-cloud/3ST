import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import { PremiumLadder } from "@/components/velocity/PremiumLadder";
import type {
  VelocityChart,
  VelocityCoverage,
  VelocityStatus,
} from "@/lib/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export const Route = createFileRoute("/delta-velocity")({
  component: DeltaVelocityPage,
});

const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "SENSEX"] as const;

/** Trailing-mean window from the paper: no contract yields v_t before this many minutes. */
const SMOOTH_N = 15;
const POLL_MS = 30_000;

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

function fmtV(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(5);
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Open interest runs to eight figures; full digits crowd the tile and read no better. */
function fmtOi(v: number | null | undefined): string {
  if (v == null) return "—";
  if (Math.abs(v) >= 1e7) return `${(v / 1e7).toFixed(2)}Cr`;
  if (Math.abs(v) >= 1e5) return `${(v / 1e5).toFixed(2)}L`;
  return v.toLocaleString();
}

function changeTone(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-foreground";
  return v > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400";
}

function DeltaVelocityPage() {
  const [underlying, setUnderlying] = useState<string>("NIFTY");
  const [expiry, setExpiry] = useState<string | null>(null);
  const [status, setStatus] = useState<VelocityStatus | null>(null);
  const [coverage, setCoverage] = useState<VelocityCoverage | null>(null);
  const [chart, setChart] = useState<VelocityChart | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    // allSettled, not all: these are three independent reads, and one of them
    // failing (a stale API missing a newer route, say) must not blank the other
    // two. A page that shows "collector stopped" because an unrelated endpoint
    // 404'd is actively misleading.
    const [s, c, k] = await Promise.allSettled([
      api.get<VelocityStatus>("/velocity/status"),
      api.get<VelocityCoverage>(`/velocity/coverage?underlying=${underlying}`),
      api.get<VelocityChart>(
        `/velocity/chart?underlying=${underlying}${expiry ? `&expiry=${expiry}` : ""}`,
      ),
    ]);
    if (s.status === "fulfilled") setStatus(s.value);
    if (c.status === "fulfilled") setCoverage(c.value);
    if (k.status === "fulfilled") setChart(k.value);

    const failures = [s, c, k]
      .filter((r): r is PromiseRejectedResult => r.status === "rejected")
      .map((r) => (r.reason instanceof Error ? r.reason.message : String(r.reason)));
    setError(failures.length ? failures[0] : null);
    setLoading(false);
  }, [underlying, expiry]);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const today = coverage?.days?.[coverage.days.length - 1];
  const minutes = today?.minutes ?? 0;
  const legs = today?.legs ?? 0;
  const sample = status?.last_report?.underlyings?.[underlying];

  const p95 = chart?.thresholds?.p95 ?? null;
  const rows = useMemo(() => {
    const src = chart?.minutes ?? [];
    return src.map((m) => ({
      ...m,
      // Spot value repeated only on high-velocity minutes, so the scatter marks
      // exactly where on the price path v_t spiked.
      spike: p95 != null && m.v_max != null && m.v_max >= p95 ? m.spot : null,
    }));
  }, [chart, p95]);

  const ctx = chart?.context ?? null;
  const withVelocity = rows.filter((r) => r.v_max != null).length;
  const corr = chart?.correlation;
  const lagRows = (corr?.lag_profile ?? []).filter((p) => p.corr != null);
  const corrReady = lagRows.length > 0;

  const spotDomain = useMemo(() => {
    const vals = rows.map((r) => r.spot).filter((v) => Number.isFinite(v));
    if (!vals.length) return undefined;
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.08, 1);
    return [lo - pad, hi + pad] as [number, number];
  }, [rows]);

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Delta Velocity</h1>
          <p className="text-xs text-muted-foreground">
            Minute-level option-state velocity. Collection only — no detection, no orders.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {UNDERLYINGS.map((u) => (
            <Button
              key={u}
              size="sm"
              variant={u === underlying ? "default" : "outline"}
              onClick={() => {
                setUnderlying(u);
                setExpiry(null);
              }}
            >
              {u}
            </Button>
          ))}
          <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {chart?.expiries?.length ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Expiry</span>
          <Button
            size="sm"
            variant={expiry === null ? "default" : "outline"}
            onClick={() => setExpiry(null)}
          >
            All
          </Button>
          {chart.expiries.map((e) => {
            const dte = chart.session_date
              ? Math.round(
                  (new Date(`${e}T00:00:00`).getTime() -
                    new Date(`${chart.session_date}T00:00:00`).getTime()) /
                    86_400_000,
                )
              : null;
            return (
              <Button
                key={e}
                size="sm"
                variant={expiry === e ? "default" : "outline"}
                onClick={() => setExpiry(e)}
              >
                {e}
                {dte !== null ? (
                  <span className="ml-1 opacity-70">{dte}d</span>
                ) : null}
              </Button>
            );
          })}
          <span className="text-[11px] text-muted-foreground">
            Pooling expiries hides the dominant effect — measured NIFTY 2026-08-11, DTE 0 ran
            4.9× the DTE 14 p95 and supplied every reading above 0.015 that session.
          </span>
        </div>
      ) : null}

      {error ? (
        <Card>
          <CardContent className="py-3 text-sm text-red-600 dark:text-red-400">{error}</CardContent>
        </Card>
      ) : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat
          label="Spot"
          value={fmtNum(ctx?.spot)}
          hint={
            ctx?.spot_change != null
              ? `${ctx.spot_change > 0 ? "+" : ""}${fmtNum(ctx.spot_change)} (${fmtNum(ctx.spot_change_pct, 2)}%)`
              : "since session open"
          }
          tone={changeTone(ctx?.spot_change)}
        />
        <Stat
          label="ATM"
          value={ctx?.atm != null ? String(ctx.atm) : "—"}
          hint={
            ctx?.straddle != null
              ? `straddle ${fmtNum(ctx.straddle)} (${fmtNum(ctx.straddle_pct, 2)}%)`
              : "no straddle"
          }
        />
        <Stat label="PCR" value={ctx?.pcr != null ? fmtNum(ctx.pcr, 3) : "—"} hint="PE OI / CE OI" />
        <Stat label="CE OI" value={fmtOi(ctx?.ce_oi)} hint="tracked window" />
        <Stat label="PE OI" value={fmtOi(ctx?.pe_oi)} hint="tracked window" />
      </div>

      <p className="-mt-2 text-[11px] text-muted-foreground">
        PCR and OI cover {ctx?.scope ?? "the tracked window"} — not the full chain, so they
        will differ from the OI desks. Net GEX and the gamma-flip regime live on{" "}
        <Link to="/gamma-density" className="underline underline-offset-2">
          Gamma Density
        </Link>
        .
      </p>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Collector"
          value={status?.alive ? "running" : "stopped"}
          hint={status?.last_report?.ts ? `last ${status.last_report.ts.slice(11, 19)}` : "no sample yet"}
          tone={
            status?.alive
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-red-600 dark:text-red-400"
          }
        />
        <Stat
          label="Minutes today"
          value={String(minutes)}
          hint={`${coverage?.sessions ?? 0} session(s) archived`}
        />
        <Stat
          label="Contracts"
          value={String(chart?.contracts ?? legs)}
          hint={
            sample?.legs_valid != null
              ? `${sample.legs_valid} legs with valid delta`
              : `ATM ${chart?.atm_strike ?? "—"}`
          }
        />
        <Stat
          label="v_t minutes"
          value={String(withVelocity)}
          hint={p95 != null ? `p95 ${fmtV(p95)}` : `needs ${SMOOTH_N + 1} samples`}
        />
      </div>

      {withVelocity === 0 ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">No readings yet</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>
              v_t is a trailing mean of {SMOOTH_N} Delta observations, so a contract yields nothing
              until it has been sampled {SMOOTH_N + 1} times — {SMOOTH_N + 1} minutes after the
              collector starts.
            </p>
            <p className="font-mono text-xs">
              {minutes} of {SMOOTH_N + 1} minutes collected
            </p>
            <p>
              The archive builds forward only — Kite cannot supply historical option state for
              expired contracts, so there is no backfill.
            </p>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            Option prices — change since session open
            {chart?.ladder?.atm_at_open != null ? (
              <Badge variant="outline" className="ml-2 font-mono text-[10px]">
                pinned to open ATM {chart.ladder.atm_at_open}
              </Badge>
            ) : null}
            {chart?.nearest_expiry ? (
              <Badge variant="outline" className="ml-2 font-mono text-[10px]">
                {chart.nearest_expiry}
              </Badge>
            ) : null}
          </CardTitle>
          <p className="text-[11px] text-muted-foreground">
            ATM both sides, then OTM calls up and OTM puts down. Pinned to the opening ATM —
            re-striking mid-session would show a premium change nobody traded. Hover a legend
            entry to isolate it.
          </p>
        </CardHeader>
        <CardContent className="p-0">
          <PremiumLadder ladder={chart?.ladder ?? null} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            Spot
            {chart?.atm_strike != null ? (
              <Badge variant="outline" className="ml-2 font-mono text-[10px]">
                ATM {chart.atm_strike}
              </Badge>
            ) : null}
            {p95 != null ? (
              <span className="ml-2 text-[10px] font-normal text-muted-foreground">
                dots mark minutes where v_t crossed the session p95
              </span>
            ) : null}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="h-[240px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="clock" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis
                  domain={spotDomain}
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v) => Number(v).toFixed(0)}
                  width={56}
                />
                <Tooltip
                  contentStyle={{ fontSize: 11 }}
                  formatter={(v: number | string, name: string) =>
                    name === "spot" ? Number(v).toFixed(2) : v
                  }
                />
                {chart?.atm_strike != null ? (
                  <ReferenceLine
                    y={chart.atm_strike}
                    stroke="currentColor"
                    strokeDasharray="4 4"
                    opacity={0.4}
                  />
                ) : null}
                <Line
                  type="monotone"
                  dataKey="spot"
                  stroke="#2563eb"
                  strokeWidth={1.6}
                  dot={false}
                  isAnimationActive={false}
                />
                <Scatter dataKey="spike" fill="#dc2626" shape="circle" isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            Delta velocity
            <span className="ml-2 text-[10px] font-normal text-muted-foreground">
              v_max across contracts, v_med underneath
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="h-[200px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="clock" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v) => Number(v).toExponential(0)}
                  width={56}
                />
                <Tooltip
                  contentStyle={{ fontSize: 11 }}
                  formatter={(v: number | string) => Number(v).toFixed(6)}
                />
                {p95 != null ? (
                  <ReferenceLine y={p95} stroke="#dc2626" strokeDasharray="4 4" opacity={0.6} />
                ) : null}
                <Line
                  type="monotone"
                  dataKey="v_max"
                  stroke="#7c3aed"
                  strokeWidth={1.6}
                  dot={false}
                  connectNulls
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="v_med"
                  stroke="#94a3b8"
                  strokeWidth={1}
                  dot={false}
                  connectNulls
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            Does v_t say anything spot does not?
            {corr?.interpretation ? (
              <Badge variant="outline" className="ml-2 font-mono text-[10px]">
                {corr.interpretation}
              </Badge>
            ) : null}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {corrReady ? (
            <>
              <div className="h-[180px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={lagRows} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                    <XAxis dataKey="lag_min" tick={{ fontSize: 10 }} />
                    <YAxis
                      domain={[-1, 1]}
                      tick={{ fontSize: 10 }}
                      tickFormatter={(v) => Number(v).toFixed(1)}
                      width={56}
                    />
                    <Tooltip
                      contentStyle={{ fontSize: 11 }}
                      formatter={(v: number | string) => Number(v).toFixed(3)}
                    />
                    <ReferenceLine y={0} stroke="currentColor" opacity={0.4} />
                    <Bar dataKey="corr" fill="#0d9488" isAnimationActive={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                Correlation of v_t against the absolute one-minute index return, by lag. Positive
                lag means v_t moved first. A tall bar at lag 0 with no lead would mean v_t is
                largely a restatement of index speed — computable without options data at all.
              </p>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              {corr?.interpretation ?? "No correlation yet."} A correlation over a handful of
              minutes is noise, so this waits for a fuller session.
            </p>
          )}
        </CardContent>
      </Card>

      <p className="text-[11px] text-muted-foreground">
        Feature per arXiv 2608.05373 eq. 1, grouped by (session, expiry, strike, type). Delta uses
        each minute's own spot. Read-only: this desk never places orders.
      </p>
    </div>
  );
}
