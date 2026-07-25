import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import type {
  AnalogueConfig,
  AnalogueCycleKind,
  AnalogueSnapshot,
  OiUnderlying,
} from "@/lib/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const Route = createFileRoute("/analogue")({
  component: AnaloguePage,
});

const DEFAULT_UNDERLYINGS: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return Number(v).toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function FanTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { dataKey?: string | number; name?: string; value?: number; color?: string }[];
  label?: string | number;
}) {
  if (!active || !payload?.length) return null;
  const want = new Set(["current", "median", "p25", "p75"]);
  const rows = payload.filter(
    (p) => p.dataKey != null && want.has(String(p.dataKey)) && p.value != null && !Number.isNaN(Number(p.value)),
  );
  if (!rows.length) return null;

  const order = ["current", "median", "p25", "p75"];
  rows.sort((a, b) => order.indexOf(String(a.dataKey)) - order.indexOf(String(b.dataKey)));

  const labels: Record<string, string> = {
    current: "Current cycle",
    median: "Analogue median",
    p25: "25th pct",
    p75: "75th pct",
  };

  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="mb-1.5 border-b pb-1 font-semibold">Day {label}</div>
      {rows.map((p) => (
        <div key={String(p.dataKey)} className="flex justify-between gap-4 font-mono" style={{ color: p.color }}>
          <span>{labels[String(p.dataKey)] ?? p.name}</span>
          <span>{fmtPct(Number(p.value))}</span>
        </div>
      ))}
    </div>
  );
}

