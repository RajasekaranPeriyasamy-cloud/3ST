import { useMemo } from "react";

import type { VolumeFootprintOiLadder, VolumeFootprintOiRow } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Session-open OI, current OI and ΔOI per strike, beside the volume profile.
 *
 * Two display rules this component exists to hold:
 *
 * 1. **`null` is not zero.** A strike whose baseline was never captured renders
 *    an em dash, never a `0` bar. Only a measured, unmoved strike shows zero.
 *    The backend keeps the distinction (`analysis/volume_profile/service.py`);
 *    losing it here would invent confidence the data does not have.
 * 2. **One bar scale across both sides.** CE and PE bars are drawn against the
 *    same `max_abs_doi`, so a call bar and a put bar of equal length mean equal
 *    contracts. Scaling each column to its own max would make a quiet side look
 *    as active as a busy one.
 */

const CALL = "#f43f5e";
const PUT = "#14b8a6";
const SPOT_ROW = "#0891b2";

function fmtOi(v: number | null | undefined): string {
  if (v == null) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e5) return `${(v / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(v);
}

function fmtDoi(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${fmtOi(v)}`;
}

/**
 * ΔOI as a share of its baseline.
 *
 * Blank rather than an em dash when absent: the absolute ΔOI directly above it
 * already carries the "—" that says unmeasured, and repeating it would read as
 * a second missing quantity. A percentage is missing here only when the strike
 * opened empty, which the absolute number already makes obvious.
 *
 * Anything past 999% is clamped to `>999%` — a handful of contracts written
 * onto a near-empty strike produces five-figure percentages that would set the
 * column width for every other row.
 */
function fmtPct(v: number | null | undefined): string {
  if (v == null) return "";
  if (Math.abs(v) > 999) return v > 0 ? ">999%" : "<−999%";
  return `${v > 0 ? "+" : ""}${v.toFixed(v === 0 || Math.abs(v) >= 10 ? 0 : 1)}%`;
}

/** ΔOI bar growing out from a shared centre line. */
function DoiBar({
  value,
  max,
  color,
  align,
}: {
  value: number | null;
  max: number;
  color: string;
  align: "left" | "right";
}) {
  // Unmeasured renders as empty track, not a zero-length bar — the two must not
  // look alike.
  const pct = value == null || max <= 0 ? 0 : Math.min(100, (Math.abs(value) / max) * 100);
  return (
    <div className={`flex h-3 w-full ${align === "left" ? "justify-end" : "justify-start"}`}>
      <div
        className="h-full rounded-[1px]"
        style={{
          width: `${pct}%`,
          background: color,
          // Unwind (negative) reads hollow; build reads solid.
          opacity: value != null && value < 0 ? 0.32 : 0.75,
        }}
      />
    </div>
  );
}

