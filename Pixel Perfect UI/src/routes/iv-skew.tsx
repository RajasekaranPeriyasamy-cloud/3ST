import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Pause, Play, RefreshCw } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  IvSkewConfig,
  IvSkewDailySeries,
  IvSkewExpiry,
  IvSkewSeries,
  IvSkewSnapshot,
  OiUnderlying,
} from "@/lib/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip as UiTooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export const Route = createFileRoute("/iv-skew")({
  component: IvSkewPage,
});

const FALLBACK_UNDERLYINGS: OiUnderlying[] = [
  "NIFTY",
  "BANKNIFTY",
  "SENSEX",
  "CRUDEOIL",
  "CRUDEOILM",
  "NATURALGAS",
];

const CALL_COLOR = "#6366f1";
const PUT_COLOR = "#ef4444";
const POSITIVE = "#10b981";
const NEGATIVE = "#f97316";

function fmt(value: number | null | undefined, digits = 2, suffix = ""): string {
  return value == null ? "—" : `${value.toFixed(digits)}${suffix}`;
}

function signed(value: number | null | undefined, digits = 2): string {
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

/** Plain-language read of the risk reversal — the thing the desk exists to answer. */
function readSkew(rr: number | null | undefined): { text: string; tone: string } {
  if (rr == null) return { text: "No reading", tone: "text-muted-foreground" };
  if (rr > 0.25) return { text: "Calls bid — upside tail is the expensive one", tone: "text-emerald-500" };
  if (rr < -0.25) return { text: "Puts bid — defensive skew", tone: "text-orange-500" };
  return { text: "Flat — neither tail is favoured", tone: "text-muted-foreground" };
}

function ConfidenceBadge({ row }: { row: IvSkewExpiry }) {
  if (!row.warnings?.length) {
    return (
      <Badge variant="outline" className="border-emerald-500/40 text-emerald-500">
        clean
      </Badge>
    );
  }
  return (
    <TooltipProvider>
      <UiTooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="outline"
            className={cn(
              "cursor-help gap-1",
              row.ok ? "border-amber-500/40 text-amber-500" : "border-destructive/40 text-destructive",
            )}
          >
            <AlertTriangle className="h-3 w-3" />
            {row.confidence}
          </Badge>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          <ul className="list-disc space-y-1 pl-4 text-xs">
            {row.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </TooltipContent>
      </UiTooltip>
    </TooltipProvider>
  );
}

function IvSkewPage() {
  const [config, setConfig] = useState<IvSkewConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [snap, setSnap] = useState<IvSkewSnapshot | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [auto, setAuto] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [daily, setDaily] = useState<IvSkewDailySeries | null>(null);
  const [intraday, setIntraday] = useState<IvSkewSeries | null>(null);
  const [rank, setRank] = useState(0);
  const [cleanOnly, setCleanOnly] = useState(true);

  useEffect(() => {
    api.get<IvSkewConfig>("/skew/config", { silent: true }).then(setConfig).catch(() => {});
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<IvSkewSnapshot>(
        `/skew/snapshot?underlying=${encodeURIComponent(underlying)}`,
        { silent: true },
      );
      setSnap(data);
    } catch (e) {
      setSnap(null);
      setError(e instanceof Error ? e.message : "Failed to load IV skew");
    } finally {
      setLoading(false);
    }
  }, [underlying]);

  // History reads the archive only — no Kite — so it is deliberately separate
  // from the live snapshot poll and survives the daily token expiry.
  const refreshHistory = useCallback(async () => {
    const params = `underlying=${encodeURIComponent(underlying)}&rank=${rank}`;
    const [d, s] = await Promise.allSettled([
      api.get<IvSkewDailySeries>(`/skew/daily?${params}&clean_only=${cleanOnly}`, { silent: true }),
      api.get<IvSkewSeries>(`/skew/series?underlying=${encodeURIComponent(underlying)}`, {
        silent: true,
      }),
    ]);
    setDaily(d.status === "fulfilled" ? d.value : null);
    setIntraday(s.status === "fulfilled" ? s.value : null);
  }, [underlying, rank, cleanOnly]);

  // Drop the previous underlying's data the moment the selection changes. A
  // snapshot takes ~2.6s warm and up to 16s cold, and leaving NIFTY numbers
  // sitting under a NATURALGAS header for that long is worse than showing
  // nothing — it reads as a live quote for the wrong instrument.
  useEffect(() => {
    setSnap(null);
    setSelected("");
    setDaily(null);
    setIntraday(null);
  }, [underlying]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  useEffect(() => {
    if (!auto || !config) return;
    const id = window.setInterval(() => void refresh(), (config.refresh_seconds ?? 60) * 1000);
    return () => window.clearInterval(id);
  }, [auto, config, refresh]);

  // Keep a valid selection as the underlying (and so the expiry list) changes.
  useEffect(() => {
    const rows = snap?.expiries ?? [];
    if (!rows.length) {
      setSelected("");
      return;
    }
    if (!rows.some((r) => r.expiry === selected)) {
      setSelected((rows.find((r) => r.ok) ?? rows[0]).expiry);
    }
  }, [snap, selected]);

  const underlyings = config?.underlyings?.length ? config.underlyings : FALLBACK_UNDERLYINGS;
  const rows = snap?.expiries ?? [];
  const row = rows.find((r) => r.expiry === selected) ?? null;
  const targetPct = Math.round((snap?.target_delta ?? config?.target_delta ?? 0.25) * 100);

  const smile = useMemo(() => {
    return (row?.points ?? []).map((p) => ({
      strike: p.strike,
      call_iv: p.option_type === "CE" ? p.iv : null,
      put_iv: p.option_type === "PE" ? p.iv : null,
    }));
  }, [row]);

  // Clean rows only. A degraded expiry can print an RR an order of magnitude
  // off (NATURALGAS 42 DTE printed −12.35 against clean readings of +2.09 and
  // +0.01) and its axis range flattens the real ones into a straight line. The
  // hidden count is captioned rather than dropped silently.
  const resolved = useMemo(() => rows.filter((r) => r.ok && r.risk_reversal != null), [rows]);
  const termStructure = useMemo(
    () =>
      resolved
        .filter((r) => r.confidence === "clean")
        .map((r) => ({
          label: `${r.dte}d`,
          expiry: r.expiry,
          rr: r.risk_reversal as number,
        })),
    [resolved],
  );
  const termHidden = resolved.length - termStructure.length;

  const dailyData = useMemo(
    () =>
      (daily?.points ?? []).map((p) => ({
        date: p.date.slice(5),
        rr: p.rr,
        fly: p.fly,
        dte: p.dte,
        expiry: p.expiry,
      })),
    [daily],
  );

  const intradayData = useMemo(() => {
    const rows = intraday?.points ?? [];
    return rows
      .map((s) => {
        const match = s.expiries.find((e) => e.rank === rank && e.ok);
        return match ? { ts: s.ts.slice(11, 16), rr: match.rr ?? null } : null;
      })
      .filter((d): d is { ts: string; rr: number | null } => d !== null);
  }, [intraday, rank]);

  const read = readSkew(row?.risk_reversal);

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">IV Skew</h1>
          <p className="text-sm text-muted-foreground">
            {targetPct}Δ risk reversal and butterfly, per expiry — priced off the forward, not spot
          </p>
        </div>
        <Link to="/iv-smile" className="text-sm text-primary hover:underline">
          IV Smile →
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
              <SelectTrigger className="w-[160px]">
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
            <Select value={selected} onValueChange={setSelected} disabled={!rows.length}>
              <SelectTrigger className="w-[190px]">
                <SelectValue placeholder={loading ? "Loading…" : "No expiries"} />
              </SelectTrigger>
              <SelectContent>
                {rows.map((r) => (
                  <SelectItem key={r.expiry} value={r.expiry}>
                    {r.expiry} ({r.dte}d)
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
              <RefreshCw className={`mr-1 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <Button variant={auto ? "default" : "outline"} size="sm" onClick={() => setAuto((a) => !a)}>
              {auto ? <Pause className="mr-1 h-4 w-4" /> : <Play className="mr-1 h-4 w-4" />}
              {auto ? "Auto" : "Paused"}
            </Button>
          </div>
          {snap && (
            <div className="ml-auto text-right text-xs text-muted-foreground">
              <div>
                {snap.reference_source === "future" ? "Front future" : "Spot"}{" "}
                <span className="font-medium text-foreground">{snap.reference.toLocaleString()}</span>
              </div>
              <div>Updated {snap.updated_at.slice(11, 19)}</div>
            </div>
          )}
        </CardContent>
      </Card>

      {error && (
        <Card className="border-destructive/50">
          <CardContent className="pt-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {row?.ok && (
        <>
          <div className="grid gap-4 md:grid-cols-4">
            <Card>
              <CardContent className="pt-4">
                <p className="text-xs text-muted-foreground">{targetPct}Δ risk reversal</p>
                <p
                  className={cn(
                    "text-2xl font-semibold",
                    (row.risk_reversal ?? 0) > 0.25 && "text-emerald-500",
                    (row.risk_reversal ?? 0) < -0.25 && "text-orange-500",
                  )}
                >
                  {signed(row.risk_reversal)}
                </p>
                <p className={cn("text-xs", read.tone)}>{read.text}</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <p className="text-xs text-muted-foreground">{targetPct}Δ butterfly</p>
                <p className="text-2xl font-semibold">{signed(row.butterfly)}</p>
                <p className="text-xs text-muted-foreground">
                  wings vs ATM — tail convexity bid
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <p className="text-xs text-muted-foreground">ATM IV</p>
                <p className="text-2xl font-semibold">{fmt(row.atm_iv, 2, "%")}</p>
                <p className="text-xs text-muted-foreground">
                  parity gap {fmt(row.atm_parity_gap, 3)} — 0 means the forward is right
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <p className="text-xs text-muted-foreground">Forward</p>
                <p className="text-2xl font-semibold">{fmt(row.forward, 2)}</p>
                <p className="text-xs text-muted-foreground">
                  basis {signed(row.forward_basis)} vs {snap?.reference_source === "future" ? "front future" : "spot"}
                </p>
              </CardContent>
            </Card>
          </div>

          {row.warnings.length > 0 && (
            <Card className="border-amber-500/40">
              <CardContent className="flex gap-3 pt-4 text-sm">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                <ul className="list-disc space-y-1 pl-4 text-muted-foreground">
                  {row.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </>
      )}

      {row && !row.ok && (
        <Card className="border-destructive/50">
          <CardContent className="pt-4 text-sm text-destructive">
            {row.expiry}: {row.error}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-4 pb-2">
          <div>
            <CardTitle className="text-base">
              Daily {targetPct}Δ risk reversal — {rank === 0 ? "nearest" : `#${rank + 1}`} expiry
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              One reading per session, taken from the day's last clean sample. Keyed by expiry
              rank, not contract — expiries roll, so watch <span className="font-medium">DTE</span>{" "}
              in the tooltip for the sawtooth.
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Select value={String(rank)} onValueChange={(v) => setRank(Number(v))}>
              <SelectTrigger className="w-[130px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="0">Nearest</SelectItem>
                <SelectItem value="1">2nd expiry</SelectItem>
                <SelectItem value="2">3rd expiry</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant={cleanOnly ? "default" : "outline"}
              size="sm"
              onClick={() => setCleanOnly((c) => !c)}
            >
              {cleanOnly ? "Clean only" : "All sessions"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="h-[300px]">
          {dailyData.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={dailyData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip
                  formatter={(value, name) => [signed(Number(value)), name]}
                  labelFormatter={(label, payload) => {
                    const p = payload?.[0]?.payload as { expiry?: string; dte?: number } | undefined;
                    return p ? `${label} · ${p.expiry} (${p.dte}d)` : String(label);
                  }}
                />
                <Legend />
                <ReferenceLine y={0} stroke="currentColor" strokeOpacity={0.5} />
                <Line
                  type="monotone"
                  dataKey="rr"
                  name={`${targetPct}Δ RR`}
                  stroke={CALL_COLOR}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                  connectNulls={false}
                />
                <Line
                  type="monotone"
                  dataKey="fly"
                  name={`${targetPct}Δ fly`}
                  stroke={POSITIVE}
                  strokeWidth={1}
                  strokeDasharray="4 4"
                  dot={false}
                  connectNulls={false}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-1 text-sm text-muted-foreground">
              <p>No completed sessions archived yet.</p>
              <p className="text-xs">
                The sampler writes every 5 minutes during market hours; a session rolls up to a
                daily point after its close.
              </p>
            </div>
          )}
        </CardContent>
        {daily?.excluded_degraded?.length ? (
          <CardContent className="pt-0 text-xs text-muted-foreground">
            {daily.excluded_degraded.length} session
            {daily.excluded_degraded.length === 1 ? "" : "s"} held back as degraded:{" "}
            {daily.excluded_degraded.slice(-6).join(", ")}
            {cleanOnly ? " — switch to All sessions to include them." : ""}
          </CardContent>
        ) : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              IV by strike — {selected || "—"}
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              OTM wings only, Black-76 off the forward. The dashed lines are the {targetPct}Δ
              readings the risk reversal is the gap between.
            </p>
          </CardHeader>
          <CardContent className="h-[380px]">
            {smile.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={smile} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                  <XAxis dataKey="strike" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} unit="%" domain={["auto", "auto"]} />
                  <Tooltip
                    formatter={(value, name) => [
                      value == null ? "—" : `${Number(value).toFixed(2)}%`,
                      name,
                    ]}
                  />
                  <Legend />
                  {row?.forward != null && (
                    <ReferenceLine
                      x={Math.round((row.forward as number) / (snap?.strike_step ?? 50)) * (snap?.strike_step ?? 50)}
                      stroke="currentColor"
                      strokeOpacity={0.4}
                      label={{ value: "F", fontSize: 11 }}
                    />
                  )}
                  {row?.call_iv != null && (
                    <ReferenceLine y={row.call_iv} stroke={CALL_COLOR} strokeDasharray="4 4" />
                  )}
                  {row?.put_iv != null && (
                    <ReferenceLine y={row.put_iv} stroke={PUT_COLOR} strokeDasharray="4 4" />
                  )}
                  <Line
                    type="monotone"
                    dataKey="put_iv"
                    name="OTM put IV"
                    stroke={PUT_COLOR}
                    dot={false}
                    connectNulls
                  />
                  <Line
                    type="monotone"
                    dataKey="call_iv"
                    name="OTM call IV"
                    stroke={CALL_COLOR}
                    dot={false}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <p className="py-16 text-center text-sm text-muted-foreground">
                {loading ? "Loading skew…" : "No resolvable legs for this expiry"}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Risk reversal by tenor</CardTitle>
            <p className="text-xs text-muted-foreground">
              Skew is not one number — it has a term structure.
              {termHidden > 0 &&
                ` ${termHidden} degraded expiry${termHidden === 1 ? "" : "s"} hidden — see the table below.`}
            </p>
          </CardHeader>
          <CardContent className="h-[380px]">
            {termStructure.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={termStructure} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                  <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(value) => [signed(Number(value)), `${targetPct}Δ RR`]} />
                  <ReferenceLine y={0} stroke="currentColor" strokeOpacity={0.5} />
                  <Bar dataKey="rr" name={`${targetPct}Δ RR`}>
                    {termStructure.map((d) => (
                      <Cell key={d.expiry} fill={d.rr >= 0 ? POSITIVE : NEGATIVE} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <p className="py-16 text-center text-sm text-muted-foreground">
                {loading ? "Loading…" : "Nothing resolved"}
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">
            Intraday path — {intraday?.session_date ?? "no session archived"}
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            {rank === 0 ? "Nearest" : `#${rank + 1}`} expiry, sampled every 5 minutes. Latest
            archived session, not necessarily today.
          </p>
        </CardHeader>
        <CardContent className="h-[260px]">
          {intradayData.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={intradayData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis dataKey="ts" tick={{ fontSize: 11 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
                <Tooltip formatter={(value) => [signed(Number(value)), `${targetPct}Δ RR`]} />
                <ReferenceLine y={0} stroke="currentColor" strokeOpacity={0.5} />
                <Line
                  type="monotone"
                  dataKey="rr"
                  name={`${targetPct}Δ RR`}
                  stroke={CALL_COLOR}
                  dot={false}
                  connectNulls={false}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-16 text-center text-sm text-muted-foreground">
              Nothing sampled yet for this underlying.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">All expiries</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="py-2 pr-4">Expiry</th>
                <th className="py-2 pr-4">DTE</th>
                <th className="py-2 pr-4 text-right">{targetPct}Δ RR</th>
                <th className="py-2 pr-4 text-right">Fly</th>
                <th className="py-2 pr-4 text-right">ATM IV</th>
                <th className="py-2 pr-4 text-right">Fwd basis</th>
                <th className="py-2 pr-4 text-right">Strikes ±</th>
                <th className="py-2 pr-4">25Δ from</th>
                <th className="py-2">Chain</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.expiry}
                  className={cn(
                    "cursor-pointer border-b last:border-0 hover:bg-muted/40",
                    r.expiry === selected && "bg-muted/60",
                  )}
                  onClick={() => setSelected(r.expiry)}
                >
                  <td className="py-2 pr-4 font-medium">{r.expiry}</td>
                  <td className="py-2 pr-4">{r.dte}</td>
                  <td
                    className={cn(
                      "py-2 pr-4 text-right font-medium tabular-nums",
                      (r.risk_reversal ?? 0) > 0.25 && "text-emerald-500",
                      (r.risk_reversal ?? 0) < -0.25 && "text-orange-500",
                    )}
                  >
                    {signed(r.risk_reversal)}
                  </td>
                  <td className="py-2 pr-4 text-right tabular-nums">{signed(r.butterfly)}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{fmt(r.atm_iv, 2, "%")}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{signed(r.forward_basis)}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{r.half_width ?? "—"}</td>
                  <td className="py-2 pr-4 text-xs text-muted-foreground">{r.quality}</td>
                  <td className="py-2">
                    <ConfidenceBadge row={r} />
                  </td>
                </tr>
              ))}
              {!rows.length && (
                <tr>
                  <td colSpan={9} className="py-8 text-center text-sm text-muted-foreground">
                    {loading ? "Loading…" : "No data"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
