import { useMemo } from "react";

import type {
  GammaConcentration,
  GammaPinLock,
  VolumeProfileSnapshot,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CE_COLOR, NEG_GAMMA, PE_COLOR, POS_GAMMA, fmt } from "./shared";

const UNAVAILABLE_COPY: Record<string, string> = {
  too_few_bars: "Too few session bars yet — a profile off the opening minutes would be noise.",
  no_session_bars: "No futures candles for this session yet.",
  fetch_failed: "Could not fetch session candles. See log/errors.jsonl.",
  future_unresolved: "Front-month future could not be resolved — refresh instruments.",
  unknown_underlying: "No configuration for this underlying.",
  engine_error: "The footprint engine could not build a profile. See log/errors.jsonl.",
};

/** Distance in points and in strike steps — the desk thinks in both. */
function Gap({
  from,
  to,
  step,
}: {
  from: number | null | undefined;
  to: number | null | undefined;
  step: number;
}) {
  if (from == null || to == null) return <span className="text-muted-foreground">—</span>;
  const d = to - from;
  const steps = step > 0 ? d / step : 0;
  const tone = Math.abs(steps) <= 1 ? PE_COLOR : Math.abs(steps) <= 2 ? undefined : CE_COLOR;
  return (
    <span className="font-mono tabular-nums" style={tone ? { color: tone } : undefined}>
      {d >= 0 ? "+" : "−"}
      {Math.abs(d).toFixed(0)}
      <span className="ml-1 text-[11px] text-muted-foreground">
        {Math.abs(steps).toFixed(1)} step{Math.abs(steps) === 1 ? "" : "s"}
      </span>
    </span>
  );
}

function Row({ label, value, extra }: { label: string; value: string; extra?: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-baseline gap-2">
        <span className="font-mono tabular-nums font-semibold">{value}</span>
        {extra}
      </span>
    </div>
  );
}

export function VolumeConfluencePanel({
  vp,
  conc,
  pinLock,
  strikeStep,
}: {
  vp: VolumeProfileSnapshot | null | undefined;
  conc: GammaConcentration | null | undefined;
  pinLock: GammaPinLock | null | undefined;
  strikeStep: number;
}) {
  const pin = pinLock?.pin_mode ?? conc?.pin_strike ?? null;
  const posPeak = conc?.pos_gamma_peak_strike ?? null;

  /**
   * The value area is the market's own containment band. The pin measure uses a
   * fixed ±1 strike step; comparing the two is what can eventually calibrate
   * PIN_CONTAINMENT_STEPS instead of leaving it a reasoned guess.
   */
  const vaSteps = useMemo(() => {
    if (vp?.value_area_pts == null || strikeStep <= 0) return null;
    return vp.value_area_pts / (2 * strikeStep);
  }, [vp?.value_area_pts, strikeStep]);

  const pinInsideVa =
    pin != null && vp?.val != null && vp?.vah != null ? pin >= vp.val && pin <= vp.vah : null;

  if (!vp?.available) {
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-base">Volume confluence</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1">
          <p className="text-sm text-muted-foreground">
            {UNAVAILABLE_COPY[vp?.reason ?? ""] ?? "Session volume profile unavailable."}
          </p>
          {vp?.bars ? (
            <p className="text-[11px] text-muted-foreground">{vp.bars} bars so far.</p>
          ) : null}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2 py-3">
        <div>
          <CardTitle className="text-base">Volume confluence</CardTitle>
          <p className="mt-1 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
            where dealers must hedge vs where business happened
          </p>
        </div>
        <Badge variant="outline" className="text-[11px] font-normal">
          {vp.bars} bars
        </Badge>
      </CardHeader>

      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          <Row
            label="POC (most business)"
            value={fmt(vp.poc, 0)}
            extra={<Gap from={vp.poc} to={pin} step={strikeStep} />}
          />
          <Row
            label="Value area"
            value={`${fmt(vp.val, 0)} – ${fmt(vp.vah, 0)}`}
            extra={
              <span className="text-[11px] text-muted-foreground">
                ±{vaSteps != null ? vaSteps.toFixed(1) : "—"} steps
              </span>
            }
          />
          <Row
            label="Pin vs POC"
            value={pin != null ? fmt(pin, 0) : "—"}
            extra={
              pinInsideVa == null ? null : (
                <Badge
                  variant="outline"
                  className={`h-5 px-1.5 text-[11px] ${
                    pinInsideVa
                      ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300"
                      : "border-amber-500/40 text-amber-700 dark:text-amber-300"
                  }`}
                >
                  {pinInsideVa ? "in value" : "outside value"}
                </Badge>
              )
            }
          />
          <Row
            label="+γ peak vs POC"
            value={fmt(posPeak, 0)}
            extra={<Gap from={vp.poc} to={posPeak} step={strikeStep} />}
          />
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border/60 pt-2.5 text-xs">
          <span className="text-muted-foreground">
            Tilt{" "}
            <span
              className="font-mono tabular-nums"
              style={{
                color:
                  vp.tilt_pp == null
                    ? undefined
                    : vp.tilt_pp > 0
                      ? POS_GAMMA
                      : vp.tilt_pp < 0
                        ? NEG_GAMMA
                        : undefined,
              }}
            >
              {vp.tilt_pp != null ? `${vp.tilt_pp > 0 ? "+" : ""}${vp.tilt_pp.toFixed(1)}pp` : "—"}
            </span>
          </span>
          <span className="text-muted-foreground">
            OVL <span className="font-mono tabular-nums">{vp.overlap_pct?.toFixed(0) ?? "—"}</span>
          </span>
          {vp.balance_verdict ? (
            <Badge variant="outline" className="h-5 px-1.5 text-[11px] font-normal">
              {vp.balance_verdict}
            </Badge>
          ) : null}
        </div>

        {/* Two honesty lines the engine's own README insists on. */}
        <div className="space-y-1 border-t border-border/60 pt-2.5">
          <p className="text-[11px] text-muted-foreground">
            Buy/sell split is inferred from candle geometry ({vp.engine}), not measured order flow
            — read tilt and OVL as structure, not verified flow.
          </p>
          <p className="text-[11px] text-muted-foreground">
            {vp.price_axis === "future"
              ? "Options are written on the future, so volume and strikes already share one axis."
              : `Futures volume shifted onto the index axis${
                  vp.basis?.median != null ? ` (basis ~${vp.basis.median.toFixed(0)})` : ""
                }${
                  vp.basis?.matched_bars != null ? ` · ${vp.basis.matched_bars} matched bars` : ""
                }.`}
            {vp.residual_label === "DRIFT"
              ? " Arithmetic self-check reports DRIFT — do not trade the shape until it settles."
              : ""}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
