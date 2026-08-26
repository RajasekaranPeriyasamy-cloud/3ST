import { useMemo } from "react";

import type { VolumeFootprintTiltHistory } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Where today's session tilt sits against the recent window.
 *
 * The card's job is to stop three misreadings the raw percentile invites:
 *
 * 1. **Like-for-like only.** Both sides are read at the same elapsed session
 *    minute — the header says which. The backend refuses any other comparison;
 *    this just makes the basis visible so nobody assumes it is closing-vs-closing.
 * 2. **`n` travels with the percentile, always.** "6th of 16" and "6th of 30"
 *    are different claims and must not render alike.
 * 3. **Backfilled sessions are counted out loud.** Recomputed history is valid
 *    but it is a reconstruction, and a window that is mostly reconstruction
 *    deserves to say so.
 */

const BUY = "#14b8a6";
const SELL = "#f43f5e";
const NEUTRAL = "#64748b";

function tiltColor(v: number | null | undefined, dead: number): string {
  if (v == null) return NEUTRAL;
  if (v > dead) return BUY;
  if (v < -dead) return SELL;
  return NEUTRAL;
}

function fmtPp(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}pp`;
}

function hhmm(minute: number | null | undefined): string {
  if (minute == null) return "—";
  // Session opens 09:15; the checkpoint is elapsed minutes from there.
  const total = 9 * 60 + 15 + minute;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

/** Why the comparison is withheld, in the reader's terms rather than the API's. */
const REASONS: Record<string, string> = {
  too_early: "too early in the session to rank — the first checkpoint is 15 minutes in",
  no_history: "no prior sessions stored at this checkpoint yet",
  window_too_thin: "fewer than 5 prior sessions — too thin to rank against",
  profile_unavailable: "the session profile is unavailable",
};

export function TiltHistoryCard({
  hist,
  className,
}: {
  hist: VolumeFootprintTiltHistory | null | undefined;
  className?: string;
}) {
  const dead = hist?.dead_zone_pp ?? 5;

  const bars = useMemo(() => {
    const series = hist?.series ?? [];
    if (!series.length) return [];
    const span = Math.max(...series.map((p) => Math.abs(p.tilt_pp)), Math.abs(hist?.current_tilt_pp ?? 0), 1);
    return series.map((p) => ({ ...p, pct: (Math.abs(p.tilt_pp) / span) * 100, span }));
  }, [hist]);

  if (!hist || (!hist.available && !hist.series?.length)) {
    const why = hist?.reason ? REASONS[hist.reason] ?? hist.reason : null;
    return (
      <Card className={className}>
        <CardHeader className="py-3">
          <CardTitle className="text-sm">Tilt vs recent sessions</CardTitle>
        </CardHeader>
        <CardContent className="py-4 text-xs text-muted-foreground">
          {hist?.current_tilt_pp != null ? (
            <>
              Today is{" "}
              <span style={{ color: tiltColor(hist.current_tilt_pp, dead) }}>
                {fmtPp(hist.current_tilt_pp)}
              </span>
              , but {why ?? "no comparison is available"}.
            </>
          ) : (
            <>No comparison available{why ? ` — ${why}` : ""}.</>
          )}
          {hist?.n ? <> Window holds {hist.n}.</> : null}
        </CardContent>
      </Card>
    );
  }

  const pct = hist.percentile;
  const cur = hist.current_tilt_pp;
  const backfilled = hist.backfilled ?? 0;
  const allBackfill = backfilled === hist.n && hist.n > 0;

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-start justify-between gap-2 py-3">
        <div>
          <CardTitle className="text-sm">Tilt vs recent sessions</CardTitle>
          <p className="mt-1 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
            compared at {hhmm(hist.checkpoint_min)} · same point of every session
          </p>
        </div>
        {backfilled > 0 ? (
          <Badge variant="outline" className="text-[10px] font-normal">
            {allBackfill ? "all backfilled" : `${backfilled}/${hist.n} backfilled`}
          </Badge>
        ) : null}
      </CardHeader>

      <CardContent className="space-y-3">
        <div className="flex items-baseline gap-3">
          <span
            className="font-mono text-2xl"
            style={{ color: tiltColor(cur, dead) }}
          >
            {fmtPp(cur)}
          </span>
          <span className="text-xs text-muted-foreground">
            {pct != null ? (
              <>
                <span className="font-medium text-foreground">{pct.toFixed(0)}th percentile</span> of{" "}
                {hist.n} prior session{hist.n === 1 ? "" : "s"}
              </>
            ) : (
              <>no rank</>
            )}
          </span>
        </div>

        <div className="grid grid-cols-3 gap-2 text-[11px]">
          {(
            [
              ["Window low", hist.min],
              ["Median", hist.median],
              ["Window high", hist.max],
            ] as const
          ).map(([label, v]) => (
            <div key={label}>
              <div className="text-[9px] uppercase tracking-[0.08em] text-muted-foreground">
                {label}
              </div>
              <div className="font-mono" style={{ color: tiltColor(v, dead) }}>
                {fmtPp(v)}
              </div>
            </div>
          ))}
        </div>

        {/* Oldest left, today's marker at the right — the shape of the window
            matters as much as the rank, and a single number hides a trend. */}
        <div className="flex h-16 items-center gap-[2px]">
          {bars.map((p) => (
            <div
              key={p.date}
              className="flex h-full flex-1 flex-col justify-center"
              title={`${p.date} · ${fmtPp(p.tilt_pp)} · ${p.source}`}
            >
              <div className="flex h-1/2 items-end">
                {p.tilt_pp > 0 ? (
                  <div
                    className="w-full rounded-t-[1px]"
                    style={{
                      height: `${p.pct}%`,
                      background: BUY,
                      opacity: p.source === "backfill" ? 0.45 : 0.85,
                    }}
                  />
                ) : null}
              </div>
              <div className="h-px w-full bg-border" />
              <div className="flex h-1/2 items-start">
                {p.tilt_pp < 0 ? (
                  <div
                    className="w-full rounded-b-[1px]"
                    style={{
                      height: `${p.pct}%`,
                      background: SELL,
                      opacity: p.source === "backfill" ? 0.45 : 0.85,
                    }}
                  />
                ) : null}
              </div>
            </div>
          ))}
        </div>

        <p className="text-[10px] leading-relaxed text-muted-foreground">
          {hist.first_date && hist.last_date ? (
            <>
              {hist.first_date} → {hist.last_date}.{" "}
            </>
          ) : null}
          Faded bars are backfilled — recomputed from historical minute bars rather than observed
          as the session ran. Tilt is a model output, not measured order flow, so read this as
          where today sits among recent estimates of the same kind. Inside ±{dead}pp the engine
          refuses to name a side at all.
        </p>
      </CardContent>
    </Card>
  );
}
