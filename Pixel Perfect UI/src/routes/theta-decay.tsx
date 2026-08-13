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
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import type {
  ThetaCaptureBucket,
  ThetaCaptureQuality,
  ThetaDecayChart,
  ThetaDecayStatus,
  ThetaVelocityChart,
} from "@/lib/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export const Route = createFileRoute("/theta-decay")({
  component: ThetaDecayPage,
});

const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "SENSEX"] as const;
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

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return `${v.toFixed(digits)}%`;
}

const QUALITY_LABEL: Record<ThetaCaptureQuality, string> = {
  ok: "usable",
  too_few_windows: "too few windows to judge yet",
  theta_too_small: "theta below the noise floor",
  vega_dominated: "vol term swamps the decomposition",
  no_data: "no data",
};

function qualityTone(q: ThetaCaptureQuality): string {
  return q === "ok" ? "text-foreground" : "text-muted-foreground line-through decoration-1";
}

/** Capture near 1.0 is the interesting case; colour only the extremes. */
function captureTone(row: ThetaCaptureBucket): string {
  if (row.quality !== "ok" || row.capture == null) return "text-muted-foreground";
  if (row.capture < 0.5 || row.capture > 1.5) return "text-amber-600 dark:text-amber-400";
  return "text-emerald-600 dark:text-emerald-400";
}

function changeTone(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-foreground";
  return v > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400";
}

