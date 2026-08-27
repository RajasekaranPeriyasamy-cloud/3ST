import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type {
  BuildupCell,
  BuildupClass,
  BuildupGrid,
  BuildupRow,
  BuildupScale,
  BuildupStatus,
} from "@/lib/types";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export const Route = createFileRoute("/chain-buildup")({
  component: ChainBuildupPage,
});

const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "SENSEX"] as const;
const TIMEFRAMES = [5, 15, 30, 60] as const;
const RANGES: { value: string; label: string }[] = [
  { value: "atm5", label: "ATM ±5" },
  { value: "atm10", label: "ATM ±10" },
  { value: "atm20", label: "ATM ±20" },
  { value: "all", label: "All strikes" },
];
const POLL_MS = 60_000;
/** Same debounce OI Tracker uses, for the same reason: the alert re-evaluates on
 *  every poll, and a breach that persists is one event, not one per minute. */
const ALERT_DEBOUNCE_MS = 120_000;

/** Row and header heights are fixed so the three panes line up without a grid. */
const ROW_H = 26;
const HEAD_H = 30;
const COL_W = 66;

// CE is red, PE is green — hue encodes the *side*, not direction. Direction is
// fill (build-up) against hatching (unwinding), magnitude is intensity.
const HUE = { ce: "239,68,68", pe: "34,197,94" } as const;

function compact(v: number | null | undefined): string {
  if (v == null) return "—";
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 1e7) return `${sign}${(abs / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${sign}${(abs / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}k`;
  return `${sign}${abs.toFixed(0)}`;
}

function signed(v: number | null | undefined): string {
  if (v == null || v === 0) return v === 0 ? "0" : "—";
  return `${v > 0 ? "+" : ""}${compact(v)}`;
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

const CLASS_LABEL: Record<BuildupClass, string> = {
  long_buildup: "Long build-up",
  short_buildup: "Short build-up",
  short_covering: "Short covering",
  long_unwinding: "Long unwinding",
};

/** Shading for one cell. Clamped at p95 so one outlier cannot wash the grid. */
function cellStyle(
  value: number | null,
  scale: BuildupScale | undefined,
  side: "ce" | "pe",
): { style: React.CSSProperties; strong: boolean } {
  if (value == null || value === 0) return { style: {}, strong: false };
  const ceiling = scale?.p95 && scale.p95 > 0 ? scale.p95 : Math.abs(value);
  const mag = Math.min(1, Math.abs(value) / ceiling);
  const alpha = 0.12 + 0.72 * mag;
  const rgb = HUE[side];
  if (value > 0) {
    return { style: { backgroundColor: `rgba(${rgb},${alpha})` }, strong: mag > 0.5 };
  }
  // Unwinding: same hue, hatched. A paler fill alone is indistinguishable from
  // a small build-up, which is the one confusion this grid cannot afford.
  return {
    style: {
      backgroundImage:
        `repeating-linear-gradient(45deg, rgba(${rgb},${alpha * 0.8}) 0 3px,` +
        ` transparent 3px 7px)`,
    },
    strong: false,
  };
}

const CENTRE_HEAD = "sticky top-0 z-10 bg-background text-[10px] font-medium";
const CENTRE_HEAD_CE = CENTRE_HEAD + " text-red-500";
const CENTRE_HEAD_PE = CENTRE_HEAD + " text-emerald-500";
const BASE_W = 66;
const DELTA_W = 62;
const PCT_W = 52;

/** Cumulative ΔOI as a percent of the baseline. Em-dash when there is no
 *  baseline to divide by, rather than a misleading 0%. */
function pctText(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v > 0 ? "+" : "") + v.toFixed(1) + "%";
}

function centreCell(strong: boolean): string {
  return (
    "border-b border-border/40 text-center font-mono text-[10px] tabular-nums " +
    (strong ? "text-white" : "text-foreground")
  );
}

/** Which column the ladder is ordered by. Every key reads off a value the grid
 *  already carries, so sorting is a pure reorder — no refetch. */
type SortKey =
  | "strike"
  | "ce_base"
  | "ce_delta"
  | "ce_pct"
  | "pe_base"
  | "pe_delta"
  | "pe_pct";

