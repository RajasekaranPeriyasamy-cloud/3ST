import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Plotly from "plotly.js-dist-min";
import type {
  Annotations,
  Config,
  Data,
  Layout,
  PlotHoverEvent,
  PlotMouseEvent,
  PlotRelayoutEvent,
  Shape,
} from "plotly.js";

import type { GammaHistoryPoint, GammaReversal, GammaSnapshot, GammaStrikeRow } from "@/lib/types";
import {
  SESSION_CHART,
  SESSION_PLOT_INSET,
  SESSION_SHELL,
  sessionChartTheme,
} from "@/components/charts/sessionChartTheme";
import { useTheme } from "@/hooks/useTheme";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

type PlotDiv = HTMLDivElement & {
  on: (event: string, callback: (event: unknown) => void) => void;
  removeAllListeners?: (event?: string) => void;
};

/** Nearest-strike info card (mirrors Net GEX by Strike panel fields). */
type SessionInfoRow = {
  strike: number;
  net_gex_cr: number;
  ce_oi: number;
  pe_oi: number;
  ce_oi_base: number;
  pe_oi_base: number;
  ce_doi: number | null;
  pe_doi: number | null;
};

type HoverLine = { text: string; color?: string };

/** Min half-width in strike steps so ATM ± ~1 nearby OI bars show initially. */
const ATM_OI_HALF_STEPS = 1;

type ReversalTf = "1m" | "5m" | "15m";
type ReversalGexMode = "live" | "research";

const REVERSAL_LOCK_HINT: Record<ReversalTf, string> = {
  "1m": "1m TF · same-side lock ~18m",
  "5m": "5m bar-close · lock ~55m · use 1m for faster",
  "15m": "15m bar-close · lock ~105m · use 1m for faster",
};

const POS_GREEN = "#22c55e";
const NEG_RED = "#ef4444";
/** Amber Net Gamma — readable on light plot (was ivory on dark). */
const NET_AMBER = "#ca8a04";
const SPOT_BLUE = "#2563eb";
/** ATM IV — darker pink for contrast on white (distinct from Spot / GEX). */
const ATM_IV_PINK = "#db2777";
/** Session futures POC — violet on white legend + light plot. */
/** GEX zero (sign boundary). Slate-400 — the theme zeroline #cbd5e1 is
 *  near-invisible against the #eef1f4 grid on a white plot. */
const GEX_ZERO = "#94a3b8";
const FUT_POC = "#7c3aed";
const CALL_OI = "#ef4444";
const PUT_OI = "#22c55e";
const VOL_POS_FILL = "rgba(34, 197, 94, 0.28)";

/** Right-pane share for CE/PE OI strip (rest is the time series). */
const OI_STRIP_DOMAIN: [number, number] = [0.78, 1];
const SESSION_DOMAIN_WITH_OI: [number, number] = [0, 0.76];
const SESSION_DOMAIN_FULL: [number, number] = [0, 1];

/**
 * Price-level overlay palette (Spot axis). Deliberately outside the GEX series
 * hues above — green/red/amber/blue/pink/violet are all taken by traces.
 * Prev day / prev week stay greyscale: they are background structure, not a
 * gamma reading, and should recede behind the walls.
 */
const LEVEL_CALL_WALL = "#ea580c";
const LEVEL_PUT_WALL = "#0d9488";
const LEVEL_FLIP = "#4f46e5";
const LEVEL_PIN = "#475569";
const LEVEL_SIGMA = "#78716c";
const LEVEL_PREV_DAY = "#94a3b8";
const LEVEL_PREV_WEEK = "#64748b";

type LevelKey = "walls" | "flip" | "pin" | "sigma" | "prevDay" | "prevWeek";

const LEVEL_TOGGLES: { key: LevelKey; label: string; title: string }[] = [
  {
    key: "walls",
    label: "Walls",
    title: "Call / Put gamma walls (peak dealer gamma strikes) on the Spot axis",
  },
  {
    key: "flip",
    label: "Flip",
    title: "Gamma flip / zero-gamma level — dealer hedging changes sign across it",
  },
  {
    key: "pin",
    label: "Pin",
    title: "Pin strike — heaviest gamma concentration strike",
  },
  {
    key: "sigma",
    label: "±1σ",
    title: "Expected-move band (straddle when quoted, else ATM IV)",
  },
  {
    key: "prevDay",
    label: "Prev D",
    title: "Previous day high / low / close",
  },
  {
    key: "prevWeek",
    label: "Prev W",
    title: "Previous week high / low / close",
  },
];

type SessionLevel = {
  key: LevelKey;
  label: string;
  value: number;
  color: string;
  dash: "solid" | "dot" | "dash" | "dashdot";
  width: number;
  /** Echo the current value in the legend chip row (gamma levels only). */
  chip: boolean;
};

/**
 * Snapshot → enabled price levels. Everything here is already computed
 * server-side; this only picks and labels. Non-finite / absent values drop
 * out silently so a missing wall never draws a line at 0.
 */
function resolveSessionLevels(
  snap: GammaSnapshot,
  opts: Record<LevelKey, boolean>,
): SessionLevel[] {
  const out: SessionLevel[] = [];
  const push = (
    key: LevelKey,
    label: string,
    raw: number | null | undefined,
    color: string,
    dash: SessionLevel["dash"],
    width: number,
    chip: boolean,
  ) => {
    if (!opts[key]) return;
    if (raw == null || !Number.isFinite(raw)) return;
    out.push({ key, label, value: Number(raw), color, dash, width, chip });
  };

  push("walls", "Call wall", snap.call_wall, LEVEL_CALL_WALL, "dash", 1.6, true);
  push("walls", "Put wall", snap.put_wall, LEVEL_PUT_WALL, "dash", 1.6, true);
  push("flip", "Flip", snap.flip_level, LEVEL_FLIP, "dashdot", 1.6, true);
  push("pin", "Pin", snap.concentration?.pin_strike, LEVEL_PIN, "dot", 1.5, true);
  push("sigma", "+1σ", snap.expected_move?.sigma1_up, LEVEL_SIGMA, "dot", 1, true);
  push("sigma", "−1σ", snap.expected_move?.sigma1_dn, LEVEL_SIGMA, "dot", 1, true);

  const ref = snap.reference_levels;
  push("prevDay", "PDH", ref?.prev_day_high, LEVEL_PREV_DAY, "dot", 1, false);
  push("prevDay", "PDL", ref?.prev_day_low, LEVEL_PREV_DAY, "dot", 1, false);
  push("prevDay", "PDC", ref?.prev_day_close, LEVEL_PREV_DAY, "dot", 1, false);
  push("prevWeek", "PWH", ref?.prev_week_high, LEVEL_PREV_WEEK, "dot", 1, false);
  push("prevWeek", "PWL", ref?.prev_week_low, LEVEL_PREV_WEEK, "dot", 1, false);
  push("prevWeek", "PWC", ref?.prev_week_close, LEVEL_PREV_WEEK, "dot", 1, false);

  return out;
}

type LevelExtras = { shapes: Partial<Shape>[]; annotations: Partial<Annotations>[] };

/** Paper-y of the session pane's bottom edge (volume pane takes [0, 0.28]). */
const LEVEL_PANE_TOP = 0.985;
const levelPaneBottom = (hasVolume: boolean) => (hasVolume ? 0.375 : 0.015);

/**
 * Horizontal level lines on the Spot axis, clipped to the visible band.
 *
 * In-range: full-width line + a left-edge label chip (Sensibull-style).
 * Out of range: a stacked edge marker (▲ above / ▼ below) so an off-screen
 * wall still reads its value — the Spot scale is deliberately narrow
 * (see `computeSpotYRange`) and must not be stretched to swallow a far wall.
 */
function buildLevelLayoutExtras(
  levels: SessionLevel[],
  yRange: [number, number] | undefined,
  hasVolume: boolean,
): LevelExtras {
  const shapes: Partial<Shape>[] = [];
  const annotations: Partial<Annotations>[] = [];
  if (!levels.length) return { shapes, annotations };

  const lo = yRange ? Math.min(yRange[0], yRange[1]) : Number.NEGATIVE_INFINITY;
  const hi = yRange ? Math.max(yRange[0], yRange[1]) : Number.POSITIVE_INFINITY;
  const span = hi - lo;
  // Roughly one label height on the Spot axis — used to stagger labels that
  // would otherwise print on top of each other at near-identical levels.
  const labelGap = Number.isFinite(span) ? span * 0.045 : 0;

  const inRange = levels
    .filter((l) => l.value >= lo && l.value <= hi)
    .sort((a, b) => a.value - b.value);
  // Closest-to-visible first, so the nearest off-screen level sits at the edge.
  const above = levels.filter((l) => l.value > hi).sort((a, b) => a.value - b.value);
  const below = levels.filter((l) => l.value < lo).sort((a, b) => b.value - a.value);

  let lastLabelY: number | null = null;
  let stagger = 0;
  for (const l of inRange) {
    shapes.push({
      type: "line",
      xref: "paper",
      yref: "y2",
      x0: 0,
      x1: 1,
      y0: l.value,
      y1: l.value,
      line: { color: l.color, width: l.width, dash: l.dash },
      layer: "below",
    });
    const collides = lastLabelY != null && labelGap > 0 && l.value - lastLabelY < labelGap;
    stagger = collides ? (stagger + 1) % 3 : 0;
    lastLabelY = l.value;
    annotations.push({
      text: `${l.label} ${formatSpot(l.value)}`,
      xref: "paper",
      yref: "y2",
      x: 0.004 + stagger * 0.11,
      y: l.value,
      showarrow: false,
      xanchor: "left",
      yanchor: "middle",
      font: { size: 9, color: "#ffffff", family: SESSION_CHART.fontFamily },
      bgcolor: l.color,
      borderpad: 2,
      opacity: 0.92,
    });
  }

  const pushEdge = (list: SessionLevel[], paperY: number, arrow: string, dir: -1 | 1) => {
    list.forEach((l, i) => {
      annotations.push({
        text: `${arrow} ${l.label} ${formatSpot(l.value)}`,
        xref: "paper",
        yref: "paper",
        x: 0.004,
        y: paperY,
        yshift: dir * i * 13,
        showarrow: false,
        xanchor: "left",
        yanchor: dir < 0 ? "top" : "bottom",
        font: { size: 9, color: "#ffffff", family: SESSION_CHART.fontFamily },
        bgcolor: l.color,
        borderpad: 2,
        opacity: 0.78,
      });
    });
  };
  pushEdge(above, LEVEL_PANE_TOP, "▲", -1);
  pushEdge(below, levelPaneBottom(hasVolume), "▼", 1);

  return { shapes, annotations };
}

