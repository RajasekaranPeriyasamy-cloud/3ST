import { useEffect, useMemo, useRef, useState } from "react";
import Plotly from "plotly.js-dist-min";
import type { Config, Data, Layout, PlotHoverEvent, PlotMouseEvent, Shape } from "plotly.js";

import type { GammaSnapshot } from "@/lib/types";
import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";

/** Net GEX by regime: positive = dark green, negative = darker red. */
export const GEX_POS = "#166534";
export const GEX_NEG = "#991b1b";
/** Sensibull-style OI: Call = red, Put = green (brighter than GEX) */
export const CALL_OI = "#ef4444";
export const PUT_OI = "#22c55e";
export const IV_CURVE = "#c026d3";
const SPOT_LINE = "#0891b2";
const FLIP_LINE = "#db2777";
const DOM_LINE = "#0d9488";
const DENSITY_LINE = "#6366f1";
/** Pin strike — amber, matches ConcentrationBoard; dotted like Fut POC. */
const PIN_LINE = "#ca8a04";
const FUT_POC_LINE = "#7c3aed";
const NET_GEX_CHIP = "#ca8a04";

/** Tallest OI uses this fraction of the plot height above GEX zero. */
const OI_HEIGHT_FRAC = 0.8;

type StrikeRow = {
  strike: number;
  net_gex_cr: number;
  total_density: number;
  iv_pct: number | null;
  ce_oi: number;
  pe_oi: number;
  ce_oi_base: number;
  pe_oi_base: number;
  ce_doi: number | null;
  pe_doi: number | null;
};

type LevelMark = { x: number; label: string; stroke: string };

type OiSeg = {
  x: number;
  height: number;
  base: number;
  color: string;
  kind: "solid" | "stripe" | "hollow";
};

type PlotDiv = HTMLDivElement & {
  on: (event: string, callback: (event: unknown) => void) => void;
  removeAllListeners?: (event?: string) => void;
};

function avgIvPct(ce: number | null | undefined, pe: number | null | undefined): number | null {
  const vals = [ce, pe].filter((v): v is number => v != null && Number.isFinite(v));
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function medianStep(strikes: number[]): number {
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

/** Sensibull-style OI column segments: solid base, stripes = ↑, hollow = ↓. */
function oiSegments(
  x: number,
  color: string,
  baseOi: number,
  doi: number | null,
  showBase: boolean,
  showChange: boolean,
  currentOi: number,
  oiToY: (v: number) => number,
): OiSeg[] {
  const segs: OiSeg[] = [];
  const safeBase = Math.max(0, Number.isFinite(baseOi) ? baseOi : 0);
  const safeCurrent = Math.max(0, Number.isFinite(currentOi) ? currentOi : 0);

  if (showBase && showChange && doi != null && Number.isFinite(doi)) {
    if (doi >= 0) {
      if (safeBase > 0) {
        segs.push({ x, height: oiToY(safeBase), base: 0, color, kind: "solid" });
      }
      if (doi > 0) {
        segs.push({
          x,
          height: oiToY(doi),
          base: oiToY(safeBase),
          color,
          kind: "stripe",
        });
      }
    } else {
      if (safeCurrent > 0) {
        segs.push({ x, height: oiToY(safeCurrent), base: 0, color, kind: "solid" });
      }
      if (safeBase > safeCurrent) {
        segs.push({
          x,
          height: oiToY(safeBase - safeCurrent),
          base: oiToY(safeCurrent),
          color,
          kind: "hollow",
        });
      }
    }
  } else if (showBase) {
    const val = safeCurrent > 0 ? safeCurrent : safeBase;
    if (val > 0) {
      segs.push({ x, height: oiToY(val), base: 0, color, kind: "solid" });
    }
  } else if (showChange && doi != null && Number.isFinite(doi) && doi !== 0) {
    const mag = Math.abs(doi);
    if (doi > 0) {
      segs.push({ x, height: oiToY(mag), base: 0, color, kind: "stripe" });
    } else {
      segs.push({ x, height: oiToY(mag), base: 0, color, kind: "hollow" });
    }
  }

  return segs.filter((s) => s.height > 0);
}

function pushOiBarTrace(
  data: Data[],
  segs: OiSeg[],
  kind: OiSeg["kind"],
  name: string,
  width: number,
) {
  const filtered = segs.filter((s) => s.kind === kind);
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
              bgcolor: "rgba(255,255,255,0.15)",
            },
            line: { color, width: 1 },
          }
        : { color: "rgba(0,0,0,0)", line: { color, width: 1.5 } };

  data.push({
    type: "bar",
    name,
    showlegend: false,
    x: filtered.map((s) => s.x),
    y: filtered.map((s) => s.height),
    base: filtered.map((s) => s.base),
    width,
    marker,
    // Invisible to hover so they never steal the strike info box / GEX tooltip.
    hoverinfo: "skip",
    yaxis: "y",
  } as Data);
}

