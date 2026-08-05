import { useEffect, useMemo, useRef } from "react";
import Highcharts from "highcharts/highstock";
import HighchartsReact, { type HighchartsReactRefObject } from "highcharts-react-official";

import type { StraddleWatchSnapshot } from "@/lib/types";

type Props = {
  snapshot: StraddleWatchSnapshot | null;
  loading?: boolean;
};

function toMs(ts: string): number {
  // Backend emits "YYYY-MM-DD HH:mm:ss" as IST wall clock (naive).
  const iso = ts.includes("T") ? ts : ts.replace(" ", "T");
  const d = new Date(`${iso}+05:30`);
  return d.getTime();
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

export function StraddleWatchChart({ snapshot, loading }: Props) {
  const chartRef = useRef<HighchartsReactRefObject | null>(null);

  const options = useMemo<Highcharts.Options>(() => {
    const series = snapshot?.series;
    const t = series?.t ?? [];
    return {
      chart: {
        backgroundColor: "#ffffff",
        height: 620,
        style: { fontFamily: "Segoe UI, Helvetica, Arial, sans-serif" },
      },
      title: {
        text: "Straddle Watch",
        style: { fontSize: "16px", fontWeight: "600", color: "#222" },
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
        itemStyle: { fontSize: "11px", fontWeight: "500" },
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
        gridLineColor: "#eef1f4",
        labels: { style: { fontSize: "10px", color: "#666" } },
      },
      yAxis: [
        {
          height: "62%",
          resize: { enabled: true },
          title: { text: "Call / Put Price", style: { fontSize: "11px" } },
          opposite: true,
          gridLineColor: "#eef1f4",
          labels: { style: { fontSize: "10px" } },
        },
        {
          top: "65%",
          height: "22%",
          offset: 0,
          title: { text: "OI", style: { fontSize: "11px" } },
          opposite: true,
          gridLineColor: "#eef1f4",
          labels: {
            style: { fontSize: "10px" },
            formatter() {
              return formatOi(Number(this.value));
            },
          },
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
          color: "#111111",
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
          color: "#8B0000",
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
          color: "#90A4AE",
          yAxis: 0,
          visible: false,
          data: pairs(t, series?.iv ?? []),
        },
      ],
      exporting: { enabled: true },
    };
  }, [snapshot]);

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
