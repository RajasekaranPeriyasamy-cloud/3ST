import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type {
  BuildupCell,
  BuildupClass,
  BuildupPrevSession,
  BuildupGrid,
  BuildupRow,
  BuildupLevel,
  BuildupLevelKey,
  BuildupLevels,
  BuildupScale,
  BuildupFlow,
  BuildupStatus,
  BuildupTrackPoint,
  Metric,
} from "@/lib/types";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export const Route = createFileRoute("/chain-buildup")({
  component: ChainBuildupPage,
});

const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "SENSEX"] as const;
const TIMEFRAMES = [1, 3, 5, 15, 30, 60] as const;
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
  signed = false,
): { style: React.CSSProperties; strong: boolean } {
  if (value == null || value === 0) return { style: {}, strong: false };
  const ceiling = scale?.p95 && scale.p95 > 0 ? scale.p95 : Math.abs(value);
  const mag = Math.min(1, Math.abs(value) / ceiling);
  const alpha = 0.12 + 0.72 * mag;
  const rgb = HUE[side];
  if (value > 0 || signed) {
    // A signed metric (delta) already carries direction in its hue, so it is
    // always a solid fill. Hatching is reserved for OI unwinding, where hue is
    // taken by the CE/PE side and direction has nowhere else to live.
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

/** What the wing cells show. OI and volume are both per-bucket or cumulative,
 *  but they never share a colour scale — volume is an order of magnitude larger
 *  on the same strike, so one ceiling would leave every OI cell white.
 *
 *  `Metric` is imported rather than declared here: the same six names key the
 *  backend's TOTAL_METRICS and the grid's scale blocks, and a second copy is
 *  a place for them to drift apart. */

const METRICS: { value: Metric; label: string }[] = [
  { value: "delta", label: "ΔOI / bucket" },
  { value: "cum", label: "ΔOI cumulative" },
  { value: "vol", label: "Volume / bucket" },
  { value: "cum_vol", label: "Volume cumulative" },
  { value: "delta_vol", label: "Delta (buy−sell)" },
  { value: "cum_delta_vol", label: "Cum Delta" },
];

/** Volume metrics are one-signed; delta is genuinely two-signed and should read
 *  that way — buying green, selling red — rather than borrowing the CE/PE hue. */
const SIGNED_METRICS: Metric[] = ["delta_vol", "cum_delta_vol"];

function metricValue(cell: BuildupCell | undefined, metric: Metric): number | null {
  if (!cell) return null;
  switch (metric) {
    case "delta":
      return cell.d_oi;
    case "cum":
      return cell.cum;
    case "vol":
      return cell.d_volume;
    case "cum_vol":
      return cell.cum_volume;
    case "delta_vol":
      return cell.delta_vol;
    case "cum_delta_vol":
      return cell.cum_delta_vol;
  }
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

/** Short tags stamped on the strike column. Kept to two or three characters so
 *  they sit beside a five-digit strike without pushing the centre block wider. */
const LEVEL_TAG: Record<BuildupLevelKey, string> = {
  call_wall: "CW",
  put_wall: "PW",
  pin: "PIN",
  flip: "FLIP",
  fut_poc: "POC",
};

const LEVEL_RGB: Record<BuildupLevelKey, string> = {
  call_wall: "rgb(239 68 68)",
  put_wall: "rgb(16 185 129)",
  pin: "rgb(99 102 241)",
  flip: "rgb(245 158 11)",
  fut_poc: "rgb(14 165 233)",
};

const LEVEL_TONE: Record<BuildupLevelKey, string> = {
  call_wall: "bg-red-500/85 text-white",
  put_wall: "bg-emerald-500/85 text-white",
  pin: "bg-primary/80 text-primary-foreground",
  flip: "bg-amber-500/85 text-white",
  fut_poc: "bg-sky-500/85 text-white",
};

function levelTitle(l: BuildupLevel): string {
  const parts = [`${l.label} ${l.price == null ? "—" : l.price.toLocaleString()}`];
  if (l.kind === "price" && l.between)
    parts.push(`between ${l.between[0] ?? "—"} and ${l.between[1] ?? "—"}`);
  parts.push(l.source === "live" ? "live snapshot" : "from session history");
  if (l.note) parts.push(l.note);
  return parts.join(" · ");
}

/** Why a level is missing, in the operator's words rather than the API's. */
const SKIP_REASON: Record<string, string> = {
  not_recorded_this_session: "not recorded that day (walls are stored only from 2026-08-27)",
  no_gamma_history_for_session: "no gamma history for that session",
  gamma_unavailable: "gamma snapshot unavailable",
  gamma_history_unavailable: "gamma history unreadable",
  poc_unavailable: "POC unavailable",
  no_trail_yet: "no POC trail for that session",
  not_sampled: "this underlying is not POC-sampled",
  unavailable: "unavailable",
};

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

/** Columns rendered beyond the viewport on each side.
 *
 *  Enough that a fast scroll or a sync-scroll nudge from the other wing does not
 *  expose blank space before the next frame, small enough that a 1m session
 *  (~375 columns) still renders a few dozen cells per row instead of all of
 *  them. Six was where blank edges stopped appearing while dragging the bar. */
const OVERSCAN_COLS = 6;

/** Which slice of the columns is worth putting in the DOM.
 *
 *  At 5m a session is ~75 columns and rendering all of them is free. At 1m it is
 *  ~375, and with two wings and 21 strikes that was ~17k DOM nodes and about a
 *  second per re-render — every sort, every metric change. The table keeps its
 *  full width so the scrollbar, the sync-scroll and the "open on the latest
 *  column" jump all behave exactly as before; only the cells between the
 *  spacers actually exist. */
function useColumnWindow(
  scrollRef: React.RefObject<HTMLDivElement | null>,
  total: number,
): { start: number; end: number; measure: () => void } {
  const [win, setWin] = useState({ start: 0, end: Math.min(total, 60) });

  const measure = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const first = Math.max(0, Math.floor(el.scrollLeft / COL_W) - OVERSCAN_COLS);
    const span = Math.ceil(el.clientWidth / COL_W) + OVERSCAN_COLS * 2;
    const next = { start: first, end: Math.min(total, first + span) };
    // Skip the state write when nothing moved: scroll fires continuously, and
    // re-rendering the wing on every event is the cost this exists to avoid.
    setWin((prev) => (prev.start === next.start && prev.end === next.end ? prev : next));
  }, [scrollRef, total]);

  useEffect(() => {
    measure();
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure, total]);

  return { start: win.start, end: win.end, measure };
}

/** Height of the totals strip's plot area, in px. */
const STRIP_H = 34;
const BAR_W = 22;

/** One bucket of the CE/PE totals strip: grouped bars, CE left, PE right.
 *
 *  Height carries magnitude here, where the grid below uses alpha — a strip one
 *  row tall has no room for intensity to read, and a bar does not compete with
 *  the numbers in the cells underneath it.
 *
 *  Direction keeps the grid's meaning but changes weight: a solid bar is
 *  build-up, an outlined bar is unwinding. That is the same distinction the
 *  cells draw with hatching, which does not survive at this size.
 */
function StripCell({
  ce,
  pe,
  ceiling,
  bucket,
}: {
  ce: number | null;
  pe: number | null;
  ceiling: number;
  bucket: string;
}) {
  const bar = (v: number | null, hue: string) => {
    // null is not zero: no strike carried this field in this bucket. A flat bar
    // would read as "no net change", which is the opposite of "not measured".
    if (v == null) return <div style={{ width: BAR_W }} />;
    const h = ceiling > 0 ? Math.max(1, (Math.min(Math.abs(v), ceiling) / ceiling) * STRIP_H) : 1;
    const building = v >= 0;
    return (
      <div
        style={{
          width: BAR_W,
          height: h,
          backgroundColor: building ? `rgba(${hue},0.75)` : "transparent",
          border: building ? undefined : `1.5px solid rgba(${hue},0.9)`,
        }}
      />
    );
  };

  const title = `${bucket} · CE ${signed(ce)} · PE ${signed(pe)}`;

  return (
    <div className="flex h-full items-end justify-center gap-1" title={title}>
      <div className="flex items-end" style={{ height: STRIP_H }}>{bar(ce, HUE.ce)}</div>
      <div className="flex items-end" style={{ height: STRIP_H }}>{bar(pe, HUE.pe)}</div>
    </div>
  );
}

/** Centre-block summary: this session's build against the whole of the previous
 *  one, CE on the left and PE on the right, matching the ladder either side.
 *
 *  It exists first to keep the three panes aligned — the wings' strip added a
 *  header row, and the panes line up by fixed heights rather than a grid — but
 *  a spacer that says something beats a spacer that does not.
 *
 *  Both figures are "change since that session's open", so the ratio between
 *  them is a pace: above 1x the chain is being written faster than it was all
 *  of the previous day. Today solid, previous outlined, the same weights the
 *  strip uses for build-up against unwinding.
 */
function CumulativeBand({
  band,
  label,
}: {
  band: {
    ce: number | null;
    pe: number | null;
    prevCe: number | null;
    prevPe: number | null;
    prevDate: string | null;
    reason: string | null;
  };
  label: string;
}) {
  const pace = (now: number | null, prev: number | null) =>
    now != null && prev != null && prev !== 0 ? Math.abs(now) / Math.abs(prev) : null;

  const sideBlock = (
    now: number | null,
    prev: number | null,
    hue: string,
    tone: string,
    align: "items-start" | "items-end",
  ) => {
    const ceiling = Math.max(Math.abs(now ?? 0), Math.abs(prev ?? 0)) || 1;
    const w = (v: number | null) => (v == null ? 0 : (Math.abs(v) / ceiling) * 62);
    const p = pace(now, prev);
    return (
      <div className={`flex flex-col justify-center gap-[3px] ${align}`}>
        <div className="flex items-baseline gap-1.5 font-mono text-[11px] tabular-nums">
          <span style={{ color: `rgb(${hue})` }}>{signed(now)}</span>
          {p == null ? null : (
            <span className={`text-[9px] ${tone}`}>{p.toFixed(2)}x</span>
          )}
        </div>
        {/* Widths, not heights: a 40px band has room across but not up. */}
        <div className="flex flex-col gap-[2px]">
          <div style={{ width: w(now), height: 4, backgroundColor: `rgba(${hue},0.75)` }} />
          {prev == null ? null : (
            <div style={{ width: w(prev), height: 4, border: `1px solid rgba(${hue},0.85)` }} />
          )}
        </div>
      </div>
    );
  };

  const title = band.reason
    ? `${label} · session so far — CE ${signed(band.ce)} · PE ${signed(band.pe)}. ${band.reason}`
    : `${label} · session so far vs all of ${band.prevDate} — ` +
      `CE ${signed(band.ce)} vs ${signed(band.prevCe)} · PE ${signed(band.pe)} vs ${signed(band.prevPe)}`;

  return (
    <div className="flex h-full items-center justify-between px-2" title={title}>
      {sideBlock(band.ce, band.prevCe, HUE.ce, "text-red-400/80", "items-start")}
      <div className="flex flex-col items-center text-[8px] leading-tight text-muted-foreground">
        <span>session</span>
        <span>{band.reason ? "no prior" : `vs ${band.prevDate?.slice(5) ?? "—"}`}</span>
      </div>
      {sideBlock(band.pe, band.prevPe, HUE.pe, "text-emerald-400/80", "items-end")}
    </div>
  );
}

function Wing({
  side,
  rows,
  buckets,
  metric,
  scale,
  classCodes,
  trackMarks,
  flow,
  totals,
  totalsCeiling,
  scrollRef,
  onScroll,
}: {
  side: "ce" | "pe";
  rows: BuildupRow[];
  buckets: { key: string }[];
  metric: Metric;
  scale: BuildupScale | undefined;
  classCodes: Record<BuildupClass, string>;
  trackMarks: Map<string, { key: BuildupLevelKey; edge: boolean }[]>;
  flow: BuildupFlow | null;
  totals: { ce: (number | null)[]; pe: (number | null)[] } | null;
  totalsCeiling: number;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  onScroll: () => void;
}) {
  const { start, end, measure } = useColumnWindow(scrollRef, buckets.length);
  const shown = buckets.slice(start, end);
  // Spacer widths stand in for the columns that are not in the DOM, so the
  // table's internal geometry matches its declared width and the rendered
  // columns land exactly where unvirtualised ones would.
  const padLeft = start * COL_W;
  const padRight = Math.max(0, (buckets.length - end) * COL_W);

  const handleScroll = () => {
    measure();
    onScroll();
  };

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="min-w-0 flex-1 overflow-x-auto">
      <table
        className="border-separate border-spacing-0"
        style={{ width: buckets.length * COL_W }}
      >
        <thead>
          <tr>
            {padLeft > 0 ? <th style={{ width: padLeft, height: HEAD_H }} /> : null}
            {shown.map((b) => (
              <th
                key={b.key}
                style={{ width: COL_W, height: HEAD_H }}
                className="sticky top-0 z-10 bg-background text-[10px] font-medium text-muted-foreground"
              >
                {b.key}
              </th>
            ))}
            {padRight > 0 ? <th style={{ width: padRight, height: HEAD_H }} /> : null}
          </tr>
          {/* Totals strip. In the thead, so it sticks under the time labels and
              stays visible while the ladder scrolls — and inside THIS table, so
              it cannot drift from the columns the way a sibling element would.
              Same reason the futures flow strip is a tfoot. */}
          {totals ? (
            <tr>
              {padLeft > 0 ? <th style={{ width: padLeft }} /> : null}
              {shown.map((b, local) => {
                const i = start + local;
                return (
                  <th
                    key={b.key}
                    style={{ width: COL_W, height: STRIP_H + 6, top: HEAD_H }}
                    className="sticky z-10 border-b border-border/60 bg-background p-0 align-bottom font-normal"
                  >
                    <StripCell
                      ce={totals.ce[i] ?? null}
                      pe={totals.pe[i] ?? null}
                      ceiling={totalsCeiling}
                      bucket={b.key}
                    />
                  </th>
                );
              })}
              {padRight > 0 ? <th style={{ width: padRight }} /> : null}
            </tr>
          ) : null}
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.strike}>
              {padLeft > 0 ? <td style={{ width: padLeft }} /> : null}
              {shown.map((b, local) => {
                // Absolute index: cells, level marks and the flow strip are all
                // keyed by position in the session, not position in the window.
                const i = start + local;
                const cell = row[side].cells[i];
                const value = metricValue(cell, metric);
                const signedMetric = SIGNED_METRICS.includes(metric);
                const { style, strong } = cellStyle(
                  value ?? null,
                  scale,
                  // Delta reads by direction, not by which side of the chain it
                  // sits on: buying green, selling red, whichever wing it is in.
                  signedMetric ? (value != null && value < 0 ? "ce" : "pe") : side,
                  signedMetric,
                );
                const marks = trackMarks.get(`${i}|${row.strike}`);
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
                    {/* Level track: a hairline on the cell rather than a glyph,
                        so it reads as a path across the session without
                        competing with the ΔOI number it sits on. */}
                    {marks?.map((m, mi) =>
                      m.edge ? (
                        <span
                          key={m.key}
                          className="pointer-events-none absolute inset-x-0 top-0 h-[2px]"
                          style={{ background: LEVEL_RGB[m.key], opacity: 0.9 }}
                        />
                      ) : (
                        <span
                          key={m.key}
                          className="pointer-events-none absolute inset-y-0"
                          style={{
                            background: LEVEL_RGB[m.key],
                            opacity: 0.85,
                            width: 3,
                            left: 2 + mi * 4,
                          }}
                        />
                      ),
                    )}
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
              {padRight > 0 ? <td style={{ width: padRight }} /> : null}
            </tr>
          ))}
        </tbody>
        {/* The strip lives INSIDE the wing table rather than beside it: the
            columns scroll, and a separate element would have to mirror that
            scroll to stay aligned. A tfoot cannot drift. */}
        {flow ? (
          <tfoot>
            <tr>
              {padLeft > 0 ? <td style={{ width: padLeft }} /> : null}
              {shown.map((b, local) => {
                const pt = flow.points[start + local];
                const v = pt?.volume ?? null;
                const ceiling = flow.max_volume ?? 0;
                const h = v != null && ceiling > 0 ? Math.max(1, (v / ceiling) * 22) : 0;
                return (
                  <td
                    key={b.key}
                    style={{ width: COL_W, height: 26 }}
                    title={
                      v == null
                        ? `${b.key}: no future bar`
                        : `${b.key} · future volume ${compact(v)} · cum ${compact(pt?.cum_volume)}`
                    }
                    className="border-t border-border/60 bg-background/60 align-bottom"
                  >
                    <div className="flex h-[24px] items-end justify-center">
                      <div
                        style={{ height: h, width: COL_W - 12 }}
                        className={v == null ? "" : "bg-sky-500/60"}
                      />
                    </div>
                  </td>
                );
              })}
              {padRight > 0 ? <td style={{ width: padRight }} /> : null}
            </tr>
          </tfoot>
        ) : null}
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
  const [metric, setMetric] = useState<Metric>("delta");
  const [flow, setFlow] = useState<BuildupFlow | null>(null);
  const [widen, setWiden] = useState<boolean>(false);
  const [sync, setSync] = useState<boolean>(true);
  const [sortKey, setSortKey] = useState<SortKey>("strike");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [minAbsOi, setMinAbsOi] = useState<number>(25_000);
  const [breachOnly, setBreachOnly] = useState<boolean>(false);
  const [thresholdMode, setThresholdMode] = useState<"fixed" | "adaptive">("fixed");

  const [grid, setGrid] = useState<BuildupGrid | null>(null);
  const [levels, setLevels] = useState<BuildupLevels | null>(null);
  const [showLevels, setShowLevels] = useState<boolean>(true);
  const [status, setStatus] = useState<BuildupStatus | null>(null);
  const [expiries, setExpiries] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ceRef = useRef<HTMLDivElement>(null);
  const peRef = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);
  const lastAlertRef = useRef<{ ce: number; pe: number }>({ ce: 0, pe: 0 });
  const anchoredView = useRef<string | null>(null);

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

    // The grid is awaited alone. /buildup/status walks every archived session
    // file to report coverage, which grew with the collector's strike width and
    // now runs seconds — tens of seconds when the single API process is also
    // sampling. Awaiting both together let that slow, decorative call hold the
    // whole ladder blank, which is what it did.
    try {
      setGrid(await api.get<BuildupGrid>(`/buildup/grid?${q}`));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [underlying, sessionDate, expiry, timeframe, range, baseline, widen, minAbsOi, thresholdMode]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  // Coverage feeds only the session dropdown, so it is fetched independently and
  // may land late; nothing on screen waits for it.
  useEffect(() => {
    let cancelled = false;
    api
      .get<BuildupStatus>(`/buildup/status?underlying=${underlying}`, { silent: true })
      .then((s) => !cancelled && setStatus(s))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [underlying]);

  // Levels are fetched on their own, never as part of the grid request: the
  // grid is a pure archive read and this one reaches the gamma snapshot, so a
  // gamma outage must grey this panel rather than blank the ladder. `silent`
  // for the same reason — it degrades in place instead of raising a toast.
  useEffect(() => {
    if (!showLevels || !grid) {
      if (!showLevels) setLevels(null);
      return;
    }
    const q = new URLSearchParams({
      underlying: grid.underlying,
      session_date: grid.session_date,
      expiry: grid.expiry,
      strikes: grid.rows.map((r) => r.strike).join(","),
      track: "true",
      bucket_ends: grid.buckets.map((b) => b.end).join(","),
    });
    let cancelled = false;
    api
      .get<BuildupLevels>(`/buildup/levels?${q}`, { silent: true })
      .then((r) => !cancelled && setLevels(r))
      .catch(() => !cancelled && setLevels(null));
    return () => {
      cancelled = true;
    };
  }, [showLevels, grid?.underlying, grid?.session_date, grid?.expiry, grid?.rows, grid?.buckets]);

  // Underlying flow strip. Its own request for the same reason levels have one:
  // it makes a Kite historical call while the grid does not, so a futures-feed
  // problem greys the strip instead of blanking the ladder.
  useEffect(() => {
    if (!grid) return;
    const q = new URLSearchParams({
      underlying: grid.underlying,
      session_date: grid.session_date,
      timeframe_min: String(grid.timeframe_min),
      bucket_ends: grid.buckets.map((b) => b.end).join(","),
    });
    let cancelled = false;
    api
      .get<BuildupFlow>(`/buildup/flow?${q}`, { silent: true })
      .then((r) => !cancelled && setFlow(r))
      .catch(() => !cancelled && setFlow(null));
    return () => {
      cancelled = true;
    };
  }, [grid?.underlying, grid?.session_date, grid?.timeframe_min, grid?.buckets]);

  // Expiry list follows the chosen session, not today.
  useEffect(() => {
    const q = new URLSearchParams({ underlying });
    if (sessionDate) q.set("session_date", sessionDate);
    api
      .get<{ expiries: string[] }>(`/buildup/expiries?${q}`, { silent: true })
      .then((r) => setExpiries(r.expiries ?? []))
      .catch(() => setExpiries([]));
  }, [underlying, sessionDate]);

  // Open on the latest columns — once per view, not once per bucket.
  //
  // This used to depend on buckets.length, which grows every time the session
  // does: at 1m that is every single minute, so each poll dragged the scroll
  // back to the right edge and threw away wherever the operator was looking.
  // Anchoring is a property of "which grid am I looking at", so it keys on the
  // view and a newly appended bucket is not a new view.
  useEffect(() => {
    if (!grid) return;
    const view = `${grid.underlying}|${grid.session_date}|${grid.timeframe_min}|${grid.expiry}`;
    if (anchoredView.current === view) return;
    anchoredView.current = view;
    for (const ref of [ceRef, peRef]) {
      const el = ref.current;
      if (el) el.scrollLeft = el.scrollWidth;
    }
  }, [grid]);

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
  // Strike levels keyed by the row they land on; price levels keyed by the row
  // they sit *below*, so the rule draws on that row's lower edge.
  const { tagsByStrike, rulesByStrike } = useMemo(() => {
    const tags = new Map<number, BuildupLevel[]>();
    const rules = new Map<number, BuildupLevel[]>();
    if (showLevels && levels) {
      for (const l of levels.levels) {
        if (!l.in_ladder) continue;
        if (l.kind === "strike" && l.strike != null) {
          tags.set(l.strike, [...(tags.get(l.strike) ?? []), l]);
        } else if (l.kind === "price" && l.between?.[0] != null) {
          const anchor = l.between[0];
          rules.set(anchor, [...(rules.get(anchor) ?? []), l]);
        }
      }
    }
    return { tagsByStrike: tags, rulesByStrike: rules };
  }, [levels, showLevels]);

  // Where each level sat at every bucket, keyed "<bucketIndex>|<strike>" so the
  // wing can ask one question per cell. A strike level marks the row it sat on;
  // a price level marks the row it sat ABOVE, drawn on that cell's lower edge —
  // the same convention the centre block uses, for the same reason.
  const trackMarks = useMemo(() => {
    const marks = new Map<string, { key: BuildupLevelKey; edge: boolean }[]>();
    const pts = showLevels ? levels?.track?.points : undefined;
    if (!pts || !grid) return marks;
    const ladder = grid.rows.map((r) => r.strike).sort((a, b) => a - b);
    const put = (i: number, strike: number, key: BuildupLevelKey, edge: boolean) => {
      const k = `${i}|${strike}`;
      marks.set(k, [...(marks.get(k) ?? []), { key, edge }]);
    };
    pts.forEach((pt: BuildupTrackPoint, i) => {
      for (const key of ["call_wall", "put_wall", "pin"] as const) {
        const v = pt[key];
        if (v != null && ladder.includes(v)) put(i, v, key, false);
      }
      for (const key of ["flip", "fut_poc"] as const) {
        const v = pt[key];
        if (v == null) continue;
        // The row immediately below the price: the level lies on its top edge.
        const below = [...ladder].reverse().find((x) => x <= v);
        if (below != null) put(i, below, key, true);
      }
    });
    return marks;
  }, [levels, showLevels, grid]);

  const scaleKey = metric;

  // The strip totals whatever the wings are showing, so it follows `metric`.
  // One ceiling for both sides: grouped bars exist to be compared, and two
  // ceilings would make equal heights mean different amounts.
  const stripTotals = grid
    ? { ce: grid.bucket_totals.ce[metric] ?? [], pe: grid.bucket_totals.pe[metric] ?? [] }
    : null;
  const stripCeiling = grid?.scale_totals?.[metric]?.p95 ?? 0;
  const prevSession = grid?.bucket_totals.prev_session ?? null;

  /** The band summarises a SESSION, so it reads the cumulative form of whatever
   *  the wings are showing — "session cumulative" of a per-bucket metric is its
   *  running total. Mirrors CUMULATIVE_FORM in features.py. */
  const CUM_FORM: Record<Metric, Metric> = {
    delta: "cum",
    cum: "cum",
    vol: "cum_vol",
    cum_vol: "cum_vol",
    delta_vol: "cum_delta_vol",
    cum_delta_vol: "cum_delta_vol",
  };
  const bandMetric = CUM_FORM[metric];

  /** Today so far, and the same measure over the whole previous session.
   *
   *  Today's figure is the last NON-null bucket, not the last: a session's
   *  newest bucket can be a partial minute with nothing recorded yet, and
   *  reading "—" there would blank the band a beat after every poll. */
  const band = (() => {
    const todayOf = (xs: (number | null)[] | undefined) => {
      if (!xs) return null;
      for (let i = xs.length - 1; i >= 0; i--) if (xs[i] != null) return xs[i];
      return null;
    };
    const comparable = !!prevSession?.available && prevSession.metrics.includes(bandMetric);
    return {
      ce: todayOf(grid?.bucket_totals.ce[bandMetric]),
      pe: todayOf(grid?.bucket_totals.pe[bandMetric]),
      prevCe: comparable ? (prevSession?.ce?.[bandMetric] ?? null) : null,
      prevPe: comparable ? (prevSession?.pe?.[bandMetric] ?? null) : null,
      prevDate: prevSession?.date ?? null,
      reason: comparable
        ? null
        : (prevSession?.reason ??
          "No previous-session comparison for this metric — it would need the quote rule re-run over that whole session."),
    };
  })();

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

        <select
          className="h-8 rounded-md border bg-background px-2 text-xs"
          value={metric}
          onChange={(e) => setMetric(e.target.value as Metric)}
        >
          {METRICS.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>

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
        <label
          className="flex items-center gap-1 text-xs text-muted-foreground"
          title="Call/Put wall, pin, gamma flip and futures POC on the ladder"
        >
          <input
            type="checkbox"
            checked={showLevels}
            onChange={(e) => setShowLevels(e.target.checked)}
          />
          Gamma levels
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

      {SIGNED_METRICS.includes(metric) && grid ? (
        (() => {
          let classified = 0;
          let unclassified = 0;
          for (const r of grid.rows) {
            for (const sideKey of ["ce", "pe"] as const) {
              for (const c of r[sideKey].cells) {
                classified += Math.abs(c.delta_vol ?? 0);
                unclassified += c.unclassified_vol ?? 0;
              }
            }
          }
          const total = classified + unclassified;
          const pct = total > 0 ? (unclassified / total) * 100 : 100;
          if (pct < 5) return null;
          return (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-[11px]">
              <strong>{pct.toFixed(0)}% of this session&apos;s volume is unclassified.</strong>{" "}
              Trade direction needs the bid/ask at the time of the trade, which has only been
              archived since 2026-08-30 — earlier sessions have none, so a delta of zero here
              means &quot;could not tell&quot;, not &quot;balanced&quot;.
            </div>
          );
        })()
      ) : null}

      {flow && !flow.available ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-[11px]">
          Underlying flow strip unavailable —{" "}
          {flow.reason === "futures_bars_unavailable"
            ? "front-month future bars could not be fetched (Kite session expired, or the feed is down)"
            : flow.reason === "no_bars_for_session"
              ? "no future bars for this session"
              : flow.reason}
          . Volume per strike is unaffected; it comes from the archive.
        </div>
      ) : null}
      {flow?.available ? (
        <div className="text-[11px] text-muted-foreground">
          Flow strip: front-month future volume, {flow.coverage}/{flow.buckets} buckets ·{" "}
          {compact(flow.total_volume)} total. Volume only: the strike ladder classifies
          direction from its archived book, but these are Kite OHLCV candles for the
          future, which carry no bid/ask to classify against.
        </div>
      ) : null}

      {showLevels && levels ? (
        <div className="flex flex-wrap items-center gap-2 rounded-md border p-2 text-[11px]">
          <span className="font-medium">Gamma levels</span>
          <Badge variant="outline" className="text-[10px]">
            {levels.source === "live" ? "live" : `history · ${levels.session_date}`}
          </Badge>
          {levels.gamma_regime ? (
            <Badge variant="outline" className="text-[10px]">
              {levels.gamma_regime} gamma
            </Badge>
          ) : null}
          {levels.levels.map((l) => (
            <span
              key={l.key}
              title={levelTitle(l)}
              className={`flex items-center gap-1 rounded-sm px-1 ${
                l.in_ladder ? "" : "opacity-50"
              }`}
            >
              <span className={`rounded-sm px-0.5 text-[8px] font-bold ${LEVEL_TONE[l.key]}`}>
                {LEVEL_TAG[l.key]}
              </span>
              <span className="font-mono tabular-nums">{fmtNum(l.price, 0)}</span>
              {l.note ? <span className="text-muted-foreground">(derived)</span> : null}
              {l.in_ladder ? null : <span className="text-muted-foreground">off ladder</span>}
            </span>
          ))}
          {/* Missing levels are named, not silently dropped: "no call wall" and
              "call wall not recorded that day" mean very different things. */}
          {Object.entries(levels.skipped).map(([key, reason]) => (
            <span key={key} className="text-muted-foreground">
              {LEVEL_TAG[key as BuildupLevelKey] ?? key}:{" "}
              {SKIP_REASON[reason] ?? reason}
            </span>
          ))}
          {levels.track ? (
            <span className="text-muted-foreground">
              track:{" "}
              {(Object.keys(LEVEL_TAG) as BuildupLevelKey[])
                .map((k) => `${LEVEL_TAG[k]} ${levels.track!.coverage[k] ?? 0}/${levels.track!.buckets}`)
                .join(" · ")}
            </span>
          ) : null}
          {levels.expiry_match === false ? (
            <span className="font-medium text-amber-600 dark:text-amber-400">
              levels are for {levels.gamma_expiry}, ladder is {levels.grid_expiry} — not comparable
            </span>
          ) : null}
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
              trackMarks={trackMarks}
              flow={flow}
              totals={stripTotals}
              totalsCeiling={stripCeiling}
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
                  {/* Restores the row alignment the wings' strip broke: the
                      three panes line up by fixed heights, not a grid, so a row
                      added to the wing heads must be matched here or every
                      strike label slides 40px off its own CE/PE cells.
                      It carries the summary rather than being blank. */}
                  <tr>
                    <th
                      colSpan={7}
                      style={{ height: STRIP_H + 6, top: HEAD_H }}
                      className="sticky z-10 border-b border-border/60 bg-muted/30 p-0 font-normal"
                    >
                      <CumulativeBand
                        band={band}
                        label={METRICS.find((m) => m.value === bandMetric)?.label ?? ""}
                      />
                    </th>
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
                          style={{
                            height: ROW_H,
                            width: 74,
                            // A price level falls BETWEEN strikes, so it draws on
                            // the row's lower edge rather than inside any cell.
                            ...(rulesByStrike.has(row.strike)
                              ? { borderBottom: "2px solid rgb(245 158 11 / 0.9)" }
                              : {}),
                          }}
                          title={(rulesByStrike.get(row.strike) ?? []).map(levelTitle).join(" | ")}
                          className={`relative border-b border-border/40 text-center font-mono text-[11px] font-semibold tabular-nums ${
                            row.atm ? "bg-primary/20 text-primary" : ""
                          }`}
                        >
                          {row.strike.toLocaleString()}
                          {(tagsByStrike.get(row.strike) ?? []).map((l, i) => (
                            <span
                              key={l.key}
                              title={levelTitle(l)}
                              style={{ right: 1, top: 1 + i * 8 }}
                              className={`absolute rounded-sm px-0.5 text-[7px] font-bold leading-[8px] ${
                                LEVEL_TONE[l.key]
                              } ${l.note ? "opacity-70 italic" : ""}`}
                            >
                              {LEVEL_TAG[l.key]}
                            </span>
                          ))}
                          {(rulesByStrike.get(row.strike) ?? []).map((l, i) => (
                            <span
                              key={l.key}
                              title={levelTitle(l)}
                              style={{ left: 1, bottom: -4 - i * 8 }}
                              className={`absolute rounded-sm px-0.5 text-[7px] font-bold leading-[8px] ${
                                LEVEL_TONE[l.key]
                              }`}
                            >
                              {LEVEL_TAG[l.key]}
                            </span>
                          ))}
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
              trackMarks={trackMarks}
              flow={flow}
              totals={stripTotals}
              totalsCeiling={stripCeiling}
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
