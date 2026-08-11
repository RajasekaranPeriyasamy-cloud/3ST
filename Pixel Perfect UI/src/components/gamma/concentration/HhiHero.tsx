import { useMemo } from "react";

import type { GammaConcentration, GammaSnapshot } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import {
  NEG_GAMMA,
  POS_GAMMA,
  bandColor,
  bandLabel,
  bandTextClass,
  dteLabel,
  fmt,
  formatDayMon,
  gexIndian,
  ordinal,
  signedPct,
} from "./shared";

/**
 * The gauge domain is 0→1 (a true HHI range), not a clamped 0→0.5 — a compressed
 * 0-DTE book routinely prints above 0.3 and must not peg the bar.
 */
const GAUGE_MAX = 1;

function pctOfGauge(v: number | null | undefined): number | null {
  if (v == null || !Number.isFinite(v)) return null;
  return Math.min(100, Math.max(0, (v / GAUGE_MAX) * 100));
}

function Tile({
  label,
  value,
  hint,
  valueClass,
  color,
  colorClass,
}: {
  label: string;
  value: string;
  hint?: string | null;
  valueClass?: string;
  color?: string;
  colorClass?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p
        className={`font-mono tabular-nums leading-tight ${
          valueClass ?? "text-2xl font-semibold"
        } ${colorClass ?? ""}`}
        style={color ? { color } : undefined}
      >
        {value}
      </p>
      {hint ? (
        <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

export function HhiHero({
  snap,
  conc,
}: {
  snap: GammaSnapshot;
  conc: GammaConcentration | null | undefined;
}) {
  const hhi = conc?.hhi ?? null;
  const label = bandLabel(conc?.band, conc?.band_label);
  const mean5 = conc?.hhi_mean_5 ?? null;

  const gaugePct = pctOfGauge(hhi);
  const meanPct = pctOfGauge(mean5);
  const compressedPct = pctOfGauge(conc?.band_cut_compressed ?? null);
  const balancedPct = pctOfGauge(conc?.band_cut_balanced ?? null);

  const eyebrow = useMemo(() => {
    const bits = [snap.underlying, "gamma concentration", "HHI"];
    const dte = dteLabel(snap.dte);
    if (dte) bits.push(dte);
    return bits.join(" · ");
  }, [snap.underlying, snap.dte]);

  const explainer = useMemo(() => {
    const base =
      "HHI measures how tightly dealer gamma clusters on a few strikes rather than spreading across the chain.";
    if (hhi == null) return `${base} Not enough gamma mass on the chain to measure it right now.`;
    const shape =
      conc?.band === "concentrated"
        ? "At this reading the book is stacked on very few strikes."
        : conc?.band === "mixed"
          ? "At this reading the book leans on a handful of strikes."
          : "At this reading the book is spread broadly across the chain.";
    return `${base} ${shape} The peaks below mark where dealers hold the most long and short gamma.`;
  }, [hhi, conc?.band]);

  const basisNote =
    conc?.mass_basis === "net"
      ? "net basis · |CE γ + PE γ|"
      : "gross basis · |CE γ| + |PE γ|";

  return (
    <Card>
      <CardContent className="space-y-6 pt-6">
        <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-primary">
          {eyebrow}
        </p>

        <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
          <p
            className={`font-mono text-6xl font-light leading-none tabular-nums tracking-tight sm:text-7xl ${bandTextClass(
              conc?.band,
            )}`}
          >
            {hhi != null ? hhi.toFixed(3) : "—"}
          </p>
          <div className="flex flex-col gap-1 pb-1">
            <p className="text-2xl font-light uppercase tracking-wide text-foreground/90 sm:text-3xl">
              {label}
            </p>
            <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              {conc?.hhi_dod_pct != null
                ? `${signedPct(conc.hhi_dod_pct, 0)} vs prior session`
                : "no prior session on this basis yet"}
            </p>
          </div>
        </div>

        {/* Gauge: value marker + 5-session mean tick + band cut ticks */}
        <div className="space-y-2">
          {meanPct != null ? (
            <div className="relative h-4">
              <span
                className="absolute -translate-x-1/2 whitespace-nowrap text-[9px] uppercase tracking-[0.1em] text-muted-foreground"
                style={{ left: `${meanPct}%` }}
              >
                5-sess mean {mean5?.toFixed(3)}
              </span>
            </div>
          ) : null}
          <div className="relative h-2.5 overflow-visible rounded-full bg-muted">
            {gaugePct != null ? (
              <div
                className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-500"
                style={{ width: `${gaugePct}%`, background: bandColor(conc?.band) }}
              />
            ) : null}
            {[balancedPct, compressedPct].map((p, i) =>
              p == null ? null : (
                <div
                  key={i}
                  className="absolute -top-0.5 bottom-[-2px] w-px bg-foreground/25"
                  style={{ left: `${p}%` }}
                  title={
                    i === 0
                      ? `balanced ≥ ${conc?.band_cut_balanced}`
                      : `compressed ≥ ${conc?.band_cut_compressed}`
                  }
                />
              ),
            )}
            {meanPct != null ? (
              <div
                className="absolute -top-1 bottom-[-4px] w-0.5 bg-foreground/60"
                style={{ left: `${meanPct}%` }}
              />
            ) : null}
            {gaugePct != null ? (
              <div
                className="absolute -top-1 bottom-[-4px] w-1 rounded-sm bg-foreground"
                style={{ left: `calc(${gaugePct}% - 2px)` }}
              />
            ) : null}
          </div>
          <div className="flex justify-between text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
            <span>dispersed</span>
            <span>balanced</span>
            <span>compressed</span>
          </div>
        </div>

        <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">{explainer}</p>

        <div className="grid gap-x-8 gap-y-5 border-t border-border/60 pt-5 sm:grid-cols-2 lg:grid-cols-3">
          <Tile
            label="Net dealer γ"
            value={gexIndian(snap.total_gex)}
            hint={snap.gamma_regime === "positive" ? "dampening" : "amplifying"}
            color={snap.gamma_regime === "positive" ? POS_GAMMA : NEG_GAMMA}
          />
          <Tile
            label="Spot"
            value={fmt(snap.spot, 0)}
            hint={[dteLabel(snap.dte), formatDayMon(snap.expiry)].filter(Boolean).join(" · ")}
          />
          <Tile
            label="+γ peak"
            value={fmt(conc?.pos_gamma_peak_strike)}
            hint="most dealer long gamma"
            color={POS_GAMMA}
          />
          <Tile
            label="−γ peak"
            value={fmt(conc?.neg_gamma_peak_strike)}
            hint="most dealer short gamma"
            color={NEG_GAMMA}
          />
          <Tile
            label="5-session mean"
            value={mean5 != null ? mean5.toFixed(3) : "—"}
            hint={
              mean5 != null
                ? `HHI · ${bandLabel(conc?.hhi_mean_5_band)}`
                : "builds as sessions are recorded"
            }
          />
          <Tile
            label="Percentile"
            value={
              conc?.hhi_percentile_30d != null ? ordinal(conc.hhi_percentile_30d) : "—"
            }
            hint={
              conc?.hhi_session_count != null && conc.hhi_session_count > 0
                ? `of ${conc.hhi_session_count} recorded session${
                    conc.hhi_session_count === 1 ? "" : "s"
                  } · incl. today`
                : "no comparable sessions yet"
            }
            colorClass={
              conc?.hhi_percentile_30d != null ? bandTextClass(conc?.band) : undefined
            }
          />
        </div>

        <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
          {basisNote} · window ±{snap.strike_window ?? "—"} strikes
        </p>
      </CardContent>
    </Card>
  );
}
