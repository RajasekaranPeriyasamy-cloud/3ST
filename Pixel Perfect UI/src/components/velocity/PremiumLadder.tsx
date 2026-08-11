import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { VelocityLadder } from "@/lib/types";

/**
 * Premium change since the session's first snapshot, ATM +/- 5.
 *
 * Twelve near-identical lines are hard to read, so three things carry the
 * structure rather than colour alone: calls are warm and puts cool, the stroke
 * thins and dashes as the strike moves further out of the money, and hovering a
 * legend entry dims the rest. Without that last one the chart is decorative.
 */

const CALL_COLORS = ["#dc2626", "#ea580c", "#f97316", "#fb923c", "#fdba74", "#fed7aa"];
const PUT_COLORS = ["#059669", "#0d9488", "#14b8a6", "#2dd4bf", "#5eead4", "#99f6e4"];

function seriesStyle(offset: number, optionType: string) {
  const depth = Math.abs(offset);
  const palette = optionType === "CE" ? CALL_COLORS : PUT_COLORS;
  return {
    stroke: palette[Math.min(depth, palette.length - 1)],
    strokeWidth: depth === 0 ? 2 : 1.3,
    strokeDasharray: depth === 0 ? undefined : `${Math.max(2, 8 - depth)} ${2 + depth}`,
  };
}

export function PremiumLadder({ ladder }: { ladder: VelocityLadder | null }) {
  const [focus, setFocus] = useState<string | null>(null);

  const series = ladder?.series ?? [];

  // Recharts wants one row per x value; the payload is series-of-points, and
  // series have different lengths because strikes drift out of the tracked
  // window. Pivoting on clock leaves those cells undefined, which Recharts
  // renders as a gap rather than a false zero.
  const rows = useMemo(() => {
    const byClock = new Map<string, Record<string, number | string>>();
    for (const s of series) {
      for (const p of s.points) {
        const row = byClock.get(p.clock) ?? { clock: p.clock };
        row[s.label] = p.change;
        byClock.set(p.clock, row);
      }
    }
    return Array.from(byClock.values()).sort((a, b) =>
      String(a.clock).localeCompare(String(b.clock)),
    );
  }, [series]);

  const partial = useMemo(() => {
    if (!rows.length) return [];
    return series.filter((s) => s.coverage < rows.length * 0.9);
  }, [series, rows.length]);

  if (!series.length) {
    // Two very different causes look identical here, so name both rather than
    // asserting the one this component cannot verify: it only receives the
    // ladder, and has no way to know whether the session has archived minutes.
    return (
      <p className="p-4 text-sm text-muted-foreground">
        No ladder in this response. Either the session has no archived snapshots yet, or
        the API predates this panel — check that <code>/velocity/chart</code> returns a{" "}
        <code>ladder</code> key, and restart the API if it does not.
      </p>
    );
  }

  return (
    <>
      <div className="h-[320px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
            <XAxis dataKey="clock" tick={{ fontSize: 10 }} minTickGap={40} />
            <YAxis
              tick={{ fontSize: 10 }}
              tickFormatter={(v) => Number(v).toFixed(0)}
              width={56}
            />
            <Tooltip
              contentStyle={{ fontSize: 11 }}
              formatter={(v: number | string, name: string) => [Number(v).toFixed(2), name]}
            />
            <ReferenceLine y={0} stroke="currentColor" opacity={0.45} />
            <Legend
              wrapperStyle={{ fontSize: 10 }}
              onMouseEnter={(e) => setFocus(String(e.dataKey ?? ""))}
              onMouseLeave={() => setFocus(null)}
            />
            {series.map((s) => {
              const style = seriesStyle(s.offset, s.option_type);
              const dimmed = focus !== null && focus !== s.label;
              return (
                <Line
                  key={s.label}
                  type="monotone"
                  dataKey={s.label}
                  stroke={style.stroke}
                  strokeWidth={focus === s.label ? style.strokeWidth + 1 : style.strokeWidth}
                  strokeDasharray={style.strokeDasharray}
                  strokeOpacity={dimmed ? 0.12 : 1}
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {partial.length ? (
        <p className="px-4 pb-3 text-[11px] text-muted-foreground">
          Partial coverage:{" "}
          {partial
            .map((s) => `${s.label} (${s.coverage}/${rows.length} min)`)
            .join(", ")}{" "}
          — these strikes left the tracked ATM±5 window as spot moved, so the series
          stops rather than flattening.
        </p>
      ) : null}
    </>
  );
}