function toMs(t: string | null | undefined, tsMs?: number | null): number | null {
  if (tsMs != null && Number.isFinite(tsMs)) return Number(tsMs);
  if (!t) return null;
  const ms = new Date(t).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function formatHhMmIst(t: string | null | undefined, tsMs?: number | null): string | null {
  const ms = toMs(t, tsMs);
  if (ms == null) return null;
  return new Date(ms).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  });
}

/** Compact pivot / confirm bits: `08:39 pivot · conf 09:12` or pivot-only for legacy. */
export function formatReversalChipTimes(rev: Pick<GammaReversal, "t" | "ts_ms" | "confirmed_at" | "confirmed_ts_ms">): string {
  const pivot = formatHhMmIst(rev.t, rev.ts_ms);
  const conf = formatHhMmIst(rev.confirmed_at, rev.confirmed_ts_ms);
  if (pivot == null) return conf == null ? "—" : `conf ${conf}`;
  if (conf == null) return pivot;
  return `${pivot} pivot · conf ${conf}`;
}

/**
 * Delay between a signal's labelled confirm bar and the moment it actually
 * appeared, in whole minutes. Both chip times are backdated, so a signal whose
 * gate cleared late advertises a confirm time nobody could have traded on.
 * Returns null when there is nothing to disclose: no emit stamp (signals frozen
 * before the field existed) or a delay too small to matter.
 */
function reversalEmitLagMin(
  rev: Pick<GammaReversal, "confirmed_ts_ms" | "confirmed_at" | "emitted_ts_ms" | "emitted_at">,
  minMinutes = 2,
): number | null {
  const conf = toMs(rev.confirmed_at ?? null, rev.confirmed_ts_ms);
  const seen = toMs(rev.emitted_at ?? null, rev.emitted_ts_ms);
  if (conf == null || seen == null) return null;
  const mins = Math.round((seen - conf) / 60000);
  return mins >= minMinutes ? mins : null;
}

/**
 * Human label for the gate holding a signal back.
 *
 * Once the confirm window has closed the pivot can never promote, so it reads
 * as settled rather than pending — a permanently "waiting" chip is worse than
 * no chip.
 */
function formatBlockedBy(
  blocked: string | null | undefined,
  expired?: boolean | null,
): string | null {
  if (!blocked) return null;
  const parts = String(blocked)
    .split("+")
    .map((p) => (p === "gex" ? "GEX" : p === "oi" ? "OI" : p))
    .filter(Boolean);
  if (!parts.length) return null;
  const gates = parts.join(" + ");
  return expired ? `${gates} never cleared` : `waiting ${gates}`;
}

function lastFinite(arr: (number | null)[]): number | null {
  for (let i = arr.length - 1; i >= 0; i--) {
    const v = arr[i];
    if (v != null && Number.isFinite(v)) return v;
  }
  return null;
}

/** Format GEX already in ₹Cr units (OI Movers–style compact legend). */
function formatGexCr(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1000) return `${value.toFixed(0)} Cr`;
  if (abs >= 10) return `${value.toFixed(1)} Cr`;
  if (abs >= 1) return `${value.toFixed(2)} Cr`;
  return `${value.toFixed(3)} Cr`;
}