function FanChart({ snap }: { snap: AnalogueSnapshot }) {
  const dayNow = snap.day_in_cycle;

  const chartData = useMemo(() => {
    const byDay = new Map<
      number,
      { day: number; current?: number; median?: number; p25?: number; p75?: number }
    >();

    const ensure = (day: number) => {
      let row = byDay.get(day);
      if (!row) {
        row = { day };
        byDay.set(day, row);
      }
      return row;
    };

    for (const p of snap.current_path ?? []) ensure(p.day).current = p.cum_pct;
    for (const p of snap.median_path ?? []) ensure(p.day).median = p.cum_pct;
    for (const p of snap.p25_path ?? []) ensure(p.day).p25 = p.cum_pct;
    for (const p of snap.p75_path ?? []) ensure(p.day).p75 = p.cum_pct;

    return [...byDay.values()].sort((a, b) => a.day - b.day);
  }, [snap]);

  const analogues = (snap.analogue_paths ?? []).slice(0, 36);

  return (
    <div className="h-[420px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={chartData} margin={{ top: 12, right: 16, left: 8, bottom: 18 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
          <XAxis
            dataKey="day"
            type="number"
            domain={["dataMin", "dataMax"]}
            tick={{ fontSize: 11 }}
            allowDecimals={false}
            label={{
              value: "Trading day in cycle (0 = first day after prev expiry)",
              position: "insideBottom",
              offset: -4,
              fontSize: 11,
            }}
          />
          <YAxis
            tick={{ fontSize: 11 }}
            tickFormatter={(v) => `${v}%`}
            width={48}
          />
          <Tooltip
            content={<FanTooltip />}
            cursor={{ stroke: "#94a3b8", strokeDasharray: "4 4" }}
            isAnimationActive={false}
          />
          <Legend
            wrapperStyle={{ fontSize: 12 }}
            payload={[
              { value: "Current cycle", type: "line", color: "#e2e8f0" },
              { value: "Analogue median", type: "line", color: "#f97316" },
              { value: "25th / 75th", type: "line", color: "#fb923c" },
              { value: "Ended up", type: "line", color: "rgba(16,185,129,0.7)" },
              { value: "Ended down", type: "line", color: "rgba(239,68,68,0.7)" },
            ]}
          />
          <ReferenceLine
            x={dayNow}
            stroke="#94a3b8"
            strokeDasharray="4 4"
            label={{ value: `Today (day ${dayNow})`, fontSize: 10, fill: "#94a3b8" }}
          />

          {analogues.map((a, idx) => (
            <Line
              key={`${a.expiry}-${idx}`}
              data={a.path}
              type="monotone"
              dataKey="cum_pct"
              stroke={a.ended_up ? "rgba(16,185,129,0.3)" : "rgba(239,68,68,0.3)"}
              strokeWidth={1}
              dot={false}
              legendType="none"
              tooltipType="none"
              isAnimationActive={false}
              activeDot={false}
              name={a.ended_up ? "_up" : "_dn"}
            />
          ))}

          <Line
            type="monotone"
            dataKey="p25"
            stroke="#fb923c"
            strokeWidth={1.25}
            strokeDasharray="2 3"
            dot={false}
            name="25th"
            isAnimationActive={false}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="p75"
            stroke="#fb923c"
            strokeWidth={1.25}
            strokeDasharray="2 3"
            dot={false}
            name="75th"
            isAnimationActive={false}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="median"
            stroke="#f97316"
            strokeWidth={2.5}
            strokeDasharray="6 4"
            dot={false}
            name="Analogue median"
            isAnimationActive={false}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="current"
            stroke="#e2e8f0"
            strokeWidth={2.5}
            dot={{ r: 3, fill: "#e2e8f0" }}
            activeDot={{ r: 5 }}
            name="Current cycle"
            isAnimationActive={false}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function AnaloguePage() {
  const [config, setConfig] = useState<AnalogueConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [cycleKind, setCycleKind] = useState<AnalogueCycleKind>("monthly");
  const [band, setBand] = useState(4);
  const [overrideOn, setOverrideOn] = useState(false);
  const [overrideMove, setOverrideMove] = useState("");
  const [auto, setAuto] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [snap, setSnap] = useState<AnalogueSnapshot | null>(null);

  useEffect(() => {
    api
      .get<AnalogueConfig>("/analogue/config", { silent: true })
      .then((c) => {
        setConfig(c);
        if (c.default_cycle_kind === "weekly" || c.default_cycle_kind === "monthly") {
          setCycleKind(c.default_cycle_kind);
        }
        if (typeof c.default_similarity_band_pct === "number") {
          setBand(c.default_similarity_band_pct);
        }
      })
      .catch(() => {});
  }, []);

  const underlyings = config?.underlyings?.length
    ? (config.underlyings as OiUnderlying[])
    : DEFAULT_UNDERLYINGS;
  const bandMin = config?.similarity_band_min ?? 0.5;
  const bandMax = config?.similarity_band_max ?? 15;

  const fetchSnap = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const q = new URLSearchParams({
        underlying,
        cycle_kind: cycleKind,
        similarity_band_pct: String(band),
      });
      if (overrideOn && overrideMove.trim() !== "") {
        const v = Number(overrideMove);
        if (Number.isFinite(v)) q.set("override_move_pct", String(v));
      }
      const data = await api.get<AnalogueSnapshot>(`/analogue/snapshot?${q}`);
      setSnap(data);
    } catch (e: unknown) {
      setSnap(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [underlying, cycleKind, band, overrideOn, overrideMove]);

  useEffect(() => {
    void fetchSnap();
  }, [fetchSnap]);

  useEffect(() => {
    if (!auto || !config) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSnap();
    }, (config.refresh_seconds ?? 300) * 1000);
    return () => clearInterval(id);
  }, [auto, config, fetchSnap]);

  const stats = snap?.stats;

  return (
    <div className="mx-auto flex max-w-[1280px] flex-col gap-6 pb-10 p-4 md:p-6">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Where can price go from here?
            {snap?.label ? (
              <span className="text-muted-foreground"> — {snap.label}</span>
            ) : null}
          </h1>
          <p className="text-sm text-muted-foreground max-w-2xl">
            Match historical expiry cycles with a similar % move at the same day-in-cycle, then fan
            remaining paths to expiry. Expiry weekdays follow NSE/BSE Sep-2025 revision (NIFTY /
            BANKNIFTY → Tuesday, SENSEX → Thursday). Read-only.
          </p>
        </div>
        <Badge variant="outline">Complementary</Badge>
      </header>

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
            <Label>Cycle</Label>
            <Select
              value={cycleKind}
              onValueChange={(v) => setCycleKind(v as AnalogueCycleKind)}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="monthly">Monthly</SelectItem>
                <SelectItem value="weekly">Weekly</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1 min-w-[180px]">
            <Label>Similarity band (±{band.toFixed(2)}%)</Label>
            <input
              type="range"
              min={bandMin}
              max={bandMax}
              step={0.25}
              value={band}
              onChange={(e) => setBand(Number(e.target.value))}
              className="w-full accent-orange-500"
            />
          </div>
          <div className="space-y-1">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={overrideOn}
                onCheckedChange={(c) => setOverrideOn(c === true)}
              />
              Override move %
            </label>
            <Input
              className="w-[110px] font-mono"
              disabled={!overrideOn}
              value={overrideMove}
              placeholder={snap ? String(snap.move_so_far_pct.toFixed(2)) : "0.00"}
              onChange={(e) => setOverrideMove(e.target.value)}
            />
          </div>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <Checkbox checked={auto} onCheckedChange={(c) => setAuto(c === true)} />
            Auto refresh
          </label>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void fetchSnap()}
            disabled={loading}
          >
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setAuto((a) => !a)}>
            {auto ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          </Button>
          <Button variant="link" size="sm" asChild>
            <Link to="/rrg">RRG →</Link>
          </Button>
        </CardContent>
      </Card>

      {error ? (
        <Card className="border-destructive/50">
          <CardContent className="py-4 text-sm text-destructive">
            {error}
            {/session|login|auth|401|403/i.test(error) ? (
              <>
                {" "}
                <Link to="/login" className="underline">
                  Login
                </Link>{" "}
                if session expired.
              </>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {snap ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Card>
              <CardContent className="py-3">
                <div className="text-[11px] uppercase text-muted-foreground">Cycle start</div>
                <div className="font-mono text-sm font-semibold">
                  {snap.cycle_pending
                    ? "Next session"
                    : (snap.cycle_start ?? "—")}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Prev expiry {snap.prev_expiry}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="py-3">
                <div className="text-[11px] uppercase text-muted-foreground">Current expiry</div>
                <div className="font-mono text-sm font-semibold">{snap.current_expiry}</div>
                <div className="text-[10px] text-muted-foreground">
                  {snap.cycle_kind}
                  {snap.cycle_pending ? " · pending" : ""}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="py-3">
                <div className="text-[11px] uppercase text-muted-foreground">Day in cycle</div>
                <div className="font-mono text-lg font-semibold">
                  {snap.cycle_pending ? "0 (pending)" : snap.day_in_cycle}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {snap.cycle_pending
                    ? "Starts next session"
                    : `${snap.days_remaining} days left`}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="py-3">
                <div className="text-[11px] uppercase text-muted-foreground">Move so far</div>
                <div className="font-mono text-lg font-semibold">
                  {fmtPct(snap.move_so_far_pct)}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  Spot {fmt(snap.spot, 2)} · start {fmt(snap.cycle_start_px, 2)}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="py-3">
                <div className="text-[11px] uppercase text-muted-foreground">Matched</div>
                <div className="font-mono text-lg font-semibold">{snap.matched}</div>
                <div className="text-[10px] text-muted-foreground">
                  ±{snap.similarity_band_pct.toFixed(2)}% @ day {snap.day_in_cycle}
                </div>
              </CardContent>
            </Card>
          </div>

          {stats ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardContent className="py-3">
                  <div className="text-[11px] uppercase text-muted-foreground">
                    Median expiry level
                  </div>
                  <div className="font-mono text-xl font-semibold">
                    {fmt(stats.median_expiry_level, 0)}
                  </div>
                  <div className="text-sm text-muted-foreground">
                    {fmtPct(stats.median_remaining_pct)} from spot
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="py-3">
                  <div className="text-[11px] uppercase text-muted-foreground">25th–75th</div>
                  <div className="font-mono text-sm font-semibold">
                    {fmt(stats.p25_expiry_level, 0)} – {fmt(stats.p75_expiry_level, 0)}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    {fmtPct(stats.p25_remaining_pct)} to {fmtPct(stats.p75_remaining_pct)}
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="py-3">
                  <div className="text-[11px] uppercase text-muted-foreground">10th–90th</div>
                  <div className="font-mono text-sm font-semibold">
                    {fmt(stats.p10_expiry_level, 0)} – {fmt(stats.p90_expiry_level, 0)}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    {fmtPct(stats.p10_remaining_pct)} to {fmtPct(stats.p90_remaining_pct)}
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="py-3">
                  <div className="text-[11px] uppercase text-muted-foreground">
                    P(further up / down)
                  </div>
                  <div className="flex gap-4 pt-1">
                    <div>
                      <div className="font-mono text-xl font-semibold text-emerald-500">
                        {(stats.p_further_up * 100).toFixed(0)}%
                      </div>
                      <div className="text-[10px] text-muted-foreground">Further up</div>
                    </div>
                    <div>
                      <div className="font-mono text-xl font-semibold text-red-500">
                        {(stats.p_further_down * 100).toFixed(0)}%
                      </div>
                      <div className="text-[10px] text-muted-foreground">Further down</div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          ) : null}

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-base">Analogue path fan</CardTitle>
              <p className="text-xs text-muted-foreground">
                White = current cycle · Orange dashed = analogue median · Shaded = 25–75 band ·
                Green/red thin = historical matches (ended higher / lower from today&apos;s day).
              </p>
            </CardHeader>
            <CardContent>
              <FanChart snap={snap} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-base">How to read</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-muted-foreground">
              {(snap.reasoning ?? []).map((line, i) => (
                <p key={i}>{line}</p>
              ))}
              <p>
                Similarity band keeps only past cycles whose cumulative move at day{" "}
                {snap.day_in_cycle} was within ±{snap.similarity_band_pct.toFixed(2)}% of the move
                used for matching ({fmtPct(snap.move_used_for_match_pct)}). Widen the band for more
                matches; tighten for closer analogues.
              </p>
              {snap.disclaimer ? (
                <p className="text-[10px] text-muted-foreground/80 pt-2">{snap.disclaimer}</p>
              ) : null}
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}