export function OiLadderCard({
  ladder,
  className,
}: {
  ladder: VolumeFootprintOiLadder | null | undefined;
  className?: string;
}) {
  const rows = useMemo(() => {
    if (!ladder?.available) return [];
    // Highest strike at the top, so the ladder reads the same way up as the
    // price axis on the profile chart beside it.
    return [...(ladder.rows ?? [])].sort((a, b) => b.strike - a.strike);
  }, [ladder]);

  if (!ladder?.available) {
    return (
      <Card className={className}>
        <CardHeader className="py-3">
          <CardTitle className="text-sm">Strike OI ladder</CardTitle>
        </CardHeader>
        <CardContent className="py-4 text-xs text-muted-foreground">
          OI ladder unavailable
          {ladder?.reason ? ` (${ladder.reason})` : ""} — the profile is unaffected.
        </CardContent>
      </Card>
    );
  }

  const max = ladder.max_abs_doi ?? 0;
  const spot = ladder.spot ?? null;
  const step = ladder.strike_step ?? 50;
  const usesFallback = (ladder.oi_baseline_prev_close_count ?? 0) > 0;

  const isSpotRow = (r: VolumeFootprintOiRow) =>
    spot != null && Math.abs(r.strike - spot) <= step / 2;

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-start justify-between gap-2 py-3">
        <div>
          <CardTitle className="text-sm">Strike OI ladder</CardTitle>
          <p className="mt-1 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
            ΔOI since session open · calls left · puts right
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          {spot != null ? (
            <span className="font-mono text-[11px]" style={{ color: SPOT_ROW }}>
              spot {spot.toFixed(0)}
            </span>
          ) : null}
          {usesFallback ? (
            // The baseline is not uniformly a 09:20 capture — say so rather than
            // letting the column header imply it.
            <Badge variant="outline" className="text-[10px] font-normal">
              mixed baseline
            </Badge>
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="space-y-2">
        <div className="grid grid-cols-[1fr_auto_1fr] gap-x-2 text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
          <div className="text-right" style={{ color: CALL }}>
            Call ΔOI · %
          </div>
          <div className="text-center">Strike</div>
          <div style={{ color: PUT }}>Put ΔOI · %</div>
        </div>

        <div className="max-h-[420px] overflow-y-auto">
          <table className="w-full border-collapse text-[11px]">
            <tbody>
              {rows.map((r) => {
                const atSpot = isSpotRow(r);
                return (
                  <tr
                    key={r.strike}
                    className="border-b border-border/40 last:border-0"
                    style={atSpot ? { background: `${SPOT_ROW}14` } : undefined}
                  >
                    {/* Call side: open OI, ΔOI number, bar growing leftward. */}
                    <td className="w-14 py-1 text-right font-mono text-muted-foreground">
                      {fmtOi(r.ce_open_oi)}
                    </td>
                    <td className="w-16 py-1 text-right font-mono leading-tight">
                      <div style={{ color: CALL }}>{fmtDoi(r.ce_doi)}</div>
                      <div className="text-[9px] text-muted-foreground">
                        {fmtPct(r.ce_doi_pct)}
                      </div>
                    </td>
                    <td className="py-1 pr-1">
                      <DoiBar value={r.ce_doi} max={max} color={CALL} align="left" />
                    </td>

                    <td
                      className="w-16 py-1 text-center font-mono font-medium"
                      style={atSpot ? { color: SPOT_ROW } : undefined}
                    >
                      {r.strike.toFixed(0)}
                    </td>

                    <td className="py-1 pl-1">
                      <DoiBar value={r.pe_doi} max={max} color={PUT} align="right" />
                    </td>
                    <td className="w-16 py-1 font-mono leading-tight">
                      <div style={{ color: PUT }}>{fmtDoi(r.pe_doi)}</div>
                      <div className="text-[9px] text-muted-foreground">
                        {fmtPct(r.pe_doi_pct)}
                      </div>
                    </td>
                    <td className="w-14 py-1 font-mono text-muted-foreground">
                      {fmtOi(r.pe_open_oi)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
          <span>
            Session Δ:{" "}
            <span style={{ color: CALL }}>{fmtDoi(ladder.total_ce_doi)}</span> CE ·{" "}
            <span style={{ color: PUT }}>{fmtDoi(ladder.total_pe_doi)}</span> PE
          </span>
          {ladder.oi_baseline_note ? <span>{ladder.oi_baseline_note}</span> : null}
        </div>

        <p className="text-[10px] leading-relaxed text-muted-foreground">
          Grey columns are OI at the baseline — the first capture after 09:20 IST, falling back to
          previous-day close where no open was recorded. A dash means no baseline was captured for
          that leg, which is not the same as no change. The small percentage is ΔOI against that
          same baseline; bars stay on absolute contracts, because a large percentage on a thin
          strike is not the same size of trade as a small one on a heavy strike.
          {ladder.volume_available === false ? (
            <> Session volume is not shown: the session is too thin to shape a profile.</>
          ) : null}
        </p>
      </CardContent>
    </Card>
  );
}
