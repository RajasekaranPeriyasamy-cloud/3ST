import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useAnalyticsDesk } from "@/context/AnalyticsDeskContext";
import type { OiProfileSnapshot } from "@/lib/types";
import { WidgetShell } from "./WidgetShell";
import { useWidgetPoll } from "./useWidgetPoll";

const POS = "#059669";
const NEG = "#e11d48";

export function OiProfileWidget() {
  const { underlying } = useAnalyticsDesk();
  // Futures OI profile — do not pass options expiry from the desk toolbar.
  const url = `/oi-profile/snapshot?underlying=${encodeURIComponent(underlying)}&interval=5min&days=5`;
  const { data, loading, error, authError } = useWidgetPoll<OiProfileSnapshot>(url);

  const chartData = useMemo(() => {
    const rows = data?.profile ?? [];
    const mid = data?.poc_price;
    let slice = rows;
    if (mid != null && rows.length > 24) {
      const idx = rows.reduce(
        (best, b, i) =>
          Math.abs(b.price_mid - mid) < Math.abs(rows[best].price_mid - mid) ? i : best,
        0,
      );
      const lo = Math.max(0, idx - 12);
      slice = rows.slice(lo, lo + 24);
    }
    return slice.map((b) => ({
      price: b.price_mid,
      buildup: b.buildup,
      unwind: -b.unwind,
    }));
  }, [data]);

  return (
    <WidgetShell
      title="OI Profile"
      fullRoute="/oi-profile"
      loading={loading}
      authError={authError}
      error={error}
      meta={
        data && !data.empty ? (
          <>
            {data.meta?.fut_symbol ?? underlying} · {data.stats?.day_interpretation ?? "—"} · POC{" "}
            {data.poc_price ?? "—"}
          </>
        ) : data?.meta?.fut_symbol ? (
          <>{data.meta.fut_symbol}</>
        ) : null
      }
    >
      {error && !data ? (
        <p className="py-8 text-center text-xs text-destructive">{error}</p>
      ) : !data || data.empty ? (
        <p className="py-8 text-center text-xs text-muted-foreground">
          {loading ? "Loading…" : data?.message || "No OI profile"}
        </p>
      ) : (
        <div className="space-y-2">
          <div className="grid grid-cols-3 gap-2 text-[10px]">
            <div className="rounded border bg-muted/40 px-2 py-1">
              <div className="text-muted-foreground">Price</div>
              <div className="font-mono font-semibold">
                {data.stats.current_price?.toFixed(1) ?? "—"}
              </div>
            </div>
            <div className="rounded border bg-muted/40 px-2 py-1">
              <div className="text-muted-foreground">OI</div>
              <div className="font-mono font-semibold">
                {data.stats.current_oi?.toLocaleString() ?? "—"}
              </div>
            </div>
            <div className="rounded border bg-muted/40 px-2 py-1">
              <div className="text-muted-foreground">POC</div>
              <div className="font-mono font-semibold">{data.poc_price ?? "—"}</div>
            </div>
          </div>
          {chartData.length ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={chartData}
                layout="vertical"
                stackOffset="sign"
                margin={{ top: 4, right: 8, bottom: 4, left: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis type="number" tick={{ fontSize: 9 }} />
                <YAxis
                  type="category"
                  dataKey="price"
                  width={48}
                  tick={{ fontSize: 9 }}
                  tickFormatter={(v) => Number(v).toFixed(0)}
                />
                <Tooltip
                  contentStyle={{ fontSize: 11, borderRadius: 8 }}
                  formatter={(value: number, name: string) => [
                    Math.abs(value).toLocaleString(),
                    name === "buildup" ? "Buildup" : "Unwind",
                  ]}
                />
                <ReferenceLine x={0} stroke="#94a3b8" />
                <Bar dataKey="unwind" name="unwind" fill={NEG} stackId="oi" />
                <Bar dataKey="buildup" name="buildup" fill={POS} stackId="oi" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-6 text-center text-xs text-muted-foreground">
              Futures candles loaded, but no OI-by-price profile yet.
            </p>
          )}
        </div>
      )}
    </WidgetShell>
  );
}
