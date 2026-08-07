import { useMemo } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useAnalyticsDesk } from "@/context/AnalyticsDeskContext";
import type { GammaSnapshot } from "@/lib/types";
import { WidgetShell } from "./WidgetShell";
import { useWidgetPoll } from "./useWidgetPoll";

const POS = "#059669";
const NEG = "#e11d48";

export function GammaDensityWidget() {
  const { underlying, expiry } = useAnalyticsDesk();
  const url = expiry
    ? `/gamma-density/snapshot?underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(expiry)}`
    : null;
  const { data, loading, error, authError } = useWidgetPoll<GammaSnapshot>(url);

  const chartData = useMemo(
    () =>
      (data?.strikes ?? []).map((r) => ({
        strike: r.strike,
        net_gex_cr: r.net_gex / 1e7,
      })),
    [data],
  );

  return (
    <WidgetShell
      title="Gamma Density"
      fullRoute="/gamma-density"
      loading={loading}
      authError={authError}
      error={error}
      meta={
        data ? (
          <>
            {data.gamma_regime} · GEX {(data.total_gex / 1e7).toFixed(1)} Cr · HHI{" "}
            {data.concentration?.hhi != null ? data.concentration.hhi.toFixed(2) : "—"}
            {data.concentration?.band ? ` (${data.concentration.band})` : ""} · Dom{" "}
            {data.concentration?.dominant_strike ?? "—"}
          </>
        ) : null
      }
    >
      {!data ? (
        <p className="py-8 text-center text-xs text-muted-foreground">
          {loading ? "Loading…" : "No gamma data"}
        </p>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis
              dataKey="strike"
              type="number"
              domain={["dataMin", "dataMax"]}
              tick={{ fontSize: 10 }}
            />
            <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => Number(v).toFixed(1)} width={36} />
            <Tooltip
              contentStyle={{ fontSize: 11, borderRadius: 8 }}
              formatter={(v: number) => [`${v.toFixed(2)} Cr`, "Net GEX"]}
            />
            <ReferenceLine y={0} stroke="#94a3b8" />
            <ReferenceLine x={data.spot} stroke="#ea580c" strokeWidth={1.5} />
            {data.flip_level != null ? (
              <ReferenceLine x={data.flip_level} stroke="#7c3aed" strokeDasharray="4 4" />
            ) : null}
            {data.concentration?.dominant_strike != null ? (
              <ReferenceLine
                x={data.concentration.dominant_strike}
                stroke="#0d9488"
                strokeDasharray="3 3"
              />
            ) : null}
            <Bar dataKey="net_gex_cr" name="Net GEX" radius={[2, 2, 0, 0]}>
              {chartData.map((d, i) => (
                <Cell key={i} fill={d.net_gex_cr >= 0 ? POS : NEG} />
              ))}
            </Bar>
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </WidgetShell>
  );
}
