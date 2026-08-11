import { useMemo, useState } from "react";

import type { GammaConcentration, GammaSnapshot } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  CLIFF_LINE,
  NEG_GAMMA,
  PIN_LINE,
  POS_GAMMA,
  SPOT_LINE,
  fmt,
} from "./shared";

const W = 640;
const PAD_TOP = 34;
const PAD_BOTTOM = 26;
const X_AXIS = 190; // zero line for the signed γ bars
const BAR_RIGHT_MAX = 250;
const BAR_LEFT_MAX = 128;
const CUM_MAX = 430;
const LABEL_X = 54;

type LadderRow = {
  strike: number;
  netGex: number;
  grossGex: number;
  share: number;
  shareSq: number;
  cum: number;
  y: number;
};

export function GammaLadder({
  snap,
  conc,
  cliffStrike,
}: {
  snap: GammaSnapshot;
  conc: GammaConcentration | null | undefined;
  cliffStrike: number | null;
}) {
  const [hover, setHover] = useState<LadderRow | null>(null);

  const shareByStrike = useMemo(() => {
    const map = new Map<number, { share: number; shareSq: number }>();
    for (const c of conc?.top_contributors ?? []) {
      map.set(c.strike, { share: c.share ?? 0, shareSq: c.share_sq ?? (c.share ?? 0) ** 2 });
    }
    return map;
  }, [conc?.top_contributors]);

  const { rows, rowH, height, maxAbs } = useMemo(() => {
    const strikes = [...(snap.strikes ?? [])].sort((a, b) => b.strike - a.strike);
    const n = strikes.length;
    const h = n > 0 ? Math.max(8, Math.min(16, Math.round(560 / Math.max(n, 1)))) : 12;
    const gross = strikes.map(
      (r) => Math.abs(Number(r.ce_gex ?? 0) || 0) + Math.abs(Number(r.pe_gex ?? 0) || 0),
    );
    const totalGross = gross.reduce((a, b) => a + b, 0);
    const peak = Math.max(1, ...strikes.map((r) => Math.abs(Number(r.net_gex ?? 0) || 0)));

    let running = 0;
    const out: LadderRow[] = strikes.map((r, i) => {
      running += gross[i];
      const s = shareByStrike.get(r.strike);
      return {
        strike: r.strike,
        netGex: Number(r.net_gex ?? 0) || 0,
        grossGex: gross[i],
        share: s?.share ?? (totalGross > 0 ? gross[i] / totalGross : 0),
        shareSq: s?.shareSq ?? 0,
        cum: totalGross > 0 ? running / totalGross : 0,
        y: PAD_TOP + i * h + h / 2,
      };
    });
    return {
      rows: out,
      rowH: h,
      height: PAD_TOP + n * h + PAD_BOTTOM,
      maxAbs: peak,
    };
  }, [snap.strikes, shareByStrike]);

  /** Interpolate a y for any price level so spot/pin/cliff sit between strikes. */
  const yForLevel = useMemo(() => {
    return (level: number | null | undefined): number | null => {
      if (level == null || rows.length === 0) return null;
      if (rows.length === 1) return rows[0].y;
      const hi = rows[0];
      const lo = rows[rows.length - 1];
      if (level >= hi.strike) return hi.y;
      if (level <= lo.strike) return lo.y;
      for (let i = 1; i < rows.length; i += 1) {
        const a = rows[i - 1];
        const b = rows[i];
        if (level <= a.strike && level >= b.strike) {
          const t = (a.strike - level) / (a.strike - b.strike || 1);
          return a.y + t * (b.y - a.y);
        }
      }
      return null;
    };
  }, [rows]);

  const cumPath = useMemo(() => {
    if (!rows.length) return "";
    const pts = rows.map((r) => `${(X_AXIS + r.cum * CUM_MAX).toFixed(1)},${r.y.toFixed(1)}`);
    return `M ${X_AXIS},${rows[0].y.toFixed(1)} L ${pts.join(" L ")}`;
  }, [rows]);

  const cumArea = useMemo(() => {
    if (!rows.length) return "";
    const last = rows[rows.length - 1];
    return `${cumPath} L ${X_AXIS},${last.y.toFixed(1)} Z`;
  }, [cumPath, rows]);

  const ySpot = yForLevel(snap.spot);
  const yPin = yForLevel(conc?.pin_strike);
  const yCliff = yForLevel(cliffStrike);
  const yPosPeak = yForLevel(conc?.pos_gamma_peak_strike);
  const yNegPeak = yForLevel(conc?.neg_gamma_peak_strike);

  // Label every Nth strike so the axis stays readable at 40+ rows.
  const labelEvery = rowH >= 14 ? 1 : rowH >= 11 ? 2 : 3;

  if (!rows.length) {
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-sm">Cumulative Γ exposure</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground">No strike gamma in the window yet.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2 py-3">
        <div>
          <CardTitle className="text-sm">Cumulative Γ exposure</CardTitle>
          <p className="mt-1 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
            bars: net dealer γ per strike · curve: running share of gross γ
          </p>
        </div>
        <div className="flex flex-col items-end gap-0.5 font-mono text-[10px] tabular-nums">
          <span style={{ color: SPOT_LINE }}>SPOT {fmt(snap.spot, 0)}</span>
          <span style={{ color: POS_GAMMA }}>+γ PEAK {fmt(conc?.pos_gamma_peak_strike)}</span>
          <span style={{ color: NEG_GAMMA }}>−γ PEAK {fmt(conc?.neg_gamma_peak_strike)}</span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="relative">
          <svg
            viewBox={`0 0 ${W} ${height}`}
            width="100%"
            role="img"
            aria-label="Cumulative gamma exposure by strike"
            onMouseLeave={() => setHover(null)}
          >
            {/* cumulative γ area + dashed edge */}
            <path d={cumArea} fill="currentColor" className="text-muted-foreground/15" />
            <path
              d={cumPath}
              fill="none"
              stroke="currentColor"
              strokeWidth={1}
              strokeDasharray="3 3"
              className="text-muted-foreground/70"
            />

            {/* zero axis for the signed bars */}
            <line
              x1={X_AXIS}
              x2={X_AXIS}
              y1={PAD_TOP - 8}
              y2={height - PAD_BOTTOM + 4}
              stroke="currentColor"
              strokeWidth={1}
              className="text-border"
            />

            {rows.map((r, i) => {
              const w = (Math.abs(r.netGex) / maxAbs) * (r.netGex >= 0 ? BAR_RIGHT_MAX : BAR_LEFT_MAX);
              const isHover = hover?.strike === r.strike;
              return (
                <g key={r.strike}>
                  {i % labelEvery === 0 ? (
                    <text
                      x={LABEL_X}
                      y={r.y + 3}
                      textAnchor="end"
                      fontSize={9}
                      fill="currentColor"
                      className={isHover ? "text-foreground" : "text-muted-foreground"}
                      fontFamily="ui-monospace, monospace"
                    >
                      {fmt(r.strike)}
                    </text>
                  ) : null}
                  <rect
                    x={r.netGex >= 0 ? X_AXIS : X_AXIS - w}
                    y={r.y - Math.max(2, rowH / 2 - 1)}
                    width={Math.max(w, 0.6)}
                    height={Math.max(3, rowH - 2)}
                    fill={r.netGex >= 0 ? POS_GAMMA : NEG_GAMMA}
                    opacity={isHover ? 1 : 0.8}
                  />
                  {/* full-width hit area for hover */}
                  <rect
                    x={0}
                    y={r.y - rowH / 2}
                    width={W}
                    height={rowH}
                    fill="transparent"
                    onMouseEnter={() => setHover(r)}
                  />
                </g>
              );
            })}

            {/* peak markers */}
            {yPosPeak != null ? (
              <line
                x1={X_AXIS - BAR_LEFT_MAX}
                x2={W}
                y1={yPosPeak}
                y2={yPosPeak}
                stroke={POS_GAMMA}
                strokeWidth={0.8}
                strokeDasharray="2 4"
                opacity={0.7}
              />
            ) : null}
            {yNegPeak != null ? (
              <line
                x1={X_AXIS - BAR_LEFT_MAX}
                x2={W}
                y1={yNegPeak}
                y2={yNegPeak}
                stroke={NEG_GAMMA}
                strokeWidth={0.8}
                strokeDasharray="2 4"
                opacity={0.7}
              />
            ) : null}

            {yPin != null ? (
              <line x1={LABEL_X + 8} x2={W} y1={yPin} y2={yPin} stroke={PIN_LINE} strokeWidth={1} />
            ) : null}
            {yCliff != null ? (
              <line
                x1={LABEL_X + 8}
                x2={W}
                y1={yCliff}
                y2={yCliff}
                stroke={CLIFF_LINE}
                strokeWidth={1}
                strokeDasharray="4 3"
              />
            ) : null}
            {ySpot != null ? (
              <>
                <line
                  x1={LABEL_X + 8}
                  x2={W}
                  y1={ySpot}
                  y2={ySpot}
                  stroke={SPOT_LINE}
                  strokeWidth={1.4}
                />
                <circle cx={X_AXIS} cy={ySpot} r={3} fill={SPOT_LINE} />
              </>
            ) : null}

            {/* top-of-book callout on the dominant strike */}
            {conc?.dominant_strike != null && conc?.dominant_share != null ? (
              (() => {
                const y = yForLevel(conc.dominant_strike);
                if (y == null) return null;
                return (
                  <text
                    x={W - 4}
                    y={y - 4}
                    textAnchor="end"
                    fontSize={9}
                    fill={POS_GAMMA}
                    fontFamily="ui-monospace, monospace"
                  >
                    {`${(conc.dominant_share * 100).toFixed(1)}% of book @ ${fmt(conc.dominant_strike)}`}
                  </text>
                );
              })()
            ) : null}
          </svg>

          {hover ? (
            <div className="pointer-events-none absolute left-2 top-2 rounded-md border border-border bg-popover/95 px-3 py-2 shadow-sm">
              <p className="font-mono text-sm font-semibold tabular-nums">
                {fmt(hover.strike)}{" "}
                <span style={{ color: hover.netGex >= 0 ? POS_GAMMA : NEG_GAMMA }}>
                  {hover.netGex >= 0 ? "+γ" : "−γ"} {(hover.share * 100).toFixed(2)}%
                </span>
              </p>
              <p className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
                contributes {hover.shareSq.toFixed(3)} to HHI
              </p>
              <p className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
                {Math.abs(hover.strike - snap.spot).toFixed(0)} pts{" "}
                {hover.strike >= snap.spot ? "above" : "below"} spot ·{" "}
                {(hover.cum * 100).toFixed(1)}% cumulative
              </p>
            </div>
          ) : null}
        </div>

        <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: POS_GAMMA }} />
            dealer long γ
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: NEG_GAMMA }} />
            dealer short γ
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-3" style={{ background: SPOT_LINE }} /> spot
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-3" style={{ background: PIN_LINE }} /> pin
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-3" style={{ background: CLIFF_LINE }} /> cliff
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