function fmtOi(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return Math.round(v).toLocaleString();
}

function formatGexCr(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1000) return `${value.toFixed(0)} Cr`;
  if (abs >= 10) return `${value.toFixed(1)} Cr`;
  if (abs >= 1) return `${value.toFixed(2)} Cr`;
  return `${value.toFixed(3)} Cr`;
}

function formatLevel(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function gexCrFromSnap(
  cr: number | null | undefined,
  raw: number | null | undefined,
): number | null {
  if (cr != null && Number.isFinite(cr)) return cr;
  if (raw != null && Number.isFinite(raw)) return raw / 1e7;
  return null;
}

type HoverLine = { text: string; color?: string };

function buildHoverLines(
  d: StrikeRow,
  opts: { showIvCurve: boolean; showCePeOi: boolean; showDoi: boolean },
): HoverLine[] {
  const lines: HoverLine[] = [
    { text: `Strike: ${d.strike.toLocaleString()}` },
    {
      text: `Net GEX: ${d.net_gex_cr.toFixed(2)} ₹Cr`,
      color: d.net_gex_cr >= 0 ? GEX_POS : GEX_NEG,
    },
    { text: `Γ×OI density: ${(d.total_density / 1e6).toFixed(2)}M` },
  ];
  if (opts.showIvCurve && d.iv_pct != null) lines.push({ text: `IV: ${d.iv_pct.toFixed(1)}%` });
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

/** Plotly hovertext: color spans for CE/PE/GEX lines (plain text when no color). */
function hoverLinesToHtml(lines: HoverLine[]): string {
  return lines
    .map((l) =>
      l.color ? `<span style="color:${l.color}">${l.text}</span>` : l.text,
    )
    .join("<br>");
}

function rowFromHoverX(rows: StrikeRow[], x: unknown): StrikeRow | null {
  const n = typeof x === "number" ? x : Number(x);
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

export interface GexStrikePlotlyProps {
  snap: GammaSnapshot;
  showDayLevels?: boolean;
  showWeekLevels?: boolean;
  showIvCurve?: boolean;
  showCePeOi?: boolean;
  showDoi?: boolean;
  height?: number;
  className?: string;
}

/**
 * Net GEX by strike · Γ×OI density (Plotly).
 * CE/PE OI columns: Put left / Call right of strike; solid base, striped ΔOI↑, hollow ΔOI↓.
 */
export function GexStrikePlotly({
  snap,
  showDayLevels = true,
  showWeekLevels = true,
  showIvCurve = false,
  showCePeOi = true,
  showDoi = true,
  height = 400,
  className,
}: GexStrikePlotlyProps) {
  const plotRef = useRef<HTMLDivElement>(null);
  const rowsRef = useRef<StrikeRow[]>([]);
  const infoPinnedRef = useRef(false);
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const [infoRow, setInfoRow] = useState<StrikeRow | null>(null);
  const [infoPinned, setInfoPinned] = useState(false);
  infoPinnedRef.current = infoPinned;

  const ref = snap.reference_levels;

  const marks = useMemo(() => {
    const raw: LevelMark[] = [];
    if (showDayLevels) {
      if (ref?.prev_day_high != null)
        raw.push({ x: Number(ref.prev_day_high), label: "PDH", stroke: "#16a34a" });
      if (ref?.prev_day_low != null)
        raw.push({ x: Number(ref.prev_day_low), label: "PDL", stroke: "#dc2626" });
      if (ref?.prev_day_close != null)
        raw.push({ x: Number(ref.prev_day_close), label: "PDC", stroke: "#475569" });
    }
    if (showWeekLevels) {
      if (ref?.prev_week_high != null)
        raw.push({ x: Number(ref.prev_week_high), label: "PWH", stroke: "#65a30d" });
      if (ref?.prev_week_low != null)
        raw.push({ x: Number(ref.prev_week_low), label: "PWL", stroke: "#e11d48" });
      if (ref?.prev_week_close != null)
        raw.push({ x: Number(ref.prev_week_close), label: "PWC", stroke: "#64748b" });
    }
    const byX = new Map<number, LevelMark>();
    for (const m of raw) {
      if (!Number.isFinite(m.x)) continue;
      const key = Math.round(m.x * 100) / 100;
      const prev = byX.get(key);
      if (!prev) byX.set(key, { ...m, x: key });
      else byX.set(key, { ...prev, label: `${prev.label}/${m.label}` });
    }
    return [...byX.values()];
  }, [ref, showDayLevels, showWeekLevels]);

  const chartModel = useMemo(() => {
    const data: StrikeRow[] = (snap.strikes ?? []).map((r) => {
      const ceBase = r.ce_oi_base ?? 0;
      const peBase = r.pe_oi_base ?? 0;
      return {
        strike: r.strike,
        net_gex_cr: r.net_gex / 1e7,
        total_density: r.total_density,
        iv_pct: avgIvPct(r.ce_iv, r.pe_iv),
        ce_oi: r.ce_oi || 0,
        pe_oi: r.pe_oi || 0,
        ce_oi_base: ceBase,
        pe_oi_base: peBase,
        ce_doi: r.ce_doi ?? null,
        pe_doi: r.pe_doi ?? null,
      };
    });

    let oiMax = 1;
    for (const d of data) {
      oiMax = Math.max(
        oiMax,
        d.ce_oi,
        d.pe_oi,
        d.ce_oi_base,
        d.pe_oi_base,
        d.ce_oi_base + Math.max(0, d.ce_doi ?? 0),
        d.pe_oi_base + Math.max(0, d.pe_doi ?? 0),
        Math.abs(d.ce_doi ?? 0),
        Math.abs(d.pe_doi ?? 0),
      );
    }
    oiMax = Math.max(oiMax * 1.08, 1);

    const overlaysOn = showCePeOi || showDoi;
    const strikes = data.map((d) => d.strike);
    const step = medianStep(strikes);
    // Put left / Call right of each strike tick — keep clear of the thin center GEX bar.
    const oiOffset = step * 0.32;
    const oiWidth = step * (showCePeOi && showDoi ? 0.24 : 0.2);
    const gexWidth = overlaysOn ? step * 0.1 : step * 0.55;

    const gexVals = data.map((d) => d.net_gex_cr);
    const gexMinRaw = gexVals.length ? Math.min(0, ...gexVals) : 0;
    const gexMaxRaw = gexVals.length ? Math.max(0, ...gexVals) : 0;
    const absMax = Math.max(Math.abs(gexMinRaw), gexMaxRaw, 0.01);
    let yMin = gexMinRaw < 0 ? gexMinRaw * 1.08 : gexMinRaw;
    let yMax = gexMaxRaw > 0 ? gexMaxRaw * 1.08 : gexMaxRaw;
    if (overlaysOn) {
      // Ensure headroom above GEX 0 so OI columns stay visible (Recharts pixel mapping).
      yMax = Math.max(yMax, absMax * 0.9);
    }
    if (yMax <= yMin) {
      yMax = yMin + absMax;
    }

    const oiYMax = Math.max(yMax, 1e-9) * OI_HEIGHT_FRAC;
    const oiToY = (v: number) => {
      if (!(oiMax > 0) || !Number.isFinite(v) || v <= 0) return 0;
      return (v / oiMax) * oiYMax;
    };

    const extraXs = [
      snap.spot,
      snap.flip_level,
      snap.concentration?.dominant_strike,
      snap.concentration?.pin_strike,
      snap.session_poc?.poc,
      ...marks.map((m) => m.x),
    ].filter((v): v is number => v != null && Number.isFinite(v));

    const strikeMin = strikes.length ? Math.min(...strikes) : snap.spot;
    const strikeMax = strikes.length ? Math.max(...strikes) : snap.spot;
    const xMin = Math.min(strikeMin, ...extraXs);
    const xMax = Math.max(strikeMax, ...extraXs);
    const pad = Math.max((xMax - xMin) * 0.02, 25);

    return {
      data,
      oiMax,
      overlaysOn,
      oiOffset,
      oiWidth,
      gexWidth,
      yMin,
      yMax,
      oiToY,
      xMin: Math.floor(xMin - pad),
      xMax: Math.ceil(xMax + pad),
    };
  }, [
    snap.strikes,
    snap.spot,
    snap.flip_level,
    snap.concentration?.dominant_strike,
    snap.concentration?.pin_strike,
    snap.session_poc?.poc,
    showCePeOi,
    showDoi,
    marks,
  ]);

  rowsRef.current = chartModel.data;

  // Purge only on unmount — never on live snap/theme updates (that killed hover after pan).
  useEffect(() => {
    const el = plotRef.current;
    return () => {
      if (el) Plotly.purge(el);
    };
  }, []);

  useEffect(() => {
    const el = plotRef.current as PlotDiv | null;
    if (!el) return;

    const { data: rows, overlaysOn, oiOffset, oiWidth, gexWidth, yMin, yMax, oiToY, xMin, xMax } =
      chartModel;

    if (!rows.length) {
      Plotly.purge(el);
      setInfoRow(null);
      setInfoPinned(false);
      return;
    }

    // Blue-black, not #0b0f14 (near pure black) -- matches the shell's --card.
    const paper = isDark ? "#1b2029" : "#ffffff";
    const plotBg = isDark ? "#171b22" : "#fafafa";
    const grid = isDark ? "rgba(148, 163, 184, 0.18)" : "rgba(148, 163, 184, 0.28)";
    const axis = isDark ? "rgba(226, 232, 240, 0.75)" : "rgba(51, 65, 85, 0.85)";
    const zeroLine = isDark ? "rgba(226, 232, 240, 0.45)" : "rgba(100, 116, 139, 0.55)";
    const hoverBg = isDark ? "rgba(15, 23, 42, 0.95)" : "rgba(255, 255, 255, 0.96)";
    const hoverBorder = isDark ? "rgba(148, 163, 184, 0.45)" : "rgba(100, 116, 139, 0.35)";

    const traces: Data[] = [];

    const hoverText = rows.map((d) =>
      hoverLinesToHtml(buildHoverLines(d, { showIvCurve, showCePeOi, showDoi })),
    );

    // Net GEX bars (primary) — sole hover source (overlay/curve traces use hoverinfo skip).
    traces.push({
      type: "bar",
      name: "Net GEX",
      x: rows.map((d) => d.strike),
      y: rows.map((d) => d.net_gex_cr),
      width: gexWidth,
      marker: {
        color: rows.map((d) => (d.net_gex_cr >= 0 ? GEX_POS : GEX_NEG)),
        line: { width: 0 },
      },
      hovertext: hoverText,
      hovertemplate: "%{hovertext}<extra></extra>",
      hoverinfo: "text",
      textposition: "none",
      yaxis: "y",
    } as Data);

    // CE/PE OI overlays — Put left, Call right of strike
    if (overlaysOn) {
      const putSegs: OiSeg[] = [];
      const callSegs: OiSeg[] = [];
      for (const d of rows) {
        putSegs.push(
          ...oiSegments(
            d.strike - oiOffset,
            PUT_OI,
            d.pe_oi_base,
            d.pe_doi,
            showCePeOi,
            showDoi,
            d.pe_oi,
            oiToY,
          ),
        );
        callSegs.push(
          ...oiSegments(
            d.strike + oiOffset,
            CALL_OI,
            d.ce_oi_base,
            d.ce_doi,
            showCePeOi,
            showDoi,
            d.ce_oi,
            oiToY,
          ),
        );
      }
      pushOiBarTrace(traces, putSegs, "solid", "Put OI", oiWidth);
      pushOiBarTrace(traces, putSegs, "stripe", "Put ΔOI↑", oiWidth);
      pushOiBarTrace(traces, putSegs, "hollow", "Put ΔOI↓", oiWidth);
      pushOiBarTrace(traces, callSegs, "solid", "Call OI", oiWidth);
      pushOiBarTrace(traces, callSegs, "stripe", "Call ΔOI↑", oiWidth);
      pushOiBarTrace(traces, callSegs, "hollow", "Call ΔOI↓", oiWidth);
    }

    // Overlay curves — skip hover so pan/zoom never steals the strike info box.
    traces.push({
      type: "scatter",
      mode: "lines",
      name: "Γ×OI density",
      x: rows.map((d) => d.strike),
      y: rows.map((d) => d.total_density),
      yaxis: "y2",
      line: { color: DENSITY_LINE, width: 2, shape: "spline" },
      hoverinfo: "skip",
    } as Data);

    if (showIvCurve) {
      traces.push({
        type: "scatter",
        mode: "lines",
        name: "IV Curve",
        x: rows.map((d) => d.strike),
        y: rows.map((d) => d.iv_pct),
        yaxis: "y4",
        line: { color: IV_CURVE, width: 2 },
        connectgaps: true,
        hoverinfo: "skip",
      } as Data);
    }

    const shapes: Partial<Shape>[] = [
      {
        type: "line",
        xref: "paper",
        yref: "y",
        x0: 0,
        x1: 1,
        y0: 0,
        y1: 0,
        line: { color: zeroLine, width: 1 },
      },
    ];
    const annotations: Layout["annotations"] = [];

    const addVline = (
      x: number | null | undefined,
      label: string,
      color: string,
      dash?: "solid" | "dot" | "dash" | "longdash" | "dashdot" | "longdashdot",
    ) => {
      if (x == null || !Number.isFinite(x)) return;
      shapes.push({
        type: "line",
        xref: "x",
        yref: "paper",
        x0: x,
        x1: x,
        y0: 0,
        y1: 1,
        line: { color, width: 2, ...(dash && dash !== "solid" ? { dash } : {}) },
      });
      annotations.push({
        x,
        y: 1,
        xref: "x",
        yref: "paper",
        text: label,
        showarrow: false,
        yanchor: "bottom",
        yshift: 2,
        font: { size: 10, color, family: "ui-sans-serif, system-ui, sans-serif" },
      });
    };

    addVline(snap.spot, "Spot", SPOT_LINE);
    addVline(snap.flip_level, "Flip", FLIP_LINE, "dash");
    addVline(snap.concentration?.dominant_strike, "Dom", DOM_LINE, "dashdot");
    // PIN — dotted to match Fut POC (not dashed).
    addVline(snap.concentration?.pin_strike, "PIN", PIN_LINE, "dot");
    // Session futures volume POC (violet dotted) — readable on light/dark.
    addVline(snap.session_poc?.poc, "Fut POC", FUT_POC_LINE, "dot");
    for (const m of marks) {
      addVline(m.x, m.label, m.stroke, "dash");
    }

    const needIvAxis = showIvCurve;

    const layout: Partial<Layout> = {
      autosize: true,
      height,
      margin: { l: 28, r: needIvAxis ? 28 : 16, t: 28, b: 36 },
      paper_bgcolor: paper,
      plot_bgcolor: plotBg,
      font: { color: axis, size: 11, family: "ui-monospace, SFMono-Regular, Menlo, monospace" },
      showlegend: false,
      barmode: "overlay",
      bargap: 0.15,
      // x-mode: hover by strike column (works after pan; thin bars break "closest").
      hovermode: "x",
      hoverdistance: 80,
      // Keep pan/zoom + hovermode across Plotly.react (live snap polls).
      uirevision: "gex-strike",
      // Zoom default; pan stays on modebar. Hover works in both modes.
      dragmode: "zoom",
      hoverlabel: {
        bgcolor: hoverBg,
        bordercolor: hoverBorder,
        font: { size: 12, color: axis, family: "ui-sans-serif, system-ui, sans-serif" },
        align: "left",
      },
      xaxis: {
        title: { text: "", font: { size: 11, color: axis } },
        range: [xMin, xMax],
        gridcolor: grid,
        zeroline: false,
        linecolor: grid,
        tickfont: { color: axis, size: 10 },
        separatethousands: true,
      },
      yaxis: {
        title: { text: "", font: { size: 11, color: axis } },
        range: [yMin, yMax],
        gridcolor: grid,
        zeroline: false,
        showticklabels: false,
        showline: false,
        ticks: "",
        tickfont: { color: axis, size: 10 },
      },
      yaxis2: {
        overlaying: "y",
        side: "right",
        showgrid: false,
        zeroline: false,
        showticklabels: false,
        showline: false,
        ticks: "",
        visible: !needIvAxis,
      },
      ...(needIvAxis
        ? {
            yaxis4: {
              overlaying: "y" as const,
              side: "right" as const,
              showgrid: false,
              zeroline: false,
              showticklabels: false,
              showline: false,
              ticks: "" as const,
            },
          }
        : {}),
      shapes: shapes as Shape[],
      annotations,
    };

    const config: Partial<Config> = {
      responsive: true,
      displaylogo: false,
      staticPlot: false,
      scrollZoom: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"],
      displayModeBar: "hover",
    };

    const applyInfoFromEvent = (ev: PlotHoverEvent | PlotMouseEvent, pin: boolean) => {
      const pt = ev.points?.[0];
      if (!pt) return;
      const row = rowFromHoverX(rowsRef.current, pt.x);
      if (!row) return;
      setInfoRow(row);
      if (pin) setInfoPinned(true);
    };

    void Plotly.react(el, traces, layout, config).then(() => {
      el.removeAllListeners?.("plotly_hover");
      el.removeAllListeners?.("plotly_unhover");
      el.removeAllListeners?.("plotly_click");
      el.removeAllListeners?.("plotly_doubleclick");

      el.on("plotly_hover", (raw) => {
        if (infoPinnedRef.current) return;
        applyInfoFromEvent(raw as PlotHoverEvent, false);
      });
      el.on("plotly_unhover", () => {
        if (!infoPinnedRef.current) setInfoRow(null);
      });
      // Click pins the HTML info card (reliable after pan when native hover is flaky).
      el.on("plotly_click", (raw) => {
        applyInfoFromEvent(raw as PlotMouseEvent, true);
      });
      el.on("plotly_doubleclick", () => {
        setInfoPinned(false);
        setInfoRow(null);
      });
    });
  }, [
    chartModel,
    marks,
    snap.spot,
    snap.flip_level,
    snap.concentration?.dominant_strike,
    snap.concentration?.pin_strike,
    snap.session_poc?.poc,
    showIvCurve,
    showCePeOi,
    showDoi,
    height,
    isDark,
  ]);

  const infoLines = infoRow
    ? buildHoverLines(infoRow, { showIvCurve, showCePeOi, showDoi })
    : null;

  const netGex = gexCrFromSnap(snap.total_gex_cr, snap.total_gex);

  // Session-chart style legend chips (theme-aware labels).
  const valueChips: Array<{
    label: string;
    value: string;
    color: string;
    solid: boolean;
  }> = [
    { label: "Net GEX", value: formatGexCr(netGex), color: NET_GEX_CHIP, solid: true },
    { label: "Spot", value: formatLevel(snap.spot), color: SPOT_LINE, solid: true },
    ...(snap.concentration?.pin_strike != null && Number.isFinite(snap.concentration.pin_strike)
      ? [
          {
            label: "PIN",
            value: formatLevel(snap.concentration.pin_strike),
            color: PIN_LINE,
            solid: false as const,
          },
        ]
      : []),
    ...(snap.session_poc?.poc != null && Number.isFinite(snap.session_poc.poc)
      ? [
          {
            label: "Fut POC",
            value: formatLevel(snap.session_poc.poc),
            color: FUT_POC_LINE,
            solid: false as const,
          },
        ]
      : []),
  ];

  if (!snap.strikes?.length) {
    return (
      <p className={cn("py-12 text-center text-sm text-muted-foreground", className)}>
        No strike GEX rows
      </p>
    );
  }

  // Plot host is the first DOM child so React never reuses a Plotly-managed node for the
  // value strip (inserting chips before the host orphaned a second stacked chart).
  // CSS order keeps the strip visually above the plot.
  return (
    <div className={cn("relative flex w-full flex-col rounded-md", className)}>
      <div className="relative order-2">
        <div ref={plotRef} style={{ width: "100%", minHeight: height }} />
        {infoLines ? (
          <div
            className={cn(
              "pointer-events-auto absolute right-2 top-2 z-10 max-w-[240px] rounded-md border px-2.5 py-2 text-[11px] leading-relaxed shadow-md",
              "border-border/80 bg-background/95 text-foreground backdrop-blur-sm",
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
      </div>
      <div className="order-1 flex flex-wrap gap-x-5 gap-y-2 px-1 pb-2 pt-0.5">
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
    </div>
  );
}

export default GexStrikePlotly;