function ThetaDecayPage() {
  const [underlying, setUnderlying] = useState<string>("NIFTY");
  const [expiry, setExpiry] = useState<string | null>(null);
  const [showVelocity, setShowVelocity] = useState(false);
  const [chart, setChart] = useState<ThetaDecayChart | null>(null);
  const [status, setStatus] = useState<ThetaDecayStatus | null>(null);
  const [velocity, setVelocity] = useState<ThetaVelocityChart | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    // allSettled, not all — same reasoning as the Delta Velocity page: one read
    // failing must not blank the others.
    const [c, s] = await Promise.allSettled([
      api.get<ThetaDecayChart>(
        `/decay/chart?underlying=${underlying}${expiry ? `&expiry=${expiry}` : ""}`,
      ),
      api.get<ThetaDecayStatus>(`/decay/status?underlying=${underlying}`),
    ]);
    if (c.status === "fulfilled") setChart(c.value);
    if (s.status === "fulfilled") setStatus(s.value);

    const failures = [c, s]
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

  // Velocity is a separate ~2s call behind its own endpoint; only fetch it if
  // the panel is actually open.
  useEffect(() => {
    if (!showVelocity) return;
    let cancelled = false;
    void api
      .get<ThetaVelocityChart>(
        `/decay/velocity?underlying=${underlying}${expiry ? `&expiry=${expiry}` : ""}`,
      )
      .then((v) => {
        if (!cancelled) setVelocity(v);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [showVelocity, underlying, expiry]);

  const ctx = chart?.context ?? null;
  const rows = chart?.minutes ?? [];
  const capture = chart?.capture;
  const usable = (capture?.by_dte ?? []).filter((r) => r.quality === "ok");

  const nearest = chart?.burn_by_dte?.[0] ?? null;
  const lastStraddle = useMemo(() => {
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      if (rows[i].burn_straddle != null) return rows[i].burn_straddle;
    }
    return null;
  }, [rows]);

  const ladder = useMemo(() => {
    const byStrike = new Map<number, { strike: number; ce: number | null; pe: number | null }>();
    for (const row of chart?.burn_by_strike ?? []) {
      const entry = byStrike.get(row.strike) ?? { strike: row.strike, ce: null, pe: null };
      if (row.option_type === "CE") entry.ce = row.burn_pct_day;
      else entry.pe = row.burn_pct_day;
      byStrike.set(row.strike, entry);
    }
    return [...byStrike.values()].sort((a, b) => a.strike - b.strike);
  }, [chart]);

  const velocityRows = velocity?.minutes ?? [];
  const lagRows = (velocity?.correlation?.lag_profile ?? []).filter((p) => p.corr != null);

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">
            Theta Decay
            {chart?.session_date ? (
              <Badge variant="outline" className="ml-2 align-middle font-mono text-[10px]">
                session {chart.session_date}
              </Badge>
            ) : null}
          </h1>
          <p className="text-xs text-muted-foreground">
            How fast premium bleeds, and how much of it the tape actually hands over. Read-only —
            this desk never places orders.
            {chart?.session_date &&
            chart.session_date !== new Date().toISOString().slice(0, 10)
              ? " Showing the latest archived session — today has not been collected yet."
              : ""}
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
          {chart.expiries.map((e) => (
            <Button
              key={e}
              size="sm"
              variant={expiry === e ? "default" : "outline"}
              onClick={() => setExpiry(e)}
            >
              {e}
            </Button>
          ))}
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
          label="ATM straddle burn"
          value={fmtPct(lastStraddle, 2)}
          hint="per calendar day, % of premium"
          tone="text-amber-600 dark:text-amber-400"
        />
        <Stat
          label="Nearest expiry burn"
          value={nearest ? fmtPct(nearest.p50, 2) : "—"}
          hint={nearest ? `median, ${nearest.dte}d to expiry` : "no bucket"}
        />
        <Stat
          label="Decay capture"
          value={
            usable.length
              ? fmtNum(usable.reduce((a, r) => a + (r.capture ?? 0), 0) / usable.length, 2)
              : "—"
          }
          hint={
            usable.length
              ? `mean of ${usable.length} usable bucket(s)`
              : "no bucket passed the gate"
          }
        />
        <Stat
          label="Collector"
          value={status?.collector_alive ? "running" : "stopped"}
          hint={`${status?.coverage?.sessions ?? 0} session(s) archived`}
          tone={
            status?.collector_alive
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-red-600 dark:text-red-400"
          }
        />
      </div>

      <p className="-mt-2 text-[11px] text-muted-foreground">
        Burn rate is <span className="font-mono">−theta / premium</span>, so it is comparable
        across a ₹5 wing and a ₹200 straddle where absolute theta is not. It tracks{" "}
        <span className="font-mono">1/T</span> almost exactly. This desk has no collector of its
        own — it reads the{" "}
        <Link to="/delta-velocity" className="underline underline-offset-2">
          Delta Velocity
        </Link>{" "}
        archive and re-derives greeks at zero carry.
      </p>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            Burn rate through the session
            <span className="ml-2 text-[10px] font-normal text-muted-foreground">
              ATM straddle re-struck each minute; median across all tracked strikes underneath
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="h-[240px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="clock" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
                  width={56}
                />
                <Tooltip
                  contentStyle={{ fontSize: 11 }}
                  formatter={(v: number | string) => `${Number(v).toFixed(2)}%/day`}
                />
                <Line
                  type="monotone"
                  dataKey="burn_straddle"
                  name="ATM straddle"
                  stroke="#d97706"
                  strokeWidth={1.8}
                  dot={false}
                  connectNulls
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="burn_med"
                  name="median strike"
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

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Burn rate by time to expiry</CardTitle>
            <p className="text-[11px] text-muted-foreground">
              ATM theta ∝ σ/√T and ATM premium ∝ σ√T, so theta/premium scales as 1/T — halving
              the time to expiry roughly doubles the daily burn.
            </p>
          </CardHeader>
          <CardContent>
            <table className="w-full text-xs">
              <thead className="text-muted-foreground">
                <tr className="border-b">
                  <th className="py-1 text-left font-normal">DTE</th>
                  <th className="py-1 text-left font-normal">Expiry</th>
                  <th className="py-1 text-right font-normal">Median</th>
                  <th className="py-1 text-right font-normal">p95</th>
                  <th className="py-1 text-right font-normal">n</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {(chart?.burn_by_dte ?? []).map((b) => (
                  <tr key={b.expiry} className="border-b last:border-0">
                    <td className="py-1">{b.dte}d</td>
                    <td className="py-1 text-muted-foreground">{b.expiry}</td>
                    <td className="py-1 text-right font-semibold text-amber-600 dark:text-amber-400">
                      {fmtPct(b.p50, 2)}
                    </td>
                    <td className="py-1 text-right text-muted-foreground">{fmtPct(b.p95, 2)}</td>
                    <td className="py-1 text-right text-muted-foreground">{b.n}</td>
                  </tr>
                ))}
                {!chart?.burn_by_dte?.length ? (
                  <tr>
                    <td colSpan={5} className="py-3 text-center text-muted-foreground">
                      No buckets yet.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              Decay capture
              {capture?.horizon_min ? (
                <Badge variant="outline" className="ml-2 font-mono text-[10px]">
                  {capture.horizon_min}-min windows
                </Badge>
              ) : null}
            </CardTitle>
            <p className="text-[11px] text-muted-foreground">
              Of the decay the model quoted, how much the tape handed over. 1.0 is exact; below 1
              means premium held up better than theory.
            </p>
          </CardHeader>
          <CardContent>
            <table className="w-full text-xs">
              <thead className="text-muted-foreground">
                <tr className="border-b">
                  <th className="py-1 text-left font-normal">DTE</th>
                  <th className="py-1 text-right font-normal">Capture</th>
                  <th className="py-1 text-right font-normal">θ share</th>
                  <th className="py-1 text-right font-normal">ν share</th>
                  <th className="py-1 text-left font-normal">Quality</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {(capture?.by_dte ?? []).map((r) => (
                  <tr key={r.dte} className="border-b last:border-0">
                    <td className={`py-1 ${qualityTone(r.quality)}`}>{r.dte}d</td>
                    <td className={`py-1 text-right font-semibold ${captureTone(r)}`}>
                      {r.capture == null ? "—" : r.capture.toFixed(2)}
                    </td>
                    <td className="py-1 text-right text-muted-foreground">
                      {r.theta_share == null ? "—" : `${(r.theta_share * 100).toFixed(1)}%`}
                    </td>
                    <td className="py-1 text-right text-muted-foreground">
                      {r.vega_share == null ? "—" : `${(r.vega_share * 100).toFixed(0)}%`}
                    </td>
                    <td className="py-1 font-sans text-[10px] text-muted-foreground">
                      {QUALITY_LABEL[r.quality]}
                    </td>
                  </tr>
                ))}
                {!capture?.by_dte?.length ? (
                  <tr>
                    <td colSpan={5} className="py-3 text-center text-muted-foreground">
                      {capture?.note ?? "No windows yet."}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
            {capture?.by_dte?.length ? (
              <p className="mt-2 text-[10px] text-muted-foreground">{capture.note}</p>
            ) : null}
            <p className="mt-2 text-[10px] text-muted-foreground">
              IV here is inverted from the same price being decomposed, so delta and vega alone
              reproduce a one-minute move with R² above 0.95 and theta is left with under 1% of
              it. Hence the long window, and hence the quality column — a bucket where theta is
              below the noise floor, or where the vol term swamps it, is struck through rather
              than hidden.
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            Burn rate across the strike ladder
            <span className="ml-2 text-[10px] font-normal text-muted-foreground">
              at the session&apos;s last observation
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="h-[220px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={ladder} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="strike" tick={{ fontSize: 10 }} minTickGap={20} />
                <YAxis
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
                  width={56}
                />
                <Tooltip
                  contentStyle={{ fontSize: 11 }}
                  formatter={(v: number | string) => `${Number(v).toFixed(2)}%/day`}
                />
                {chart?.atm_strike != null ? (
                  <ReferenceLine
                    x={chart.atm_strike}
                    stroke="currentColor"
                    strokeDasharray="4 4"
                    opacity={0.4}
                  />
                ) : null}
                <Bar dataKey="ce" name="CE" fill="#0d9488" isAnimationActive={false} />
                <Bar dataKey="pe" name="PE" fill="#7c3aed" isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="px-4 pb-3 text-[10px] text-muted-foreground">
            Legs priced under ₹{status?.defaults?.min_premium ?? 5} are blanked — one tick is a
            large fraction of their value, so theta/premium there is noise with a decimal point.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center justify-between text-sm">
            <span>
              Theta velocity
              <span className="ml-2 text-[10px] font-normal text-muted-foreground">
                the Delta Velocity analogue, with the deterministic clock removed
              </span>
            </span>
            <Button size="sm" variant="outline" onClick={() => setShowVelocity((v) => !v)}>
              {showVelocity ? "Hide" : "Load"}
            </Button>
          </CardTitle>
        </CardHeader>
        {showVelocity ? (
          <CardContent>
            {velocity?.correlation?.interpretation ? (
              <p className="mb-2 text-[11px] text-muted-foreground">
                Lag profile against |1-min spot return|:{" "}
                <span className="font-mono">{velocity.correlation.interpretation}</span>
                {velocity.correlation.best_corr != null ? (
                  <>
                    {" "}
                    at r = {velocity.correlation.best_corr.toFixed(3)}. Weak, and it lags rather
                    than leads — this panel is a diagnostic, not a signal.
                  </>
                ) : null}
              </p>
            ) : null}
            <div className="h-[180px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={velocityRows} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                  <XAxis dataKey="clock" tick={{ fontSize: 10 }} minTickGap={40} />
                  <YAxis
                    tick={{ fontSize: 10 }}
                    tickFormatter={(v) => Number(v).toFixed(2)}
                    width={56}
                  />
                  <Tooltip
                    contentStyle={{ fontSize: 11 }}
                    formatter={(v: number | string) => Number(v).toFixed(5)}
                  />
                  <Line
                    type="monotone"
                    dataKey="tau_max"
                    name="max"
                    stroke="#7c3aed"
                    strokeWidth={1.6}
                    dot={false}
                    connectNulls
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="tau_med"
                    name="median"
                    stroke="#94a3b8"
                    strokeWidth={1}
                    dot={false}
                    connectNulls
                    isAnimationActive={false}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            {lagRows.length ? (
              <div className="mt-3 h-[140px] w-full">
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
            ) : null}
            {velocity?.blanks ? (
              <p className="mt-2 font-mono text-[10px] text-muted-foreground">
                blanks: {velocity.blanks}
              </p>
            ) : null}
          </CardContent>
        ) : (
          <CardContent className="text-sm text-muted-foreground">
            Costs a separate ~2s read, and it is the weakest of the three metrics — measured
            2026-08-12 it correlates 0.12–0.16 with spot moves and lags them by 6–9 minutes. Load
            it if you want the diagnostic.
          </CardContent>
        )}
      </Card>

      <p className="text-[11px] text-muted-foreground">
        Greeks are re-derived from the archived implied vol at zero dividend yield, matching the
        Black-Scholes solve that produced that IV — the greeks engine&apos;s 0.012 default would
        shift ATM theta by about 5%. Theta is calendar-mode. Read-only: this desk never places
        orders.
      </p>
    </div>
  );
}
