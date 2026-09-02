import type { GammaRegime } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CE_COLOR, PE_COLOR, SPOT_LINE, fmt } from "./shared";

/** Structural states, not directional ones — tone reflects certainty, not bias. */
const STATE_TONE: Record<string, string> = {
  pinned: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  coiled_box: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  short_gamma_trend: "border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  transition: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  long_gamma_drift: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  mixed: "border-border bg-muted text-muted-foreground",
  unmeasured: "border-border bg-muted text-muted-foreground",
};

/**
 * Labels only. The ladder is ordered by **price, high to low** — it is a price
 * axis, and reading it any other way fights how every chart on the desk is laid
 * out. This array is just the label lookup, plus a deterministic tie-break when
 * two levels share a strike (pin and call wall both at 24,300, say).
 */
const LEVEL_ORDER: { key: string; label: string }[] = [
  { key: "flip", label: "Flip" },
  { key: "call_wall", label: "Call wall" },
  { key: "pos_gamma_peak", label: "+γ peak" },
  { key: "pin", label: "Pin" },
  { key: "poc", label: "POC" },
  { key: "vah", label: "VAH" },
  { key: "val", label: "VAL" },
  { key: "put_wall", label: "Put wall" },
  { key: "neg_gamma_peak", label: "−γ peak" },
];

function sigmaTone(sigma: number | null): string | undefined {
  if (sigma == null) return undefined;
  // Inside one expected move is reachable today; beyond it is not, on this session's vol.
  return Math.abs(sigma) <= 1 ? undefined : "opacity-55";
}

export function RegimePanel({ regime }: { regime: GammaRegime | null | undefined }) {
  if (!regime) {
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-base">Structural regime</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Regime unavailable for this snapshot.</p>
        </CardContent>
      </Card>
    );
  }

  const c = regime.confluence;
  const f = regime.features;
  // Price ladder: high at the top, low at the bottom, like every other price
  // axis on the desk. Ties (pin and call wall on the same strike) fall back to
  // LEVEL_ORDER so the sequence is stable between polls instead of shuffling.
  const levels = LEVEL_ORDER.map((l, i) => ({ ...l, rank: i, v: regime.levels?.[l.key] }))
    .filter((l) => l.v && l.v.level != null)
    .sort((a, b) => (b.v!.level as number) - (a.v!.level as number) || a.rank - b.rank);

  // Where spot sits in that ladder. Every level carries `pts` relative to spot,
  // so it is recoverable without another field — and marking it is what turns a
  // sorted list into something readable as market structure.
  const spot =
    levels.length && levels[0].v!.pts != null
      ? (levels[0].v!.level as number) - (levels[0].v!.pts as number)
      : null;
  const spotIndex =
    spot == null ? -1 : levels.findIndex((l) => (l.v!.level as number) < spot);

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2 py-3">
        <div>
          <CardTitle className="text-base">Structural regime</CardTitle>
          <p className="mt-1 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
            confluence · levels in σ · state
          </p>
        </div>
        <Badge variant="outline" className={STATE_TONE[regime.state] ?? STATE_TONE.mixed}>
          {regime.label}
        </Badge>
      </CardHeader>

      <CardContent className="space-y-4">
        <p className="text-sm leading-relaxed text-muted-foreground">{regime.description}</p>

        {regime.evidence?.length ? (
          <ul className="space-y-1 border-t border-border/60 pt-3">
            {regime.evidence.map((e) => (
              <li key={e} className="flex gap-2 text-xs text-foreground/90">
                <span className="text-muted-foreground">·</span>
                {e}
              </li>
            ))}
          </ul>
        ) : null}

        {/* Confluence — the question neither gamma nor volume can answer alone. */}
        <div className="space-y-1.5 border-t border-border/60 pt-3">
          <div className="flex items-baseline justify-between gap-2 text-xs">
            {/* `gap_pts` is poc − pin (options/regime.py), so the label reads in
                that direction. Labelled "Pin vs POC" it said the pin sat below the
                POC when it sat above — the volume panel showed the same pair with
                the opposite sign. */}
            <span className="text-muted-foreground">POC vs pin</span>
            <span className="flex items-baseline gap-2">
              <span className="font-mono tabular-nums">
                {fmt(c?.pin)} / {fmt(c?.poc)}
              </span>
              {c?.gap_steps != null ? (
                <span
                  className="font-mono text-[11px] tabular-nums"
                  style={{ color: c.aligned ? PE_COLOR : CE_COLOR }}
                >
                  {c.gap_pts != null ? `${c.gap_pts > 0 ? "+" : "−"}${Math.abs(c.gap_pts).toFixed(0)}` : "—"}
                  {" · "}
                  {Math.abs(c.gap_steps).toFixed(2)} step
                </span>
              ) : (
                <span className="text-[11px] text-muted-foreground">not comparable</span>
              )}
            </span>
          </div>
          <p className="text-[11px] text-muted-foreground">
            {c?.aligned == null
              ? "No volume profile to compare against — this is not agreement."
              : c.aligned
                ? "Gamma magnet and traded value agree — the level has both mechanical and participatory support."
                : "Gamma magnet sits away from where business happened — structural only."}
            {c?.pin_in_value === false && c?.value_area
              ? ` Pin is outside the ${c.value_area.width_pts.toFixed(0)}-pt value area.`
              : ""}
          </p>
        </div>

        {/* Every level measured against the move the market is pricing. */}
        <div className="space-y-1 border-t border-border/60 pt-3">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              Levels in σ
            </span>
            <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
              1σ = {regime.sigma1_pts != null ? `${regime.sigma1_pts.toFixed(0)} pts` : "—"}
            </span>
          </div>
          {levels.map((l, i) => (
            <div key={l.key}>
              {i === spotIndex && spot != null ? (
                <div className="my-1 flex items-center gap-2">
                  <span className="h-px flex-1 bg-sky-500/40" aria-hidden />
                  <span
                    className="font-mono text-[11px] tabular-nums"
                    style={{ color: SPOT_LINE }}
                  >
                    spot {fmt(spot)}
                  </span>
                  <span className="h-px flex-1 bg-sky-500/40" aria-hidden />
                </div>
              ) : null}
            <div
              className={`flex items-baseline justify-between gap-2 text-xs ${sigmaTone(l.v!.sigma) ?? ""}`}
            >
              <span className="text-muted-foreground">{l.label}</span>
              <span className="flex items-baseline gap-2 font-mono tabular-nums">
                <span>{fmt(l.v!.level)}</span>
                <span
                  className="w-16 text-right text-[11px]"
                  style={{
                    color:
                      l.v!.sigma == null
                        ? undefined
                        : l.v!.sigma > 0
                          ? CE_COLOR
                          : l.v!.sigma < 0
                            ? PE_COLOR
                            : undefined,
                  }}
                >
                  {l.v!.sigma != null
                    ? `${l.v!.sigma > 0 ? "+" : "−"}${Math.abs(l.v!.sigma).toFixed(2)}σ`
                    : "—"}
                </span>
              </span>
            </div>
            </div>
          ))}
          <p className="pt-1 text-[11px] text-muted-foreground">
            Dimmed levels sit beyond one expected move on this session&apos;s vol.
          </p>
        </div>

        <p className="border-t border-border/60 pt-2.5 text-[11px] text-muted-foreground">
          Describes the book&apos;s structure only — no directional or positional read.
          {f?.box_pts != null
            ? ` Wall box ${f.box_pts.toFixed(0)} pts${
                f.box_sigma != null ? ` (${f.box_sigma.toFixed(2)}σ)` : ""
              }.`
            : ""}
        </p>
      </CardContent>
    </Card>
  );
}
