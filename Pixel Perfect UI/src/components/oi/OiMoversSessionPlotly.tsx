import { useEffect, useMemo, useRef } from "react";
import Plotly from "plotly.js-dist-min";
import type { Config, Data, Layout } from "plotly.js";

import type { OiMoversHistoryPoint, OiMoversSnapshot } from "@/lib/types";
import {
  SESSION_CHART,
  SESSION_PLOT_INSET,
  SESSION_SHELL,
} from "@/components/charts/sessionChartTheme";
import { cn } from "@/lib/utils";

const CE_RED = "#ef4444";
const PE_GREEN = "#22c55e";
/** Amber PCR — readable on light plot (was ivory on dark). */
const PCR_AMBER = "#b45309";
const SPOT_BLUE = "#2563eb";
/** Session futures POC — violet on white legend + light plot. */
const FUT_POC = "#7c3aed";

function toMs(t: string | null | undefined, tsMs?: number | null): number | null {
  if (tsMs != null && Number.isFinite(tsMs)) return Number(tsMs);
  if (!t) return null;
  const ms = new Date(t).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function forwardFill(
  gridMs: number[],
  samples: { ms: number; value: number }[],
): (number | null)[] {
  if (!gridMs.length || !samples.length) return gridMs.map(() => null);
  let si = 0;
  let last: number | null = null;
  const firstMs = samples[0].ms;
  const filled: (number | null)[] = [];
  for (const ms of gridMs) {
    while (si < samples.length && samples[si].ms <= ms) {
      last = samples[si].value;
      si += 1;
    }
    filled.push(ms < firstMs ? null : last);
  }
  return filled;
}

function trimLeadingNulls(
  x: Date[],
  y: (number | null)[],
): { x: Date[]; y: number[] } {
  let start = 0;
  while (start < y.length && (y[start] == null || !Number.isFinite(y[start] as number))) {
    start += 1;
  }
  if (start >= y.length) return { x: [], y: [] };
  const outX: Date[] = [];
  const outY: number[] = [];
  for (let i = start; i < y.length; i++) {
    const v = y[i];
    if (v == null || !Number.isFinite(v)) continue;
    outX.push(x[i]);
    outY.push(v);
  }
  return { x: outX, y: outY };
}

function collectSamples(
  history: OiMoversHistoryPoint[] | undefined,
  chart: OiMoversHistoryPoint[],
  key: keyof OiMoversHistoryPoint,
): { ms: number; value: number }[] {
  const byMs = new Map<number, number>();
  for (const source of [chart, history ?? []]) {
    for (const h of source) {
      const ms = toMs(h.t, h.ts_ms);
      const raw = h[key];
      if (ms == null || raw == null || !Number.isFinite(Number(raw))) continue;
      byMs.set(ms, Number(raw));
    }
  }
  return [...byMs.entries()]
    .map(([ms, value]) => ({ ms, value }))
    .sort((a, b) => a.ms - b.ms);
}

/** Wide PCR axis so small ratio moves don't look like vertical spikes. */
function pcrAxisRange(values: number[]): [number, number] | undefined {
  if (!values.length) return undefined;
  let lo = values[0];
  let hi = values[0];
  for (const v of values) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const mid = (lo + hi) / 2;
  // At least ±0.45 around mid (span ≥ 0.9); pad observed range ~3×.
  const half = Math.max((hi - lo) * 1.5, 0.45);
  return [Math.max(0, mid - half), mid + half];
}

function formatOi(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString();
}

function formatPcr(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(3);
}

function formatSpot(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

/** Pct change of current vs Open/PD base — e.g. ``(+22.7%)``. */
function formatPctFromBase(
  curr: number | null | undefined,
  base: number | null | undefined,
): string {
  if (curr == null || base == null || !Number.isFinite(curr) || !Number.isFinite(base) || base === 0) {
    return "";
  }
  const pct = ((curr - base) / base) * 100;
  const sign = pct > 0 ? "+" : "";
  return `(${sign}${pct.toFixed(1)}%)`;
}

function lastOf(arr: number[]): number | null {
  return arr.length ? arr[arr.length - 1] : null;
}

export interface OiMoversSessionPlotlyProps {
  snap: OiMoversSnapshot;
  className?: string;
}

/**
 * Session OI path (Plotly): current CE/PE (solid), Open/PD baselines (dotted),
 * PCR (amber), spot (blue). Chart-only — does not alter change-board triggers.
 * Light Straddle-adjacent Plotly theme (stays light in app dark mode).
 */
export function OiMoversSessionPlotly({ snap, className }: OiMoversSessionPlotlyProps) {
  const plotRef = useRef<HTMLDivElement>(null);
  const chartRaw = (snap.chart_series?.length ? snap.chart_series : snap.history) ?? [];

  const series = useMemo(() => {
    const gridMs: number[] = [];
    const spotX: Date[] = [];
    const spotY: number[] = [];

    for (const h of chartRaw) {
      const ms = toMs(h.t, h.ts_ms);
      if (ms == null) continue;
      gridMs.push(ms);
      if (h.spot != null && Number.isFinite(h.spot)) {
        spotX.push(new Date(ms));
        spotY.push(Number(h.spot));
      }
    }

    // When chart_series/history is missing (stale API), use Fut POC path only for the time grid
    // (closes are futures, not index spot — do not plot them as Spot).
    if (!gridMs.length && snap.session_poc?.path?.length) {
      for (const p of snap.session_poc.path) {
        const ms = toMs(p.t, p.ts_ms);
        if (ms == null) continue;
        gridMs.push(ms);
      }
    }

    let ceSamples = collectSamples(snap.history, chartRaw, "ce_oi");
    let peSamples = collectSamples(snap.history, chartRaw, "pe_oi");
    let pcrSamples = collectSamples(snap.history, chartRaw, "pcr");
    const ceBaseSamples = collectSamples(snap.history, chartRaw, "ce_base_oi");
    const peBaseSamples = collectSamples(snap.history, chartRaw, "pe_base_oi");

    // Prefer dedicated side totals; fall back to PCR payload totals when the API
    // process is still on a pre-history snapshot shape.
    const snapCe =
      snap.ce_oi != null && Number.isFinite(snap.ce_oi)
        ? Number(snap.ce_oi)
        : snap.pcr?.call_oi_total != null && Number.isFinite(snap.pcr.call_oi_total)
          ? Number(snap.pcr.call_oi_total)
          : null;
    const snapPe =
      snap.pe_oi != null && Number.isFinite(snap.pe_oi)
        ? Number(snap.pe_oi)
        : snap.pcr?.put_oi_total != null && Number.isFinite(snap.pcr.put_oi_total)
          ? Number(snap.pcr.put_oi_total)
          : null;

    const lastMs = gridMs.length ? gridMs[gridMs.length - 1] : Date.now();
    if (!ceSamples.length && snapCe != null) {
      ceSamples = [{ ms: lastMs, value: snapCe }];
    }
    if (!peSamples.length && snapPe != null) {
      peSamples = [{ ms: lastMs, value: snapPe }];
    }
    if (!pcrSamples.length && snap.pcr?.chain_oi != null && Number.isFinite(snap.pcr.chain_oi)) {
      pcrSamples = [{ ms: lastMs, value: Number(snap.pcr.chain_oi) }];
    }

    const msSet = new Set(gridMs);
    for (const s of [...ceSamples, ...peSamples, ...pcrSamples]) {
      if (!msSet.has(s.ms)) {
        msSet.add(s.ms);
        gridMs.push(s.ms);
      }
    }
    gridMs.sort((a, b) => a - b);

    const xAll = gridMs.map((ms) => new Date(ms));
    const ceTrim = trimLeadingNulls(xAll, forwardFill(gridMs, ceSamples));
    const peTrim = trimLeadingNulls(xAll, forwardFill(gridMs, peSamples));
    const pcrTrim = trimLeadingNulls(xAll, forwardFill(gridMs, pcrSamples));

    // Prefer API-locked Open/PD totals. History used to re-sum the rolling ATM
    // window each poll, so the last sample could drift mid-session.
    const ceBase =
      snap.ce_base_oi != null && Number.isFinite(snap.ce_base_oi)
        ? Number(snap.ce_base_oi)
        : ceBaseSamples.length > 0
          ? ceBaseSamples[0].value
          : null;
    const peBase =
      snap.pe_base_oi != null && Number.isFinite(snap.pe_base_oi)
        ? Number(snap.pe_base_oi)
        : peBaseSamples.length > 0
          ? peBaseSamples[0].value
          : null;

    const firstMs = gridMs[0];
    const endMs = gridMs[gridMs.length - 1];
    const baseX =
      firstMs != null && endMs != null ? [new Date(firstMs), new Date(endMs)] : [];

    const currCe = snapCe ?? lastOf(ceTrim.y);
    const currPe = snapPe ?? lastOf(peTrim.y);
    const currPcr =
      snap.pcr?.chain_oi != null && Number.isFinite(snap.pcr.chain_oi)
        ? Number(snap.pcr.chain_oi)
        : lastOf(pcrTrim.y);
    const currSpot =
      snap.spot != null && Number.isFinite(snap.spot) ? Number(snap.spot) : lastOf(spotY);

    return {
      ceX: ceTrim.x,
      ceY: ceTrim.y,
      peX: peTrim.x,
      peY: peTrim.y,
      pcrX: pcrTrim.x,
      pcrY: pcrTrim.y,
      pcrRange: pcrAxisRange(pcrTrim.y),
      spotX,
      spotY,
      baseX,
      ceBase,
      peBase,
      currCe,
      currPe,
      currPcr,
      currSpot,
      hasOi: ceTrim.y.length + peTrim.y.length >= 1,
      hasGrid: gridMs.length >= 1 || spotX.length >= 2,
    };
  }, [
    chartRaw,
    snap.history,
    snap.ce_base_oi,
    snap.pe_base_oi,
    snap.ce_oi,
    snap.pe_oi,
    snap.pcr,
    snap.spot,
    snap.session_poc?.path,
  ]);

  const underlying = String(snap.underlying ?? "").trim() || "—";
  const expiryLabel = snap.expiry ? String(snap.expiry).trim() : "";
  const baseLabel =
    snap.base_source === "open" ? "Open" : snap.base_source === "prev_close" ? "PD" : "Open/PD";

  useEffect(() => {
    const el = plotRef.current;
    return () => {
      if (el) Plotly.purge(el);
    };
  }, []);

  useEffect(() => {
    const el = plotRef.current;
    if (!el) return;

    const {
      ceX,
      ceY,
      peX,
      peY,
      pcrX,
      pcrY,
      pcrRange,
      spotX,
      spotY,
      baseX,
      ceBase,
      peBase,
      hasGrid,
    } = series;
    if (!hasGrid) {
      Plotly.purge(el);
      return;
    }

    const data: Data[] = [];

    if (ceX.length > 0) {
      data.push({
        type: "scatter",
        mode: "lines",
        name: "CE OI",
        x: ceX,
        y: ceY,
        yaxis: "y",
        connectgaps: false,
        line: { color: CE_RED, width: 2.4, shape: "hv" },
        hovertemplate: "%{x|%H:%M:%S}<br>CE OI %{y:,.0f}<extra></extra>",
      } as Data);
    }
    if (peX.length > 0) {
      data.push({
        type: "scatter",
        mode: "lines",
        name: "PE OI",
        x: peX,
        y: peY,
        yaxis: "y",
        connectgaps: false,
        line: { color: PE_GREEN, width: 2.4, shape: "hv" },
        hovertemplate: "%{x|%H:%M:%S}<br>PE OI %{y:,.0f}<extra></extra>",
      } as Data);
    }

    if (baseX.length === 2 && ceBase != null) {
      data.push({
        type: "scatter",
        mode: "lines",
        name: `CE ${baseLabel}`,
        x: baseX,
        y: [ceBase, ceBase],
        yaxis: "y",
        line: { color: CE_RED, width: 1.4, dash: "dot" },
        hovertemplate: `CE ${baseLabel} %{y:,.0f}<extra></extra>`,
      } as Data);
    }
    if (baseX.length === 2 && peBase != null) {
      data.push({
        type: "scatter",
        mode: "lines",
        name: `PE ${baseLabel}`,
        x: baseX,
        y: [peBase, peBase],
        yaxis: "y",
        line: { color: PE_GREEN, width: 1.4, dash: "dot" },
        hovertemplate: `PE ${baseLabel} %{y:,.0f}<extra></extra>`,
      } as Data);
    }

    if (pcrX.length > 0) {
      data.push({
        type: "scatter",
        mode: "lines",
        name: "PCR",
        x: pcrX,
        y: pcrY,
        yaxis: "y3",
        connectgaps: false,
        line: { color: PCR_AMBER, width: 2.4, shape: "hv" },
        hovertemplate: "%{x|%H:%M:%S}<br>PCR %{y:.3f}<extra></extra>",
      } as Data);
    }

    if (spotX.length > 0) {
      data.push({
        type: "scatter",
        mode: "lines",
        name: "Spot",
        x: spotX,
        y: spotY,
        yaxis: "y2",
        line: { color: SPOT_BLUE, width: 1.5 },
        hovertemplate: "%{x|%H:%M:%S}<br>Spot %{y:,.1f}<extra></extra>",
      } as Data);
    }

    const futPoc =
      snap.session_poc?.poc != null && Number.isFinite(snap.session_poc.poc)
        ? Number(snap.session_poc.poc)
        : null;

    const { paperBg, plotBg, grid, axis, fontFamily, hoverBg, hoverBorder, hoverText } =
      SESSION_CHART;

    const layout: Partial<Layout> = {
      autosize: true,
      height: 380,
      margin: { l: 64, r: 88, t: 16, b: 44 },
      paper_bgcolor: paperBg,
      plot_bgcolor: plotBg,
      font: { color: axis, size: 11, family: fontFamily },
      // Legend lives in the HTML header so it never overlaps the plot title.
      showlegend: false,
      hovermode: "x unified",
      hoverdistance: 80,
      uirevision: "oi-movers-session",
      dragmode: "zoom",
      hoverlabel: {
        bgcolor: hoverBg,
        bordercolor: hoverBorder,
        font: { size: 12, color: hoverText, family: fontFamily },
        align: "left",
      },
      xaxis: {
        title: { text: "Time", font: { size: 11, color: axis } },
        type: "date",
        tickformat: "%H:%M",
        gridcolor: grid,
        zeroline: false,
        linecolor: grid,
        tickfont: { color: axis, size: 10 },
        domain: [0, 0.9],
      },
      yaxis: {
        title: { text: "OI", font: { size: 11, color: axis } },
        gridcolor: grid,
        zeroline: false,
        linecolor: grid,
        tickfont: { color: axis, size: 10 },
        separatethousands: true,
        side: "left",
      },
      yaxis2: {
        title: { text: "Spot", font: { size: 11, color: SPOT_BLUE } },
        overlaying: "y",
        side: "right",
        showgrid: false,
        zeroline: false,
        tickfont: { color: SPOT_BLUE, size: 10 },
        separatethousands: true,
      },
      yaxis3: {
        title: { text: "PCR", font: { size: 11, color: PCR_AMBER } },
        overlaying: "y",
        side: "right",
        position: 0.95,
        showgrid: false,
        zeroline: false,
        tickfont: { color: PCR_AMBER, size: 10 },
        anchor: "free",
        ...(pcrRange ? { range: pcrRange, autorange: false } : {}),
      },
      shapes:
        futPoc != null
          ? [
              {
                type: "line" as const,
                xref: "paper" as const,
                yref: "y2" as const,
                x0: 0,
                x1: 1,
                y0: futPoc,
                y1: futPoc,
                line: { color: FUT_POC, width: 1.5, dash: "dot" as const },
              },
            ]
          : undefined,
      annotations:
        futPoc != null
          ? [
              {
                text: "Fut POC",
                xref: "paper" as const,
                yref: "y2" as const,
                x: 1,
                y: futPoc,
                showarrow: false,
                xanchor: "right" as const,
                yanchor: "bottom" as const,
                font: { size: 10, color: FUT_POC, family: fontFamily },
              },
            ]
          : undefined,
    };

    const config: Partial<Config> = {
      responsive: true,
      displaylogo: false,
      staticPlot: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"],
      displayModeBar: true,
    };

    void Plotly.react(el, data, layout, config);
  }, [series, baseLabel, snap.session_poc?.poc]);

  const cePct = formatPctFromBase(series.currCe, series.ceBase);
  const pePct = formatPctFromBase(series.currPe, series.peBase);
  const valueChips = [
    {
      label: "CE OI",
      value: formatOi(series.currCe),
      suffix: cePct,
      color: CE_RED,
      solid: true,
    },
    {
      label: "PE OI",
      value: formatOi(series.currPe),
      suffix: pePct,
      color: PE_GREEN,
      solid: true,
    },
    { label: `CE ${baseLabel}`, value: formatOi(series.ceBase), color: CE_RED, solid: false },
    { label: `PE ${baseLabel}`, value: formatOi(series.peBase), color: PE_GREEN, solid: false },
    { label: "PCR", value: formatPcr(series.currPcr), color: PCR_AMBER, solid: true },
    { label: "Spot", value: formatSpot(series.currSpot), color: SPOT_BLUE, solid: true },
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
  ];

  return (
    <div className={cn(SESSION_SHELL, className)}>
      <div className="space-y-2 px-3 py-2">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-base font-semibold tracking-wide text-slate-800">{underlying}</span>
          {expiryLabel ? (
            <span className="text-xs text-slate-500">{expiryLabel}</span>
          ) : null}
          <span className="text-xs text-slate-500">
            CE/PE OI vs {baseLabel}
          </span>
        </div>

        {/* Legend + current values — above the plot so nothing merges with chart chrome */}
        <div className="flex flex-wrap gap-x-5 gap-y-2">
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
              <span className="text-slate-500">{c.label}</span>
              <span className="font-semibold tabular-nums" style={{ color: c.color }}>
                {c.value}
                {"suffix" in c && c.suffix ? (
                  <span className="ml-1 font-medium opacity-90">{c.suffix}</span>
                ) : null}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className={SESSION_PLOT_INSET}>
        {!series.hasGrid ? (
          <p className="px-4 py-16 text-center text-sm text-slate-500">
            Session OI path builds as the desk refreshes; spot loads from minute candles.
          </p>
        ) : (
          <div ref={plotRef} style={{ width: "100%", minHeight: 380 }} />
        )}
        {series.hasGrid && !series.hasOi ? (
          <p className="absolute bottom-2 left-3 text-[10px] text-slate-400">
            Waiting for in-session OI samples…
          </p>
        ) : null}
      </div>

      <p className="px-3 pb-3 text-[10px] text-slate-500">
        Chart-only overlay. Change boards still rank Curr − Open/PD. Baseline prefers session open
        (O) locked at first post-09:20 poll, else previous-day close (PD). CE/PE/PCR lines start
        from that open capture (API sampler keeps ticks even if this page is closed). PCR axis is
        widened so ratio moves stay readable.
      </p>
    </div>
  );
}

export default OiMoversSessionPlotly;
