import { useEffect, useMemo, useRef } from "react";
import HighchartsMod from "highcharts/highstock";
import { HighchartsReact, type HighchartsReactRefObject } from "highcharts-react-official";

import type { CasHistoryPoint } from "@/lib/types";

// Vite/CJS interop: some builds expose `{ default: Highcharts }` instead of the namespace.
const Highcharts = ((HighchartsMod as unknown as { default?: typeof HighchartsMod }).default ??
  HighchartsMod) as typeof HighchartsMod;

type Props = {
  series: CasHistoryPoint[];
  loading?: boolean;
  /** Chart only from this IST wall-clock time (default 15:00 — the CAS run-up). */
  fromHHMM?: string | null;
};

type Key = "estimate" | "official_indicative" | "spot" | "synth_f";

function toMs(ts: string): number {
  // Backend emits IST timestamps — either with +05:30 / Z offset, or naive IST wall clock.
  const raw = String(ts || "").trim();
  if (!raw) return NaN;
  if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw)) {
    return new Date(raw.includes("T") ? raw : raw.replace(" ", "T")).getTime();
  }
  const iso = raw.includes("T") ? raw : raw.replace(" ", "T");
  return new Date(`${iso}+05:30`).getTime();
}

/** Minutes past IST midnight for a row, so `fromHHMM` can clip without TZ math. */
function istMinutes(ts: string): number {
  const m = /T(\d{2}):(\d{2})/.exec(String(ts || ""));
  if (!m) return NaN;
  return Number(m[1]) * 60 + Number(m[2]);
}

function parseHHMM(v: string | null | undefined): number | null {
  if (!v) return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(v.trim());
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

function pairs(rows: CasHistoryPoint[], key: Key): Array<[number, number | null]> {
  const out: Array<[number, number | null]> = [];
  for (const r of rows) {
    const x = toMs(r.ts);
    if (!Number.isFinite(x)) continue;
    const raw = r[key];
    const v = raw == null || !Number.isFinite(Number(raw)) ? null : Number(raw);
    out.push([x, v]);
  }
  return out;
}

/** Pad tight around the data — these four series sit within ~100 pts of each other. */
function levelBounds(rows: CasHistoryPoint[]): { min?: number; max?: number } {
  const nums: number[] = [];
  for (const r of rows) {
    for (const k of ["estimate", "official_indicative", "spot", "synth_f"] as Key[]) {
      const v = r[k];
      if (v != null && Number.isFinite(Number(v))) nums.push(Number(v));
    }
  }
  if (!nums.length) return {};
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  const pad = Math.max(5, (hi - lo) * 0.12);
  return { min: Math.floor(lo - pad), max: Math.ceil(hi + pad) };
}

export function CasHistoryChart({ series, loading, fromHHMM = "15:00" }: Props) {
  const chartRef = useRef<HighchartsReactRefObject | null>(null);

  const rows = useMemo(() => {
    const cutoff = parseHHMM(fromHHMM ?? null);
    if (cutoff == null) return series;
    const clipped = series.filter((r) => {
      const m = istMinutes(r.ts);
      return !Number.isFinite(m) || m >= cutoff;
    });
    // Never show an empty chart just because the session hasn't reached the cutoff.
    return clipped.length ? clipped : series;
  }, [series, fromHHMM]);

  const options = useMemo(() => {
    const bounds = levelBounds(rows);
    const label = rows.length ? (rows[rows.length - 1].session ?? "") : "";
    return {
      chart: {
        backgroundColor: "#ffffff",
        height: 420,
        style: { fontFamily: "Segoe UI, Helvetica, Arial, sans-serif" },
      },
      // Rows are IST; without this Highcharts labels the axis in UTC (−5:30).
      time: { timezone: "Asia/Kolkata" },
      title: {
        text: `NIFTY (absolute levels)${fromHHMM ? ` · from ${fromHHMM} IST` : ""}${
          label ? ` · ${label}` : ""
        }`,
        align: "left",
        style: { fontSize: "13px", fontWeight: "600", color: "#222" },
      },
      credits: { enabled: false },
      rangeSelector: { enabled: false },
      navigator: { enabled: false },
      scrollbar: { enabled: false },
      legend: {
        enabled: true,
        align: "center",
        verticalAlign: "top",
        y: 22,
        itemStyle: { fontSize: "11px", fontWeight: "500" },
      },
      tooltip: {
        shared: true,
        xDateFormat: "%d-%m-%Y %H:%M:%S",
        valueDecimals: 2,
      },
      xAxis: {
        type: "datetime",
        ordinal: false,
        gridLineWidth: 1,
        gridLineColor: "#eef1f4",
        labels: { style: { fontSize: "10px", color: "#666" } },
        dateTimeLabelFormats: {
          second: "%H:%M:%S",
          minute: "%H:%M",
          hour: "%H:%M",
          day: "%d-%m %H:%M",
        },
      },
      yAxis: [
        {
          title: { text: "NIFTY", style: { fontSize: "11px" } },
          opposite: false,
          gridLineColor: "#eef1f4",
          labels: { style: { fontSize: "10px" }, format: "{value:,.2f}" },
          min: bounds.min,
          max: bounds.max,
          softMin: bounds.min,
          softMax: bounds.max,
        },
      ],
      plotOptions: {
        series: {
          dataGrouping: { enabled: false },
          marker: { enabled: true, radius: 2 },
          lineWidth: 1.5,
          // Gaps are meaningful here (official is null outside the window).
          connectNulls: false,
          states: { hover: { lineWidthPlus: 0 } },
        },
      },
      series: [
        {
          type: "line",
          name: "Pre-close forecast",
          color: "#1E88E5",
          lineWidth: 2,
          data: pairs(rows, "estimate"),
        },
        {
          type: "line",
          name: "Official indicative",
          color: "#E53935",
          dashStyle: "ShortDot",
          lineWidth: 2,
          data: pairs(rows, "official_indicative"),
        },
        {
          type: "line",
          name: "NIFTY spot",
          color: "#8E24AA",
          data: pairs(rows, "spot"),
        },
        {
          type: "line",
          name: "Synthetic future",
          color: "#00897B",
          data: pairs(rows, "synth_f"),
        },
      ],
      exporting: { enabled: true },
    } as Highcharts.Options;
  }, [rows, fromHHMM]);

  useEffect(() => {
    const chart = chartRef.current?.chart;
    if (!chart) return;
    if (loading) chart.showLoading("Please Wait Loading...");
    else chart.hideLoading();
  }, [loading, rows]);

  if (!series.length) {
    return (
      <div className="rounded-sm border border-slate-200 bg-white px-4 py-10 text-center text-sm text-slate-500 shadow-sm">
        No CAS history recorded yet for this session. The series builds up as the desk polls — leave
        this page open (or keep the API running) through 15:00–15:35 IST.
      </div>
    );
  }

  return (
    <div className="relative w-full rounded-sm border border-slate-200 bg-white">
      <HighchartsReact
        highcharts={Highcharts}
        constructorType="stockChart"
        options={options}
        ref={chartRef}
      />
    </div>
  );
}
