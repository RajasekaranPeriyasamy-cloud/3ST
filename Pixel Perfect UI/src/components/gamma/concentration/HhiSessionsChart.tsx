import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { GammaConcentration } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { bandColor, bandLabel, formatDayMon, signedPct } from "./shared";

type Row = {
  date: string;
  label: string;
  hhi: number;
  color: string;
  isToday: boolean;
  band: string;
};

export function HhiSessionsChart({ conc }: { conc: GammaConcentration | null | undefined }) {
  const [hovered, setHovered] = useState<Row | null>(null);

  const rows = useMemo((): Row[] => {
    const series = conc?.daily_hhi ?? [];
    const todayIso = series.length ? series[series.length - 1].date : null;
    return series.map((p) => ({
      date: p.date,
      label: formatDayMon(p.date),
      hhi: p.hhi,
      color: bandColor(p.band),
      isToday: p.date === todayIso,
      band: bandLabel(p.band),
    }));
  }, [conc?.daily_hhi]);

  const active = hovered ?? (rows.length ? rows[rows.length - 1] : null);
  const compressedCut = conc?.band_cut_compressed ?? null;
  const balancedCut = conc?.band_cut_balanced ?? null;
  const mean5 = conc?.hhi_mean_5 ?? null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2 py-3">
        <CardTitle className="text-sm">HHI · last 30 sessions</CardTitle>
        <Badge variant="outline" className="text-[10px] font-normal">
          {rows.length ? "hover a session" : "no sessions yet"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Day-end HHI is recorded once per session on the current measurement basis. The
            comparison builds as sessions are captured.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
              <div>
                <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
                  {active?.isToday ? `Today · session ${rows.length}/${rows.length}` : active?.label}
                </p>
                <p className="font-mono text-2xl font-semibold tabular-nums">
                  {active ? active.hhi.toFixed(3) : "—"}
                  <span className="ml-2 text-xs font-normal uppercase tracking-wide text-muted-foreground">
                    {active?.band}
                  </span>
                </p>
              </div>
              <div className="text-right text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
                <p>
                  vs 5-sess mean{" "}
                  <span className="font-mono text-foreground">
                    {signedPct(conc?.hhi_vs_mean_pct, 0)}
                  </span>
                </p>
                <p>
                  d/d{" "}
                  <span className="font-mono text-foreground">
                    {signedPct(conc?.hhi_dod_pct, 1)}
                  </span>
                </p>
              </div>
            </div>

            <ResponsiveContainer width="100%" height={160}>
              <BarChart
                data={rows}
                margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
                onMouseLeave={() => setHovered(null)}
              >
                <XAxis dataKey="label" hide />
                <YAxis domain={[0, "auto"]} hide />
                <Tooltip
                  isAnimationActive={false}
                  cursor={{ fill: "currentColor", opacity: 0.06 }}
                  contentStyle={{ fontSize: 11 }}
                  formatter={(v: number) => [Number(v).toFixed(3), "HHI"]}
                  labelFormatter={(l: string) => l}
                />
                {balancedCut != null ? (
                  <ReferenceLine
                    y={balancedCut}
                    stroke="currentColor"
                    strokeOpacity={0.35}
                    strokeDasharray="3 3"
                    label={{
                      value: `balanced ≥ ${balancedCut}`,
                      position: "insideRight",
                      fontSize: 9,
                      fill: "currentColor",
                      opacity: 0.6,
                    }}
                  />
                ) : null}
                {compressedCut != null ? (
                  <ReferenceLine
                    y={compressedCut}
                    stroke="currentColor"
                    strokeOpacity={0.35}
                    strokeDasharray="3 3"
                    label={{
                      value: `compressed ≥ ${compressedCut}`,
                      position: "insideRight",
                      fontSize: 9,
                      fill: "currentColor",
                      opacity: 0.6,
                    }}
                  />
                ) : null}
                {mean5 != null ? (
                  <ReferenceLine y={mean5} stroke="#94a3b8" strokeWidth={1} />
                ) : null}
                <Bar dataKey="hhi" radius={[2, 2, 0, 0]}>
                  {rows.map((r) => (
                    <Cell
                      key={r.date}
                      fill={r.color}
                      fillOpacity={r.isToday ? 1 : 0.55}
                      onMouseEnter={() => setHovered(r)}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>

            {/* Dates bracket the sample; low/high are the range over it — the two are
                deliberately not paired, since the extremes rarely fall on the edges. */}
            <div className="flex justify-between font-mono text-[10px] tabular-nums text-muted-foreground">
              <span>{rows[0].label}</span>
              <span>
                {conc?.hhi_low_30 != null && conc?.hhi_high_30 != null
                  ? `range ${conc.hhi_low_30.toFixed(3)} – ${conc.hhi_high_30.toFixed(3)}`
                  : ""}
              </span>
              <span>{rows[rows.length - 1].label}</span>
            </div>
            <p className="text-[10px] text-muted-foreground">
              Only sessions recorded on the same mass basis and strike window are comparable —
              days measured differently are excluded from this sample.
              {conc?.hhi_session_assumed_count
                ? ` ${conc.hhi_session_assumed_count} of ${rows.length} predate basis tagging; their strike window is assumed, not recorded.`
                : ""}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