type SortDir = "asc" | "desc";

function sortValue(row: BuildupRow, key: SortKey): number | null {
  switch (key) {
    case "strike":
      return row.strike;
    case "ce_base":
      return row.ce.baseline;
    case "ce_delta":
      return row.ce.total_delta;
    case "ce_pct":
      return row.ce.total_delta_pct;
    case "pe_base":
      return row.pe.baseline;
    case "pe_delta":
      return row.pe.total_delta;
    case "pe_pct":
      return row.pe.total_delta_pct;
  }
}

/** Sorts signed, not by magnitude: descending puts the heaviest build-up on top
 *  and ascending the heaviest unwinding, so one toggle reaches both ends. Rows
 *  with no value sink to the bottom either way — a strike with no baseline has
 *  not "sorted lowest", it has nothing to sort on. */
function sortRows(rows: BuildupRow[], key: SortKey, dir: SortDir): BuildupRow[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = sortValue(a, key);
    const bv = sortValue(b, key);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return (av - bv) * sign;
  });
}

function SortHead({
  label,
  sortKey,
  active,
  dir,
  width,
  className,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  active: SortKey;
  dir: SortDir;
  width: number;
  className: string;
  onSort: (k: SortKey) => void;
}) {
  const on = active === sortKey;
  return (
    <th style={{ height: HEAD_H, width }} className={className}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        title={`Sort by ${label}`}
        className={`flex w-full items-center justify-center gap-0.5 ${
          on ? "font-bold" : "opacity-90 hover:opacity-100"
        }`}
      >
        {label}
        {/* Inactive columns get a neutral glyph, not a faded ascending one:
            opacity alone made "sorted ascending" and "not sorted" look alike. */}
        <span className={`text-[8px] leading-none ${on ? "" : "opacity-40"}`}>
          {on ? (dir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );
}

function cellTitle(cell: BuildupCell, bucket: string, side: string): string {
  const parts = [
    `${side} @ ${bucket}`,
    `OI ${cell.oi == null ? "—" : cell.oi.toLocaleString()}`,
    `ΔOI ${signed(cell.d_oi)}${cell.d_oi_pct == null ? "" : ` (${cell.d_oi_pct}%)`}`,
    `Cumulative ${signed(cell.cum)}`,
    `LTP ${cell.ltp == null ? "—" : fmtNum(cell.ltp)}`,
    `ΔPrice ${cell.d_price == null ? "—" : fmtNum(cell.d_price)}`,
  ];
  if (cell.cls) parts.push(CLASS_LABEL[cell.cls]);
  return parts.join("\n");
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardContent className="py-2 px-3">
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className="font-mono text-base font-semibold">{value}</div>
        {hint ? <div className="text-[10px] text-muted-foreground">{hint}</div> : null}
      </CardContent>
    </Card>
  );
}

function Wing({
  side,
  rows,
  buckets,
  metric,
  scale,
  classCodes,
  scrollRef,
  onScroll,
}: {
  side: "ce" | "pe";
  rows: BuildupRow[];
  buckets: { key: string }[];
  metric: "delta" | "cum";
  scale: BuildupScale | undefined;
  classCodes: Record<BuildupClass, string>;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  onScroll: () => void;
}) {
  return (
    <div ref={scrollRef} onScroll={onScroll} className="min-w-0 flex-1 overflow-x-auto">
      <table
        className="border-separate border-spacing-0"
        style={{ width: buckets.length * COL_W }}
      >
        <thead>
          <tr>
            {buckets.map((b) => (
              <th
                key={b.key}
                style={{ width: COL_W, height: HEAD_H }}
                className="sticky top-0 z-10 bg-background text-[10px] font-medium text-muted-foreground"
              >
                {b.key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.strike}>
              {buckets.map((b, i) => {
                const cell = row[side].cells[i];
                const value = cell ? (metric === "cum" ? cell.cum : cell.d_oi) : null;
                const { style, strong } = cellStyle(value ?? null, scale, side);
                return (
                  <td
                    key={b.key}
                    style={{ width: COL_W, height: ROW_H, ...style }}
                    title={cell ? cellTitle(cell, b.key, side.toUpperCase()) : undefined}
                    className={`relative border-b border-r border-border/40 text-center font-mono text-[10px] tabular-nums ${
                      strong ? "text-white" : "text-foreground"
                    } ${row.atm ? "border-b-primary/40" : ""} ${
                      cell?.breach ? "breach-ring font-bold" : ""
                    }`}
                  >
                    {value == null ? (
                      <span className="text-muted-foreground/40">·</span>
                    ) : (
                      <>
                        {signed(value)}
                        {cell?.cls ? (
                          <span className="absolute right-0.5 top-0 text-[7px] leading-none opacity-70">
                            {classCodes[cell.cls]}
                          </span>
                        ) : null}
                      </>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChainBuildupPage() {
  const [underlying, setUnderlying] = useState<string>("NIFTY");
  const [sessionDate, setSessionDate] = useState<string>("");
  const [expiry, setExpiry] = useState<string>("");
  const [timeframe, setTimeframe] = useState<number>(5);
  const [range, setRange] = useState<string>("atm10");
  const [baseline, setBaseline] = useState<string>("session_open");
  const [metric, setMetric] = useState<"delta" | "cum">("delta");
  const [widen, setWiden] = useState<boolean>(false);
  const [sync, setSync] = useState<boolean>(true);
  const [sortKey, setSortKey] = useState<SortKey>("strike");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [minAbsOi, setMinAbsOi] = useState<number>(25_000);
  const [breachOnly, setBreachOnly] = useState<boolean>(false);
  const [thresholdMode, setThresholdMode] = useState<"fixed" | "adaptive">("fixed");

  const [grid, setGrid] = useState<BuildupGrid | null>(null);
  const [status, setStatus] = useState<BuildupStatus | null>(null);
  const [expiries, setExpiries] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ceRef = useRef<HTMLDivElement>(null);
  const peRef = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);
  const lastAlertRef = useRef<{ ce: number; pe: number }>({ ce: 0, pe: 0 });

  const load = useCallback(async () => {
    setLoading(true);
    const q = new URLSearchParams({
      underlying,
      timeframe_min: String(timeframe),
      strike_range: range,
      baseline_mode: baseline,
      widen: String(widen),
      min_abs_oi: String(minAbsOi),
      threshold_mode: thresholdMode,
    });
    if (sessionDate) q.set("session_date", sessionDate);
    if (expiry) q.set("expiry", expiry);

    const [g, s] = await Promise.allSettled([
      api.get<BuildupGrid>(`/buildup/grid?${q}`),
      api.get<BuildupStatus>(`/buildup/status?underlying=${underlying}`, { silent: true }),
    ]);
    if (g.status === "fulfilled") {
      setGrid(g.value);
      setError(null);
    } else {
      setError(g.reason instanceof Error ? g.reason.message : String(g.reason));
    }
    if (s.status === "fulfilled") setStatus(s.value);
    setLoading(false);
  }, [underlying, sessionDate, expiry, timeframe, range, baseline, widen, minAbsOi, thresholdMode]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  // Expiry list follows the chosen session, not today.
  useEffect(() => {
    const q = new URLSearchParams({ underlying });
    if (sessionDate) q.set("session_date", sessionDate);
    api
      .get<{ expiries: string[] }>(`/buildup/expiries?${q}`, { silent: true })
      .then((r) => setExpiries(r.expiries ?? []))
      .catch(() => setExpiries([]));
  }, [underlying, sessionDate]);

  // Open on the latest columns: the live end of the session is what you want to
  // see first, and at 5-minute buckets the grid is ~75 columns wide per wing.
  useEffect(() => {
    if (!grid) return;
    for (const ref of [ceRef, peRef]) {
      const el = ref.current;
      if (el) el.scrollLeft = el.scrollWidth;
    }
  }, [grid?.buckets.length, grid?.underlying, grid?.timeframe_min]);

  // Breach alert, mirroring the OI Tracker desk: fire when most of the newest
  // bucket breached. Gated on meta.is_live -- the grid renders any archived
  // session, and toasting about a chain that stopped moving days ago is noise.
  useEffect(() => {
    if (!grid?.meta.is_live || !grid.alert) return;
    const now = Date.now();
    (["ce", "pe"] as const).forEach((side) => {
      const a = grid.alert[side];
      if (!a.alert) return;
      if (now - lastAlertRef.current[side] < ALERT_DEBOUNCE_MS) return;
      lastAlertRef.current[side] = now;
      toast.warning(
        `${side.toUpperCase()} build-up alert: ${(a.ratio * 100).toFixed(0)}% of strikes ` +
          `breached in the ${grid.buckets[grid.buckets.length - 1]?.key ?? "latest"} bucket`,
      );
    });
  }, [grid]);

  // Clicking the active column flips direction; a new column starts ascending
  // for the strike ladder and descending everywhere else — for a delta column
  // "biggest first" is what you meant by clicking it.
  //
  // Both branches must stay OUTSIDE any state updater. This previously called
  // setSortDir from inside the setSortKey updater, and <StrictMode>
  // (src/main.tsx) double-invokes updaters in development: the direction
  // toggled twice per click and never appeared to change. It looked fine on
  // :8001 only because a production build skips that double-invoke — which is
  // exactly the class of bug verifying on one surface hides.
  const onSort = useCallback(
    (key: SortKey) => {
      if (key === sortKey) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
        return;
      }
      setSortKey(key);
      setSortDir(key === "strike" ? "asc" : "desc");
    },
    [sortKey],
  );

  const mirror = useCallback(
    (from: React.RefObject<HTMLDivElement | null>, to: React.RefObject<HTMLDivElement | null>) =>
      () => {
      if (!sync || syncing.current) return;
      const src = from.current;
      const dst = to.current;
      if (!src || !dst) return;
      syncing.current = true;
      dst.scrollLeft = src.scrollLeft;
      requestAnimationFrame(() => {
        syncing.current = false;
      });
    },
    [sync],
  );

  // One sorted array feeds all three panes, so they cannot drift out of
  // row alignment. The backend always returns ascending strikes.
  const rows = useMemo(() => {
    let base = grid?.rows ?? [];
    if (breachOnly) {
      // A row survives if either side breached cumulatively, or any single
      // bucket breached. Both matter: one is "written hard all day", the other
      // "written hard just now", and hiding either defeats the filter.
      base = base.filter(
        (r) =>
          r.ce.breach ||
          r.pe.breach ||
          r.ce.cells.some((c) => c.breach) ||
          r.pe.cells.some((c) => c.breach),
      );
    }
    return sortRows(base, sortKey, sortDir);
  }, [grid?.rows, sortKey, sortDir, breachOnly]);
  const buckets = grid?.buckets ?? [];
  const scaleKey = metric === "cum" ? "cum" : "delta";
  const classCodes =
    grid?.class_codes ??
    ({
      long_buildup: "LB",
      short_buildup: "SB",
      short_covering: "SC",
      long_unwinding: "LU",
    } as Record<BuildupClass, string>);

  const sessions = status?.sessions ?? [];
  const notes = grid?.meta.notes ?? [];

  const bucketSpot = useMemo(() => {
    const last = [...buckets].reverse().find((b) => b.spot != null);
    return last?.spot ?? grid?.spot ?? null;
  }, [buckets, grid?.spot]);

  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-2 text-lg font-semibold">Option Chain Build-Up</h1>

        <select
          className="h-8 rounded-md border bg-background px-2 text-xs"
          value={underlying}
          onChange={(e) => setUnderlying(e.target.value)}
        >
          {UNDERLYINGS.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>

        <select
          className="h-8 rounded-md border bg-background px-2 text-xs"
          value={sessionDate}
          onChange={(e) => setSessionDate(e.target.value)}
        >
          <option value="">Latest session</option>
          {sessions
            .slice()
            .reverse()
            .map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
        </select>

        <select
          className="h-8 rounded-md border bg-background px-2 text-xs"
          value={expiry}
          onChange={(e) => setExpiry(e.target.value)}
        >
          <option value="">Nearest expiry</option>
          {expiries.map((e) => (
            <option key={e} value={e}>
              {e}
            </option>
          ))}
        </select>

        <div className="flex overflow-hidden rounded-md border">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-2 py-1 text-xs ${
                timeframe === tf ? "bg-primary text-primary-foreground" : "bg-background"
              }`}
            >
              {tf}m
            </button>
          ))}
        </div>

        <select
          className="h-8 rounded-md border bg-background px-2 text-xs"
          value={range}
          onChange={(e) => setRange(e.target.value)}
        >
          {RANGES.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>

        <select
          className="h-8 rounded-md border bg-background px-2 text-xs"
          value={baseline}
          onChange={(e) => setBaseline(e.target.value)}
        >
          <option value="session_open">Baseline: session open</option>
          <option value="prev_close">Baseline: prev-day close</option>
        </select>

        <div className="flex overflow-hidden rounded-md border">
          <button
            onClick={() => setMetric("delta")}
            className={`px-2 py-1 text-xs ${
              metric === "delta" ? "bg-primary text-primary-foreground" : "bg-background"
            }`}
          >
            Δ / bucket
          </button>
          <button
            onClick={() => setMetric("cum")}
            className={`px-2 py-1 text-xs ${
              metric === "cum" ? "bg-primary text-primary-foreground" : "bg-background"
            }`}
          >
            Cumulative
          </button>
        </div>

        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          <input type="checkbox" checked={widen} onChange={(e) => setWiden(e.target.checked)} />
          Fetch beyond archive
        </label>
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          <input type="checkbox" checked={sync} onChange={(e) => setSync(e.target.checked)} />
          Sync scroll
        </label>

        <div className="flex overflow-hidden rounded-md border">
          <button
            onClick={() => setThresholdMode("fixed")}
            title="One hand-picked percentage for the whole session"
            className={`px-2 py-1 text-xs ${
              thresholdMode === "fixed" ? "bg-primary text-primary-foreground" : "bg-background"
            }`}
          >
            Fixed
          </button>
          <button
            onClick={() => setThresholdMode("adaptive")}
            title="Fitted p95 per time-of-day and days-to-expiry — a flat ~5% breach rate"
            className={`px-2 py-1 text-xs ${
              thresholdMode === "adaptive" ? "bg-primary text-primary-foreground" : "bg-background"
            }`}
          >
            Adaptive
          </button>
        </div>

        <label
          className="flex items-center gap-1 text-xs text-muted-foreground"
          title="Absolute floor a move must clear before any percentage may call it a breach"
        >
          Floor
          <input
            type="number"
            min={0}
            step={5000}
            value={minAbsOi}
            onChange={(e) => setMinAbsOi(Math.max(0, Number(e.target.value) || 0))}
            className="h-8 w-20 rounded-md border bg-background px-1 text-right font-mono text-xs"
          />
        </label>
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={breachOnly}
            onChange={(e) => setBreachOnly(e.target.checked)}
          />
          Breaches only
        </label>

        <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={`mr-1 h-3 w-3 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-7">
        <Stat label="Spot" value={fmtNum(bucketSpot)} hint={grid ? `ATM ${grid.atm ?? "—"}` : ""} />
        <Stat label="Session" value={grid?.session_date ?? "—"} hint={grid?.expiry ?? ""} />
        <Stat label="CE ΔOI" value={signed(grid?.totals.ce_delta)} hint="since baseline" />
        <Stat label="PE ΔOI" value={signed(grid?.totals.pe_delta)} hint="since baseline" />
        <Stat label="PCR (OI)" value={fmtNum(grid?.totals.pcr_oi, 3)} />
        <Stat
          label="Strikes"
          value={
            breachOnly && grid
              ? `${rows.length}/${grid.meta.rendered_strikes}`
              : String(grid?.meta.rendered_strikes ?? 0)
          }
          hint={grid?.meta.source ?? ""}
        />
        <Stat
          label="Latest breach"
          value={
            grid
              ? `${(grid.alert.ce.ratio * 100).toFixed(0)}% / ${(
                  grid.alert.pe.ratio * 100
                ).toFixed(0)}%`
              : "—"
          }
          hint={
            grid
              ? `CE/PE in ${grid.buckets[grid.buckets.length - 1]?.key ?? "—"}` +
                (grid.meta.is_live ? "" : " · archived, no alerts")
              : ""
          }
        />
      </div>

      {error ? (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-2 text-xs text-destructive">
          {error}
        </div>
      ) : null}

      {notes.length ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          {notes.map((n) => (
            <div key={n}>{n}</div>
          ))}
        </div>
      ) : null}

      <Card>
        <CardContent className="p-0">
          <div className="flex items-stretch">
            <Wing
              side="ce"
              rows={rows}
              buckets={buckets}
              metric={metric}
              scale={grid?.scale.ce[scaleKey]}
              classCodes={classCodes}
              scrollRef={ceRef}
              onScroll={mirror(ceRef, peRef)}
            />

            {/* Centre block never scrolls: baseline OI flanks the strike on both
                sides, exactly where the eye returns between wings. Going outward
                from the strike: baseline OI, cumulative ΔOI against it, then that
                change as a percent — so the three numbers read as one sentence. */}
            <div className="shrink-0 border-x-2 border-border bg-muted/30">
              <table className="border-separate border-spacing-0">
                <thead>
                  <tr>
                    <SortHead
                      label="Δ%"
                      sortKey="ce_pct"
                      active={sortKey}
                      dir={sortDir}
                      width={PCT_W}
                      className={CENTRE_HEAD_CE}
                      onSort={onSort}
                    />
                    <SortHead
                      label="ΔOI"
                      sortKey="ce_delta"
                      active={sortKey}
                      dir={sortDir}
                      width={DELTA_W}
                      className={CENTRE_HEAD_CE}
                      onSort={onSort}
                    />
                    <SortHead
                      label="CE base"
                      sortKey="ce_base"
                      active={sortKey}
                      dir={sortDir}
                      width={BASE_W}
                      className={CENTRE_HEAD_CE}
                      onSort={onSort}
                    />
                    <SortHead
                      label="STRIKE"
                      sortKey="strike"
                      active={sortKey}
                      dir={sortDir}
                      width={74}
                      className="sticky top-0 z-10 bg-background text-[10px] font-semibold"
                      onSort={onSort}
                    />
                    <SortHead
                      label="PE base"
                      sortKey="pe_base"
                      active={sortKey}
                      dir={sortDir}
                      width={BASE_W}
                      className={CENTRE_HEAD_PE}
                      onSort={onSort}
                    />
                    <SortHead
                      label="ΔOI"
                      sortKey="pe_delta"
                      active={sortKey}
                      dir={sortDir}
                      width={DELTA_W}
                      className={CENTRE_HEAD_PE}
                      onSort={onSort}
                    />
                    <SortHead
                      label="Δ%"
                      sortKey="pe_pct"
                      active={sortKey}
                      dir={sortDir}
                      width={PCT_W}
                      className={CENTRE_HEAD_PE}
                      onSort={onSort}
                    />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const baseLabel = baseline === "prev_close" ? "prev close" : "session open";
                    const ceTotal = cellStyle(row.ce.total_delta, grid?.scale.ce.cum, "ce");
                    const peTotal = cellStyle(row.pe.total_delta, grid?.scale.pe.cum, "pe");
                    return (
                      <tr key={row.strike}>
                        <td
                          style={{ height: ROW_H, width: PCT_W, ...ceTotal.style }}
                          title={
                            row.ce.breach
                              ? `CE BREACH — cumulative ΔOI vs ${baseLabel} clears both thresholds`
                              : `CE cumulative ΔOI vs ${baseLabel}, as a percent of it`
                          }
                          className={
                            centreCell(ceTotal.strong) +
                            (row.ce.breach ? " breach-ring font-bold" : "")
                          }
                        >
                          {pctText(row.ce.total_delta_pct)}
                        </td>
                        <td
                          style={{ height: ROW_H, width: DELTA_W, ...ceTotal.style }}
                          title={`CE cumulative ΔOI since ${baseLabel}`}
                          className={centreCell(ceTotal.strong)}
                        >
                          {signed(row.ce.total_delta)}
                        </td>
                        <td
                          style={{ height: ROW_H, width: BASE_W }}
                          title={`CE ${baseLabel} OI`}
                          className="border-b border-border/40 text-center font-mono text-[10px] tabular-nums text-muted-foreground"
                        >
                          {compact(row.ce.baseline)}
                        </td>
                        <td
                          style={{ height: ROW_H, width: 74 }}
                          className={`border-b border-border/40 text-center font-mono text-[11px] font-semibold tabular-nums ${
                            row.atm ? "bg-primary/20 text-primary" : ""
                          }`}
                        >
                          {row.strike.toLocaleString()}
                        </td>
                        <td
                          style={{ height: ROW_H, width: BASE_W }}
                          title={`PE ${baseLabel} OI`}
                          className="border-b border-border/40 text-center font-mono text-[10px] tabular-nums text-muted-foreground"
                        >
                          {compact(row.pe.baseline)}
                        </td>
                        <td
                          style={{ height: ROW_H, width: DELTA_W, ...peTotal.style }}
                          title={`PE cumulative ΔOI since ${baseLabel}`}
                          className={centreCell(peTotal.strong)}
                        >
                          {signed(row.pe.total_delta)}
                        </td>
                        <td
                          style={{ height: ROW_H, width: PCT_W, ...peTotal.style }}
                          title={
                            row.pe.breach
                              ? `PE BREACH — cumulative ΔOI vs ${baseLabel} clears both thresholds`
                              : `PE cumulative ΔOI vs ${baseLabel}, as a percent of it`
                          }
                          className={
                            centreCell(peTotal.strong) +
                            (row.pe.breach ? " breach-ring font-bold" : "")
                          }
                        >
                          {pctText(row.pe.total_delta_pct)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <Wing
              side="pe"
              rows={rows}
              buckets={buckets}
              metric={metric}
              scale={grid?.scale.pe[scaleKey]}
              classCodes={classCodes}
              scrollRef={peRef}
              onScroll={mirror(peRef, ceRef)}
            />
          </div>

          {!rows.length && !loading ? (
            <div className="p-6 text-center text-xs text-muted-foreground">
              No archived rows for this selection.
            </div>
          ) : null}
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
        <span className="font-medium text-foreground">CE left · PE right · time reads left → right</span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-3 w-6 rounded-sm"
            style={{ backgroundColor: `rgba(${HUE.ce},0.8)` }}
          />
          CE build-up
        </span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-3 w-6 rounded-sm"
            style={{
              backgroundImage: `repeating-linear-gradient(45deg, rgba(${HUE.ce},0.6) 0 3px, transparent 3px 7px)`,
            }}
          />
          CE unwinding
        </span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-3 w-6 rounded-sm"
            style={{ backgroundColor: `rgba(${HUE.pe},0.8)` }}
          />
          PE build-up
        </span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-3 w-6 rounded-sm"
            style={{
              backgroundImage: `repeating-linear-gradient(45deg, rgba(${HUE.pe},0.6) 0 3px, transparent 3px 7px)`,
            }}
          />
          PE unwinding
        </span>
        {(Object.keys(CLASS_LABEL) as BuildupClass[]).map((k) => (
          <Badge key={k} variant="outline" className="font-mono text-[10px]">
            {classCodes[k]} = {CLASS_LABEL[k]}
          </Badge>
        ))}
        <span className="flex items-center gap-1">
          <span className="breach-ring inline-block h-3 w-6 rounded-sm" />
          breach
        </span>
        <span>
          Intensity scales to the 95th percentile of |{metric === "cum" ? "cumulative" : "Δ"}OI| in
          view.
        </span>
        {grid ? (
          <span>
            Breach ={" "}
            {grid.thresholds.mode === "adaptive" ? (
              <>
                fitted p95, {grid.thresholds.pct_min}–{grid.thresholds.pct_max}% across the
                session (DTE {grid.thresholds.dte_bucket}, {grid.thresholds.fitted_sessions}{" "}
                session-files)
              </>
            ) : (
              <>|Δ%| &gt; {grid.thresholds.pct}%</>
            )}{" "}
            per {grid.timeframe_min}m bucket (
            {grid.thresholds.cum_pct}% cumulative for a strike) <strong>and</strong> ≥{" "}
            {compact(grid.thresholds.min_abs_oi)} contracts. Alert at{" "}
            {(grid.thresholds.alert_ratio * 100).toFixed(0)}% of the newest bucket.
          </span>
        ) : null}
      </div>
    </div>
  );
}