function formatSpot(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function formatAtmIv(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value.toFixed(2)}%`;
}

function gexCrFromSnap(
  cr: number | null | undefined,
  raw: number | null | undefined,
): number | null {
  if (cr != null && Number.isFinite(cr)) return cr;
  if (raw != null && Number.isFinite(raw)) return raw / 1e7;
  return null;
}

function medianStrikeStep(strikes: number[]): number {
  if (strikes.length < 2) return 50;
  const sorted = [...strikes].sort((a, b) => a - b);
  const gaps: number[] = [];
  for (let i = 1; i < sorted.length; i++) {
    const g = sorted[i]! - sorted[i - 1]!;
    if (g > 0) gaps.push(g);
  }
  if (!gaps.length) return 50;
  gaps.sort((a, b) => a - b);
  return gaps[Math.floor(gaps.length / 2)]!;
}

type SessionOiSeg = {
  /** Strike (pinned to Spot / right Y). */
  y: number;
  /** Segment length in OI contracts. */
  width: number;
  /** Distance from the right edge (OI=0) to the segment's right edge. */
  base: number;
  color: string;
  kind: "solid" | "stripe" | "hollow";
};

/**
 * Sensibull-style OI segments on a dedicated right strip.
 * Y = strike (yaxis2). X grows left from the strip's right edge (reversed xaxis4).
 */
function sessionOiSegments(
  strikeY: number,
  color: string,
  baseOi: number,
  doi: number | null,
  showBase: boolean,
  showChange: boolean,
  currentOi: number,
): SessionOiSeg[] {
  const segs: SessionOiSeg[] = [];
  const safeBase = Math.max(0, Number.isFinite(baseOi) ? baseOi : 0);
  const safeCurrent = Math.max(0, Number.isFinite(currentOi) ? currentOi : 0);

  const push = (kind: SessionOiSeg["kind"], base: number, width: number) => {
    if (!(width > 0)) return;
    segs.push({ y: strikeY, width, base, color, kind });
  };

  if (showBase && showChange && doi != null && Number.isFinite(doi)) {
    if (doi >= 0) {
      push("solid", 0, safeBase);
      push("stripe", safeBase, Math.max(0, doi));
    } else {
      push("solid", 0, safeCurrent);
      push("hollow", safeCurrent, Math.max(0, safeBase - safeCurrent));
    }
  } else if (showBase) {
    const val = safeCurrent > 0 ? safeCurrent : safeBase;
    push("solid", 0, val);
  } else if (showChange && doi != null && Number.isFinite(doi) && doi !== 0) {
    push(doi > 0 ? "stripe" : "hollow", 0, Math.abs(doi));
  }

  return segs;
}

function pushSessionOiTrace(
  data: Data[],
  segs: SessionOiSeg[],
  kind: SessionOiSeg["kind"],
  name: string,
  barThick: number,
) {
  const filtered = segs.filter((s) => s.kind === kind && s.width > 0);
  if (!filtered.length) return;
  const color = filtered[0]!.color;
  const marker: Record<string, unknown> =
    kind === "solid"
      ? { color, line: { width: 0 } }
      : kind === "stripe"
        ? {
            color,
            pattern: {
              shape: "/",
              size: 5,
              solidity: 0.4,
              fgcolor: color,
              bgcolor: "rgba(255,255,255,0.85)",
            },
            line: { color, width: 1 },
          }
        : { color: "rgba(0,0,0,0)", line: { color, width: 1.5 } };

  data.push({
    type: "bar",
    orientation: "h",
    name,
    showlegend: false,
    y: filtered.map((s) => s.y),
    x: filtered.map((s) => s.width),
    base: filtered.map((s) => s.base),
    width: barThick,
    marker,
    hoverinfo: "skip",
    cliponaxis: false,
    xaxis: "x4",
    yaxis: "y2",
  } as Data);
}

function fmtOi(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return Math.round(v).toLocaleString();
}

function toSessionInfoRow(r: GammaStrikeRow): SessionInfoRow {
  return {
    strike: r.strike,
    net_gex_cr: Number.isFinite(r.net_gex) ? r.net_gex / 1e7 : 0,
    ce_oi: r.ce_oi || 0,
    pe_oi: r.pe_oi || 0,
    ce_oi_base: r.ce_oi_base ?? 0,
    pe_oi_base: r.pe_oi_base ?? 0,
    ce_doi: r.ce_doi ?? null,
    pe_doi: r.pe_doi ?? null,
  };
}

function buildSessionHoverLines(
  d: SessionInfoRow,
  opts: { showCePeOi: boolean; showDoi: boolean },
): HoverLine[] {
  const lines: HoverLine[] = [
    { text: `Strike: ${d.strike.toLocaleString()}` },
    {
      text: `Net GEX: ${d.net_gex_cr.toFixed(2)} ₹Cr`,
      color: d.net_gex_cr >= 0 ? POS_GREEN : NEG_RED,
    },
  ];
  if ((opts.showCePeOi || opts.showDoi) && d.ce_oi !== 0) {
    lines.push({
      text: `Call OI: ${fmtOi(d.ce_oi)} · base ${fmtOi(d.ce_oi_base)} · Δ ${fmtOi(d.ce_doi)}`,
      color: CALL_OI,
    });
  }
  if ((opts.showCePeOi || opts.showDoi) && d.pe_oi !== 0) {
    lines.push({
      text: `Put OI: ${fmtOi(d.pe_oi)} · base ${fmtOi(d.pe_oi_base)} · Δ ${fmtOi(d.pe_doi)}`,
      color: PUT_OI,
    });
  }
  return lines;
}

function rowFromHoverY(rows: SessionInfoRow[], y: unknown): SessionInfoRow | null {
  const n = typeof y === "number" ? y : Number(y);
  if (!Number.isFinite(n) || !rows.length) return null;
  let best = rows[0]!;
  let bestDist = Math.abs(best.strike - n);
  for (let i = 1; i < rows.length; i++) {
    const d = Math.abs(rows[i]!.strike - n);
    if (d < bestDist) {
      best = rows[i]!;
      bestDist = d;
    }
  }
  return best;
}

/** Spot-path y-range with pad large enough for ATM ± a few strikes' OI (not full chain). */
function computeSpotYRange(
  spotY: number[],
  snapSpot: number | null | undefined,
  step: number,
): [number, number] | undefined {
  const spotVals = [
    ...spotY,
    ...(snapSpot != null && Number.isFinite(snapSpot) ? [Number(snapSpot)] : []),
  ];
  if (!spotVals.length) return undefined;
  const lo = Math.min(...spotVals);
  const hi = Math.max(...spotVals);
  const pad = Math.max((hi - lo) * 0.03, ATM_OI_HALF_STEPS * step);
  return [lo - pad, hi + pad];
}

/**
 * CE/PE OI bar traces for strikes inside [yMin, yMax] (± one step).
 * Does not touch yaxis2 — caller owns Spot scale.
 */
function buildSessionOiBundle(
  strikeRows: GammaStrikeRow[],
  yMin: number,
  yMax: number,
  showCePeOi: boolean,
  showDoi: boolean,
): { traces: Data[]; oiMax: number } {
  const overlaysOn = showCePeOi || showDoi;
  if (!overlaysOn || !strikeRows.length || !Number.isFinite(yMin) || !Number.isFinite(yMax)) {
    return { traces: [], oiMax: 0 };
  }

  const strikes = strikeRows.map((r) => r.strike).filter((s) => Number.isFinite(s));
  const step = medianStrikeStep(strikes);
  const lo = Math.min(yMin, yMax);
  const hi = Math.max(yMin, yMax);
  const visibleRows = strikeRows.filter(
    (r) => Number.isFinite(r.strike) && r.strike >= lo - step && r.strike <= hi + step,
  );

  let oiMax = 0;
  for (const r of visibleRows) {
    const ceBase = r.ce_oi_base ?? 0;
    const peBase = r.pe_oi_base ?? 0;
    const ceDoi = r.ce_doi ?? 0;
    const peDoi = r.pe_doi ?? 0;
    oiMax = Math.max(
      oiMax,
      r.ce_oi || 0,
      r.pe_oi || 0,
      ceBase,
      peBase,
      ceBase + Math.max(0, ceDoi),
      peBase + Math.max(0, peDoi),
      Math.abs(ceDoi),
      Math.abs(peDoi),
    );
  }

  if (!visibleRows.length || !(oiMax > 0)) {
    return { traces: [], oiMax: 0 };
  }

  oiMax = Math.max(oiMax, 1);
  const yOff = step * 0.13;
  const barThick = step * 0.2;
  const putSegs: SessionOiSeg[] = [];
  const callSegs: SessionOiSeg[] = [];
  for (const r of visibleRows) {
    putSegs.push(
      ...sessionOiSegments(
        r.strike - yOff,
        PUT_OI,
        r.pe_oi_base ?? 0,
        r.pe_doi ?? null,
        showCePeOi,
        showDoi,
        r.pe_oi || 0,
      ),
    );
    callSegs.push(
      ...sessionOiSegments(
        r.strike + yOff,
        CALL_OI,
        r.ce_oi_base ?? 0,
        r.ce_doi ?? null,
        showCePeOi,
        showDoi,
        r.ce_oi || 0,
      ),
    );
  }

  const traces: Data[] = [];
  pushSessionOiTrace(traces, putSegs, "solid", "Put OI", barThick);
  pushSessionOiTrace(traces, putSegs, "stripe", "Put ΔOI↑", barThick);
  pushSessionOiTrace(traces, putSegs, "hollow", "Put ΔOI↓", barThick);
  pushSessionOiTrace(traces, callSegs, "solid", "Call OI", barThick);
  pushSessionOiTrace(traces, callSegs, "stripe", "Call ΔOI↑", barThick);
  pushSessionOiTrace(traces, callSegs, "hollow", "Call ΔOI↓", barThick);
  return { traces, oiMax };
}

function parseY2RangeFromRelayout(ed: PlotRelayoutEvent): [number, number] | "autorange" | null {
  const rec = ed as Record<string, unknown>;
  if (rec["yaxis2.autorange"] === true) return "autorange";
  const nested = rec["yaxis2.range"];
  if (Array.isArray(nested) && nested.length >= 2) {
    const a = Number(nested[0]);
    const b = Number(nested[1]);
    if (Number.isFinite(a) && Number.isFinite(b)) return [a, b];
  }
  const a = Number(rec["yaxis2.range[0]"]);
  const b = Number(rec["yaxis2.range[1]"]);
  if (Number.isFinite(a) && Number.isFinite(b)) return [a, b];
  return null;
}

type GexSample = { ms: number; net: number; pos: number | null; neg: number | null };

/** Collect GEX samples — prefer full-day history ticks over sparse chart attach. */
function collectGexSamples(
  history: GammaHistoryPoint[] | undefined,
  chart: GammaHistoryPoint[],
): GexSample[] {
  const source =
    history?.some((h) => h.total_gex != null && Number.isFinite(h.total_gex)) ? history : chart;
  const out: GexSample[] = [];
  for (const h of source) {
    const ms = toMs(h.t, h.ts_ms);
    if (ms == null || h.total_gex == null || !Number.isFinite(h.total_gex)) continue;
    const pos =
      h.pos_gex != null && Number.isFinite(h.pos_gex) ? h.pos_gex / 1e7 : null;
    const neg =
      h.neg_gex != null && Number.isFinite(h.neg_gex) ? h.neg_gex / 1e7 : null;
    out.push({ ms, net: h.total_gex / 1e7, pos, neg });
  }
  out.sort((a, b) => a.ms - b.ms);
  return out;
}

/**
 * Forward-fill a numeric series onto a dense time grid.
 * - Before the first sample: leave null (earlier session not recorded — no invented line).
 * - From each sample onward: carry forward until the next sample.
 * Spot / volume are never filled here.
 */
function fillGexSeries(
  gridMs: number[],
  samples: { ms: number; value: number }[],
): (number | null)[] {
  if (!gridMs.length || !samples.length) return gridMs.map(() => null);
  let si = 0;
  let last: number | null = null;
  const filled: (number | null)[] = [];
  for (const ms of gridMs) {
    while (si < samples.length && samples[si]!.ms <= ms) {
      last = samples[si]!.value;
      si += 1;
    }
    filled.push(last);
  }
  return filled;
}

/** Collect ATM IV samples — prefer history ticks that carry atm_iv. */
function collectAtmIvSamples(
  history: GammaHistoryPoint[] | undefined,
  chart: GammaHistoryPoint[],
): { ms: number; value: number }[] {
  const source =
    history?.some((h) => h.atm_iv != null && Number.isFinite(h.atm_iv)) ? history : chart;
  const out: { ms: number; value: number }[] = [];
  for (const h of source) {
    const ms = toMs(h.t, h.ts_ms);
    if (ms == null || h.atm_iv == null || !Number.isFinite(h.atm_iv)) continue;
    out.push({ ms, value: Number(h.atm_iv) });
  }
  out.sort((a, b) => a.ms - b.ms);
  return out;
}

export interface GexSessionPlotlyProps {
  snap: GammaSnapshot;
  /** Options gear (GammaOptionsPopover) — rendered top-right of the panel. */
  optionsSlot?: ReactNode;
  reversalTf: ReversalTf;
  onReversalTfChange: (tf: ReversalTf) => void;
  reversalGexGate: boolean;
  onReversalGexGateChange: (on: boolean) => void;
  reversalGexMode: ReversalGexMode;
  onReversalGexModeChange: (mode: ReversalGexMode) => void;
  reversalOiGate: boolean;
  onReversalOiGateChange: (on: boolean) => void;
  className?: string;
}

/**
 * Day-spot GEX recorder (Plotly dual pane).
 * Top: +VE GEX (green), −VE GEX (red), Net Gamma (amber), Spot (blue, right),
 * ATM IV (pink, dedicated overlay axis). Bottom: minute volume when candles carry it.
 * Light Straddle-adjacent Plotly theme (stays light in app dark mode).
 */
export function GexSessionPlotly({
  snap,
  optionsSlot,
  reversalTf,
  onReversalTfChange,
  reversalGexGate,
  onReversalGexGateChange,
  reversalGexMode,
  onReversalGexModeChange,
  reversalOiGate,
  onReversalOiGateChange,
  className,
}: GexSessionPlotlyProps) {
  const plotRef = useRef<HTMLDivElement>(null);
  const { isDark } = useTheme();
  const [showCePeOi, setShowCePeOi] = useState(true);
  const [showDoi, setShowDoi] = useState(true);
  const [showAtmIv, setShowAtmIv] = useState(true);
  /** Price-level overlays default off — the pane already carries 8 series + markers. */
  const [levelOpts, setLevelOpts] = useState<Record<LevelKey, boolean>>({
    walls: false,
    flip: false,
    pin: false,
    sigma: false,
    prevDay: false,
    prevWeek: false,
  });
  const [infoRow, setInfoRow] = useState<SessionInfoRow | null>(null);
  const [infoPinned, setInfoPinned] = useState(false);
  const infoPinnedRef = useRef(false);
  infoPinnedRef.current = infoPinned;

  /** User-panned Spot/Strike range — preserved across live polls; cleared on autorange / identity reset. */
  const userY2RangeRef = useRef<[number, number] | null>(null);
  const spotRangeRef = useRef<[number, number] | undefined>(undefined);
  const coreTracesRef = useRef<Data[]>([]);
  const volumeTracesRef = useRef<Data[]>([]);
  const plotConfigRef = useRef<Partial<Config>>({});
  const layoutBaseRef = useRef<Partial<Layout>>({});
  const infoRowsRef = useRef<SessionInfoRow[]>([]);
  const oiOptsRef = useRef({ showCePeOi: true, showDoi: true });
  const strikeRowsRef = useRef<GammaStrikeRow[]>([]);
  const prevUnderlyingRef = useRef<string | null>(null);
  const prevOverlaysRef = useRef<boolean | null>(null);
  /** Level overlays are re-clipped on pan, so the pieces they compose from are refs. */
  const levelsRef = useRef<SessionLevel[]>([]);
  const baseShapesRef = useRef<Partial<Shape>[]>([]);
  const baseAnnotationsRef = useRef<Partial<Annotations>[]>([]);

  const chartRaw = (snap.chart_series?.length ? snap.chart_series : snap.history) ?? [];

  const series = useMemo(() => {
    const spotX: Date[] = [];
    const spotY: number[] = [];
    const volX: Date[] = [];
    const volY: number[] = [];
    const gridMs: number[] = [];

    for (const h of chartRaw) {
      const ms = toMs(h.t, h.ts_ms);
      if (ms == null) continue;
      gridMs.push(ms);

      if (h.spot != null && Number.isFinite(h.spot)) {
        spotX.push(new Date(ms));
        spotY.push(Number(h.spot));
      }
      if (h.volume != null && Number.isFinite(h.volume) && Number(h.volume) > 0) {
        volX.push(new Date(ms));
        volY.push(Number(h.volume));
      }
    }

    const gexSamples = collectGexSamples(snap.history, chartRaw);
    const atmIvSamples = collectAtmIvSamples(snap.history, chartRaw);

    // Ensure sample timestamps are on the grid so fill steps land on exact poll times.
    const msSet = new Set(gridMs);
    for (const s of gexSamples) {
      if (!msSet.has(s.ms)) {
        msSet.add(s.ms);
        gridMs.push(s.ms);
      }
    }
    for (const s of atmIvSamples) {
      if (!msSet.has(s.ms)) {
        msSet.add(s.ms);
        gridMs.push(s.ms);
      }
    }
    gridMs.sort((a, b) => a - b);

    const netFilled = fillGexSeries(
      gridMs,
      gexSamples.map((s) => ({ ms: s.ms, value: s.net })),
    );
    const posSamples = gexSamples
      .filter((s): s is GexSample & { pos: number } => s.pos != null)
      .map((s) => ({ ms: s.ms, value: s.pos }));
    const negSamples = gexSamples
      .filter((s): s is GexSample & { neg: number } => s.neg != null)
      .map((s) => ({ ms: s.ms, value: s.neg }));
    const posFilled = fillGexSeries(gridMs, posSamples);
    const negFilled = fillGexSeries(gridMs, negSamples);
    const atmIvFilled = fillGexSeries(gridMs, atmIvSamples);

    const gexX: Date[] = [];
    const netY: (number | null)[] = [];
    const posY: (number | null)[] = [];
    const negY: (number | null)[] = [];

    for (let i = 0; i < gridMs.length; i++) {
      const net = netFilled[i];
      const pos = posFilled[i];
      const neg = negFilled[i];
      if (net == null && pos == null && neg == null) continue;
      gexX.push(new Date(gridMs[i]));
      netY.push(net);
      posY.push(pos);
      negY.push(neg);
    }

    const atmIvX: Date[] = [];
    const atmIvY: (number | null)[] = [];
    for (let i = 0; i < gridMs.length; i++) {
      const iv = atmIvFilled[i];
      if (iv == null) continue;
      atmIvX.push(new Date(gridMs[i]));
      atmIvY.push(iv);
    }

    const sampleX = gexSamples.map((s) => new Date(s.ms));
    const sampleNetY = gexSamples.map((s) => s.net);

    const hasComponentSeries = posSamples.length > 0 || negSamples.length > 0;

    return {
      gexX,
      netY,
      posY,
      negY,
      sampleX,
      sampleNetY,
      spotX,
      spotY,
      volX,
      volY,
      atmIvX,
      atmIvY,
      hasComponentSeries,
    };
  }, [chartRaw, snap.history]);

  const levelDefs = useMemo(() => resolveSessionLevels(snap, levelOpts), [snap, levelOpts]);

  const underlying = String(snap.underlying ?? "").trim() || "—";
  const expiryLabel = snap.expiry ? String(snap.expiry).trim() : "";
  const reversals = snap.reversals ?? [];
  const gexRelaxed = snap.reversals_gex_relaxed === true;
  const gexWaiting = snap.reversals_gex_waiting === true;
  const gexSamplesN = snap.reversals_gex_samples ?? 0;
  const gexMinSamples = snap.reversals_gex_min_samples ?? 5;
  const gexHistoryPartial = snap.gex_history_partial === true;
  const gexHistoryStartedLabel = formatHhMmIst(snap.gex_history_started_at ?? null);

  // Purge only on unmount — live series updates must not destroy pan/hover state.
  useEffect(() => {
    const el = plotRef.current;
    return () => {
      if (el) Plotly.purge(el);
    };
  }, []);

  useEffect(() => {
    const el = plotRef.current as PlotDiv | null;
    if (!el) return;

    const {
      gexX,
      netY,
      posY,
      negY,
      sampleX,
      sampleNetY,
      spotX,
      spotY,
      volX,
      volY,
      atmIvX,
      atmIvY,
      hasComponentSeries,
    } = series;
    if (gexX.length < 1 && spotX.length < 1) {
      Plotly.purge(el);
      setInfoRow(null);
      setInfoPinned(false);
      return;
    }
    const plotAtmIv = showAtmIv && atmIvX.length > 0;

    const overlaysOn = showCePeOi || showDoi;
    const strikeRows = (snap.strikes ?? []) as GammaStrikeRow[];
    strikeRowsRef.current = strikeRows;
    oiOptsRef.current = { showCePeOi, showDoi };
    infoRowsRef.current = strikeRows
      .filter((r) => Number.isFinite(r.strike))
      .map(toSessionInfoRow);

    // Reset panned Spot range when underlying changes or OI overlays are toggled.
    if (prevUnderlyingRef.current !== null && prevUnderlyingRef.current !== underlying) {
      userY2RangeRef.current = null;
    }
    prevUnderlyingRef.current = underlying;
    if (prevOverlaysRef.current !== null && prevOverlaysRef.current !== overlaysOn) {
      userY2RangeRef.current = null;
    }
    prevOverlaysRef.current = overlaysOn;

    const coreTraces: Data[] = [];
    const hasVolume = volX.length > 1;

    // Top pane (xaxis / yaxis / yaxis2): component GEX + net + spot
    if (hasComponentSeries && gexX.length > 0) {
      coreTraces.push({
        type: "scatter",
        mode: "lines",
        name: "+VE GEX",
        x: gexX,
        y: posY,
        xaxis: "x",
        yaxis: "y",
        line: { color: POS_GREEN, width: 2.2, shape: "spline", smoothing: 0.5 },
        hovertemplate: "%{x|%H:%M:%S}<br>+VE GEX %{y:.2f} Cr<extra></extra>",
      } as Data);
      coreTraces.push({
        type: "scatter",
        mode: "lines",
        name: "−VE GEX",
        x: gexX,
        y: negY,
        xaxis: "x",
        yaxis: "y",
        line: { color: NEG_RED, width: 2.2, shape: "spline", smoothing: 0.5 },
        hovertemplate: "%{x|%H:%M:%S}<br>−VE GEX %{y:.2f} Cr<extra></extra>",
      } as Data);
    }

    if (gexX.length > 0) {
      coreTraces.push({
        type: "scatter",
        mode: "lines",
        name: "Net Gamma",
        x: gexX,
        y: netY,
        xaxis: "x",
        yaxis: "y",
        line: { color: NET_AMBER, width: 2.4, shape: "spline", smoothing: 0.5 },
        hovertemplate: "%{x|%H:%M:%S}<br>Net Gamma %{y:.2f} Cr<extra></extra>",
      } as Data);
    }

    // Sign-flip markers only. A dot per sample put ~400 markers on the pane and
    // buried the amber Net Gamma line under them; the only reading that changes
    // is where net gamma crosses zero, so mark the first sample of each regime.
    const flipX: Date[] = [];
    const flipY: number[] = [];
    const flipColors: string[] = [];
    const flipLabels: string[] = [];
    for (let i = 1; i < sampleNetY.length; i++) {
      const curr = sampleNetY[i]!;
      const prev = sampleNetY[i - 1]!;
      if ((curr >= 0) === (prev >= 0)) continue;
      flipX.push(sampleX[i]!);
      flipY.push(curr);
      flipColors.push(curr >= 0 ? POS_GREEN : NEG_RED);
      flipLabels.push(curr >= 0 ? "+VE" : "−VE");
    }

    if (flipX.length > 0) {
      coreTraces.push({
        type: "scatter",
        mode: "markers",
        name: "Sign flip",
        x: flipX,
        y: flipY,
        text: flipLabels,
        xaxis: "x",
        yaxis: "y",
        marker: {
          size: 9,
          color: flipColors,
          line: { color: "#fff", width: 1 },
        },
        hovertemplate: "%{x|%H:%M:%S}<br>Net Gamma turns %{text}<br>%{y:.2f} Cr<extra></extra>",
      } as Data);
    }

    if (spotX.length > 0) {
      coreTraces.push({
        type: "scatter",
        mode: "lines",
        name: "Spot",
        x: spotX,
        y: spotY,
        xaxis: "x",
        yaxis: "y2",
        line: { color: SPOT_BLUE, width: 1.4 },
        hovertemplate: "%{x|%H:%M:%S}<br>Spot %{y:,.1f}<extra></extra>",
      } as Data);
    }

    if (plotAtmIv) {
      coreTraces.push({
        type: "scatter",
        mode: "lines",
        name: "ATM IV",
        x: atmIvX,
        y: atmIvY,
        xaxis: "x",
        yaxis: "y5",
        line: { color: ATM_IV_PINK, width: 1.6, shape: "hv" },
        hovertemplate: "%{x|%H:%M:%S}<br>ATM IV %{y:.2f}%<extra></extra>",
      } as Data);
    }

    const bullX: Date[] = [];
    const bullY: number[] = [];
    const bearX: Date[] = [];
    const bearY: number[] = [];
    const bullMutedX: Date[] = [];
    const bullMutedY: number[] = [];
    const bearMutedX: Date[] = [];
    const bearMutedY: number[] = [];
    // Confirmation markers at confirmed_at (distinct arrow vs pivot triangle).
    const bullConfX: Date[] = [];
    const bullConfY: number[] = [];
    const bearConfX: Date[] = [];
    const bearConfY: number[] = [];
    const bullConfMutedX: Date[] = [];
    const bullConfMutedY: number[] = [];
    const bearConfMutedX: Date[] = [];
    const bearConfMutedY: number[] = [];
    for (const r of reversals) {
      const ms = toMs(r.t, r.ts_ms);
      if (ms == null || r.spot == null) continue;
      const muted =
        r.provisional === true ||
        r.partial_ungated === true ||
        (gexHistoryPartial && reversalGexGate && r.gex_confirm !== true);
      if (r.side === "bullish") {
        if (muted) {
          bullMutedX.push(new Date(ms));
          bullMutedY.push(r.spot);
        } else {
          bullX.push(new Date(ms));
          bullY.push(r.spot);
        }
      } else if (muted) {
        bearMutedX.push(new Date(ms));
        bearMutedY.push(r.spot);
      } else {
        bearX.push(new Date(ms));
        bearY.push(r.spot);
      }

      const confMs = toMs(r.confirmed_at, r.confirmed_ts_ms);
      if (confMs == null) continue;
      if (r.side === "bullish") {
        if (muted) {
          bullConfMutedX.push(new Date(confMs));
          bullConfMutedY.push(r.spot);
        } else {
          bullConfX.push(new Date(confMs));
          bullConfY.push(r.spot);
        }
      } else if (muted) {
        bearConfMutedX.push(new Date(confMs));
        bearConfMutedY.push(r.spot);
      } else {
        bearConfX.push(new Date(confMs));
        bearConfY.push(r.spot);
      }
    }
    if (bullMutedX.length) {
      coreTraces.push({
        type: "scatter",
        mode: "markers",
        name: "Bull pivot (provisional)",
        x: bullMutedX,
        y: bullMutedY,
        xaxis: "x",
        yaxis: "y2",
        marker: {
          color: "rgba(34, 197, 94, 0.35)",
          size: 8,
          symbol: "triangle-up",
          line: { color: "rgba(255,255,255,0.35)", width: 1 },
        },
        hovertemplate: "%{x|%H:%M}<br>Bull pivot (provisional) %{y:,.1f}<extra></extra>",
      } as Data);
    }
    if (bearMutedX.length) {
      coreTraces.push({
        type: "scatter",
        mode: "markers",
        name: "Bear pivot (provisional)",
        x: bearMutedX,
        y: bearMutedY,
        xaxis: "x",
        yaxis: "y2",
        marker: {
          color: "rgba(239, 68, 68, 0.35)",
          size: 8,
          symbol: "triangle-down",
          line: { color: "rgba(255,255,255,0.35)", width: 1 },
        },
        hovertemplate: "%{x|%H:%M}<br>Bear pivot (provisional) %{y:,.1f}<extra></extra>",
      } as Data);
    }
    if (bullX.length) {
      coreTraces.push({
        type: "scatter",
        mode: "markers",
        name: "Bull pivot",
        x: bullX,
        y: bullY,
        xaxis: "x",
        yaxis: "y2",
        marker: { color: POS_GREEN, size: 9, symbol: "triangle-up", line: { color: "#fff", width: 1 } },
        hovertemplate: "%{x|%H:%M}<br>Bull pivot %{y:,.1f}<extra></extra>",
      } as Data);
    }
    if (bearX.length) {
      coreTraces.push({
        type: "scatter",
        mode: "markers",
        name: "Bear pivot",
        x: bearX,
        y: bearY,
        xaxis: "x",
        yaxis: "y2",
        marker: { color: NEG_RED, size: 9, symbol: "triangle-down", line: { color: "#fff", width: 1 } },
        hovertemplate: "%{x|%H:%M}<br>Bear pivot %{y:,.1f}<extra></extra>",
      } as Data);
    }
    // Confirm: arrow markers at confirmed_at (Plotly has no true double-arrow; arrow-* reads distinct from pivot triangles).
    if (bullConfMutedX.length) {
      coreTraces.push({
        type: "scatter",
        mode: "markers",
        name: "Bull conf (pre-GEX)",
        x: bullConfMutedX,
        y: bullConfMutedY,
        xaxis: "x",
        yaxis: "y2",
        marker: {
          color: "rgba(34, 197, 94, 0.35)",
          size: 11,
          symbol: "arrow-up",
          line: { color: "rgba(255,255,255,0.35)", width: 1 },
        },
        hovertemplate: "%{x|%H:%M}<br>Bull conf (pre-GEX) %{y:,.1f}<extra></extra>",
      } as Data);
    }
    if (bearConfMutedX.length) {
      coreTraces.push({
        type: "scatter",
        mode: "markers",
        name: "Bear conf (pre-GEX)",
        x: bearConfMutedX,
        y: bearConfMutedY,
        xaxis: "x",
        yaxis: "y2",
        marker: {
          color: "rgba(239, 68, 68, 0.35)",
          size: 11,
          symbol: "arrow-down",
          line: { color: "rgba(255,255,255,0.35)", width: 1 },
        },
        hovertemplate: "%{x|%H:%M}<br>Bear conf (pre-GEX) %{y:,.1f}<extra></extra>",
      } as Data);
    }
    if (bullConfX.length) {
      coreTraces.push({
        type: "scatter",
        mode: "markers",
        name: "Bull conf",
        x: bullConfX,
        y: bullConfY,
        xaxis: "x",
        yaxis: "y2",
        marker: { color: POS_GREEN, size: 12, symbol: "arrow-up", line: { color: "#fff", width: 1 } },
        hovertemplate: "%{x|%H:%M}<br>Bull conf %{y:,.1f}<extra></extra>",
      } as Data);
    }
    if (bearConfX.length) {
      coreTraces.push({
        type: "scatter",
        mode: "markers",
        name: "Bear conf",
        x: bearConfX,
        y: bearConfY,
        xaxis: "x",
        yaxis: "y2",
        marker: { color: NEG_RED, size: 12, symbol: "arrow-down", line: { color: "#fff", width: 1 } },
        hovertemplate: "%{x|%H:%M}<br>Bear conf %{y:,.1f}<extra></extra>",
      } as Data);
    }

    // Spot axis from spot path only (pad ≥ ATM ± few strikes). Never full chain.
    const strikes = strikeRows.map((r) => r.strike).filter((s) => Number.isFinite(s));
    const step = medianStrikeStep(strikes);
    const spotRange = computeSpotYRange(spotY, snap.spot, step);
    spotRangeRef.current = spotRange;

    // Prefer preserved pan; else spot-based default.
    const y2Range: [number, number] | undefined =
      userY2RangeRef.current ?? spotRange ?? undefined;

    const { traces: oiTraces, oiMax } =
      overlaysOn && y2Range != null
        ? buildSessionOiBundle(strikeRows, y2Range[0], y2Range[1], showCePeOi, showDoi)
        : { traces: [] as Data[], oiMax: 0 };

    const volumeTraces: Data[] = [];
    if (hasVolume) {
      volumeTraces.push({
        type: "scatter",
        mode: "lines",
        name: "Volume",
        x: volX,
        y: volY,
        xaxis: "x2",
        yaxis: "y3",
        line: { color: POS_GREEN, width: 1.2, shape: "hv" },
        fill: "tozeroy",
        fillcolor: VOL_POS_FILL,
        hovertemplate: "%{x|%H:%M:%S}<br>Vol %{y:,.0f}<extra></extra>",
      } as Data);
    }

    coreTracesRef.current = coreTraces;
    volumeTracesRef.current = volumeTraces;

    const sessionXDomain = overlaysOn && oiMax > 0 ? SESSION_DOMAIN_WITH_OI : SESSION_DOMAIN_FULL;
    const applyY2Range = userY2RangeRef.current ?? spotRange;

    // ATM IV sits on a free right overlay just inside the session pane edge so Spot
    // (yaxis2) and the OI strip (xaxis4) keep their existing scales.
    const atmIvAxisPos = overlaysOn && oiMax > 0 ? 0.74 : 0.98;

    const { paperBg, plotBg, grid, axis, zeroline, fontFamily, annotation, hoverBg, hoverBorder, hoverText } =
      sessionChartTheme(isDark);

    const pocLevel = snap.session_poc?.poc;
    const hasPoc = pocLevel != null && Number.isFinite(pocLevel);
    const baseShapes: Partial<Shape>[] = [
      // GEX zero — the sign boundary, so it has to read at a glance. Plotly's
      // built-in zeroline takes a colour and a width but no dash, so this is a
      // shape instead and yaxis.zeroline stays off (otherwise a faint solid line
      // sits under the dotted one). Lives in baseShapes so the incremental
      // relayout path keeps it too.
      {
        type: "line",
        xref: "paper",
        yref: "y",
        x0: 0,
        x1: 1,
        y0: 0,
        y1: 0,
        line: { color: GEX_ZERO, width: 2, dash: "dot" },
        layer: "below",
      },
      ...(hasPoc
        ? ([
            {
              type: "line",
              xref: "paper",
              yref: "y2",
              x0: 0,
              x1: 1,
              y0: pocLevel,
              y1: pocLevel,
              line: { color: FUT_POC, width: 1.5, dash: "dot" },
            },
          ] as Partial<Shape>[])
        : []),
    ];
    const baseAnnotations: Partial<Annotations>[] = [
      {
        text: `${underlying} · +VE / −VE / Net Gamma`,
        xref: "paper",
        yref: "paper",
        x: 0.01,
        y: 1.06,
        showarrow: false,
        font: { size: 11, color: annotation, family: fontFamily },
      },
      ...(hasPoc
        ? [
            {
              text: "Fut POC",
              xref: "paper" as const,
              yref: "y2" as const,
              x: 1,
              y: pocLevel,
              showarrow: false,
              xanchor: "right" as const,
              yanchor: "bottom" as const,
              font: { size: 10, color: FUT_POC, family: fontFamily },
            },
          ]
        : []),
    ];

    levelsRef.current = levelDefs;
    baseShapesRef.current = baseShapes;
    baseAnnotationsRef.current = baseAnnotations;
    const levelExtras = buildLevelLayoutExtras(levelDefs, applyY2Range, hasVolume);

    const layout: Partial<Layout> = {
      autosize: true,
      height: hasVolume ? 480 : 340,
      margin: { l: 56, r: plotAtmIv ? 72 : 56, t: 36, b: 40 },
      paper_bgcolor: paperBg,
      plot_bgcolor: plotBg,
      font: { color: axis, size: 11, family: fontFamily },
      showlegend: false,
      barmode: "overlay",
      hovermode: "x unified",
      hoverdistance: 80,
      uirevision: "gex-session",
      dragmode: "zoom",
      hoverlabel: {
        bgcolor: hoverBg,
        bordercolor: hoverBorder,
        font: { size: 12, color: hoverText, family: fontFamily },
        align: "left",
      },
      xaxis: {
        title: hasVolume ? undefined : { text: "Time", font: { size: 11, color: axis } },
        type: "date",
        tickformat: "%H:%M",
        gridcolor: grid,
        zeroline: false,
        linecolor: grid,
        tickfont: { color: axis, size: 10 },
        matches: hasVolume ? "x2" : undefined,
        showticklabels: !hasVolume,
        anchor: hasVolume ? "y" : undefined,
        domain: sessionXDomain,
      },
      yaxis: {
        title: { text: "GEX (Cr)", font: { size: 11, color: axis } },
        gridcolor: grid,
        // Drawn as a dotted shape in baseShapes instead — Plotly zerolines
        // cannot be dashed, and a solid one underneath would show through.
        zeroline: false,
        linecolor: grid,
        tickfont: { color: axis, size: 10 },
        separatethousands: true,
        side: "left",
        domain: hasVolume ? [0.36, 1] : [0, 1],
      },
      yaxis2: {
        title: { text: "Spot / Strike", font: { size: 11, color: SPOT_BLUE } },
        overlaying: "y",
        side: "right",
        showgrid: false,
        zeroline: false,
        tickfont: { color: SPOT_BLUE, size: 10 },
        separatethousands: true,
        ...(applyY2Range ? { range: applyY2Range, autorange: false } : {}),
      },
      yaxis5: plotAtmIv
        ? {
            title: { text: "ATM IV %", font: { size: 10, color: ATM_IV_PINK } },
            overlaying: "y" as const,
            side: "right" as const,
            anchor: "free" as const,
            position: atmIvAxisPos,
            showgrid: false,
            zeroline: false,
            tickfont: { color: ATM_IV_PINK, size: 9 },
            ticksuffix: "%",
            layer: "above traces" as const,
            visible: true,
          }
        : {
            visible: false,
            overlaying: "y" as const,
            showgrid: false,
            showticklabels: false,
            zeroline: false,
          },
      xaxis4:
        overlaysOn && oiMax > 0
          ? {
              domain: OI_STRIP_DOMAIN,
              anchor: "y",
              range: [oiMax, 0] as [number, number],
              autorange: false as const,
              showgrid: false,
              zeroline: false,
              showticklabels: false,
              title: { text: "OI", font: { size: 10, color: axis } },
              tickfont: { color: axis, size: 9 },
              fixedrange: true,
              visible: true,
            }
          : {
              visible: false,
              domain: [0, 0.001] as [number, number],
              showgrid: false,
              showticklabels: false,
            },
      ...(hasVolume
        ? {
            xaxis2: {
              title: { text: "Time", font: { size: 11, color: axis } },
              type: "date" as const,
              tickformat: "%H:%M",
              gridcolor: grid,
              zeroline: false,
              linecolor: grid,
              tickfont: { color: axis, size: 10 },
              anchor: "y3",
              domain: sessionXDomain,
            },
            yaxis3: {
              title: { text: "Volume", font: { size: 11, color: axis } },
              gridcolor: grid,
              zeroline: true,
              zerolinecolor: zeroline,
              zerolinewidth: 1,
              linecolor: grid,
              tickfont: { color: axis, size: 10 },
              separatethousands: true,
              domain: [0, 0.28],
              anchor: "x2",
            },
          }
        : {}),
      shapes: [...baseShapes, ...levelExtras.shapes],
      annotations: [...baseAnnotations, ...levelExtras.annotations],
    };

    const config: Partial<Config> = {
      responsive: true,
      displaylogo: false,
      staticPlot: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"],
      displayModeBar: true,
    };

    layoutBaseRef.current = layout;
    plotConfigRef.current = config;

    const applyInfoFromEvent = (ev: PlotHoverEvent | PlotMouseEvent, pin: boolean) => {
      const points = ev.points ?? [];
      if (!points.length) return;
      // Prefer Spot / pivot / conf points on yaxis2 (strike neighborhood); unified hover includes them.
      let yVal: unknown;
      for (const pt of points) {
        const name = String((pt.data as { name?: string } | undefined)?.name ?? "");
        const ya = String((pt.data as { yaxis?: string } | undefined)?.yaxis ?? "y");
        if (
          ya === "y2" ||
          name === "Spot" ||
          name.includes("pivot") ||
          name.includes("conf")
        ) {
          yVal = pt.y;
          break;
        }
      }
      if (yVal == null) {
        // Fallback: any y2 point, else first point y (best-effort near cursor).
        for (const pt of points) {
          const ya = String((pt.data as { yaxis?: string } | undefined)?.yaxis ?? "y");
          if (ya === "y2") {
            yVal = pt.y;
            break;
          }
        }
      }
      if (yVal == null) yVal = points[0]?.y;
      const row = rowFromHoverY(infoRowsRef.current, yVal);
      if (!row) return;
      setInfoRow(row);
      if (pin) setInfoPinned(true);
    };

    const refreshOiForYRange = (yRange: [number, number], preserveY2: boolean) => {
      const opts = oiOptsRef.current;
      const { traces: nextOi, oiMax: nextMax } = buildSessionOiBundle(
        strikeRowsRef.current,
        yRange[0],
        yRange[1],
        opts.showCePeOi,
        opts.showDoi,
      );
      const overlays = opts.showCePeOi || opts.showDoi;
      const domain = overlays && nextMax > 0 ? SESSION_DOMAIN_WITH_OI : SESSION_DOMAIN_FULL;
      const nextLayout: Partial<Layout> = {
        ...layoutBaseRef.current,
        xaxis: {
          ...(layoutBaseRef.current.xaxis as object),
          domain,
        },
        yaxis2: {
          ...(layoutBaseRef.current.yaxis2 as object),
          ...(preserveY2
            ? { range: yRange, autorange: false }
            : spotRangeRef.current
              ? { range: spotRangeRef.current, autorange: false }
              : {}),
        },
        xaxis4:
          overlays && nextMax > 0
            ? {
                domain: OI_STRIP_DOMAIN,
                anchor: "y",
                range: [nextMax, 0] as [number, number],
                autorange: false as const,
                showgrid: false,
                zeroline: false,
                showticklabels: false,
                title: { text: "OI", font: { size: 10, color: axis } },
                tickfont: { color: axis, size: 9 },
                fixedrange: true,
                visible: true,
              }
            : {
                visible: false,
                domain: [0, 0.001] as [number, number],
                showgrid: false,
                showticklabels: false,
              },
      };
      if (layoutBaseRef.current.xaxis2) {
        nextLayout.xaxis2 = {
          ...(layoutBaseRef.current.xaxis2 as object),
          domain,
        };
      }

      // Panning changes which levels are on-screen, so re-clip rather than
      // carrying the spread-in shapes/annotations from the previous range.
      const effRange = preserveY2 ? yRange : (spotRangeRef.current ?? yRange);
      const nextLevels = buildLevelLayoutExtras(levelsRef.current, effRange, hasVolume);
      nextLayout.shapes = [...baseShapesRef.current, ...nextLevels.shapes];
      nextLayout.annotations = [...baseAnnotationsRef.current, ...nextLevels.annotations];

      layoutBaseRef.current = nextLayout;
      const data = [...coreTracesRef.current, ...nextOi, ...volumeTracesRef.current];
      void Plotly.react(el, data, nextLayout, plotConfigRef.current);
    };

    void Plotly.react(el, [...coreTraces, ...oiTraces, ...volumeTraces], layout, config).then(() => {
      el.removeAllListeners?.("plotly_relayout");
      el.removeAllListeners?.("plotly_hover");
      el.removeAllListeners?.("plotly_unhover");
      el.removeAllListeners?.("plotly_click");
      el.removeAllListeners?.("plotly_doubleclick");

      el.on("plotly_relayout", (raw) => {
        const parsed = parseY2RangeFromRelayout(raw as PlotRelayoutEvent);
        if (parsed === "autorange") {
          userY2RangeRef.current = null;
          const fallback = spotRangeRef.current;
          if (fallback) refreshOiForYRange(fallback, false);
          return;
        }
        if (!parsed) return;
        const normalized: [number, number] = [
          Math.min(parsed[0], parsed[1]),
          Math.max(parsed[0], parsed[1]),
        ];
        const prev = userY2RangeRef.current;
        if (
          prev &&
          Math.abs(prev[0] - normalized[0]) < 1e-6 &&
          Math.abs(prev[1] - normalized[1]) < 1e-6
        ) {
          return;
        }
        userY2RangeRef.current = normalized;
        refreshOiForYRange(normalized, true);
      });

      el.on("plotly_hover", (raw) => {
        if (infoPinnedRef.current) return;
        applyInfoFromEvent(raw as PlotHoverEvent, false);
      });
      el.on("plotly_unhover", () => {
        if (!infoPinnedRef.current) setInfoRow(null);
      });
      el.on("plotly_click", (raw) => {
        applyInfoFromEvent(raw as PlotMouseEvent, true);
      });
      el.on("plotly_doubleclick", () => {
        setInfoPinned(false);
        setInfoRow(null);
        userY2RangeRef.current = null;
        const fallback = spotRangeRef.current;
        if (fallback) refreshOiForYRange(fallback, false);
      });
    });
  }, [
    series,
    reversals,
    underlying,
    showCePeOi,
    showDoi,
    showAtmIv,
    levelDefs,
    snap.strikes,
    snap.spot,
    snap.session_poc,
    gexHistoryPartial,
    reversalGexGate,
    isDark,
  ]);

  const hasGexSeries = series.gexX.length >= 1;
  const hasAnySeries = hasGexSeries || series.spotX.length >= 2;

  const currPos =
    gexCrFromSnap(snap.pos_gex_cr, snap.pos_gex) ?? lastFinite(series.posY);
  const currNeg =
    gexCrFromSnap(snap.neg_gex_cr, snap.neg_gex) ?? lastFinite(series.negY);
  const currNet =
    gexCrFromSnap(snap.total_gex_cr, snap.total_gex) ?? lastFinite(series.netY);
  const currSpot =
    snap.spot != null && Number.isFinite(snap.spot)
      ? snap.spot
      : lastFinite(series.spotY);
  const currAtmIv =
    snap.atm_iv != null && Number.isFinite(snap.atm_iv)
      ? snap.atm_iv
      : lastFinite(series.atmIvY);

  const infoLines = infoRow
    ? buildSessionHoverLines(infoRow, { showCePeOi, showDoi })
    : null;

  const valueChips = [
    {
      label: "+VE GEX",
      value: formatGexCr(currPos),
      color: POS_GREEN,
      solid: true,
    },
    {
      label: "−VE GEX",
      value: formatGexCr(currNeg),
      color: NEG_RED,
      solid: true,
    },
    {
      label: "Net Gamma",
      value: formatGexCr(currNet),
      color: NET_AMBER,
      solid: true,
    },
    {
      label: "Spot",
      value: formatSpot(currSpot),
      color: SPOT_BLUE,
      solid: true,
    },
    ...(showAtmIv
      ? [
          {
            label: "ATM IV",
            value: formatAtmIv(currAtmIv),
            color: ATM_IV_PINK,
            solid: true as const,
          },
        ]
      : []),
    ...(snap.session_poc?.poc != null && Number.isFinite(snap.session_poc.poc)
      ? [
          {
            label: "Fut POC",
            value: formatSpot(snap.session_poc.poc),
            color: FUT_POC,
            solid: true as const,
          },
        ]
      : []),
    ...levelDefs
      .filter((l) => l.chip)
      .map((l) => ({
        label: l.label,
        value: formatSpot(l.value),
        color: l.color,
        solid: false as const,
      })),
    ...(snap.sign_mode || snap.strike_window != null
      ? [
          {
            label: "sign",
            value: String(snap.sign_mode ?? "—"),
            color: "#64748b",
            solid: true as const,
          },
          {
            label: "window",
            value: snap.strike_window != null ? `±${snap.strike_window}` : "—",
            color: "#64748b",
            solid: true as const,
          },
        ]
      : []),
  ];

  return (
    <div className={cn(SESSION_SHELL, className)}>
      {optionsSlot ? (
        <div className="report-controls absolute right-3 top-3 z-20">{optionsSlot}</div>
      ) : null}

      <div className="space-y-2 px-3 py-2 pr-28">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <div className="flex min-w-0 items-baseline gap-1.5">
            <span className="truncate font-semibold tracking-wide text-foreground">{underlying}</span>
            {expiryLabel ? (
              <span className="truncate text-[10px] text-muted-foreground">{expiryLabel}</span>
            ) : null}
          </div>
          <div className="flex items-center gap-1.5">
            <Label htmlFor="gex-session-reversal-tf" className="text-muted-foreground">
              Reversal TF
            </Label>
            <Select
              value={reversalTf}
              onValueChange={(v) => onReversalTfChange(v as ReversalTf)}
            >
              <SelectTrigger id="gex-session-reversal-tf" className="h-7 w-[72px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="1m">1m</SelectItem>
                <SelectItem value="5m">5m</SelectItem>
                <SelectItem value="15m">15m</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <label className="flex items-center gap-1.5 text-muted-foreground">
            <Checkbox
              checked={reversalGexGate}
              onCheckedChange={(v) => onReversalGexGateChange(v === true)}
            />
            Require GEX
          </label>
          {reversalGexGate ? (
            <div className="flex items-center gap-1.5">
              <Label htmlFor="gex-session-gex-mode" className="text-muted-foreground">
                GEX mode
              </Label>
              <Select
                value={reversalGexMode}
                onValueChange={(v) => onReversalGexModeChange(v as ReversalGexMode)}
              >
                <SelectTrigger id="gex-session-gex-mode" className="h-7 w-[100px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="live">Live</SelectItem>
                  <SelectItem value="research">Research</SelectItem>
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <label className="flex items-center gap-1.5 text-muted-foreground">
            <Checkbox
              checked={reversalOiGate}
              onCheckedChange={(v) => onReversalOiGateChange(v === true)}
            />
            Require OI
          </label>
          <label
            className="flex items-center gap-1.5 text-muted-foreground"
            title="CE/PE OI bars for ATM-nearest strikes in the Spot band; pan Spot/Strike to reveal more"
          >
            <Checkbox checked={showCePeOi} onCheckedChange={(v) => setShowCePeOi(v === true)} />
            CE/PE OI
          </label>
          <label
            className="flex items-center gap-1.5 text-muted-foreground"
            title="Striped ΔOI ↑ / hollow ΔOI ↓ on the strike-pinned OI strip"
          >
            <Checkbox checked={showDoi} onCheckedChange={(v) => setShowDoi(v === true)} />
            ΔOI
          </label>
          <label
            className="flex items-center gap-1.5 text-muted-foreground"
            title="ATM IV % on a dedicated right overlay axis (forward-filled after first sample)"
          >
            <Checkbox checked={showAtmIv} onCheckedChange={(v) => setShowAtmIv(v === true)} />
            ATM IV
          </label>
          <span className="text-[10px] text-muted-foreground">{REVERSAL_LOCK_HINT[reversalTf]}</span>
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className="font-medium text-foreground/80">Levels</span>
          {LEVEL_TOGGLES.map((t) => (
            <label key={t.key} className="flex items-center gap-1.5 text-muted-foreground" title={t.title}>
              <Checkbox
                checked={levelOpts[t.key]}
                onCheckedChange={(v) => setLevelOpts((prev) => ({ ...prev, [t.key]: v === true }))}
              />
              {t.label}
            </label>
          ))}
          <span className="text-[10px] text-muted-foreground">
            horizontal on the Spot axis · levels outside the visible band pin to the plot edge (▲
            above / ▼ below)
          </span>
        </div>

        {gexWaiting ? (
          <p className="text-[11px] text-sky-700">
            Waiting for GEX samples ({gexSamplesN}/{gexMinSamples}) — Live mode
            (price pivots may show as provisional until GEX confirms)
          </p>
        ) : null}

        {gexHistoryPartial && gexHistoryStartedLabel ? (
          <p className="text-[11px] text-amber-700">
            GEX history starts at {gexHistoryStartedLabel} — no earlier GEX samples on
            disk (gaps stay empty; not reverse-filled). Spot/volume still show the full
            day from candles. Reversal markers before {gexHistoryStartedLabel} are
            spot-only (muted); after that, Require GEX still applies.
          </p>
        ) : null}

        {gexRelaxed && !gexHistoryPartial ? (
          <p className="text-[11px] text-amber-700">
            GEX sparse — gate relaxed (showing ungated spot pivots until enough GEX samples exist).
          </p>
        ) : null}

        {reversals.length > 0 ? (
          <div className="flex flex-wrap gap-2 text-[11px]">
            {reversals.map((r) => {
              const muted =
                r.provisional === true ||
                r.partial_ungated === true ||
                (gexHistoryPartial && reversalGexGate && r.gex_confirm !== true);
              return (
              <span
                key={`${r.t}-${r.side}-${r.tf ?? ""}`}
                className={
                  muted
                    ? r.side === "bullish"
                      ? "rounded border border-emerald-500/25 bg-emerald-50/50 px-2 py-0.5 text-emerald-800/70"
                      : "rounded border border-rose-500/25 bg-rose-50/50 px-2 py-0.5 text-rose-800/70"
                    : r.side === "bullish"
                      ? "rounded border border-emerald-500/40 bg-emerald-50 px-2 py-0.5 text-emerald-800"
                      : "rounded border border-rose-500/40 bg-rose-50 px-2 py-0.5 text-rose-800"
                }
              >
                <span className="font-semibold tracking-wide">{underlying}</span>
                {" · "}
                {formatReversalChipTimes(r)}
                  {(() => {
                    const lag = reversalEmitLagMin(r);
                    if (lag == null) return null;
                    const seen = formatHhMmIst(r.emitted_at ?? null, r.emitted_ts_ms);
                    return (
                      <span
                        className="ml-1 rounded bg-slate-500/15 px-1 text-[10px] text-slate-700"
                        title={`Confirm bar is backdated; this signal only became visible at ${seen} IST, ${lag} min later.`}
                      >
                        seen {seen} (+{lag}m)
                      </span>
                    );
                  })()}
                  {" · "}
                  {r.label}
                  {muted ? (
                    <span className="ml-1 rounded bg-muted px-1 text-[10px] text-muted-foreground">
                      {r.provisional === true ? "provisional" : "pre-GEX"}
                    </span>
                  ) : null}
                  {formatBlockedBy(r.blocked_by, r.gate_expired) ? (
                    <span
                      className={
                        r.gate_expired
                          ? "ml-1 rounded bg-slate-500/15 px-1 text-[10px] text-slate-600"
                          : "ml-1 rounded bg-sky-500/15 px-1 text-[10px] text-sky-800"
                      }
                      title={
                        r.gate_expired
                          ? "Confirm window closed with the gate still unmet — this pivot cannot promote."
                          : "Hard gate not yet cleared for this pivot."
                      }
                    >
                      {formatBlockedBy(r.blocked_by, r.gate_expired)}
                    </span>
                  ) : null}
                {r.oi_align ? (
                  <span className="ml-1 rounded bg-amber-500/15 px-1 text-[10px] text-amber-800">
                    OI
                  </span>
                ) : null}
              </span>
              );
            })}
          </div>
        ) : gexWaiting ? null : (
          <p className="text-[11px] text-muted-foreground">
            {gexHistoryPartial && gexHistoryStartedLabel && reversalGexGate
              ? `No ${reversalTf} pivots yet — none before ${gexHistoryStartedLabel}, and none passed the GEX gate in the recorded window.`
              : gexRelaxed && !reversalOiGate
                ? `No spot pivots yet for ${reversalTf}.`
                : `No reversals for ${reversalTf}${
                    reversalGexGate || reversalOiGate
                      ? ` with ${[reversalGexGate ? "GEX" : null, reversalOiGate ? "OI" : null]
                          .filter(Boolean)
                          .join("+")} gate${reversalGexGate && reversalOiGate ? "s" : ""}`
                      : ""
                  }.`}
          </p>
        )}
      </div>

      {/* Legend + current values — OI Movers style, above the plot */}
      <div className="flex flex-wrap gap-x-5 gap-y-2 px-3 pb-2">
        {valueChips.map((c) => (
          <div key={c.label} className="flex items-baseline gap-1.5 font-mono text-sm">
            <span
              className="mb-0.5 inline-block w-4 border-t-[2.5px]"
              style={{
                borderColor: c.color,
                borderStyle: c.solid ? "solid" : "dotted",
              }}
              aria-hidden
            />
            <span className="text-muted-foreground">{c.label}</span>
            <span className="font-semibold tabular-nums" style={{ color: c.color }}>
              {c.value}
            </span>
          </div>
        ))}
      </div>

      <div className={SESSION_PLOT_INSET}>
        {!hasAnySeries ? (
          <p className="px-4 py-16 text-center text-sm text-muted-foreground">
            Day spot path loads from minute candles; GEX lines appear after the first recorded in-session sample.
          </p>
        ) : (
          <div
            ref={plotRef}
            style={{ width: "100%", minHeight: series.volX.length > 1 ? 480 : 340 }}
          />
        )}
        {infoLines ? (
          <div
            className={cn(
              "pointer-events-auto absolute right-2 top-2 z-10 max-w-[240px] rounded-md border px-2.5 py-2 text-[11px] leading-relaxed shadow-md",
              "border-border bg-card/95 text-foreground backdrop-blur-sm",
            )}
            role="status"
          >
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {infoPinned ? "Pinned" : "Strike"}
              </span>
              {infoPinned ? (
                <button
                  type="button"
                  className="text-[10px] text-muted-foreground underline-offset-2 hover:underline"
                  onClick={() => {
                    setInfoPinned(false);
                    setInfoRow(null);
                  }}
                >
                  Clear
                </button>
              ) : null}
            </div>
            <ul className="space-y-0.5 font-mono tabular-nums">
              {infoLines.map((line) => (
                <li key={line.text} style={line.color ? { color: line.color } : undefined}>
                  {line.text}
                </li>
              ))}
            </ul>
            {!infoPinned ? (
              <p className="mt-1.5 text-[10px] text-muted-foreground">
                Click to pin · dbl-click chart to clear
              </p>
            ) : null}
          </div>
        ) : null}
        {!hasGexSeries && hasAnySeries ? (
          <p className="absolute bottom-2 left-3 text-[10px] text-muted-foreground/70">
            Waiting for in-session GEX samples…
          </p>
        ) : null}
      </div>

      <p className="px-3 pb-3 text-[10px] text-muted-foreground">
        Green = +VE GEX · Red = −VE GEX · Amber = Net Gamma (forward-filled after the
        first recorded sample; earlier session / gaps stay empty). Blue = spot (right
        axis). Pink = ATM IV % (own axis; forward-filled after first sample only).
        Violet = Fut POC (session futures volume point of control; dotted on Spot axis).
        Dot on the Net Gamma line = sign flip (green = turned +VE, red = turned
        −VE); no dot means the regime held.
        {levelDefs.length
          ? " Levels draw at their current snapshot value (flat for the whole session, not an intraday trail) — orange = call wall, teal = put wall, indigo = gamma flip, slate = pin, stone = ±1σ, grey = prev day / week."
          : ""}
        Bottom = minute volume (front-month futures when cash-index volume is empty)
        {series.volX.length > 1 ? "" : " — waiting for futures candles"}.
        {showCePeOi || showDoi
          ? " Right Put/Call OI strip shows ATM-nearest strikes in the Spot band (solid=base, stripes=ΔOI↑, hollow=ΔOI↓). Pan Spot/Strike vertically to reveal other strikes' OI; hover for strike Net GEX / OI."
          : ""}{" "}
        Triangle = {reversalTf} pivot (live); arrow = price confirmation
        {reversalGexGate || reversalOiGate
          ? ` — gated confirm uses ${[reversalGexGate ? "GEX" : null, reversalOiGate ? "OI" : null]
              .filter(Boolean)
              .join("+")} (muted = provisional / pre-GEX until gates clear)`
          : " (spot-only)"}
        {reversalTf !== "1m"
          ? ` — ${reversalTf} pivots stamp on TF bar close (up to ${reversalTf} lag vs the eye); switch to 1m for faster signals`
          : ""}
        {gexHistoryPartial && reversalGexGate
          ? " — muted pre-recording markers are spot-only (no GEX gate)"
          : ""}
        . Changing dealer sign or strike window mid-session mixes the recorded series.
      </p>
    </div>
  );
}

export default GexSessionPlotly;
