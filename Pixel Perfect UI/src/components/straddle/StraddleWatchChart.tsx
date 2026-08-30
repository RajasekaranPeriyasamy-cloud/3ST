import { useEffect, useMemo, useRef } from "react";
import HighchartsMod from "highcharts/highstock";
import {
  HighchartsReact,
  type HighchartsReactRefObject,
} from "highcharts-react-official";

import type { StraddleWatchSnapshot } from "@/lib/types";
import { useTheme } from "@/hooks/useTheme";

// Vite/CJS interop: some builds expose `{ default: Highcharts }` instead of the namespace.
const Highcharts =
  ((HighchartsMod as unknown as { default?: typeof HighchartsMod }).default ??
    HighchartsMod) as typeof HighchartsMod;

type Props = {
  snapshot: StraddleWatchSnapshot | null;
  loading?: boolean;
};

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

function pairs(t: string[], values: Array<number | null | undefined>): Array<[number, number | null]> {
  const out: Array<[number, number | null]> = [];
  const n = Math.min(t.length, values.length);
  for (let i = 0; i < n; i++) {
    const v = values[i];
    out.push([toMs(t[i]), v == null || Number.isNaN(Number(v)) ? null : Number(v)]);
  }
  return out;
}

function formatOi(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e7) return `${(value / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `${(value / 1e5).toFixed(1)} L`;
  return value.toLocaleString();
}

function ivAxisBounds(values: Array<number | null | undefined>): {
  min?: number;
  max?: number;
} {
  const nums = values.filter((v): v is number => v != null && !Number.isNaN(Number(v))).map(Number);
  if (!nums.length) return {};
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  // Pad so small intraday IV moves are visible; floor pad at 1 vol point.
  const span = hi - lo;
  const pad = Math.max(1, span * 0.25 || 1);
  return {
    min: Math.max(0, Math.floor((lo - pad) * 10) / 10),
    max: Math.ceil((hi + pad) * 10) / 10,
  };
}

export function StraddleWatchChart({ snapshot, loading }: Props) {
  const chartRef = useRef<HighchartsReactRefObject | null>(null);
  const { isDark } = useTheme();

  const options = useMemo(() => {
    const series = snapshot?.series;
    const t = series?.t ?? [];
    const ivBounds = ivAxisBounds(series?.iv ?? []);
    // Blue-black surface, soft off-white ink. Two series carry colours that only
    // work on white -- Straddle Price (#111) and Call OI (dark red) -- so they
    // get a light-on-dark counterpart rather than vanishing into the plot.
    const surface = isDark ? "#1b2029" : "#ffffff";
    const gridLine = isDark ? "rgba(148, 163, 184, 0.16)" : "#eef1f4";
    const ink = isDark ? "#e6ebf2" : "#222";
    const axisInk = isDark ? "rgba(212, 220, 232, 0.78)" : "#666";
    const straddleInk = isDark ? "#e2e8f0" : "#111111";
    const callOiInk = isDark ? "#f87171" : "#8B0000";
    const ivInk = isDark ? "#a5b4fc" : "#5C6BC0";
    const opts = {
      chart: {
        backgroundColor: surface,
        height: 620,
        style: { fontFamily: "Segoe UI, Helvetica, Arial, sans-serif" },
      },
      // Kite candles are IST; without this Highcharts labels the axis in UTC (−5:30).
      time: {
        timezone: "Asia/Kolkata",
      },
      title: {
        text: "Straddle Watch",
        style: { fontSize: "16px", fontWeight: "600", color: ink },
      },
      credits: { enabled: false },
      rangeSelector: { enabled: false },
      navigator: {
        enabled: true,
        height: 40,
        series: { type: "areaspline", color: "#7cb5ec", fillOpacity: 0.15 },
      },
      scrollbar: { enabled: true },
      legend: {
        enabled: true,
        align: "center",
        verticalAlign: "top",
        y: 28,
        itemStyle: { fontSize: "11px", fontWeight: "500", color: axisInk },
        itemHoverStyle: { color: ink },
      },
      tooltip: {
        shared: true,
        xDateFormat: "%d-%m-%Y %H:%M",
        valueDecimals: 2,
      },
      xAxis: {
        type: "datetime",
        ordinal: false,
        gridLineWidth: 1,
        gridLineColor: gridLine,
        labels: { style: { fontSize: "10px", color: axisInk } },
        dateTimeLabelFormats: {
          minute: "%H:%M",
          hour: "%H:%M",
          day: "%d-%m %H:%M",
          week: "%d-%m",
          month: "%b '%y",
        },
      },
      yAxis: [
        {
          // Price pane (right)
          height: "62%",
          resize: { enabled: true },
          title: { text: "Call / Put Price", style: { fontSize: "11px", color: axisInk } },
          opposite: true,
          gridLineColor: gridLine,
          labels: { style: { fontSize: "10px", color: axisInk } },
        },
        {
          // OI pane (right)
          top: "65%",
          height: "22%",
          offset: 0,
          title: { text: "OI", style: { fontSize: "11px", color: axisInk } },
          opposite: true,
          gridLineColor: gridLine,
          labels: {
            style: { fontSize: "10px", color: axisInk },
            formatter() {
              return formatOi(Number(this.value));
            },
          },
        },
        {
          // IV scale on the left of the price pane (own axis — not mixed with premium ₹)
          height: "62%",
          opposite: false,
          gridLineWidth: 0,
          title: {
            text: "IV %",
            style: { fontSize: "11px", color: ivInk },
          },
          labels: {
            style: { fontSize: "10px", color: ivInk },
            format: "{value:.1f}",
          },
          min: ivBounds.min,
          max: ivBounds.max,
          softMin: ivBounds.min,
          softMax: ivBounds.max,
          showEmpty: false,
        },
      ],
      plotOptions: {
        series: {
          showInNavigator: true,
          dataGrouping: { enabled: false },
          marker: { enabled: false },
          lineWidth: 1.5,
          states: { hover: { lineWidthPlus: 0 } },
        },
      },
      series: [
        {
          type: "line",
          name: "Call Price",
          color: "#C2185B",
          yAxis: 0,
          data: pairs(t, series?.call_price ?? []),
        },
        {
          type: "line",
          name: "Put Price",
          color: "#7CB342",
          yAxis: 0,
          data: pairs(t, series?.put_price ?? []),
        },
        {
          type: "line",
          name: "Straddle Price",
          color: straddleInk,
          yAxis: 0,
          lineWidth: 2,
          data: pairs(t, series?.straddle_price ?? []),
        },
        {
          type: "line",
          name: "Straddle VWAP (ATP)",
          color: "#B0BEC5",
          yAxis: 0,
          visible: false,
          data: pairs(t, series?.straddle_vwap ?? []),
        },
        {
          type: "line",
          name: "Call OI",
          color: callOiInk,
          yAxis: 1,
          data: pairs(t, series?.call_oi ?? []),
          tooltip: {
            pointFormatter() {
              const y = this.y;
              return `<span style="color:${this.color}">●</span> ${this.series.name}: <b>${
                y == null ? "—" : formatOi(y)
              }</b><br/>`;
            },
          },
        },
        {
          type: "line",
          name: "Put OI",
          color: "#43A047",
          yAxis: 1,
          data: pairs(t, series?.put_oi ?? []),
          tooltip: {
            pointFormatter() {
              const y = this.y;
              return `<span style="color:${this.color}">●</span> ${this.series.name}: <b>${
                y == null ? "—" : formatOi(y)
              }</b><br/>`;
            },
          },
        },
        {
          type: "line",
          name: "IV",
          color: ivInk,
          yAxis: 2,
          visible: false,
          lineWidth: 2,
          data: pairs(t, series?.iv ?? []),
          tooltip: {
            valueSuffix: "%",
            valueDecimals: 2,
            pointFormatter() {
              const y = this.y;
              return `<span style="color:${this.color}">●</span> ${this.series.name}: <b>${
                y == null ? "—" : `${Number(y).toFixed(2)}%`
              }</b><br/>`;
            },
          },
        },
      ],
      exporting: { enabled: true },
    } as Highcharts.Options;
    return opts;
  }, [snapshot, isDark]);

  useEffect(() => {
    const chart = chartRef.current?.chart;
    if (!chart) return;
    if (loading) {
      chart.showLoading("Please Wait Loading...");
    } else {
      chart.hideLoading();
    }
  }, [loading, snapshot]);

  return (
    <div className="relative w-full rounded-sm border border-border bg-card">
      <HighchartsReact
        highcharts={Highcharts}
        constructorType="stockChart"
        options={options}
        ref={chartRef}
      />
    </div>
  );
}
